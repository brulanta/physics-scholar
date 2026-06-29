"""web MCP server：无状态网络检索工具（s2 + arxiv + openalex）。

迁移方式——**业务体零改写**：直接 import 原有的三个 langchain `@tool` 对象，
用 langchain_mcp_adapters 的 `to_fastmcp()` 原样转换为 FastMCP 工具。

`to_fastmcp` 会把 langchain 工具的 `name`、`description`、以及 args_schema 的完整
JSON schema（含每个 `Field(description=...)`）逐字搬过来，故：
  - 工具名天然钉死为原名（s2_search_tool / arxiv_tool / openalex_tool），
    无需手写 `@mcp.tool(name=...)`；MultiServerMCPClient 默认不加 server 前缀，
    graph.py:728 透传给 SSE 的 `ev["name"]` 与前端工具名映射不受影响。
  - 不必把 Pydantic 字段平铺成函数签名手抄 description（消除转写出错风险）。

三个网络工具各自的模块级速率锁（_S2_LOCK / _ARXIV_LOCK / _OA_LOCK 等）随模块
一起 import 进本子进程，天然与父进程隔离——前提是新旧路径互斥（PS_USE_MCP），
否则锁会分裂成两份导致超频 429。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from langchain_mcp_adapters.tools import to_fastmcp

from src.rag.tools.s2_tool import s2_search_tool
from src.rag.tools.arxiv_tool import arxiv_tool
from src.rag.tools.openalex_tool import openalex_tool

mcp = FastMCP(
    "web",
    tools=[
        to_fastmcp(s2_search_tool),
        to_fastmcp(arxiv_tool),
        to_fastmcp(openalex_tool),
    ],
)
