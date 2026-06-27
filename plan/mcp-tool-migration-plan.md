# 工具 MCP 封装与转移：内嵌工具 → 本地 stdio MCP server，agent → MCP client

## Context（为什么做这件事）

PhysicsScholar 现有 6 个 agent 工具全部以 langchain `@tool` 形式**内嵌在主程序**里，由 `build_agent(user_id)`（[src/rag/graph.py:342-354](../src/rag/graph.py#L342-L354)）直接 `bind_tools` + `ToolNode`。用户希望：

1. **解耦代码**——工具从 agent 主程序剥离，独立成进程。
2. **独立运行环境**——工具跑在自己的子进程里。
3. **为日后插拔第三方工具铺路**——agent 改造成 MCP client 后，未来可同时挂载本地 stdio 工具与第三方 HTTP MCP 工具。

本次只做**内部解耦**（本地 stdio，暂不对外部客户端开放）。多租户 `user_id` 已退化为单用户（全项目默认 `"default"`），借此次一并简化掉闭包。

预期结果：6 个工具搬进 `src/mcp_servers/` 下的 3 个 stdio server，agent 经 `MultiServerMCPClient` 消费，业务逻辑零改写，端到端行为与现状一致，并为 HTTP 第三方工具留好挂载口。

## 关键约束（用户已确认）

- 传输：原生工具用 **stdio 子进程**；第三方日后走 HTTP（本次不实现，架构留口）。
- server 分组：**`rag_tool` + `lookup_local_paper_id` 一个 server**（local）；`s2`/`arxiv`/`openalex` 一个 server（web）；`jina` 单独一个 server（jina，因依赖 sub_llm、耗时长、可独立重启）。
- `user_id`：去掉闭包，server 内固定 `os.getenv("PS_USER_ID", "default")`，**不进 LLM 可见的工具 schema**。
- 新旧路径**互斥**：用开关 `PS_USE_MCP` 控制，绝不同时激活（否则速率限制锁分裂成两份，超频被 429）。

## 现状关键事实

- 6 工具均 `@tool(args_schema=PydanticModel)`，返回 **JSON 字符串**（`rag_tool` 返回纯文本），统一含 `success`/`error_type`/`agent_hint`。
- 网络工具各带模块级全局速率限制状态（线程锁 + `_LAST_CALL` + `_BLOCK_UNTIL` + 失败缓存）：[s2_tool.py:56-58](../src/rag/tools/s2_tool.py#L56)、[arxiv_tool.py:42-44](../src/rag/tools/arxiv_tool.py#L42)、[openalex_tool.py:49-51](../src/rag/tools/openalex_tool.py#L49)、[jina_tool.py:69-71](../src/rag/tools/jina_tool.py#L69)。搬进单进程 server 后天然隔离正确（前提：新旧互斥）。
- `rag_tool` 依赖 [ingestor.get_vectorstore()](../src/core/ingestor.py#L16)（模块级单例 Chroma）、`chunker`、`config.RAG_*/RERANK_*`；`jina_tool` 依赖 [src/llm.py](../src/llm.py) 的 `sub_llm`。
- 打包：PyInstaller onedir，入口 [app.py](../app.py)；frozen 时 `sys.executable` 指向 exe 自身，重启用 `subprocess.Popen([sys.executable])`（[app.py:54](../app.py#L54)）。tray 退出/重启用 `os._exit(0)`（[app.py:55](../app.py#L55)、[app.py:59](../app.py#L59)）**绕过 lifespan**。
- lifespan 现仅 `init_db()`（[src/main.py:11-14](../src/main.py#L11-L14)）。
- 依赖缺口：`requirements.txt` 无 `mcp`、无 `langchain-mcp-adapters`。当前 langchain==1.2.13 / langchain-core==1.2.23 / langgraph==1.1.3 / pydantic==2.12.5。

## 实现方案

### 选型
- server：官方 `mcp` SDK 的 `mcp.server.fastmcp.FastMCP`，`@mcp.tool()` + `mcp.run(transport="stdio")`。
- client：`langchain_mcp_adapters.client.MultiServerMCPClient`，`get_tools()` 产出标准 `BaseTool`，兼容 `bind_tools`/`ToolNode`。

### 新增文件
```
src/mcp_servers/
  __init__.py        # run_server(name) 分发；_inject_config_env() 把生效 key 写入子进程 env
  local_server.py    # rag_tool + lookup_local_paper_id
  web_server.py      # s2 + arxiv + openalex
  jina_server.py     # jina
src/rag/mcp_client.py # MultiServerMCPClient 单例：startup/shutdown/restart/get_tools + _server_params(dev/frozen 分形态)
```

### 工具迁移方式（业务体零改写）
- 业务逻辑函数（`hybrid_search`/`_rerank`/`format_context`/`build_filter`、各 `_rate_limit`/`_error_payload`/请求逻辑）**原样保留**，server 文件 import 后调用。
- 暴露层 `@tool` → `@mcp.tool(name="<原名>")`：**显式钉死原工具名**，避免 adapters 加 server 前缀影响 [graph.py:728](../src/rag/graph.py#L728) `on_tool_start` 透传给 SSE 的 `ev["name"]` 与前端工具名映射。
- schema 复用：Pydantic 字段平铺成函数签名 `Annotated[type, Field(description=...)]`，`Field(description=...)` **逐字一字不差抄写**（如 [RagToolRequest](../src/rag/tools/rag_tool.py#L111)），直接影响 LLM 调用质量。
- FastMCP 工具直接返回 str（含现有 JSON 字符串）OK，adapters 还原为 `ToolMessage.content`，[graph.py:120](../src/rag/graph.py#L120) `_tool_ok` 逻辑不改。
- 删 `make_rag_tool(user_id)`/`make_paper_id_search_tool(user_id)` 闭包，server 内 `USER_ID = os.getenv("PS_USER_ID", "default")`，工具签名不出现 user_id。

### frozen 子进程拉起（入口分流）
[app.py](../app.py) 顶部 `sys.path.insert`（[app.py:10](../app.py#L10)）之后、`import uvicorn` 之前插入 env 分流：
```python
import os, sys
if os.environ.get("PS_MCP_SERVER"):          # local / web / jina
    from src.mcp_servers import run_server
    run_server(os.environ["PS_MCP_SERVER"])   # mcp.run(stdio)，阻塞
    sys.exit(0)
# 否则继续原 tray-app
```
`_server_params(name)`（在 mcp_client.py）：
- frozen：`command=sys.executable, args=[]`
- dev：`command=sys.executable, args=[str(ROOT/"app.py")]`（dev 也走 app.py，路径与 frozen 一致）
- 两者 `env` 都注入 `PS_MCP_SERVER=name`、`PS_USER_ID=default` 及 `_inject_config_env()`（透传 MAIN/SUB/EMBEDDING/JINA/S2/OPENALEX key；config.py 的 env 优先于 yaml，天然覆盖）。

### MultiServerMCPClient（留 http 口）
```python
MultiServerMCPClient({
    "local": _server_params("local"),
    "web":   _server_params("web"),
    "jina":  _server_params("jina"),
    # 日后: "thirdparty": {"url": "...", "transport": "streamable_http"},
})
```

### agent 侧改造（单例 + 生命周期）
- `get_tools()` 是 async 且 `build_agent` 每轮调用 → **进程级单例**，lifespan 启动时加载一次缓存（[src/main.py:11-14](../src/main.py#L11-L14) 加 `await mc.startup()` / `await mc.shutdown()`）。
- [build_agent](../src/rag/graph.py#L342) 改用 `get_tools()` 缓存，删 [graph.py:343-344](../src/rag/graph.py#L343) 的 `make_*` 与对应 import；graph 结构、`call_llm`、流式 `_consume_events` 零改动。adapters tool 异步执行，项目走 `ainvoke`/`astream_events`（[graph.py:509](../src/rag/graph.py#L509)）匹配。
- **孤儿子进程防护**：[app.py:52-59](../app.py#L52-L59) 的 `on_restart`/`on_exit` 在 `os._exit` 前显式 terminate 子进程 pid（lifespan 被 `os._exit` 绕过）。
- **热重载传播**：`update_config`（[routes.py:62-64](../src/api/routes.py#L62) 区域）末尾 `await mc.restart()`，让子进程重读 yaml 拿新 key。

### 版本兼容（已核实，2026-06 PyPI）
`langchain-mcp-adapters` 最新版 **0.3.0**（2026-06-10）依赖：`langchain-core>=1.0.0,<2.0.0`、`mcp>=1.9.2`、`typing-extensions>=4.14.0`。逐条对照项目现状均满足：langchain-core 1.2.23 落在区间内（**不降级**）、typing-extensions 4.15.0 满足、adapters **不约束 pydantic**（2.12.5 安全）。结论：**无依赖地狱，直接用 adapters，原兜底的自写胶水方案用不上。**

**关键：必须 pin `langchain-mcp-adapters>=0.3.0`。** 因为 0.2.x（末版 0.2.2，2026-03）仍停在 `langchain-core<1` 旧世代，若不写下限，pip 可能解析到 0.2.x 反向把项目 langchain 降级、踩坏 agent。

仍需实装时确认一项（非阻塞）：`get_tools()` 产出能被 langchain 1.2.x 的 `bind_tools`/`ToolNode` 正常吃下（接口层面应兼容，跑通即可）。兜底（极不可能触发）：弃用 adapters，自写薄封装（`mcp` client SDK 调 `call_tool` + `StructuredTool.from_function` 包成 langchain tool），架构其余不变。

### 打包
[physics_scholar.spec](../physics_scholar.spec) hiddenimports 补 `mcp`、`langchain_mcp_adapters`、stdio/anyio 相关、`src.mcp_servers.*`，否则 frozen 子进程 import 失败。

## 分阶段实施与验证（每阶段可独立回滚：`PS_USE_MCP=false`）

- **阶段 0（脚手架 + web）**：加依赖、建 `src/mcp_servers/` 骨架、app.py 分流、mcp_client.py 单例、lifespan 接线。**先只挂 web_server**（无状态、不依赖 chroma/sub_llm，最易跑通 stdio 端到端），其余仍走老内嵌。
  验证：`get_tools()` 见 3 个 web 工具；触发 s2 检索的 query → SSE `tool_start`/`tool_end` 正常、返回含 `success`；连发两次看速率锁在子进程生效。
- **阶段 1（local）**：迁 `rag_tool`+`lookup`。验证：chroma 在子进程初始化、`user_id=default` 过滤正确、rerank 走通；对已入库论文提问对比 body 文本一致；lookup 返回 doc_id 正确。
  **并发专测（高风险）**：上传入库（主进程写 chroma，[ingestor.py:37](../src/core/ingestor.py#L37) `write_to_chroma`）与 local server（子进程读同一 `CHROMA_DIR`）同时发生，看是否 `database is locked` / 读陈旧 HNSW。若锁冲突，缓解：入库低频可串行化，或评估把写也搬进 local server 独占 chroma。
- **阶段 2（jina）**：迁 `jina`。验证：sub_llm 在子进程重建、分块打分耗时可接受、长文档不超时；url+query 看 scored_chunks，无 query 看全文截断。
- **阶段 3（收尾）**：三 server 全切，封存老接线，补 spec hiddenimports，`pyinstaller physics_scholar.spec` 打包验证 frozen 子进程能起、工具可调、tray 退出无孤儿进程。

## 风险点（按概率排序）

1. **frozen 子进程孤儿（高）**：`os._exit(0)` 绕过 lifespan → tray 回调显式 terminate pid。
2. **ChromaDB 多进程访问同一目录（高）**：主进程写 + 子进程读，chromadb 1.5.5 用 SQLite 持久化，可能 `database is locked`。阶段 1 必测。
3. **子进程重复初始化开销（中）**：embeddings/Chroma/sub_llm 冷启，lifespan startup 一次性付清（单例长驻）；`wait_and_open_browser` 轮询 `/api/health`（[app.py:29](../app.py#L29)）能容忍。
4. **速率限制状态分裂（中）**：新旧并存 = 两份锁，回滚开关必须互斥。
5. **子进程启动失败静默（中）**：[app.py:12-15](../app.py#L12-L15) 把 None stdout/stderr 重定向 devnull 会吞子进程报错 → startup 加超时+失败降级，子进程 stderr 接日志文件。
6. **工具名前缀（中）**：`@mcp.tool(name=...)` 钉死原名。
7. **版本兼容（已解除）**：langchain-mcp-adapters 0.3.0 要求 langchain-core>=1.0.0,<2.0.0，项目 1.2.23 满足、不降级；须 pin `>=0.3.0` 避开 0.2.x 旧世代。详见「版本兼容」小节。
8. **spec 打包遗漏（低但必现）**：hiddenimports 补全。

## 文件级改动清单

**新增**：`src/mcp_servers/{__init__,local_server,web_server,jina_server}.py`、`src/rag/mcp_client.py`
**修改**：
- [app.py](../app.py)：顶部 `PS_MCP_SERVER` 分流；tray restart/exit terminate 子进程
- [src/main.py](../src/main.py)：lifespan 加 `mc.startup/shutdown`
- [src/rag/graph.py](../src/rag/graph.py)：`build_agent` 改用 `get_tools()`，删 `make_*` 两行 + import
- [src/api/routes.py](../src/api/routes.py)：`update_config` 后 `await mc.restart()`
- [requirements.txt](../requirements.txt)：加 `mcp`、`langchain-mcp-adapters>=0.3.0`（下限必写）
- [physics_scholar.spec](../physics_scholar.spec)：hiddenimports 补全
- （可选）`src/rag/tools/*.py`：闭包 `user_id` 改读 `PS_USER_ID`，业务体不动

## 端到端验证

1. `pytest`（确认非 live 基线不被破坏）。
2. dev：`uvicorn src.main:app --reload`，前端发触发各工具的 query，看 SSE `tool_start/tool_end`、返回 `success`、速率锁生效、入库+检索并发无锁冲突。
3. frozen：`pyinstaller physics_scholar.spec` → 跑 exe，验证 3 个子进程可起、工具可调、tray 退出无孤儿进程（任务管理器核对）。

---

## 评审与往返（供用户注释）

> 此区块留给用户写评审意见 / 注入修改点。Claude 读取后在下方书面回应，逐条闭环。
