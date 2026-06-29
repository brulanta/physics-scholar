"""工具运行时开关：新旧工具路径的互斥总闸。

`PS_USE_MCP=true` 时，agent 的工具来自本地 stdio MCP server（src/mcp_servers/）；
否则走老的内嵌 langchain `@tool` 路径。两条路径**绝不同时激活**——否则网络工具
的模块级速率锁会分裂成「父进程一份 + 子进程一份」，并发请求各自计时，超频被 429。

单独成模块（而非塞进 config.py）是因为 config.py 聚焦「用户可配置的 yaml/.env」，
而本开关是开发/迁移期的部署态总闸，定位不同；放这里也避免 main.py / graph.py
各读各的导致两处取值漂移。
"""

from __future__ import annotations

import os

USE_MCP: bool = os.getenv("PS_USE_MCP", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
