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
- **`return_findings` 终止**：子 agent 工具，结构化参数 `selection=[{result_index, reason}], summary`。子 agent 检索够了就调它。`after_guard` 检测到该 tool_call → 路由 `finalize` 节点（不走 tool_node 回 call_llm）。
  > **实现定稿（偏离原计划）**：去掉了原计划的 `item_index`。编号单位是 **ToolMessage / 工具调用**（「第几次工具结果有用」），不做工具结果内部的 item 级筛选——主 agent 读整条选中的工具结果原文自己摘抄。item 级筛选（省 token）作为后续优化，第一版不上。
  > **2026-07-27 修订（Stage 4.5 解冻）**：item 级精度排上进程（用户预设精度，非可选）。拱心石 = `citation.py` `extract_candidates` 对四检索工具给干净条目（s2/arxiv/openalex=论文、rag=chunk），lookup/jina 单条整体。设计见下「Stage 4.5」。
- **`finalize` 节点**：从 state messages 找 `return_findings` args + 按顺序编号的真实 ToolMessage；**直接用 `ToolMessage.content` raw**（不经过 `citation.py` 的 `extract_candidates`——`Candidate` 只存元信息无 content 字段，而 finalize 要的是工具结果原文给主 agent 读），按 `result_index` 抠选中工具调用的整条 content 拼成 findings 写 `state['findings']`。候选 enrichment 收集仍复用 `collect_from_tool_results`（喂 retrieve 壳冒泡的原始工具结果，零改）。子 agent 全程**只点索引不转写元信息**（解中间商抄错 + 不白花 token 原样吐）。
- **摘抄归属留主 agent**：桥接层传给主 agent 的是被选中工具调用的 content raw（限长兜底，`MAX_FINDINGS_LEN=12000` 截断，jina 长 blob 截断），主 agent 自己读自己摘抄进 ref——grounding 留主 agent 保反幻觉初衷。
- **配置（修订后）**：`RETRIEVER = HarnessProfile(guard_mode="strict", prefill_level="minimal", final_prefill="light", budget_n=6)`。guard **strict**（与拆分前检索循环所在的 strict 对等——不因搬进子图就卸掉 per-call thinking 监管；「优先拆、之后考虑减」，先保对等基线，probe 验证后再议松到 soft/off）+ prefill **minimal**（非流式 ainvoke、不经流式 marker 闸门；⑤ minimal 破契约是流式空间的坑，子 agent 非流式不踩）+ budget **6**（对齐拆分前全局检索额度；子 agent 纯检索不写答案，6 次成功检索够用，且 guard 驳回重试不消耗预算——`call_llm` 扣、`thinking_guard` 驳回时 `+1` 还回）。**预算耗尽走 `finalize` 兜底**（`after_guard` 的 `budget_route=finalize`，拼已有工具结果作 findings）；`final_answer` 节点因共用 `_build_graph` 仍 add（子 agent 图里是死节点，永不路由到）。
  > 与原计划「final_answer 子 agent 必留兜底」表述出入：实际兜底由 finalize 承担（拼工具结果 = 当时的 findings），final_answer 节点保留仅为共用图结构。

## 实现阶段（每阶段独立可提交）

> **进展**：Stage 0 ✅（commit `0a66a15`）/ Stage 1 ✅（commit `18902a5`，harness `RETRIEVER` + 子 agent 图 `build_subagent`/`return_findings`/`_subagent_finalize`/`retrieve` 壳 + 候选源经 `ToolMessage.artifact` 冒泡 + `_consume_events` artifact 分支 + `subagent_prompt.py`）/ Stage 2 ✅ + **修订**（见下「Stage 2」：TOOL_DECISION_PLUGIN/output_format/协议 B 回滚原版保 CoT phase 链 + retrieve docstring 承单次寿命契约 + 状态信封 5 分类 + RETRIEVER strict/budget6 + 子 agent thinking 收紧；核心测试全过）/ Stage 3 ✅（见下「Stage 3」：harness_probe `--target sub`/`--model` + `_prepare` 主 agent 种子 bug 修复（remaining_calls 6→1，单次 retrieve 的 budget 安全网才真正生效）+ `test_harness_probe_subagent.py`；probe 真跑留 Stage 4）。子 agent 真跑子图端到端验证（Stage 4 probe / dev 实跑）待续。 / **Stage 4 ✅**（task #9 v3 prompt 定版 + RETRIEVER minimal→light + 主/子agent metadata 清零沉淀进 CLAUDE.md + result_index 系统标注修复索引 bug + Q03 端到端验证通过——详见下「Stage 4」）。**Stage 4.5 ✅**（item 级切片 + collect-selected 存储精度——详见下「Stage 4.5」）。

### Stage 0 ✅ — 分支 + 抽 `_build_graph`（零行为变化地基）
- 建分支 `t2-subagent-retrieval`。
- 从 `build_agent` 抽出 `_build_graph(llm, tools, profile, *, terminator=None, finalize_fn=None)`；`build_agent` 改调它（主 agent 路径 terminator=None，行为字节级不变）。
- `build_agent` 加可选 `llm=None`（默认走模块级 `llm`，cross-model probe 与子 agent 共用）。
- 验证：现有 `test_consume_events`/`test_citation`/主 agent 端到端零变化。

### Stage 1 ✅ — 子 agent 图 + 桥接（后端核心，ainvoke 阻塞）
- `harness_profile.py` 加 `RETRIEVER` 预置 + 进 `PRESETS`。
- `graph.py` 加 `return_findings` 工具 + `finalize` 节点（复用 `citation.py` extract）+ `build_subagent`。
- 加 `retrieve` 工具壳：`make_retrieve_tool(user_id)` 闭包，内部 `build_subagent().ainvoke()` 取 findings，拼成主 agent ToolMessage。
- `build_agent` 主 agent 工具列表换成 `[retrieve 壳]`（保留闭包模式）。
- 新增子 agent system prompt 模块（`prompts/modules/` 下，检索聚焦精简版，教降级链 s2→openalex→arxiv→jina + `return_findings` 终止协议；**从主 agent 删下的 `tool_usage` 教材搬来这里精简**）。
- **候选源冒泡（T1 seam 兑现点）**：retrieve 壳跑完子图后，把子 agent state messages 的工具结果（候选源）经 `ToolMessage.artifact`（langchain-core 1.2.23 支持——content 给主 agent LLM 看 findings、artifact 给程序）冒泡；`_consume_events` 在 retrieve 的 `on_tool_end` 把 artifact 累积进 `result["tool_results"]`（与现在累积主 agent 工具结果同构）。**`_persist_and_enrich` 零改**——候选收集的时机/关联（落库后按 agent_msg_id）不变，只是 `tool_results` 来源从「主 agent 工具」换成「子 agent 工具（经 retrieve 壳冒泡）」。
  > 不在桥接层直接写 sidecar 的原因：retrieve 壳跑完时主 agent 尚未落库、无 `agent_msg_id` 外键。
- 主 agent prompt **暂不动**（Stage 2 才去水），先验证子 agent 检索循环 + 回吐正确。
- **不接流式 custom event**（Stage 5），主 agent `_consume_events` 零改；`retrieve` 调用期间前端只见一个工具节点转圈。

### Stage 2 ✅（+ 修订）— 主 agent prompt 去水 → 修订：单次 retrieve 契约重构

**初版（commit `e907b4f`）**：删 `tool_usage.py`（降级链挪子 agent prompt；3 yaml 清 `TOOL_USAGE`）+ 主 agent `budget_n=1` 硬保证。但初版把 `TOOL_DECISION_PLUGIN` 压成「单次 retrieve 契约」、`output_format`/协议 B 改「DONE→进入正文」——**破坏了 CoT phase 链**（Phase 3 是中间 phase，DONE 后要走完 Phase 4-7 再闭合 thinking 进正文，不是直跳正文），且把通用循环逻辑特化成了单工具硬编码。

**修订（本批）**：
- **回滚 phase 链**：`TOOL_DECISION_PLUGIN`（Phase 3 = `{tool_decision_plugin}`）回滚原版 Q1/Q2/Q3「工具调用申请书」通用循环（DONE→Next Phase、PENDING→闭合等工具）；`output_format` + `build_prefill` 协议 B 同步回滚原版。理由：主 agent 仍是「调工具的循环」，budget=1 + 单工具只是当下配置；保留通用循环对未来扩展工具必要（即便当下杀鸡用牛刀）。检索编排脑（降级链）仍留子 agent。
- **单次寿命契约挪 docstring**：「retrieve 一次寿命、高度置信、拿到就答、不二次调用」是**工具契约**（与全局 budget 两根独立轴），写进 `retrieve` docstring（随 bind_tools、per-tool 可扩展），不进通用循环 PLUGIN。今天 budget=1 已机器兜底（`after_guard` remaining<0→final_answer 拦第 2 次 retrieve）；**budget>1 时的预算无关兜底（`after_guard` 数 retrieve 调用次数→final_answer）记为后续 safeguard，等加第二个工具时再上**。
- **retrieve 状态信封**：返回 `[检索状态: ...]` + `agent_hint` + findings，5 分类（SUCCESS/NO_MATCH/INFRA_FAIL/INCOMPLETE/HARD_FAIL，`_classify_retrieve_outcome`）——对齐 s2 等工具的 agent_hint 约定，让主 agent 区分「无符合材料」(NO_MATCH) 与「上游硬伤」(INFRA/HARD)。`test_retrieve_classify.py` 9 例覆盖。
- **RETRIEVER 配置**：guard off→**strict**（拆分前对等）、budget 8→**6**（对齐拆分前全局）；prefill minimal 保留（非流式无关项）。
- **子 agent thinking 收紧**：`subagent_prompt` 删「thinking 可省略」（与 strict guard 矛盾），改要求每轮 thinking 写缺口/动作/判停。**当前子 agent prompt 刻意轻量**——是否加料留待 Stage 4 probe 看 guard 拦截/契约遵守效果再定。
- **guard 纠错话术通用化**：删 Q1/Q2/Q3 硬编码引用（主/子 agent 通用，按各自 system prompt 结构）。
- **citation（Point 2）**：不向 LLM 注入「系统按 source_id 自动填元信息」这类隐藏机制描述（撤回「加回 source_id 指令」）；lean ref 格式（source_id 前缀）保留。agent 是否自发写元信息 + `enrich_refs` 是否 robust 留 probe 验，必要时加 **harness 层 strip**（渲染时只留 source_id+snippet）而非 prompt 指令。
- `_persist_and_enrich` 不动；`test_prompt_byte_equivalence` 删 3 条 stale 断言（bind-by-id/系统自动填/不翻译，编码旧「告知机械化」立场，被 Point 2 反转），保留 lean 格式断言。
- ⚠️ 主 agent 单次 retrieve 行为 + 子 agent strict/minimal 安全性待 Stage 4 probe 真跑验证。

### Stage 3 ✅ — harness_probe 搬家 + cross-model `--model`（+ 主 agent 种子 bug 修复）
- **主 agent 种子 bug（修复，折进本 stage）**：`_prepare` 原用 `profile.budget_n`(=6) 播 `remaining_calls`，但 `build_agent` 强制 `budget_n=1`——`call_llm` 从 **state** 读 remaining(=6)，致 `after_guard` 的 `remaining<0` 兜底要到第 7 次 retrieve 才触发，「单次 retrieve 契约」的 budget 安全网失效（沦为只靠 prompt 自觉），与 `build_agent` docstring「budget_n=1 硬保证」不符。修：`_prepare` 播 `remaining_calls=1`（主 agent 有效预算恒 1）；probe 主 target 同步播 1 才能当有效验收门。trace 验证：seed=1 时第 2 次 retrieve 即 remaining=-1→final_answer 拦下。
- `scripts/harness_probe.py` 适配子 agent：加 `--target {main,sub}`——`sub` 直跑 `build_subagent`+`RETRIEVER`+子 agent prompt（把检索循环从 retrieve 壳拎出来单测，不经过主 agent）；`main` 验单次 retrieve 契约 + marker 闸门流式。`--target sub` 默认 RETRIEVER、main 默认 FLASH，显式 `--profile` 优先。
- 加 `--model` argparse：`_build_override_llm` 用 `ChatOpenAI(model=...)` 覆盖 main_llm（其余 base_url/key/温度/重试/流式对齐），传 `build_agent`/`build_subagent` 的 `llm` 参数——cross-model 重跑比 harness 行为差异。
- `build_initial_state` 拆 `build_main_state`(rem=1)/`build_sub_state`(rem=profile.budget_n、findings='')。**`collect_metrics` 契约不变、代码不动**——原计划「砍 budget_hit 哨兵分支」改为不动 + 注释说明（砍会 fork collect_metrics、违背「契约不变」）：修订后 RETRIEVER guard **strict**，`guard_hits` 是真信号（子 agent 违规产 `GUARD_SENTINEL`，与 `missing_thinking_calls` 交叉校验）；但预算耗尽走 finalize（非 final_answer）→ `BUDGET_SENTINEL` 永不产、`budget_forced` 恒 False（`budget_hit` 仍由 `remaining<=0` 反映）；marker 闸门主 agent 流式专属，子 agent `marker_emit_rate` 恒 0/None（真零）。
- 子 agent probe 单测 `test_harness_probe_subagent.py`（复刻 `test_harness_probe_metrics.py` 的 `importlib.util` 文件加载）：`build_main_state` rem=1 回归门、`build_sub_state` 形状、`collect_metrics` 在子 agent 风格 transcript（return_findings 不计 tool_rounds、strict guard 哨兵真信号、预算耗尽无 BUDGET_SENTINEL）。5 例 + 既有 metrics 11 例全过；T2 核心 76 例无回归。
- ⚠️ probe 真跑（gemini cross-model 轮测）留 Stage 4——本 stage 只交付量具 + 离线单测，未触网。

### Stage 4 — probe 轮测验收（T2 验收门）→ 已转向「子 agent prompt 重构」（task #9）
- **probe 冒烟已跑**（gemini-3.1-pro-preview，Q03，`--target sub`）：plumbing ✓、strict guard 拦 2 次漏 thinking ✓（挣到工资）、Tier-1 `tool_err_request=0` ✓、预算恢复机制 ✓。但暴露 v1 轻量子 agent prompt **退化性不收敛**：42KB 自发明 `[tool_loop]/[tool_call]/[tool_response]/[end]/[start]` 循环、从不调 return_findings。
- **根因三连**：① 子 agent prompt 零标记教学真空；② guard 哨兵泄漏主 agent `[TOOL_LOOP]` 种子（**已修**：graph.py thinking_guard 哨兵去 `[TOOL_LOOP]`，与 correction_text 同口径）；③ minimal prefill 裸 `[start]` 无结构锚点。
- **认知纠偏（沉淀进 CLAUDE.md「Tool text layering」）**：拆 ≠ 降级——子 agent 要把主 agent 工具循环那段 CoT（Phase 0-3 + Q1-Q3 申请书）**原样搬过来**（改编语境、去 marker、去答案侧），不是削成轻量；docstring/schema/prompt(tool-introduction 节)/CoT 四处分工不重复；CoT 通用不耦合具体工具。
- **转向 task #9（clean-slate prompt 重构）**：handoff 契约改为 `retrieve(question, gap, constraints)`（B）；return_findings 折进工具介绍节、WHEN 不进 CoT；guard/prefill/budget-accounting 等「小头」挂起，待 prompt 定版（大头）后再调。
- 验证主 agent 行为不退化：删 prompt 编排后主 agent 稳定「只调 1 次 retrieve」（种子修复后 budget 安全网真生效）、marker 闸门流式正确。
- 印证 memory `harness-vs-llm-change-stance`：probe 是回路（存活且搬子 agent 更值钱），校准点是 `RETRIEVER` 配置 + 子 agent prompt。

> **未来扩展（deferred 留痕）**：`retrieve` 的工具特定 WHEN（「一次寿命」）目前暂栖其 docstring——因主 agent 侧无 per-tool prompt 节（T2 删 tool_usage.py 后主 agent prompt 通用、不感知具体工具）。**未来主 agent 绑定 >1 工具时，让 tool_usage 节在主 agent 侧回归，把工具特定 WHEN 从 retrieve docstring 挪进该 prompt 节**（对齐「prompt tool-introduction = WHEN」铁律）。同批 deferred：~~prefill minimal→light~~ ✅（Stage 4：minimal 非流式也破契约）、~~Stage 4 全量 re-probe~~ ✅（Q03 通过）；仍 deferred：guard 调参、return_findings 预算豁免（代码）、retrieve WHEN relocation。**item 级精度**（原 deferred 的 `item_index`）已解冻 → 见「Stage 4.5」。


### Stage 4 ✅ — task #9 prompt 定版 + result_index 系统标注修复 + 端到端验证

**task #9（v3 子 agent prompt）定版**：clean-slate 重写——照搬主 agent 工具循环 CoT（Phase 0-3 + 申请书）改编语境（检索系统/降级链/`return_findings` 收敛协议），去 marker、去答案侧；Strategy 节列 7 工具含 `return_findings`（绑「收敛返回」动作 + 预算豁免声明）。文件：`src/rag/prompts/subagent_prompt.py`。

**配置定版**：RETRIEVER `prefill_level` minimal→**light**。Q03 probe 实证 minimal（裸 `[start]` 无 `<think>` 锚点）compliance 0.0——模型吐空 content + tool_call 无 `<thinking>` 包裹，guard 连拒 3 次放弃；light 注入 `<think>…现在输出 [start]` 引导，compliance 1.0。**推翻旧认知「minimal 破契约仅限流式空间」——非流式 ainvoke 也踩**。文件：`src/rag/harness_profile.py`（注释带实证）。

**主/子 agent metadata 清零（invariant 沉淀进 CLAUDE.md）**：LLM 可见文本（@tool docstring / Field.description / prompt / prefill / guard 哨兵 / 跨边消息）严禁出现 主/子agent/subagent/子系统/子图——9 处泄漏已修（retrieve docstring「返回支撑主 agent」→「支撑你」、instruction label「主 agent 甄别」→「信息缺口」、`_classify_retrieve` agent_hints 6 处去子 agent 措辞）。dev-facing 文本（模块/函数 docstring、logger、变量名 `build_subagent`）豁免。`thinking_guard` 哨兵去 `[TOOL_LOOP]`（避免给子 agent 泄主 agent marker 种子）。

**result_index 索引 bug + 系统标注修复（用户设计）**：原实现子 agent 自数「有用结果」（主观）与 finalize 按 ToolMessage 顺序编号错位——子 agent 选 `result_index:0`（意图指 rag 综述）却映射到 s2 报错（首条 ToolMessage）。修复：①系统注入 1-based 序数标注——`_annotate_tool_results(state, tool_result)` 在每次工具后插 HumanMessage「第 N 次工具调用结果 · 工具 {name}」（N = 已有具名 ToolMessage 数 +1，与 finalize 同口径；ToolMessage content 保持干净，不影响候选抽取）；②finalize `enumerate(tool_msgs, 1)` 1-based；③**子 agent 也看到失败调用**（消耗预算无结果的调用照标 N——可诚实反馈「N 次因上游失败无果」，鼓励诚实而非掩盖）。ToolMessage content 不动（annotation 走独立 HumanMessage，候选抽取零影响）。

**端到端验证（Q03，`--target main`，gemini-3.1-pro-preview，PS_DUMP_SUBAGENT=1）**：标注正确注入（第 1/2/3 次·s2/openalex/s2）、子 agent 按 N 选 `result_index:1,3`（正确命中 s2 综述结果，**非报错**）、findings 含「检索结果 #1/#3（s2_search_tool）」+ 诚实 summary（微波光子学/光频梳/铌酸锂三综述）、主 agent compliance 1.0 / 单次 retrieve / marker 1.0 / 非空 grounded 答案。

**验收门全过**：compliance 1.0、单次 retrieve、marker 闸门、Tier-1 `tool_err_request=0`、主 agent 非空 grounded。probe 回路额外发现并修掉索引 bug（印证 memory `harness-vs-llm-change-stance`：回路是资产，校准梯度不校准点）。

> **遗留（非阻塞）**：子 agent 偶把 `result_index` 当论文级用（多结果场景下选中整批=无害，单结果大集会顶 `MAX_FINDINGS_LEN` 截断丢相关项）——正是 Stage 4.5 item 级精度要根治的。

### Stage 4.5 ✅ — item 级切片 + collect-selected（存储精度对偶）

**动机**：result_index 是**调用级**（一次工具调用=一条结果，s2 一次返 5-50 篇）。子 agent 只能选整批——大结果集会顶 `MAX_FINDINGS_LEN=12000` 截断、可能截掉相关项；且无法剔除同批里的离题项。原计划本含 `item_index`（Stage 1 实现时简化掉），现补完。

**拱心石（已验）**：`citation.py` `extract_candidates(tool_name, content)` 对四检索工具给干净可选项——s2/arxiv/openalex=论文（每篇 Candidate，含 title/authors/year/source_id）、rag=chunk（正则提 `[rag:doc_id|title,Page N]` 串头，按 doc_id+page 去重）；lookup_local_paper_id/jina 不在分派表→`[]`，作单条整体（前者单 doc_id 查询、后者整篇全文不可切）。条目语义跨工具一致。

**设计**：
- **标注扩展**：`_annotate_tool_results` 在「第 N 次工具调用结果 · 工具 {name}」后扩 item 序号——`#1 {Candidate.title} ({authors}, {year})`（rag 用 `title, Page N`）。子 agent 看 N+K 双层序号选 `(result_index=N, item_index=K)`。lookup/jina 单条不列 item（或整体作 #1）。
- **schema**：`return_findings.selection` 加可选 `item_index: int`（缺省=整条结果，向后兼容当前已验证路径）。
- **finalize 切片**：按选中的 `(N, K)` 抠单 item raw——外部工具重包 `{"papers":[pk]}`、rag 按 `\n\n---\n\n` 切块对齐串头取第 K 块。`MAX_FINDINGS_LEN` 截断压力大幅降（只打包选中项）。
- **Field 三层**：item_index 的 Field.description 只写格式+防呆（1-based、缺省整条），WHEN 不进；return_findings 用法不进 CoT（保持通用）。

**文件**：`graph.py`（`_annotate_tool_results` 扩 item 序号 + finalize 切片 + `return_findings` schema 加 item_index）、`tests/test_subagent_finalize.py`（item 级选择 + 向后兼容）、probe 复测。

**风险**：finalize 切片需 per-tool（外部重包/rag 切块对齐），但复用 extract 分派口径，风险可控；可选 item_index 保当前路径作回退。

**✅ 完成（2026-07-28）**：
- **item 级切片**：`return_findings` 加可选 `item_index`（缺省=整条，向后兼容）；`_iter_tool_items` 单一切片事实源（s2/arxiv/openalex=论文 `{"papers":[p]}`、rag=chunk 按 `---` 切、lookup/jina 单条）；`_select_items` 返 per-item 列表；标注扩「共 M 条候选，按返回顺序 #1..#M」（单条/失败调用不列）；finalize 按选 item 抠。Q03 probe 验：子 agent 用 `(result_index=1, item_index=2/4)` + `(4, item_index=3)` 精准点 3 篇，findings 不再整批+无越界（Stage 4 的 1,3,4 怪象消失）。
- **collect-selected（存储精度）**：候选收集从「全 raw」（retrieve 壳 L1115 `_tool_results_from_messages` 收子 agent 全部 ToolMessage，~20 候选）改为「仅选中项」。finalize 拼 findings 时**同源**吐 `selected_tool_results: [(name, item_json), ...]` per-item 列表（每条合法 JSON，`extract_candidates` 可解析）；retrieve 壳 artifact 改读它（缺失退回全 raw 兜底，不丢候选）。候选集从 ~20 缩到 3，消存储噪声 + `is_cited=0` 噪声行，且候选集对齐主 agent 证据范围（findings）——原「要 raw 做 source_id 提取」是 Stage 1 findings≈raw 时的前提，item 切片后前提失效。
- **findings 不合并**（用户定）：per-item 扁平 artifact 让 collect-selected 可行（每条合法 JSON）+ findings 文本保持两-blob（主 agent 按论文引用、不按调用；合并省 token 可忽略、且要 per-tool 特化，不值）。
- **验证**：101 单测绿（含 `selected_tool_results` 排除未选结果 / 向后兼容 / 预算耗尽兜底全 raw / `_select_items` per-item 列表）；Q03 probe 主 agent 指标与改前逐字一致（compliance 1.0 / 单 retrieve / marker 1.0 / ~1.5k 字 grounded）—— collect-selected 是存储层改动，零可见回归（probe 绕开 `_persist_and_enrich`，候选落库由单测 + langgraph state 契约覆盖）。

### Stage 4.5 修订 ✅ — collect-selected live 实证 + 嵌套事件泄漏修复（probe 够不着的真 bug）

**触发**：Stage 6 子项「dev-live 验证候选落库」先行。probe 绕开 `_consume_events`/`_persist_and_enrich`，故 collect-selected 的 live 落库一直只有单测+state 契约覆盖。本次走 routes.py 唯一生产路径 `chat_stream` 真跑（`scripts/dev_live_verify_candidates.py`，Q03），**抓到 Stage 1 起的潜伏 bug——collect-selected 在 live 路径完全没生效**：两次跑 DB 都=10（全 raw），收敛比 1.00。

**Bug 1（嵌套事件泄漏）— 确定性根因**：子 agent 图在 `retrieve` 工具内 `ainvoke`，其内部 `s2`/`arxiv` 工具事件经 `astream_events(v2)` 冒泡进主 `_consume_events`（`on_tool_start`/`on_tool_end`/`on_chat_model_stream` 均无 depth/run 过滤）。嵌套 `on_tool_end` 的 `artifact=None` → 走 elif 把子 agent 全量 raw 当主结果收 → 候选落库=全 raw，压过 retrieve 壳 artifact 的 collect-selected；同 handler 还发 SSE `tool_start/end`，前端出现嵌套工具节点（违反「只见一个 retrieve 节点」+ 主/子互不感知）。probe 之所以没抓到：probe 直跑 ainvoke、不经过 `_consume_events`。
  - **修**：`_consume_events` 顶部加 depth 过滤——`metadata["langgraph_checkpoint_ns"]` 含 `|` = 嵌套子图命名空间（主图是单层图，retrieve 是 tool 非 subgraph node，根层事件 ckpt_ns 单段永不含 `|`；子 agent 在 retrieve 工具内 ainvoke，事件 ckpt_ns 形如 `tool_node:X|tool_node:Y`）。字段由 `scripts/probe_astream_events.py` 实测钉死。一处过滤三症状全修（候选+SSE+thinking）。
  - **实证**：SSE 工具事件 `retrieve→s2→s2→retrieve`（4 嵌套帧）→ `retrieve` 单帧（0 嵌套）。

**Bug 2（result_index 0-based）— 模型遵从度**：depth 过滤后 DB 仍=10，排查发现 retrieve 壳 artifact 本身=10——finalize 回退全拼。根因：子 agent 写 `result_index:0`（0-based），而契约是 1-based（标注「第 1 次」+ `enumerate(tool_msgs,1)`）→ `sel_map={0:...}` 匹配不到 1/2/3 → `picked` 空 → elif 兜底全 raw。`RetrievalSelection.result_index` Field.description 只写「检索结果序号」**未注明 1-based**（`item_index` 注了），模型按程序员惯性取 0-based。
  - **修**：result_index Field.description + return_findings docstring 补「1-based，填上方「第 N 次」的 N；首次填 1 不是 0」（与 item_index 同款防呆）。
  - **诚实边界**：depth 过滤是确定性修法；result_index 防呆**降低**而非消除模型 off-by-one（本次实跑模型写了合法 1-based，findings 3029≈3 项）。若复发，备选 = 代码容忍（selection 只含 0 且无 ≥1 时 0→1 启发式，有歧义风险）。

**✅ 联合实证（2026-08-05，Q03，gemini-3.1-pro-preview，走 chat_stream）**：raw=5 / selected=3 / **DB=3**（Optical frequency combs / Lithium niobate / Microwave photonics，正是子 agent 选中且主 agent cited=1 的 3 篇），收敛比 1.00→**0.60**，SSE 嵌套帧 4→**0**。101 T2 单测绿。`dev_live_verify_candidates.py` 留作可复跑回归门。

**旁路发现（未修，记follow-up）**：非流式 `chat()`/`regenerate()`（无 route 调用，测试兜底路径）的 `_tool_results_from_messages` 不读 retrieve 壳 artifact → 子 agent 架构下候选收集=0；L1230 注释称「与流式 on_tool_end 累积同构」已失真。生产用流式，故非阻塞。

**dev 数据漂移（换机发现，已处理）**：本机 `data/chroma_db` 是旧 384 维 embedding 建的，与现配置 `bge-m3`(1024 维) 冲突——`rag_tool` 一查就崩，异常穿透子 agent 图拖整轮 HARD_FAIL。从换机前备份 `H:\离职携带资料\per_doc\physics-scholar\data`（1024 维、`ref_enrichment` 表齐全、7 篇入库）整体挪用；漂移旧 data 留底 `data.drifted_bak/`（本地，不入库，可删）。

### Stage 5（第二版）— 前端嵌套分组可视化
- 子 agent 加 `adispatch_custom_event`（langchain-core 1.2.23 支持）：thinking start/end、每个内部 tool start/end dispatch，带 layer/parent 信号。
- `_consume_events`（`graph.py:739`）加分支认 custom event，路由成新 SSE 帧（`subtask_*`，带 `parent_tool_id`=主 agent 那次 `retrieve` 的 `run_id`，复用 `run_id` 作 grouping key）。不动现有 9 帧。
- ⚠️ **与 Stage4.5 修订 depth 过滤的交互**：修订加的 depth 过滤（`langgraph_checkpoint_ns` 含 `|` 即跳过）会一并跳过子 agent dispatch 的 custom event（它们也带嵌套 ckpt_ns）。Stage 5 须把 custom-event 分支**置于 depth 过滤之前**（先认 `on_custom_event`/`subtask_*` 再过滤），或细化过滤为「跳过嵌套的既有帧（tool/chat_model）、放行嵌套 custom event」。否则嵌套可视化的信号会被自己掐掉。
- 前端：`ChatPage.vue` `streamingTools` 给 `retrieve` 条目加 `children`；`ThinkingTimeline.vue` `steps` 从单层 map 改支持工具节点展开成子时间轴（递归）；`makeStreamHandlers` 加新帧处理。`consumeSSE`（`chat.js:61`）按 `evt.type` 派发，加 key 即可。

**✅ 完成（2026-08-07）—— 专用 custom-event 通道方案（不放行原生嵌套事件）**：
- **设计落定**：用 `adispatch_custom_event("subagent_trace", {kind,...})` 作受控通道透出子 agent 进度；`_consume_events` 在 **depth 过滤之前**加 `on_custom_event` 分支转成新 `subtask` 帧（带 `parent_tool_id`=retrieve run_id）。原生嵌套 `on_tool_*`/`on_chat_model_*` 仍被 depth 过滤掐断 → collect-selected / SSE 不泄漏的修复**不回退**。两全（trace 走 custom event，原生事件照滤）。
- **后端**（`graph.py`）：`_build_graph` 加 `emit_trace` kwarg（主 agent 不传=零变化）；`call_llm` 闭包 ainvoke 前后 dispatch thinking_start/end；`tool_node` 闭包（子 agent 专属）dispatch tool_start（工具名从末条 AIMessage tool_calls 读）/tool_end（ok 复用 `_tool_ok`）；`build_subagent(emit_trace=True)`；`_consume_events` 加 `active_retrieve_run_id`（on_tool_start 捕获 / on_tool_end 清）+ on_custom_event 分支（depth 过滤前）。`_safe_dispatch_trace` 包 try/except，dispatch 失败只 debug 不阻断检索。
- **前端**：`ChatPage.vue` retrieve 条目带 `children: []` + `subtask` handler（按 parent_tool_id 挂 children，按 kind 增改 thinking/tool 子步）；`ThinkingTimeline.vue` 加 `retrieve:检索` 标签 + steps 映射 children + 模板内联 1 层嵌套子时间轴（虚线缩进，复用 spinner/✓/✕）；live-only（done 后 resetStreaming 清，不动 DB）。
- **probe 实证**（`probe_astream_events.py`，首要风险已除）：custom event **能**从子 agent 节点冒泡到主 astream_events，data 正确（kind/name/ok）；ckpt_ns 含 `|`（证必须放过滤前）；到达时机夹在 retrieve on_tool_start/end 之间（active 窗口命中）。
- **dev-live 实证**（`dev_live_verify_candidates.py`，Q03）：`subtask` 帧 10 个（3 轮思考 + s2/openalex 2 工具），序列对、`parent_tool_id` 全对齐 retrieve run_id；**DB 仍=3 选中项**（collect-selected 未回退，收敛比 0.60）；SSE 嵌套泄漏 0 帧。前端 `npm run build` 干净（`✓ built`）。78 单测绿（含 2 个 subtask 路由：含 `|` ckpt_ns 也能成帧 + active 窗口外 parent=None 降级）。
- **未验**：浏览器视觉渲染（需 `npm run dev` + 人眼看 retrieve 节点下嵌套轴实时更新）——SSE 管线 + 前端编译 + handler 逻辑（照搬主时间轴已验证模式）均过，视觉为最后确认项。

### Stage 6 ✅ — frozen 端到端 + 收尾（2026-08-14）
- **spec 审计**：T2 新增全在 graph.py（`build_subagent`/`return_findings`/`_subagent_finalize` 等已在 hiddenimports 的 `src.rag.graph` 内）+ `subagent_prompt.py`（graph.py 顶层 import + `datas` 整个 src/ 打包，双覆盖）；`adispatch_custom_event`（langchain_core，graph.py 顶层 import）。**结论：spec 零改动**。
- **frozen 构建 + 验证**：`build_release.py` 打包成功（216M bundle）；frozen exe 起得来（`/api/health` 200、前端 200、`/api/conversations` 200 = 全 T2 模块 frozen 解析 OK）；走 `/api/ask` 本地检索题，**retrieve + subtask 帧（custom-event 通道）流经 frozen exe**（`parent_tool_id` 对齐），`POST /api/ask 200`。子 agent 路径 frozen 实证通过。
- **回归**：`test_prompt_byte_equivalence`（plan 预测会红——实际绿，Phase 3 改动已带测试更新，无红灯）+ `test_harness_probe_metrics`/`_subagent` + `test_consume_events`/`test_subagent_finalize`/`test_citation`/`test_retrieve_classify` 全绿（32 + 78）。
- **顺带修**：`build_release.py` 在 GBK 控制台因 `✅` emoji 编不出抛 UnicodeEncodeError、误报失败（构建已成功）——加 `sys.stdout.reconfigure(utf-8)`。
- **收尾文档**：`top-level-progress-log.md` 标 T2 ✅ + 想法 3 闭环 + T3 解阻塞。
- **遗留（非阻塞）**：`[TOOL_LOOP]` 改名（牵动正则/prompt/probe/test，可选单做）；非流式 `chat()` 候选=0（无 route）；result_index 模型 off-by-one 监控。

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
