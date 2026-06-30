"""jina MCP server：网页/PDF 全文阅读工具（jina_tool）。

单独成 server 的理由（计划「server 分组」）：jina_tool 依赖 sub_llm 做分片打分、
长文档逐片调用耗时长，独占一个子进程便于按需独立重启、与 local/web 互不拖累。

迁移方式——**业务体零改写**：jina_tool 是普通 `@tool(args_schema=JinaRequest)`，
无 user_id 闭包，直接 import 原对象 + `to_fastmcp()` 原样转换：
  - 工具名天然钉死为原名（jina_tool），MultiServerMCPClient 默认不加前缀，
    graph.py 透传给 SSE 的 `ev["name"]` 与前端工具名映射不受影响。
  - schema/description（JinaRequest 各 Field）逐字搬运，无手抄转写风险。

## 子进程内的依赖重建
- `sub_llm`（src/llm.py）在本子进程 import 时按 config 常量重建；config 又读父进程
  经 _inject_config_env() 注入的 SUB_*/MAIN_* env（env 优先于 yaml），故打分用的副
  LLM 在子进程内正确成形——无需父进程把 sub_llm 句柄传进来。
- 打分 system prompt 用 jina_tool 内硬编码的 SLICE_SYSTEM_PROMPT 默认值；全仓无任何
  地方调用 set_slice_system_prompt 做运行期注入，故父/子进程行为一致。

## 速率锁
jina_tool 的模块级速率状态（_JINA_LOCK / _LAST_JINA_CALL / _JINA_BLOCK_UNTIL /
_RECENT_FAILED）随模块 import 进本子进程，常驻会话期间持久生效、与父进程隔离——
前提同样是新旧路径互斥（PS_USE_MCP），否则锁分裂成两份会超频 429。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from langchain_mcp_adapters.tools import to_fastmcp

from src.rag.tools.jina_tool import jina_tool

mcp = FastMCP(
    "jina",
    tools=[
        to_fastmcp(jina_tool),
    ],
)
