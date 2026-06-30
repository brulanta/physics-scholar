"""agent 侧 MCP client：进程级单例 + 常驻 stdio 会话。

## 为什么是常驻会话（而非每调用即弃）
langchain-mcp-adapters 0.3.0 的 `MultiServerMCPClient.get_tools()` 文档明确：
「A new session will be created for each tool call」——即默认每次工具调用都新起
一个子进程会话。这会击穿两件事：
  1. 速率锁失效：网络工具的模块级速率状态（_LAST_*_CALL/_BLOCK_UNTIL/失败缓存）
     随子进程重生而归零，限速形同虚设。
  2. 冷启动惩罚：local（chroma）/jina（sub_llm）每次调用都冷启，延迟不可接受。

故这里改用 adapters 官方的「持久会话」模式：用 `AsyncExitStack` 把每个 server 的
`client.session(name)` 在**进程存活期内持续开着**，工具用 `load_mcp_tools(live_session)`
绑定到这个常驻会话上。子进程长驻 → 速率锁正确持久、冷启只付一次。

对外仍暴露 `startup()/shutdown()/get_tools()/restart()` 这套接口（与迁移计划一致）。

## 传输形态（dev / frozen）
- frozen：子进程即 exe 自身，`command=sys.executable, args=[]`，靠 env `PS_MCP_SERVER` 分流。
- dev：`command=sys.executable, args=[app.py]`，dev 也走 app.py，路径与 frozen 一致。
两者 env 都注入 `PS_MCP_SERVER=name`、`PS_USER_ID` 及 `_inject_config_env()`（透传生效 key）。

## HTTP 留口
`_server_params` 现仅产 stdio；日后挂第三方 HTTP 工具时，在 `_active_connections()`
里追加 `{"transport": "streamable_http", "url": ...}` 即可，其余不动。
"""

from __future__ import annotations

import os
import sys
import asyncio
import functools
import contextlib
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from src.mcp_servers import _inject_config_env
from src.utils.logger import get_logger

logger = get_logger(__name__)


def _flatten_text_blocks(content):
    """把 adapters 产出的 list-of-text-blocks 展平回纯 str；其它形态原样返回。

    langchain-mcp-adapters 0.3.0 的 `_convert_call_tool_result` 恒把工具返回包成
    `[{"type":"text","text": "<JSON字符串>"}]`，破坏了老内嵌工具「返回纯 str」的行为
    （阶段 0 实施笔记 #5 / 阶段 1.5 P1-a）。本项目 6 工具全部只返回文本（JSON 字符串或纯
    文本），故「全为 text block」时拼回纯 str，行为与内嵌世代一致；一旦出现 image/file
    等非文本 block（理论上本项目不会），保持原 list 不动，不破坏多模态。
    """
    if (
        isinstance(content, list)
        and content
        and all(isinstance(b, dict) and b.get("type") == "text" for b in content)
    ):
        return "".join(b.get("text", "") for b in content)
    return content


def _patch_tool_flatten(tool):
    """就地包装单个 adapter 工具的成功/错误两条出口，使 ToolMessage.content 回归纯 str。

    - 成功路径：工具 `coroutine` 返回 `(content, artifact)`（response_format=
      content_and_artifact）；展平 content、保留 artifact。
    - 错误路径：MCP `isError=True` 经 `handle_tool_error` 回调返回 list-of-blocks，
      不走 coroutine 返回值；单独包一层，展平后 `status="error"` 不受影响。
    两处包装均为幂等纯转换，探针实测保留 artifact 与 status。返回工具本身（就地改）。
    """
    orig_coro = getattr(tool, "coroutine", None)
    if orig_coro is not None:

        @functools.wraps(orig_coro)
        async def _coro(*args, **kwargs):
            result = await orig_coro(*args, **kwargs)
            # content_and_artifact 形态：(content, artifact)
            if isinstance(result, tuple) and len(result) == 2:
                content, artifact = result
                return _flatten_text_blocks(content), artifact
            return _flatten_text_blocks(result)

        tool.coroutine = _coro

    err_handler = getattr(tool, "handle_tool_error", None)
    if callable(err_handler):

        @functools.wraps(err_handler)
        def _err(exc):
            return _flatten_text_blocks(err_handler(exc))

        tool.handle_tool_error = _err

    return tool

# 项目根（与 app.py 的 ROOT 同义：dev 下为仓库根，frozen 下为 _MEIPASS）
if getattr(sys, "frozen", False):
    ROOT = Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    ROOT = Path(__file__).resolve().parent.parent.parent

USER_ID = os.getenv("PS_USER_ID", "default")

# 阶段 0 挂 web（无状态网络工具）；阶段 1 并入 local（rag + lookup，依赖 chroma）；
# 阶段 2 并入 jina（依赖 sub_llm 分片打分）。至此 6 工具全部 MCP 化。
# 未挂载的工具仍走老内嵌路径（PS_USE_MCP 互斥）。
_ACTIVE_SERVERS: tuple[str, ...] = ("web", "local", "jina")


def _server_params(name: str) -> dict:
    """构造单个 stdio server 的 StdioConnection 配置（dev/frozen 分形态）。"""
    if getattr(sys, "frozen", False):
        command = sys.executable
        args: list[str] = []
    else:
        command = sys.executable
        args = [str(ROOT / "app.py")]

    # 从完整 os.environ 起步，保证子进程拿到 PATH/SystemRoot 等系统必需变量
    # （MCP stdio client 在 env 非 None 时按原样传递，不再补默认值）；
    # 再叠加分流标志与生效配置 key（env 优先于 yaml，天然覆盖）。
    env = dict(os.environ)
    env["PS_MCP_SERVER"] = name
    env["PS_USER_ID"] = USER_ID
    env.update(_inject_config_env())

    return {
        "transport": "stdio",
        "command": command,
        "args": args,
        "env": env,
        "cwd": str(ROOT),
    }


def _active_connections() -> dict[str, dict]:
    """当前要拉起的 server 连接表。日后第三方 HTTP 工具在此追加。"""
    return {name: _server_params(name) for name in _ACTIVE_SERVERS}


class _MCPClientSingleton:
    """进程级单例：常驻会话 + 工具缓存。"""

    def __init__(self) -> None:
        self._client: MultiServerMCPClient | None = None
        self._stack: contextlib.AsyncExitStack | None = None
        self._tools: list = []
        self._lock = asyncio.Lock()

    async def startup(self) -> None:
        """拉起所有 active server 的常驻 stdio 会话并缓存工具。lifespan 调一次。"""
        async with self._lock:
            if self._stack is not None:
                logger.warning("[mcp] startup 重复调用，已忽略")
                return
            connections = _active_connections()
            self._client = MultiServerMCPClient(connections)
            self._stack = contextlib.AsyncExitStack()
            tools: list = []
            for name in connections:
                session = await self._stack.enter_async_context(
                    self._client.session(name)
                )
                server_tools = await load_mcp_tools(session, server_name=name)
                # P1-a：把 adapter 的 list-of-text-blocks 出口展平回纯 str，
                # 与老内嵌工具行为一致（避免下游对 list 调 .split() 等炸裂）。
                server_tools = [_patch_tool_flatten(t) for t in server_tools]
                tools.extend(server_tools)
                logger.info(
                    "[mcp] server '%s' 已就绪，加载 %d 个工具：%s",
                    name,
                    len(server_tools),
                    [t.name for t in server_tools],
                )
            self._tools = tools

    async def shutdown(self) -> None:
        """关闭所有常驻会话（子进程随之退出）。lifespan 退出时调用。"""
        async with self._lock:
            if self._stack is None:
                return
            # stdio 会话基于 anyio task group，teardown 偶发抛 cancel-scope 噪声，
            # 静默吞掉（进程即将退出，子进程会被一并回收）。
            with contextlib.suppress(BaseException):
                await self._stack.aclose()
            self._stack = None
            self._client = None
            self._tools = []
            logger.info("[mcp] 所有 MCP 会话已关闭")

    async def restart(self) -> None:
        """重启所有会话，让子进程重读 yaml 拿新 key（配置热重载后调用）。"""
        logger.info("[mcp] 重启 MCP 会话以应用新配置")
        await self.shutdown()
        await self.startup()

    def get_tools(self) -> list:
        """返回已缓存的 langchain 工具列表（startup 后同步可取）。"""
        return self._tools


# 进程级单例
_singleton = _MCPClientSingleton()


async def startup() -> None:
    await _singleton.startup()


async def shutdown() -> None:
    await _singleton.shutdown()


async def restart() -> None:
    await _singleton.restart()


def get_tools() -> list:
    return _singleton.get_tools()
