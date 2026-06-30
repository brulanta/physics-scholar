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

- **阶段 0（脚手架 + web）✅ 已完成（2026-06-29）**：加依赖、建 `src/mcp_servers/` 骨架、app.py 分流、mcp_client.py 单例、lifespan 接线。**先只挂 web_server**（无状态、不依赖 chroma/sub_llm，最易跑通 stdio 端到端），其余仍走老内嵌。
  验证（全部通过）：`get_tools()` 见 3 个 web 工具（名/描述/schema 原样）；arxiv+s2 真实网络往返 `success=True`；LLM 完整闭环（调 arxiv → 回喂 ToolMessage → 正常出最终答）；连发两次 arxiv，第二次多等 ~1.7s → 速率锁在子进程持久生效；`PS_USE_MCP=false` 时所有模块 import 干净、走老内嵌路径。
  **实施中发现 5 处与本计划设想不一致，详见下方「阶段 0 实施笔记」——架构未变，仅 client 侧实现手段与若干 API 名称调整。**
- **阶段 1（local）✅ 已完成（2026-06-29）**：迁 `rag_tool`+`lookup`。验证（全部通过）：`get_tools()` 见 5 工具（web 3 + local 2），rag_tool schema=`[query,k,section,doc_id]`**不含 user_id**；子进程内 chroma 初始化、`user_id=default` 过滤正确、rerank 走通（过取 30→重排 3）；lookup 返回正确 doc_id；**body 文本 MCP vs 内嵌逐字节一致**（BYTE-IDENTICAL，已排除 rerank API 服务端偶发抖动的干扰）。
  **并发专测（高风险）通过**：MCP 子进程持续检索（读）与父进程 delete+重新入库（写 `write_to_chroma`）交错并发，5 写 4 读零错误、**无 `database is locked`**。chromadb 1.5.x 走 SQLite + 各进程独立连接，本地单用户入库低频场景未触发锁冲突，稳态可接受（若日后高并发触发，缓解仍是入库串行化或把写也收进 local server）。
  实现细节：rag_tool/lookup 在内嵌世代是 `make_*(user_id)` **闭包工厂**，闭包已把 user_id 烘进去、产出的 `@tool` 签名本就不含 user_id，故 local_server 直接 `to_fastmcp(make_*(USER_ID))` 即满足计划「删闭包、user_id 不进 schema」目标，**无需改 rag_tool.py / lookup_local_paper_id.py**；graph.py 的 build_agent 已是按 `mcp_names` 剔除同名内嵌工具的通用逻辑，阶段 1 仅 `_ACTIVE_SERVERS` 加 `"local"`，**graph.py 零改动**。
- **阶段 1.5（第三方质询闭环）✅ 已完成（2026-06-30）**：阶段 1 经第三方（Gemini）质询 + 本地实测，暴露 1 个 P0 真回归 + 3 项技术债/硬伤。**四项（P0-a 写后读 + 句柄泄漏二轮、P0-b frozen 路径、P1-a content 展平、P1-b freeze_support）全部修复落地并各自验证**，详见下方「阶段 1 第三方质询闭环」。frozen 形态的实机核验（Job Object kill-on-close 真连带回收子进程树）留阶段 3。
- **阶段 2（jina）**：迁 `jina`。验证：sub_llm 在子进程重建、分块打分耗时可接受、长文档不超时；url+query 看 scored_chunks，无 query 看全文截断。
- **阶段 3（收尾）**：三 server 全切，封存老接线，补 spec hiddenimports，`pyinstaller physics_scholar.spec` 打包验证 frozen 子进程能起、工具可调、tray 退出无孤儿进程。**追加 frozen 硬伤修复（见下方质询闭环 P0-b / P1-b）**：① config.py ROOT 加 `sys.frozen` 分支（否则 data/chroma/SQLite 写进 `_MEIPASS` 临时目录、重启即丢）；② 入口首行 `multiprocessing.freeze_support()` 防套娃；③ 实机任务管理器核验 Job Object 绑的是顶层 Bootloader PID、kill-on-close 真生效。

## 风险点（按概率排序）

1. **frozen 子进程孤儿（高）**：`os._exit(0)` 绕过 lifespan。**阶段 0 改用 Windows Job Object（kill-on-close）兜底**（见阶段 0 实施笔记末），不再手动 terminate pid；frozen 形态待阶段 3 实测。
2. **ChromaDB 多进程访问同一目录（高）**：主进程写 + 子进程读，chromadb 1.5.5 用 SQLite 持久化，可能 `database is locked`。阶段 1 必测。
3. **子进程重复初始化开销（中）**：embeddings/Chroma/sub_llm 冷启，lifespan startup 一次性付清（单例长驻）；`wait_and_open_browser` 轮询 `/api/health`（[app.py:29](../app.py#L29)）能容忍。
4. **速率限制状态分裂（中）**：新旧并存 = 两份锁，回滚开关必须互斥。
5. **子进程启动失败静默（中）→ 已暴露真根因并修复**：真正的崩溃源是日志写 stdout 污染 JSON-RPC（见阶段 0 实施笔记 #4），已改 logger 走 stderr。lifespan startup 仍含 try/except 失败降级（MCP 不可用不阻断启动）。
6. **工具名前缀（中）→ 已解除**：`tool_name_prefix=False`（默认）+ `to_fastmcp` 保留原名，无需 `@mcp.tool(name=...)`（见实施笔记 #2）。
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

## 阶段 0 实施笔记（2026-06-29，落地与计划设想的偏差）

架构整体未推翻——3 个 stdio server / app.py 分流 / `PS_USE_MCP` 互斥 / agent-as-client / HTTP 留口全部保留。以下 5 处是实现细节修正，后续阶段 1/2/3 须沿用：

1. **`to_fastmcp()` 取代手工平铺签名（简化，消除最大转写风险）**：langchain-mcp-adapters 0.3.0 自带 `to_fastmcp(langchain_tool)`，把现有 `@tool` 对象的 `name`/`description`/args_schema 完整 JSON **逐字**转成 FastMCP 工具。故计划「实现方案 > 工具迁移方式」里『Pydantic 字段平铺成函数签名、`Field(description=...)` 逐字抄写』**整段不再需要**，server 文件只 import 原工具对象 + `to_fastmcp`。阶段 1/2（local/jina）同样这么做（rag_tool/lookup/jina 都是现成 `@tool`）。
2. **工具名天然钉死，无需 `@mcp.tool(name=...)`**：`MultiServerMCPClient(tool_name_prefix=False)` 是默认值，client 不加 server 前缀；`to_fastmcp` 又保留原名。故计划风险 #6 与「显式钉死原工具名」自动满足，`graph.py:728` 的 `ev["name"]` 透传不受影响（已验证 3 工具名 = s2_search_tool/arxiv_tool/openalex_tool）。
3. **`MultiServerMCPClient` 无 `startup/shutdown`，改用「常驻会话」模式（核心偏差）**：0.3.0 的 `get_tools()` 文档明写「A new session will be created for each tool call」——默认每次工具调用新起子进程会话，会击穿速率锁（每调用重生 → 锁归零）与冷启动。`mcp_client.py` 改为用 `AsyncExitStack` 把 `client.session(name)` 在进程存活期常驻，`load_mcp_tools(live_session)` 绑定到常驻会话；对外仍暴露计划承诺的 `startup/shutdown/restart/get_tools`。**这是计划「agent 侧改造」小节 API 名称的修正，接口形状不变。**
4. **stdio server 的致命陷阱：日志必须走 stderr（计划风险 #5 的真实根因）**：stdio MCP 把 **stdout 当 JSON-RPC 专用通道**，任何写 stdout 的日志都会污染协议帧 → `ValidationError: JSONRPCMessage` → 会话崩溃 `CancelledError`。原 `logger.py` 输出到 `sys.stdout`，已改为 `sys.stderr`（uvicorn 惯例，MCP client 自动转发子进程 stderr）。**这是全局 logger 改动，影响所有进程，阶段 1/2 的 local/jina server 同样依赖此修复。**
5. **ToolMessage.content 是 list-of-blocks，非纯字符串（行为差异，已验证无害）**：adapters 的 `_convert_call_tool_result` 恒返回 `[{"type":"text","text": "<JSON字符串>"}]`，而老内嵌工具返回纯 str。`graph.py:_tool_ok` 只读 `.status`（不受影响）；langchain_openai 把 list-content 原样下发，gemini-3.1-pro 代理 LLM 实测接受、闭环正常。**若后续接其他 LLM 报 tool 消息格式错，回看此处。**

另：**孤儿子进程防护改用 Windows Job Object（kill-on-close）而非手动 terminate pid**。MCP stdio client 把子进程 pid 私有化、公开 API 取不到；改为 app.py 启动时把主进程放进 kill-on-close Job，子进程继承成员资格，主进程退出（含 tray `os._exit(0)` 绕过 lifespan）时内核连同子进程树一并回收。比手动 terminate 更稳，对应计划风险 #1。frozen 形态下仍需在阶段 3 实测确认。

**新增文件**：`src/rag/tool_runtime.py`（`PS_USE_MCP` 开关单一真相源，main.py/graph.py/routes.py 共用，避免各读各的漂移）。

---

## 阶段 1 实施笔记（2026-06-29）

- 验证阶段发现本机生产 chroma collection `rag_langchain` 是 RAG 升级前的 **384 维历史废数据**（与当前 bge-m3/1024 维不兼容，检索必报维度错），且注册表里那条记录指向的 PDF 本体已在跨机同步中丢失——属 gitignored `data/` 手动同步的遗留漂移，非 MCP 迁移问题。已**重置生产库**：删 `rag_langchain` collection + 清 default 注册表，从 `data/pdfs/` 现有 8 篇 PDF 重新入库为 1024 维玩具数据（eval_baseline/eval_fixed 两个 1024 维评测库未动）。**换机继续前注意：各机的 `data/` 需自行保证为 bge-m3 时代的 1024 维库，旧机器若残留 384 维库会同样报错。**

---

## 阶段 1 第三方质询闭环（2026-06-30，结论已审核，预备执行）

阶段 1 push 后经第三方（Gemini 3.1 pro）质询，3 项均已本地核代码 / 实测复核，结论与修复方向如下。**P0-a 是已实测坐实的真回归，优先级最高。**

### P0-a：ChromaDB 跨进程「写后读」可见性 —— ✅ 已修复落地（2026-06-30，commit 待提交）
**修复实现**：新增 [src/core/chroma_gen.py](../src/core/chroma_gen.py)（代际令牌 + 惰性重建，单一真相源）。主进程 `confirm_and_index` / `delete_paper` 成功末端 `chroma_gen.bump()`（写 chroma 目录下 `.gen_token`，内容为单调 `time_ns`，tmp+os.replace 原子替换）；`rag_tool` 查询第一行 `chroma_gen.ensure_fresh()`（`PS_MCP_SERVER` 门控，内嵌路径 no-op），令牌推进才重建本子进程 chroma 连接；`local_server.py` 启动 `chroma_gen.init()` 记基线。双检锁保证并发只重建一次。
- **★ 超出原笔记的关键发现（已实测坐实并修正方案）**：chromadb 1.5.5 的 `SharedSystemClient`（`chromadb/api/shared_system_client.py`）把 `System`（含 Rust HNSW segment / SQLite 连接）按 `persist_directory` 缓存在**进程级 `ClassVar` 字典**里。**只 `ingestor._vectorstore=None` 再 `Chroma(persist_directory=...)` 不够**——会拿回同一个缓存 System / 旧 HNSW，仍看不到新写入。`_rebuild` 必须先 `SharedSystemClient.clear_system_cache()` 驱逐缓存，再重建并回写 `rag_tool.vs`。两进程探针对照实测：不清缓存命中 0（甚至 InternalError）、清缓存+重建后命中。
- **附带修正原笔记措辞**：`rag_tool.vs` 是**模块级全局变量**（非闭包）；`hybrid_search`（`store ... else vs`）与直接路都在**调用时**读模块全局 `vs`，故重建后回写 `rag_tool.vs=新 store` 对两条路同时生效。`lookup` 每次开新 SQLite 连接读注册表、不碰 chroma，**无需改**。
- **重测 gate 已通过**：临时两进程脚本走生产路径（`write_to_chroma`+`bump` / 生产 `rag_tool`+`ensure_fresh`），关 hybrid/rerank 隔离 HNSW 向量路 → 入库后下一次查询命中新文档（`fix_visible=True`）；对照组阉割 `clear_system_cache` 复现失效。`pytest --ignore=tests/test_rag_chain.py` 116 passed（4 失败均为 pre-existing：test_backend 需 live server、test_s2 stale 断言，与本改动无关）。脚本已删（信任边界：tests//scripts/ 不留库）。
- **二轮质询闭环（2026-06-30，句柄泄漏）**：Gemini 指出 `_rebuild` 依赖 GC 回收旧 `System`。核 chromadb 1.5.5 源坐实——`clear_system_cache()` 仅把 ClassVar 字典重置为 `{}`、**不调 `system.stop()`**（`_release_system` 才 stop）；而释放 SQLite/HNSW 句柄的唯一路径是 `RustBindingsAPI.stop()` → `del self.bindings`；`System↔component` 互持引用成环，引用计数收不掉、只能等周期 GC。**本机句柄探针实测**：常驻进程 15 次 `_rebuild`，仅 clear 泄漏 **+245** 句柄（GC 后归零）、显式 `stop()` 后 **+5 持平且无需 gc.collect()**。**采纳轻量修复**：`_rebuild` 清缓存前先遍历 `_identifier_to_system` 显式 `stop()`，**不**用 Gemini 建议的 `gc.collect()`（避开查询热路全堆暂停）。Gemini 主推的「移动/删库 WinError 32」属非法使用场景、不付代价；真实正常使用的代价是常驻进程句柄churn，已消除。生产 `_rebuild` x15 句柄 +0、写后读 gate 仍 `HNSW_HITS=1`。

> 以下为修复前的分析记录（保留备查）：

#### 原始分析：已实测坐实，**真回归**
- **实测结论**（两进程驱动 worker，worker 开长驻连接不重建，父进程写入带 sentinel 的新文档后 worker 立即查）：
  - `similarity_search`（HNSW，进程内存路径）：**看不到**新写入（命中 0）。
  - `_collection.get(where=...)`（SQLite 直读，BM25 路径用的就是它）：**看得到**（命中 1）。
  - 即常驻子进程「脑裂」：`hybrid_search` 的 BM25 路能召回刚入库的论文、向量路召回不到，rerank 只在残缺候选池里排。
- **为何是回归**：内嵌世代 agent 与入库共享主进程同一个 `get_vectorstore()` 单例 HNSW，入库后下一轮查询天然可见；MCP 把读拆进常驻子进程后才破。真实翻车场景＝「上传论文→立刻提问该篇」，向量召回静默劣化直到子进程重启。
- **不采用 `mcp_client.restart()` 修复**（已核代码排除）：① `confirm_paper`（[routes.py:223](../src/api/routes.py#L223)）是 sync `def`，FastAPI 丢线程池 → 前端一次传多篇＝多个 `confirm_and_index` 写线程**真并发**；②「入库一次 restart 一次」在并行入库下＝**重启风暴**，且 `restart()` 不分 server，会**连带重启 web 子进程、把 S2/arxiv/openalex 速率锁归零**（恰是常驻会话当初要保住的东西）；③ restart 的 teardown↔rebuild 窗口内 `get_tools()` 返回 `[]`，撞上某轮 chat 的 `build_agent` → 当轮无 MCP 工具（竞态）。
- **采用方案：local server 侧「惰性代际检查」（pull 式，Gemini 方案 B 的去风暴实现）**：
  1. 主进程每次入库/删除成功后（`confirm_and_index` / `delete_paper` 末端），bump 一个轻量代际令牌——chroma 目录下一个 sentinel 文件，写其 mtime 或自增计数。
  2. local server 的 rag_tool / lookup 在**每次查询开头**比对令牌，若已推进则重建 `get_vectorstore()` 单例后再查；否则照用。
  - 收益：N 篇并行入库 bump N 次，下一次查询**只重建 1 次**（自动合并，无风暴）；**只动 local 的 chroma 连接，web/jina 子进程与速率锁完全不碰**；从不 teardown，无 `get_tools()` 空窗竞态。
  - **执行坑**：[rag_tool.py:14](../src/rag/tools/rag_tool.py#L14) 在**模块级** `vs = get_vectorstore()` 且 `hybrid_search` 闭包到该 `vs`；只 reset `ingestor._vectorstore` 不够，须让 rag_tool 重新取（改 `hybrid_search`/`rag_tool` 内部按需 `get_vectorstore()`，或重建后回写模块级 `vs`）。
  - 备选（重，留作日后高频入库场景）：把「写」也收进 local server，单进程内读写共享一个 HNSW，可见性问题自然消失。
- **重测 gate**：复跑跨进程写后读实测，要求 HNSW 路径在入库后下一次查询命中新文档（令牌触发重建后 `HNSW>0`）；并验证并行入库 N 篇时 web 速率锁不被连带清零。

### P1-a：`ToolMessage.content` 恒为 list-of-blocks —— ✅ 已修复落地（2026-06-30）
- adapters 的 `_convert_call_tool_result` 恒返回 `[{"type":"text","text": "<JSON>"}]`，破坏老内嵌工具的纯 `str` 行为（阶段 0 实施笔记 #5 已记，Gemini 独立复现）。
- 当前不崩：`graph.py:_tool_ok`（[graph.py:122](../src/rag/graph.py#L122)）只读 `.status`、不碰 content。未来崩点（真）：接本地开源模型（Qwen/Llama）拼 prompt 格式错乱；或引入 LangChain Memory/Callbacks/OutputParser 对 list 调 `.split()` 等 → `AttributeError`。
- **修复**：在 adapter 取回端（[mcp_client.py](../src/rag/mcp_client.py) `_patch_tool_flatten`）就地包装每个 load 出来的工具的**两条出口**，全为 text block 时展平成纯 `str`：
  - **成功路径**：工具 `coroutine` 返回 `(content, artifact)`（`response_format=content_and_artifact`）→ 展平 content、保留 artifact。
  - **错误路径**：MCP `isError=True` 走 `handle_tool_error` 回调返回 list-of-blocks（**不经 coroutine 返回值**），单独包一层展平、`status="error"` 不受影响（`_tool_ok` 仍正确）。
  - 出现 image/file 等非文本 block 时保持原 list 不动（本项目 6 工具全部只返回文本，理论上不触发），不破坏多模态。
- **核源依据**：list 包装发生在 adapters 0.3.0 [tools.py:268-271](file) `_convert_call_tool_result` 与 [tools.py:154-157](file) `_handle_mcp_tool_error`；工具是 `StructuredTool(response_format="content_and_artifact", coroutine=..., handle_tool_error=...)`（[tools.py:528-536](file)）。
- **验证**：合成 StructuredTool 探针 + **真实 local server 端到端**双覆盖——成功路径（`lookup` 正确 list 参数）content=纯 str / status=success；错误路径（schema 校验失败）content=纯 str / status=error 保留。pytest 非 live 基线不破。

### P0-b / P1-b：Frozen（PyInstaller）多进程与路径陷阱 —— 部分已坐实，部分待阶段 3 实机
- **P0-b 绝对路径 —— ✅ 已修复落地（2026-06-30）**：[config.py](../src/config.py) `ROOT` 原 `Path(__file__).resolve().parent.parent` **无 `sys.frozen` 分支**；frozen 下 `__file__` 指向 `_MEIPASS` 临时解压目录 → `data/`/chroma/SQLite/用户 yaml 写进临时目录、**重启即丢**。
  - **修复**：`ROOT` 加 frozen 分支 `Path(sys.executable).resolve().parent`（exe 真实所在目录，可写持久），dev 走原逻辑（仓库根）。**与 [app.py:5-8](../app.py#L5) 的 frozen ROOT 故意取向相反**——app.py 的 ROOT=`_MEIPASS`（供 `sys.path.insert` 导入打包内 `src`）；config.ROOT 只管「可写持久数据 + 用户 yaml」。只读打包资产不归 config.ROOT 管、已各自正确：`dist/`（[main.py:46](../src/main.py#L46) 自己的 `__file__` → `_MEIPASS/dist` ✓）、prompts `profiles/`（[builder.py:180](../src/rag/prompts/builder.py#L180) 自己的 `__file__` ✓）、`mcp_client.py` 的 ROOT 已自带 `_MEIPASS` 分支 ✓。
  - **验证**：dev 分支 ROOT 与改前**逐字节一致**（pytest 不可能因此回归，frozen 分支仅 PyInstaller 下激活）；模拟 `sys.frozen`/`sys.executable` 核 frozen 分支 ROOT 落在 exe 同级、不含 `_MEI`；config 全消费者（ingestor/registry/init_SQLite/routes/rag_tool/chroma_gen）import 链干净。
  - **行为备注（留打包阶段）**：spec 把出厂 `config/user_config.yaml` 打进 `_MEIPASS/config/`，但 `_yaml_path` 现指向 exe 同级 → **首次运行该文件尚不存在**。`_load_yaml()` 缺文件返回 `{}`、`save_config_dict` 自带 `parent.mkdir` → 首跑读 .env/硬编码默认、首次「保存设置」时在 exe 同级建 `config/`，行为正确（用户填设置页前为空配置）。若日后要让出厂默认值随首启自动落地到可写目录，需在启动时显式 copy `_MEIPASS/config` → exe 同级（本次不做，记此备查）。
- **P1-b `freeze_support()` —— ✅ 已修复落地（2026-06-30）**：全仓原无 `multiprocessing.freeze_support`。当前 MCP 子进程靠 `PS_MCP_SERVER` env 分流 + `sys.exit(0)`（[app.py](../app.py)）走 `subprocess.Popen([sys.executable])` 而非 multiprocessing，故不直接依赖它；但 PyInstaller 官方要求 frozen 入口加，防未来自身/第三方库触发的 mp spawn 在 frozen Windows 下重跑 bootloader 套娃。
  - **修复**：`app.py` 顶部 `import multiprocessing`，`__main__` 块**首行**（`_setup_kill_on_close_job` / 起线程 / `tray.run()` 之前）调 `multiprocessing.freeze_support()`。非 frozen / 非 Windows 为 no-op，dev 与 pytest 零变化。
  - **验证**：`ast.parse` 语法通过；位置断言 `__main__ < freeze_support < job < tray.run`（对真实调用点，非注释引用）成立。
- **待阶段 3 实机（无法静态定论）**：[app.py:115](../app.py#L115) `AssignProcessToJobObject(GetCurrentProcess())` 绑的是否顶层 Bootloader 进程、用户从任务管理器强杀顶层 EXE 时 kill-on-close 是否真连带回收子进程树。

---

## 评审与往返（供用户注释）

> 此区块留给用户写评审意见 / 注入修改点。Claude 读取后在下方书面回应，逐条闭环。
