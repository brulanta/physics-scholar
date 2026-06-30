"""local MCP server：本地知识库工具（rag_tool + lookup_local_paper_id）。

迁移方式——**业务体零改写**，且天然消化掉 user_id 闭包：

rag_tool / lookup_local_paper_id 在内嵌世代是 `make_*(user_id)` **闭包工厂**，
产出的 `@tool` 签名本就是 `(query, k, section, doc_id)` / `(keywords,)`——
user_id 已烘进闭包、不在 LLM 可见 schema 里。故这里只需用本进程固定的
`USER_ID = PS_USER_ID` 调一次工厂，再 `to_fastmcp()` 原样转换：
  - 工具名钉死为原名（rag_tool / lookup_local_paper_id），
    MultiServerMCPClient 不加前缀，graph.py 的 SSE `ev["name"]` 透传不受影响。
  - schema/description 逐字搬运，无手抄转写风险。
  - 计划「删闭包」的目标（user_id 不出现在工具签名）由闭包本身天然满足，
    无需改动 rag_tool.py / lookup_local_paper_id.py。

## chroma 多进程访问（计划风险 #2）
import rag_tool 时其模块级 `vs = get_vectorstore()` 会在**本子进程内**打开
CHROMA_DIR（chromadb 1.5.x 用 SQLite 持久化）。父进程入库时也持有一份 Chroma
连接（写）。二者并发访问同一 SQLite 文件可能 `database is locked`——属本阶段
必测项，验证通过前不可视作稳态。
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from langchain_mcp_adapters.tools import to_fastmcp

from src.rag.tools.rag_tool import make_rag_tool
from src.rag.tools.lookup_local_paper_id import make_paper_id_search_tool
from src.core import chroma_gen

USER_ID = os.getenv("PS_USER_ID", "default")

# 记录启动时的向量库代际基线为「已生效」，首查不空转重建；之后主进程每次入库/删除
# bump 令牌，rag_tool 查询开头 ensure_fresh() 比对到推进才重建本子进程 chroma 连接。
chroma_gen.init()

mcp = FastMCP(
    "local",
    tools=[
        to_fastmcp(make_rag_tool(USER_ID)),
        to_fastmcp(make_paper_id_search_tool(USER_ID)),
    ],
)
