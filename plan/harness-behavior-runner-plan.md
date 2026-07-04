# Harness 行为量具（behavior runner）实施计划 — 阶段②

> 派生自 [harness-ablation-plan.md](./harness-ablation-plan.md) 的 Next-stage TODO ②。
> 定位：**松绑实验（④）的前置量具**。产出一个自动化、行为感知的评测回路——喂问题→端到端跑 agent→输出行为指标表。**不含 LLM 评委、不含内容打分。**

## 为什么是它（关键路径）

④（FLASH vs STRONG 单变量松绑对照，整个计划的落点）无法在「翻一个开关后能量化行为差异」之前进行。现有 `eval_framework/evaluator.py` **不可复用**：它从不 import graph，是把人工粘贴的 `answer`/`tool_log` 喂给 LLM 评委的**内容打分器**。故②基本从零造 runner。它同时也是当下可用的回归嗅探器。

## 关键实机事实（已核验）

- `evaluator.py` 仅 import `openai`/`dotenv`，从不 `import src.rag.graph`，是纯内容评委。
- `eval_framework/test_cases.json` = **20 题**，每项含 `id`/`question`/`intents`/`reference_points`/`answer`/`tool_log`。②只取 `id`+`question`。文件是**合法 UTF-8**（此前看到的乱码是 Windows cp936 控制台显示假象，非文件问题）。
- agent 入口：`chat()`（[graph.py:511](../src/rag/graph.py#L511)）内部 `asyncio.run(agent.ainvoke(initial_state))`——但它**会写 SQLite/memory**。量具须绕开 `chat()/regenerate()`，自建 `initial_state` 直跑 `agent.ainvoke`，零持久化。
- `initial_state` 形状见 [_prepare:497-507](../src/rag/graph.py#L497)：`messages=[SystemMessage(system_prompt), HumanMessage(question)]` + `conv_id/user_id/translation/remaining_calls=6`。量具用**空 history** 复刻。
- 节点名：`call_llm` / `thinking_guard` / `tool_node` / `final_answer`（[graph.py:449-452](../src/rag/graph.py#L449)）。
- `USE_MCP` 默认 `false`（`PS_USE_MCP` 未设时，[tool_runtime.py:16](../src/rag/tool_runtime.py#L16)）→ 走内嵌工具，standalone 脚本无需 MCP server。**若设了 `PS_USE_MCP=true`，量具需 MCP server 在跑**（须提示）。

### 指标全部可从「最终 transcript」推导（避开流式增量重建）

采用单次 `agent.ainvoke` 拿最终 state，再解析 `result["messages"]`：guard 注入的假 ToolMessage（含哨兵串 `未检测到必要的 <thinking>`）与预算兜底哨兵（`已达最大调用次数上限`）**都会留在 state**，无需脆弱的 updates-delta 累积。

| 指标 | 来源 |
|---|---|
| `guard_hits` | 最终 messages 中 guard 取消哨兵 ToolMessage 计数 |
| `correction_loops` | 同上 / 最终 `thinking_retry_count` |
| `tool_rounds` | 真实 ToolMessage 计数（排除两类哨兵） |
| `budget_hit` | 最终 `remaining_calls <= 0` 或出现预算哨兵 |
| `empty_answer` | `process_llm_output(最终 content)` 为空 |
| `marker_emit_rate` | 对每条带 tool_calls 的 AIMessage 跑 `_detect_marker`，命中率 |
| `latency` / `error` | ainvoke 计时 + 异常捕获 |

> 复用 `build_agent`/`build_prompt`/`_detect_marker`/`process_llm_output`（从 `src.rag.graph` import），**不 fork 逻辑**。仅 driver+collector+表是新代码。

## 交付物（一个聚焦 PR）

`scripts/harness_probe.py`（scripts/ 是实验旋钮的家，同 `eval_retrieval.py`/`probe_rerank.py`）：

1. **题库加载**：读 `eval_framework/test_cases.json`，取 `id`+`question`。`--only Q01 Q05` 子集（每次跑烧真 token）。
2. **临时 agent driver** `run_one(question, mode, label, user_id)`：内联复刻 `initial_state`（空 history），`build_prompt`+`build_agent` 直跑 `ainvoke`。**不触 `app.db`/memory**。
3. **行为收集器**：按上表从最终 transcript 计数（无 LLM 评委）。
4. **聚合**：逐题一行 + 汇总 → JSON 落 `eval_framework/results/behavior/` + 打印表。
5. **④ 前瞻钩子**：`--label`（`FLASH`/`STRONG`…）标记每次运行；③落地 `HarnessProfile` 后同一 rig 靠重跑对照变体。

## 风险（须在脚本头注明）

- **真成本**：20 题 = 20 次完整 agent 跑（LLM + s2/arxiv/jina）。故有子集开关。
- **语料依赖**：`rag_tool` 读 `data/chroma_db`（gitignored）。无语料机器上 RAG 空召回，*内容相关*指标漂移——但 guard/marker/correction/budget 指标基本语料无关，正是④要比的。
- **流式**：`updates` 足够节点级计数；仅当要 token 级 marker 计时才上 `astream_events(v2)`——v1 不需要。
- **MCP**：`PS_USE_MCP=true` 时需 MCP server 在跑。

## 明确不做

③接线、④实验、A1/A4 工具瘦身、LLM 评委内容打分。

## 落地步骤

1. [本文件] 计划同步入 `plan/`。
2. 建 `scripts/harness_probe.py`。
3. `--only Q01` 冒烟测 1–2 题，确认收集器正确读 transcript，再全量。

## 实机验证 & 加固（2026-07-04，gemini-3.1-pro-preview）

- ✅ 量具在真机跑通 10 次 agent（FLASH/STRONG 各 5 题），收集器从最终 transcript 正确产出 guard/marker/tool_rounds/empty 等指标；含 2–4 轮真实工具调用的 transcript 也解析无误。
- ✅ **兜底三件套**（应对 RPM=5 + Google GLI/S2 上游抖动，见 `run_one`）：
  - `--timeout`（默认 360s）：`asyncio.wait_for` 给每题墙钟上限——`ainvoke` 无 request_timeout，上游挂起本会无限 stall，超时即判失败进重试。
  - `--retries`（默认 1）：对**超时/异常/空答**题级重试，每次重建 agent+state；`attempts` 列如实记录。Q03/STRONG 首次撞 300s 超时→重试救回，实证有效。
  - `--pace`（默认 5s）：题间静置，缓 RPM=5 开头撞限流。
- ⚠️ **运维事实**：S2 429 是主要墙钟成本源（bulk 端点瞬时限流，非 key 问题；key 有效），被工具 60s 冷却 + 上述 timeout/retry 吸收，不丢数据但拉长时间。`[Discuss模式]` 题（Q19/Q20）全量须 `--mode discuss`。
- ⚠️ **环境注意**：`requirements.txt` 是 UTF-16 编码（ASCII grep 会误判"查无此包"）；`rank_bm25`、`langchain-mcp-adapters`（graph.py import 链依赖）均已在册，本机 env 未同步而已，`pip install -r requirements.txt` 补齐。

## soft-violation 计数器（2026-07-04，补 ⑤ 量具缺口）

⑤ 暴露：`guard_hits` 只数 **strict 驳回哨兵 ToolMessage**，soft/off 档 guard 只打 `[guard:soft]` 日志、不注入哨兵 → `guard_hits` 在 soft 恒 0，是**假阴性**。故 ④/⑤ 的「STRONG guard=0」当时无法与真合规区分。

- **修复**：`collect_metrics` 加 **profile 无关**的 `missing_thinking_calls` —— 直接数「带 tool_calls 却缺 `<thinking>…</thinking>`」的 AIMessage。这正是 guard 的违规谓词，而违规 AIMessage 在 strict/soft/off **每种模式都留在最终 transcript**（延续本计划「指标全从 transcript 推导」的原则，不解析日志）。附 `thinking_compliance_rate`（1−违规率）+ 汇总 `missing_thinking_total`/`avg_thinking_compliance_rate`；表格加 `noThk` 列（换下冗余的 `corr`，因 `correction_loops==guard_hits`）。
- **语义**：strict 档 `missing_thinking_calls == guard_hits`（每次驳回对应一条违规 AIMessage）；soft/off 档 `guard_hits=0` 而本计数抓真违规。**将 ④/⑤ 的「STRONG guard=0」从假阴性升级为可信真零。**
- **验证**：`tests/test_harness_probe_metrics.py`（离线，合成 transcript，无网络/无 agent）—— STRONG(全合规→missing=0/compliance=1.0/guard=0)、MINIMAL(违规→guard=0 但 missing=1/compliance=0.5)、strict 一致性、哨兵不计入 tool_rounds、纯直答率为 None。5 passed。
