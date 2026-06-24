# 真流式（SSE）传输实装

## Context

当前 PhysicsScholar 的回答是"假流式"：后端 `agent.invoke()` 一次性算完整个 LangGraph 回合，前端 axios 拿到完整 `answer` 后用 8ms `setInterval` 逐字符做打字机动画。用户在工具检索（arXiv/S2/Jina）期间只能干等，且看不到 agent 在"思考还是在调工具"。

本次目标：实装**真流式**——后端把 `invoke` 换成 `astream_events(version="v2")`，用一个异步生成器维护 **thinking / tool / answer 三态状态机**，通过 SSE 实时推事件；前端 axios 换成 fetch + ReadableStream，渲染 claude.ai 风格的"思考/工具时间轴"（只展示 spinner、工具名、计时，**不展示思考正文**）+ answer 正文逐 token 流式。

**已确认决策**：① 直接替换 `/ask`、`/regenerate` 为流式（旧 `invoke` 路径保留给测试/脚本兜底）；② 前端断连即丢弃、本轮不落库；③ 时间轴仅实时显示、不持久化（DB 结构零迁移）。

## 流式 API 选型（已对比 v2 / v3 / stream_mode，2026-06 核实）

现状：v1 将在 0.4.0 弃用；**v2 当前默认、官方推荐、稳定、未弃用**；**v3 仍是 beta**（协议可能变，仅 BaseChatModel/CompiledGraph 支持），提供 typed projections + content-block 协议自动分离 reasoning/text/tool；`stream_mode`（`.astream()` 的参数，非 astream_events）的 `messages` 吐 `(token, metadata)` 且 `metadata["langgraph_node"]` 标来源、`updates` 吐节点完成后增量、`custom` 让节点主动推事件。

**选定 `astream_events(version="v2")`**，理由（按我们硬需求 A 逐token / B 自定义思考分离 / C 节点区分 / D 工具运行中实时spinner / E 排除guard驳回）：
- **D 是决定性因素**：要 claude.ai 那种"检索中…"实时转圈，需在工具**执行前**就拿到信号。v2 的 `on_tool_start` 免费提供、且只对真实进入 `tool_node` 的调用触发（天然满足 E，guard 驳回不触发）。`stream_mode` 的 `updates` 只在节点**完成后**才吐，长检索期间无 live 反馈，除非给每个工具加 `custom` 埋点——更侵入。
- **B**：v3 自动分离的是 provider 原生 reasoning 通道；我们已禁用 DeepSeek 原生 thinking、把思考写进正文 `<thinking>`，故 v3 也得自己解析标记——招牌特性对我们无效，再叠加 beta + 打包 `.exe` 风险，不取。
- v2 在**同一条流**里同时给 token (`on_chat_model_stream`) + `metadata.langgraph_node`（满足 C）+ 精确工具边界，对双粒度可视化最省事最稳；无需合并两种 stream_mode。
- 注：astream_events 仅在"被嵌套进外层 Runnable"时有不冒泡的已知问题（issue #6105），我们是直接对 compiled graph 调用，不受影响。

参考来源：
- LangGraph Streaming 官方文档 https://docs.langchain.com/oss/python/langgraph/streaming
- astream_events API 参考 https://reference.langchain.com/python/langchain-core/runnables/base/Runnable/astream_events
- LangGraph v3 Event Streaming: Typed Projections（实践分析）https://vadim.blog/langgraph-v3-event-streaming-typed-projections
- issue #6105：astream_events 嵌套 Runnable 不冒泡 https://github.com/langchain-ai/langgraph/issues/6105

## 关键技术结论（务必先验证）

**`call_llm` / `final_answer` 节点内的 LLM 调用必须异步化**（与上面 API 选型无关，三种方案都需要）。仅设 `streaming=True` 但节点内仍用同步 `.invoke()`，在 `astream_events` 下拿不到稳定的 token 级 `on_chat_model_stream`（同步节点被丢线程池，callback 不冒泡）。实现第一步先写个最小脚本验证：`async for ev in agent.astream_events(state, version="v2")` 能否拿到 `on_chat_model_stream` 且事件带 `ev["metadata"]["langgraph_node"]`。环境：py3.11 / langgraph 1.1.3 / langchain-core 1.2.23，均支持 v2 + langgraph_node。

**第一步验证已完成（2026-06-24，实跑通过）**：A 项 PASS —— `astream_events(v2)` 拿到逐 token `on_chat_model_stream`、事件带 `metadata.langgraph_node`（`{'call_llm': N}`），同一条流里含完整 `<thinking>…</thinking>` 边界 + 正文，可被状态机切分。脚本：`scripts/verify_astream.py`（临时，收尾可删）。实跑环境 langchain-core 实际为 1.2.20（仍支持 v2 + langgraph_node）。

> **发现**：节点改 async-only 后，旧的同步 `agent.invoke()` 直接抛 `TypeError: No synchronous function provided to "call_llm"`，导致 `chat()`/`regenerate()` 的非流式兜底路径失效——本 plan 原假设"旧 invoke 路径可直接保留"不成立。
> **决策（选项1）**：`chat()`/`regenerate()` 内部把 `agent.invoke(...)` 改为 `asyncio.run(agent.ainvoke(...))`，保持函数同步签名不变（仅供测试/脚本兜底，不被 async 路由调用，无嵌套事件循环问题）。
> **理由**：这是过渡态兜底，不值得为它投入更多开发（如把整条链改 async 并波及所有调用方）；改动最小、回归测试可继续跑（B 项验证 PASS）。后续步骤 3 抽 `_prepare` 时这两处自然会再被触及。

## SSE 事件契约

每帧 `data: <json>\n\n`，`media_type=text/event-stream`，json 必含 `type`：

| type | payload | 时机 |
|------|---------|------|
| `thinking_start` | `{}` | 首个 `on_chat_model_start`（`is_thinking` False→True）。一条回答只发一次，被工具/answer 打断后由 `on_tool_end` 重置 |
| `thinking_end` | `{}` | 缓冲出现 `</thinking>`(+DONE) / 合法 `on_tool_start` / `final_answer` 的 `</thinking>` |
| `tool_start` | `{name, tool_id}` | `on_tool_start`（真实工具执行；guard 伪造的 ToolMessage 不经工具节点，不触发） |
| `tool_end` | `{name, tool_id, ok}` | `on_tool_end`，并置 `is_thinking=False` |
| `answer_start` / `answer_delta` / `answer_end` | `{}` / `{text}` / `{}` | answer 阶段开始 / 正文增量 / 图根 `on_chain_end` |
| `done` | `{user_msg_id, agent_msg_id, warning, answer}` | 落库完成。**附权威 `answer`** 供前端覆盖累计文本，消除流式显示与落库的漂移 |
| `error` | `{message}` | 异常，流终止 |

## 后端改动

### 1. `src/llm.py`
- `main_llm` 加 `streaming=True`。`sub_llm` **不动**（Jina 打分等同步调用与流式无关，隔离）。
- 复核：streaming 下 tool_calls 以 delta 到达，langchain-openai 自动聚合成 `response.tool_calls`，现有串行裁剪 `[:1]` 仍成立（`parallel_tool_calls=False` 已保证）。

### 2. `src/rag/graph.py`
- 新增 `ainvoke_with_retry(llm, messages)`：照搬现有 `invoke_with_retry` 的 tenacity 装饰器（参数不变），函数体改 `async def` + `await llm.ainvoke(messages)`。同步版保留。
- `call_llm`、`final_answer` 改 `async def`，把 `invoke_with_retry(...)` → `await ainvoke_with_retry(...)`；prefill、`reasoning_content` 检测、串行裁剪、`remaining_calls` 逻辑原样保留。其余节点（`thinking_guard`/`after_guard`）保持同步，sync/async 节点可混用。
- 抽取共享 `_prepare(...)`：构建 `system_prompt`(复用 `build_prompt`)+history(`format_history`)+`build_agent`+`initial_state`，供 `chat()`/`regenerate()`/新流式生成器复用。
- 新增 `async def chat_stream(...)` / `async def regenerate_stream(...)` 生成器，共用核心 `_consume_events(agent, initial_state, request)`：
  - 状态机变量：`is_thinking`、`answer_open`、`buf`、`cur_node`、`root_run_id`。
  - `on_chat_model_start`：记 `cur_node`、清 `buf`/`answer_open`；`is_thinking` 为假才发 `thinking_start` 并置真（guard 反刍回的 call_llm 不重发）。
  - `on_chat_model_stream`：取 `chunk.content`（空 piece 跳过，防 reasoning_content）；`answer_open` 时直接 `answer_delta`；否则累积 `buf`，用 `_rfind_close_think(buf)` 找最后一个 `</thinking>.end()`，命中后判 `_detect_marker(buf)`：`cur_node=="final_answer"` 或 marker==DONE → 发 `thinking_end`+`answer_start`，把 `</thinking>` 之后子串作首个 `answer_delta`，置 `answer_open`；PENDING/None 静默。
  - `on_chat_model_end`：`answer_open` 仍为假时兜底——若 `output.tool_calls` 为空，用**权威 `output.content`**（非 buf）取 `</thinking>` 后内容补发 answer。
  - `on_tool_start`：发 `thinking_end`+`tool_start`。`on_tool_end`：发 `tool_end`，置 `is_thinking=False`。
  - 图根 `on_chain_end`（`run_id==root_run_id`，根 runnable 无 langgraph_node）：取 `output["messages"][-1].content` 作权威 final_content，发 `answer_end`。
  - 每个事件前 `if await request.is_disconnected(): break`。
  - 辅助：`_rfind_close_think`（正则取最后一个 `</thinking|think>` 的 end）、`_detect_marker`（全 buf 扫 `[TOOL_LOOP: DONE/PENDING]`）、`_tool_ok`、`_format_sse`，复用 `trim_thinking.THINK_TAG_PATTERN`。
- 落库（仅在拿到权威 final_content 后）：`process_llm_output(final_content)` → 空则兜底文案 → `ConversationRepo.ensure_exists` → `memory.add(Human)`/`memory.add(AI)` → 发 `done`。`regenerate_stream` 把 `memory.regenerate`（标旧消息+取 version）**推迟到落库时**与 add 一起做，断连不留悬挂；`done.user_msg_id=parent_id`。
- 异常处理：`except asyncio.CancelledError: raise`（断连，`finally: memory.close()`，不落库）；`except Exception: 发 error`。

### 3. `src/api/routes.py`
- `/ask`、`/regenerate` 改 `async def`，新增 `request: Request` 参数，返回 `StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})`，`gen=chat_stream(...,request)` / `regenerate_stream(...,request)`。
- `chain.py` 的 `ask()` 保留为非流式兜底，不再是主路径。

## 前端改动

### 4. `src/api/chat.js`
新增 `streamChat(payload, handlers, signal)` / `streamRegenerate(...)`：`fetch('/api/ask',{method:'POST',body,signal})` → `consumeSSE(res.body, handlers)`。`consumeSSE` 用 `getReader()`+`TextDecoder`，按 `\n\n` 切帧（残帧留缓冲），解析 `data:` 行 `JSON.parse` 后按 `evt.type` 派发 `handlers[evt.type]?.(evt)`。`AbortController().signal` 支持停止/切会话取消。旧 axios 函数保留。

### 5. `src/views/ChatPage.vue`
- 删除 8ms `setInterval` 假打字机。新增 reactive：`streamingPhase`('idle'|'thinking'|'tool'|'answer')、`streamingTools`(`[{tool_id,name,status,startedAt,endedAt}]`)。`streamingContent` 仅在 `answer_delta` 累加（保留 `streamingSessionId` 防串话/切会话竞态）。
- `_doSend`：把 `sendChatMessage`+setInterval 段替换为 `streamChat(payload, handlers, signal)`，handlers 按事件更新 phase/tools/content，`done` 存 `doneData`。**流结束后沿用现有 sessionCache 树更新逻辑**，仅把 `fullText`/`user_msg_id`/`agent_msg_id`/`warning` 来源由 `res.data` 改为 `doneData`（`fullText=doneData.answer`）。
- `handleRegenerate`：改 `streamRegenerate`，`done` 后走现有 regenerate 分支（旧节点标 `regenerated`、新 version 入树、重算 siblings）。
- 断连/`error`：清 `streamingPhase`/`streamingSessionId`，提示"生成中断"。

### 6. 新组件 `src/components/Chat/ThinkingTimeline.vue` + `ChatWindow.vue` 透传
- `ChatWindow.vue` 在现有 streaming 占位区上方条件渲染 `<ThinkingTimeline :phase :tools />`（与 `streaming-content` 同路从 ChatPage 透传）。
- 渲染：thinking 阶段 spinner+"思考中…"+计时；工具阶段每工具一个 chip `[spinner|✓] {中文名} {耗时}`；answer 阶段折叠为一行摘要，下接正文。工具名中文映射：`rag_tool`→检索本地论文、`lookup_local_paper_id`→定位本地论文、`arxiv_tool`→arXiv 检索、`s2_search_tool`→Semantic Scholar、`openalex_tool`→OpenAlex、`jina_tool`→网页/PDF 阅读。

## 风险与边界
- **chunk 跨界**：`</thinking>` 与 `[TOOL_LOOP:*]` 都可能被拆到多个 chunk —— 必须累积 `buf` 后整体检测，只转发 `</thinking>` 之后子串。
- **reasoning_content**：空 content piece 跳过；`on_chat_model_end` 兜底用 `output.content` 而非 buf。
- **SSE 缓冲**：必设 `X-Accel-Buffering:no`+`Cache-Control:no-cache`，每帧 `\n\n`；长工具链可选 ~15s 心跳 `: ping\n\n`。
- **空回答/漂移**：`done` 带权威 `answer`，前端覆盖累计文本。
- DB 零迁移（时间轴不持久化）。

## 实施顺序
1. graph.py 异步化 + 最小脚本验证 token 流 & `langgraph_node`。
2. llm.py `main_llm streaming=True`（回归旧 invoke：tool_calls 聚合/裁剪正常）。
3. graph.py `_consume_events` + `chat_stream`/`regenerate_stream` + 辅助函数。
4. routes.py StreamingResponse + 断连检测。
5. chat.js `streamChat`/`consumeSSE`。
6. ChatPage.vue 改造 + 新 state。
7. ThinkingTimeline.vue + ChatWindow 透传。
8. 打磨：断连不落库、done 带 answer、可选心跳。

## 验证
- **单元/脚本**：`astream_events` 验证脚本（确认 `on_chat_model_stream` + `langgraph_node`）；构造 DONE/PENDING/无标记/final_answer 四种输出，单测 `_consume_events` 的事件序列；`pytest`（含现有非流式 invoke 回归）。
- **端到端**：`uvicorn src.main:app --reload`(:8000) + `cd frontend && npm run dev`，①纯问答（无工具）看 thinking_start→answer 流式；②触发本地 RAG/外部检索看工具 chip 时间轴；③regenerate 分支正确；④中途切会话/关页面，确认后端 `async for` 立即停（日志无后续 token、不落库）。
- **打包路径**：`npm run build` 后访问后端端口直连，确认 dist + StreamingResponse 下 SSE 不被缓冲（必要时加心跳）。
