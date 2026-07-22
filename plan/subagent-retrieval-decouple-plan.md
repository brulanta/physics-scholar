# T2：解耦 CoT，子 agent 专注检索

> 对应 `plan/top-level-progress-log.md` 【五】想法 3 / 【六】T2（序 4）。枢纽大改动，分 7 阶段独立提交。

## Context（为什么做）

现状主 agent 一把抓 6 轮检索循环（`call_llm→thinking_guard→{tool_node|call_llm|final_answer|END}`，budget max 6）：6 个工具 schema 每轮全量 `bind_tools`（粗估 ~2000–3300 token **每轮重复付** ×6），且 `thinking_guard`/`[TOOL_LOOP]` marker/`build_prefill` 多分支全为这个循环设计，与主 agent 的「思考+决策+输出」本职耦合，难以独立调优。

T2 把**检索循环挪进一张独立编译的子 agent 图**，子 agent 包成一个 `retrieve` 工具壳给主 agent 调用——主 graph 结构零改动，主 agent 只调 1 次 `retrieve`、注意力聚焦思考与输出；子 agent 在「非流式新配置空间」（guard off + prefill minimal，老实验因流式 marker 没覆盖到的空间）独立跑检索循环。这是 T3（B/F 接线、Profile 产品化）的前置枢纽：检索循环 owner 换人后，T3 的接线对象和配置空间才 settle。

## 设计决策（已与用户对齐）

1. **子 agent 图 = 照搬 `build_agent` 骨架**，通过共享薄组装层 `_build_graph` 复用；子 agent = 新 `RETRIEVER` profile + `return_findings` 终止工具 + `finalize` 节点。
2. **共享构建低耦合**：`_build_graph` 只做组装（~30 行），业务逻辑全留模块级独立函数；差异走参数注入。解绑成本≈0（详见下「共享构建解绑设计」）。
3. **前端渐进**：Stage 1–4 后端 `ainvoke` 阻塞跑通（`retrieve` 节点整体转圈，看不到内部进度但功能正确）；Stage 5 才做 `adispatch_custom_event` + 嵌套可视化。
4. **marker 第一版保留 `[TOOL_LOOP]` 名字**（功能正确，改名牵动正则/prompt/probe 哨兵/test 多触面，不值当），改名作可选收尾。
5. **主 agent 去水**：删 `tool_usage` + 压 Phase 3 + 清 `build_prefill` 死分支；`final_answer` 可选简化（T2 保守保留）。

## 共享构建解绑设计（回应用户架构关切）

```
_build_graph(llm, tools, profile, *, terminator=None, finalize_fn=None)
  └─ 只做 add_node/add_edge 组装 + 调模块级函数（build_prefill/final_prefill/
     thinking_guard/after_guard/final_answer），不含业务逻辑
  └─ terminator 非空 → after_guard 加分支：检测到 terminator tool_call 路由到
     一个由 finalize_fn 提供的 finalize 节点（子 agent 专属）；主 agent 不传 = 不感知
```

- `build_agent(user_id, profile=FLASH, llm=None)` = `_build_graph(主 agent 工具=[retrieve 壳], FLASH)`
- `build_subagent(user_id, profile=RETRIEVER, llm=None)` = `_build_graph(检索工具 + return_findings, RETRIVER, terminator=return_findings, finalize_fn=...)`
- 未来任一边分化：fork `_build_graph` 那 ~30 行组装，业务函数全可复用，沉没成本≈0。

## 主 agent 去水范围（回应用户架构关切）

| 类别 | 项 | 处置 |
|---|---|---|
| 承重不动 | marker 闸门、guard、Phase 0/1/2/4/5/6/7 | 保留 |
| 死代码（T2 内清） | `build_prefill` 多分支、Phase 3 的 Q1/Q2/Q3、`tool_usage.py` | 清/压/删 |
| 可选简化 | `final_answer`（主 agent 兜底） | T2 保守保留，验证后或推 T3 |

## 子 agent 形态（核心认知，落地细节）

- 子 agent = 独立编译的图（有自己的 State/预算/guard），`build_subagent` 产出；对主 agent 是 `retrieve` 工具（`bind_tools` 一项），对自己是图（有循环、guard、预算）。唯一桥是 `retrieve` 工具壳函数：主 agent 递 query → 子图 `ainvoke`（非流式、同步阻塞）→ 取 findings 回吐成主 agent 的 ToolMessage。
- **`return_findings` 终止**：子 agent 工具，结构化参数 `selection=[{result_index, item_index, reason}], summary`。子 agent 检索够了就调它。`after_guard` 检测到该 tool_call → 路由 `finalize` 节点（不走 tool_node 回 call_llm）。
- **`finalize` 节点**：从 state messages 找 `return_findings` args + 按顺序编号的真实 ToolMessage；**复用 `src/rag/citation.py` 的 extract 分派器**把每个工具结果解析成 items 列表，按 `result_index/item_index` 抠选中 item 的 raw；拼成 findings 写 state。子 agent 全程**只点索引不转写元信息**（解中间商抄错 + 不白花 token 原样吐）。
- **摘抄归属留主 agent**：桥接层传给主 agent 的是被选中 item 的 content raw（限长兜底，jina 长 blob 截断），主 agent 自己读自己摘抄进 ref——grounding 留主 agent 保反幻觉初衷。
- **配置瘦身**：`RETRIEVER = HarnessProfile(guard_mode="off", prefill_level="minimal", final_prefill="light", budget_n=8)`（独立预算，非流式 minimal 安全）。`final_answer` 子 agent**必留**（头铁不收敛的兜底，预算耗尽强制返回当前 findings）。

## 实现阶段（每阶段独立可提交）

### Stage 0 — 分支 + 抽 `_build_graph`（零行为变化地基）
- 建分支 `t2-subagent-retrieval`。
- 从 `build_agent` 抽出 `_build_graph(llm, tools, profile, *, terminator=None, finalize_fn=None)`；`build_agent` 改调它（主 agent 路径 terminator=None，行为字节级不变）。
- `build_agent` 加可选 `llm=None`（默认走模块级 `llm`，cross-model probe 与子 agent 共用）。
- 验证：现有 `test_consume_events`/`test_citation`/主 agent 端到端零变化。

### Stage 1 — 子 agent 图 + 桥接（后端核心，ainvoke 阻塞）
- `harness_profile.py` 加 `RETRIEVER` 预置 + 进 `PRESETS`。
- `graph.py` 加 `return_findings` 工具 + `finalize` 节点（复用 `citation.py` extract）+ `build_subagent`。
- 加 `retrieve` 工具壳：`make_retrieve_tool(user_id)` 闭包，内部 `build_subagent().ainvoke()` 取 findings，拼成主 agent ToolMessage。
- `build_agent` 主 agent 工具列表换成 `[retrieve 壳]`（保留闭包模式）。
- 新增子 agent system prompt 模块（`prompts/modules/` 下，检索聚焦精简版，教降级链 s2→openalex→arxiv→jina + `return_findings` 终止协议；**从主 agent 删下的 `tool_usage` 教材搬来这里精简**）。
- **候选源冒泡（T1 seam 兑现点）**：retrieve 壳跑完子图后，把子 agent state messages 的工具结果（候选源）经 `ToolMessage.artifact`（langchain-core 1.2.23 支持——content 给主 agent LLM 看 findings、artifact 给程序）冒泡；`_consume_events` 在 retrieve 的 `on_tool_end` 把 artifact 累积进 `result["tool_results"]`（与现在累积主 agent 工具结果同构）。**`_persist_and_enrich` 零改**——候选收集的时机/关联（落库后按 agent_msg_id）不变，只是 `tool_results` 来源从「主 agent 工具」换成「子 agent 工具（经 retrieve 壳冒泡）」。
  > 不在桥接层直接写 sidecar 的原因：retrieve 壳跑完时主 agent 尚未落库、无 `agent_msg_id` 外键。
- 主 agent prompt **暂不动**（Stage 2 才去水），先验证子 agent 检索循环 + 回吐正确。
- **不接流式 custom event**（Stage 5），主 agent `_consume_events` 零改；`retrieve` 调用期间前端只见一个工具节点转圈。

### Stage 2 — 主 agent prompt 去水
- 删主 agent `tool_usage.py`（降级链编排挪子 agent prompt）；Phase 3 的 `TOOL_DECISION_PLUGIN`（Q1/Q2/Q3）压成极简 marker 闸门教学（保留 `[TOOL_LOOP: PENDING/DONE]` 信号，删多轮决策骨架）；`build_prefill` 清死分支降到 light。
- `_persist_and_enrich` **不动**（Stage 1 已让候选源经 artifact 冒泡到 `result["tool_results"]`，落库照常 collect + save_candidates + enrich + 幻觉检测）。Stage 2 纯 prompt/prefill 去水。
- 端到端验证：引用收集 / 幻觉检测 / is_cited 标记在子 agent 路径下正确（T1 seam 兑现）。

### Stage 3 — harness_probe 搬家 + cross-model `--model`
- `scripts/harness_probe.py` 适配子 agent：接 `build_subagent` + `RETRIEVER`；砍 `guard_hits`/`budget_hit` 哨兵分支（子 agent guard off + return_findings 不产 `GUARD_SENTINEL`/`BUDGET_SENTINEL`，恒 0/False 噪音）；`collect_metrics` 输入契约不变（`{messages, remaining_calls}`）。
- 加 `--model` argparse：`ChatOpenAI(model=args.model, ...)` 传给 `build_agent`/`build_subagent` 的 `llm` 参数（方案 1，与子 agent 绑 llm 同构）。
- 子 agent probe 单测复刻 `test_harness_probe_metrics.py` 的 `importlib.util` 文件加载模式。

### Stage 4 — probe 轮测验收（T2 验收门）
- gemini 下验证 `RETRIEVER`（guard off + prefill minimal 非流式）安全：`missing_thinking_calls` 合规、`tool_rounds` 合理、Tier-1 门 `tool_err_request` 不退化。
- 验证主 agent 行为不退化：删 prompt 编排后主 agent 稳定「只调 1 次 retrieve」、marker 闸门流式正确。
- 印证 memory `harness-vs-llm-change-stance`：probe 是回路（存活且搬子 agent 更值钱），校准点是 `RETRIEVER` 配置。

### Stage 5（第二版）— 前端嵌套分组可视化
- 子 agent 加 `adispatch_custom_event`（langchain-core 1.2.23 支持）：thinking start/end、每个内部 tool start/end dispatch，带 layer/parent 信号。
- `_consume_events`（`graph.py:739`）加分支认 custom event，路由成新 SSE 帧（`subtask_*`，带 `parent_tool_id`=主 agent 那次 `retrieve` 的 `run_id`，复用 `run_id` 作 grouping key）。不动现有 9 帧。
- 前端：`ChatPage.vue` `streamingTools` 给 `retrieve` 条目加 `children`；`ThinkingTimeline.vue` `steps` 从单层 map 改支持工具节点展开成子时间轴（递归）；`makeStreamHandlers` 加新帧处理。`consumeSSE`（`chat.js:61`）按 `evt.type` 派发，加 key 即可。

### Stage 6 — frozen 端到端 + 收尾
- `scripts/build_release.py` 打包；frozen exe 验证子 agent 路径（spec hiddenimports 补子 agent 新模块 + return_findings）。
- 回归：`test_prompt_byte_equivalence`（语义断言会因 Phase 3 改动变红，确认是真信号）、`test_consume_events`、`test_citation`、`test_harness_probe_metrics`。
- 可选收尾：`[TOOL_LOOP]` 改名 `RETRIEVAL_PENDING/RETRIEVAL_DONE`（牵动 `_MARKER_RE`+prompt+probe 哨兵+test，单做）。
- 更新 `plan/top-level-progress-log.md`（T2 完成、T3 解阻塞、想法 3 闭环）。

## 关键文件

- `src/rag/graph.py` — 抽 `_build_graph`、加 `build_subagent`/`return_findings`/`finalize`/`retrieve` 壳/桥接层；`_consume_events` 加 custom event 分支（Stage 5）；`build_prefill` 清死分支（Stage 2）。
- `src/rag/harness_profile.py` — 加 `RETRIEVER` 预置。
- `src/rag/prompts/` — 删/搬 `tool_usage.py`、压 Phase 3（`plugins.py`/`thinking.py`）、新增子 agent prompt 模块。
- `scripts/harness_probe.py` — 适配子 agent + `--model`。
- `src/llm.py` / `src/config.py` — `build_agent`/`build_subagent` 接 `llm` 参数（cross-model）。
- `frontend/src/{views/ChatPage.vue, components/Chat/ThinkingTimeline.vue, components/Chat/ChatWindow.vue, api/chat.js}` — Stage 5 嵌套分组。
- `physics_scholar.spec` — hiddenimports 补子 agent 新模块。

## 复用的现成函数（不要重写）

- `build_prefill`/`build_final_prefill`/`thinking_guard`/`after_guard`/`final_answer`（`graph.py` 模块级，子 agent 通过 profile 复用）。
- `src/rag/citation.py` 的 `extract_candidates` + 工具分派器 — `finalize` 按 `result_index/item_index` 抠 item。
- `collect_from_tool_results` / `save_candidates` / `load_enrichment_for_message` / `mark_cited` / `detect_hallucination` / `enrich_refs` / `parse_refs`（`citation.py` + `citation_store.py`）— 桥接层候选收集 + `_persist_and_enrich`，T1 seam 零改可复用。
- `harness_probe.collect_metrics`（搬子 agent，输入契约不变）。

## 验证（端到端）

- **单测**：子 agent 图（`return_findings` 终止路由、`finalize` 按索引抠、预算耗尽 `final_answer` 兜底）、桥接层（候选收集/幻觉检测/is_cited）、probe（`--model`、`RETRIEVER` 指标、哨兵分支已砍）。
- **probe 轮测（验收门）**：`RETRIEVER` 在 gemini 下 guard off + minimal 安全；主 agent 不退化。
- **dev 实跑**：`uvicorn src.main:app --reload`，提问触发子 agent 检索，验证 rich 引用完整 + lean 后台 + 流式 thinking/answer 正常（Stage 5 前检索期只见一个 `retrieve` 节点转圈）。
- **frozen**：`python scripts/build_release.py`，exe 跑通子 agent 路径。
- **回归**：`pytest tests/test_citation.py tests/test_prompt_byte_equivalence.py tests/test_consume_events.py tests/test_harness_probe_metrics.py`。

## 风险与回滚

- 核心红利：想法 3 自带「主 graph 结构零改动」——`retrieve` 工具做成 tools 列表可开关，最坏移除该工具主 agent 立即退回现状自跑检索循环，`main` 不受影响。
- 分支隔离（`t2-subagent-retrieval`）+ 每阶段独立提交，任一阶段失败可回退到上一阶段。
- 唯一不可逆：probe 轮测结论沉淀进 plan/memory（认知资产，非代码回滚问题）。
- 跨机交接：`data/` gitignored——本项不改 data 布局，无需手动转移本地资产（与 T1 不同）。
