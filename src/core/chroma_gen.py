"""ChromaDB 跨进程「写后读」可见性：代际令牌 + 惰性重建（单一真相源）。

## 背景
MCP 迁移后 RAG 读路径拆进**常驻** local MCP 子进程，子进程启动时 warm 了一份进程内 HNSW
索引。主进程（FastAPI）后续入库的新文档，子进程的 `similarity_search`（HNSW 内存路）看不到，
只有 `_collection.get`（SQLite 直读，BM25 路）看得到 → `hybrid_search` 脑裂、向量召回静默劣化
直到子进程重启。真实翻车场景＝「上传论文→立刻提问该篇」。

## 方案：惰性代际检查（pull 式，去风暴）
- 主进程入库/删除成功后 `bump()` 一个轻量代际令牌（chroma 目录下 sentinel 文件，内容为单调
  time_ns）。
- local server 的 rag_tool 每次查询开头 `ensure_fresh()` 比对令牌，已推进才驱逐 chroma 缓存并
  重建向量库连接，否则照用。
- N 篇并行入库 bump N 次，下一次查询只重建 1 次（自动合并，无风暴）；只动 local 自己的 chroma
  连接，web/jina 子进程与速率锁完全不碰；从不 teardown，无 get_tools() 空窗竞态。

## 决定性实测发现（为何必须 clear_system_cache）
chromadb 1.5.x 的 `SharedSystemClient` 把 `System`（含 Rust HNSW segment / SQLite 连接）按
`persist_directory` 缓存在**进程级 ClassVar 字典**里。只 reset `ingestor._vectorstore=None` 再
`Chroma(persist_directory=...)` 会拿回**同一个缓存 System / 旧 HNSW**，看不到新写入。两进程探针
坐实：不清缓存命中 0，`clear_system_cache()` + 重建后命中。故必须先驱逐缓存，再重建。
"""

from __future__ import annotations

import os
import time
import threading

from src.config import CHROMA_DIR

_SENTINEL = CHROMA_DIR / ".gen_token"  # 内容 = 单调 time_ns 字符串；在 data/ 下，gitignored
_lock = threading.Lock()
_last_seen: str | None = None  # 进程内已生效的令牌


def bump() -> None:
    """主进程入库/删除成功后调用：原子写一个新令牌（tmp + os.replace，免 torn read）。

    令牌写失败不阻断入库主流程——最坏退化为旧行为（子进程看不到新文档），不引入新故障面。
    """
    try:
        token = str(time.time_ns())
        tmp = _SENTINEL.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(token)
        os.replace(tmp, _SENTINEL)  # Windows 上原子替换
    except OSError:
        pass


def _read() -> str | None:
    try:
        return _SENTINEL.read_text().strip()
    except OSError:
        return None


def init() -> None:
    """子进程启动时把基线令牌记为已生效，避免首查无谓重建。"""
    global _last_seen
    _last_seen = _read()


def ensure_fresh() -> None:
    """rag_tool 查询开头调用：令牌推进才驱逐 chroma 缓存并重建向量库连接。

    仅在 MCP server 子进程内生效（靠 PS_MCP_SERVER 门控）；内嵌（旧）路径为 no-op，保持
    PS_USE_MCP=false 行为零变化。
    """
    if not os.getenv("PS_MCP_SERVER"):
        return
    global _last_seen
    cur = _read()
    if cur == _last_seen:
        return
    with _lock:  # 多并发工具调用下只重建一次（双检锁）
        cur = _read()
        if cur == _last_seen:
            return
        _rebuild()
        _last_seen = cur


def _rebuild() -> None:
    # 延迟 import：避免模块级循环（chroma_gen 只依赖 config；ingestor/rag_tool 反过来依赖
    # chroma_gen）。clear_system_cache 是关键步——见模块 docstring「决定性实测发现」。
    from chromadb.api.shared_system_client import SharedSystemClient
    from src.core import ingestor
    from src.rag.tools import rag_tool

    SharedSystemClient.clear_system_cache()  # ★ 驱逐进程级缓存 System/HNSW
    ingestor._vectorstore = None  # 下次 get_vectorstore 真正重建 Chroma
    rag_tool.vs = ingestor.get_vectorstore()  # 回写模块全局，hybrid_search/直接路同时生效
