"""本地 stdio MCP server 集合。

把原本内嵌在 agent 主程序里的 6 个 langchain `@tool` 拆成 3 个独立子进程：
  - local : rag_tool + lookup_local_paper_id（依赖 chroma 单例）
  - web   : s2 + arxiv + openalex（无状态网络工具）
  - jina  : jina（依赖 sub_llm，耗时长，可独立重启）

每个子进程由 app.py 顶部的 `PS_MCP_SERVER` 分流拉起，调用 `run_server(name)`
阻塞运行 `mcp.run(transport="stdio")`。父进程（agent）经 MultiServerMCPClient
消费，见 src/rag/mcp_client.py。
"""

from __future__ import annotations

# 父进程把生效配置透传给子进程时用的「config 常量 → config.py 环境变量名」映射。
# config.py 的 _get() 是 env 优先于 yaml，故子进程拿到这些 env 后天然覆盖 yaml，
# 与父进程（.env / yaml 解析后的结果）保持一致。
_CONFIG_ENV_MAP: dict[str, str] = {
    "MAIN_API_KEY": "MAIN_LLM_API_KEY",
    "MAIN_BASE_URL": "MAIN_LLM_BASE_URL",
    "MAIN_MODEL": "MAIN_LLM_MODEL",
    "SUB_API_KEY": "SUB_LLM_API_KEY",
    "SUB_BASE_URL": "SUB_LLM_BASE_URL",
    "SUB_MODEL": "SUB_LLM_MODEL",
    "JINA_API_KEY": "JINA_API_KEY",
    "S2_API_KEY": "S2_API_KEY",
    "OPENALEX_EMAIL": "OPENALEX_EMAIL",
    "OPENALEX_API_KEY": "OPENALEX_API_KEY",
    "EMBEDDING_API_KEY": "EMBEDDING_API_KEY",
    "EMBEDDING_BASE_URL": "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL": "EMBEDDING_MODEL",
}


def _inject_config_env() -> dict[str, str]:
    """把父进程已解析的生效配置（.env > yaml）导出为子进程环境变量。

    只导出非空值，避免用空串遮蔽子进程自己能读到的 yaml。
    返回的 dict 由 mcp_client._server_params 合并进子进程 env。
    """
    from src import config

    env: dict[str, str] = {}
    for env_key, const_name in _CONFIG_ENV_MAP.items():
        val = getattr(config, const_name, "")
        if val:
            env[env_key] = str(val)
    return env


def run_server(name: str) -> None:
    """子进程入口：按 name 加载对应 FastMCP server 并以 stdio 阻塞运行。"""
    if name == "web":
        from src.mcp_servers.web_server import mcp
    elif name == "local":
        from src.mcp_servers.local_server import mcp
    elif name == "jina":
        from src.mcp_servers.jina_server import mcp
    else:
        raise ValueError(f"未知的 MCP server 名称: {name!r}（应为 local/web/jina）")

    mcp.run(transport="stdio")
