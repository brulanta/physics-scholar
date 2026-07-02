# Harness 过约束诊断与可插拔化改造计划

> 定位：这是一份**诊断 + 设计靶子文档**，不是立即实施单。产出是「把 `graph.py` 里那坨为弱模型（ds-v4-flash）而生的约束，拆成彼此正交的开关」，为后续「同一模型、两版（或多版）harness 对照实验」提供可控变量。实施与评测量具是后续阶段（见文末 Next-stage TODO）。

## Context（为什么做这件事）

现有 harness（`src/rag/graph.py`）是围绕 **ds-v4-flash** 手工调出来的。flash 对格式遵守极差，于是架构用**重度 prefill 催眠 + thinking_guard 强制驳回 + 威胁性话术**把它逼到「能合法输出思维链、能按规矩调工具、能写答案」。

问题假设（开发者提出，待测）：**这套为弱模型定制的强约束，对更强的模型可能是负担——要么过度限制发挥，要么分散注意力。** 这是 harness engineering 的已知特性：一套 harness 未必跨模型通用。

要验证「松绑对强模型是帮助还是伤害」，前置条件是**把约束拆成可单独插拔的开关**，才能做控制变量实验（只翻一个开关，跑同一批问题，比行为指标）。本计划就是把这个「拆」的方案固化下来。

### 已确立的关键认知

1. **约束不是一块铁板，是 6~8 条正交的轴**（见下表）。大多数可干净插拔。
2. **唯一暗坑是 prefill（C）**：它表面是一个约束，实则=**协议层 C1**（模型必须吐 `<thinking>…</thinking>` + `[TOOL_LOOP: DONE/PENDING]` 标记）+ **强度层 C2**（那段威胁+自我催眠文本）两层。C1 **硬耦合流式显示状态机**（`_consume_events` 靠这俩标记决定何时放正文）；C2 才是过约束嫌疑犯。**第一刀切 C2，别碰 C1。**
3. **原生思维链已被全局禁用**（开发者为「控温」所做的决策，DS reasoner 忽略 temperature）。这是独立轴 G，关闭字段是 DS 专属（`DEEPSEEK_EXTRA_BODY`），换模型需换字段。因此 `reasoning_content` 通道在 DS 上恒空，[graph.py:408-418](../src/rag/graph.py#L408-L418) 那段误判检测在当前模型下不触发——但换强模型/别家时会复活，属于「关闭开关如何跨模型可靠」的 harness 题。
4. eval_framework **不能**当回归护栏用（内容测试、非 robust、全手动），所以任何松绑实验的前置是先有「自动 + 行为感知」的量具——那是后续阶段（②），本计划只做①。

---

## Part 1 — 约束轴清单（诊断表，带行号靶点）

| 轴 | 约束点 | 代码位置 | 为谁而设 | 能否干净插拔 | 插拔影响（翻掉它牵动什么） |
|---|--------|---------|---------|------------|--------------------------|
| **A** | **thinking_guard 强制**：缺 `<thinking>` 就驳回、为每个 tool_call 塞假 ToolMessage、退款 `remaining+1`、重试 3 次，超限强制 final_answer | [graph.py:150-213](../src/rag/graph.py#L150-L213)、路由 [216-238](../src/rag/graph.py#L216-L238) | flash | **较干净**：单一节点，改直通即可 | `after_guard` 依赖 `is_thinking_correction`/`thinking_retry_count`；guard 不再 set 时路由仍成立（走 tool_node/END）。低耦合，可独立关或降级为 soft（只警告不驳回） |
| **B** | **纠正话术语气**：「申请单/被驳回/权限永久关闭」 | [graph.py:194-204](../src/rag/graph.py#L194-L204) | flash | **干净**：纯文本，可换中性措辞不动机制 | 无结构影响。是「机制」之外的**风格轴**，与 A 正交（可留机制、换语气） |
| **C1** | **CoT/标记契约**：模型须产出 `<thinking>…</thinking>` + `[TOOL_LOOP: DONE/PENDING]` | prefill 引导见 [260-321](../src/rag/graph.py#L260-L321)；消费在 [_detect_marker 116-119](../src/rag/graph.py#L116-L119)、[流式判定 718-724](../src/rag/graph.py#L718-L724) | 显示协议 | **⚠️ 不可轻动**：硬耦合 `_consume_events` | 砍掉标记指令 → 强模型不吐 `[TOOL_LOOP: DONE]` → 流式永不 `answer_start` → 前端「空回答」（哪怕 content 写满）。[729-743](../src/rag/graph.py#L729-L743) 的 `on_chat_model_end` 兜底只在「无 tool_calls」时补，救不全。动 C1 必须连流式层一起改（见 Part 3 备选） |
| **C2** | **prefill 逼迫强度**：`<think>…我必须立刻输出<thinking>…否则…[start]` + 协议 A/B/C/纠正变体 | [build_prefill 260-321](../src/rag/graph.py#L260-L321) | flash | **可松绑**（保持 C1 契约的前提下） | **过约束头号嫌疑犯**。可降级为「RUNTIME_STATUS + 一句中性格式提示」，去掉自我催眠。不动 C1 就不动流式层，安全 |
| **D** | **RUNTIME_STATUS 预算块** | [graph.py:278-282](../src/rag/graph.py#L278-L282) | 通用 | 干净 | 纯信息，基本无副作用 |
| **E** | **final_answer 兜底 prefill**（终局威胁话术） | [graph.py:324-341](../src/rag/graph.py#L324-L341)，调用于 [253-254](../src/rag/graph.py#L253-L254) | flash | 干净 | 独立于 C，可单独换轻量版 |
| **F** | **串行裁剪**：并行 tool_calls 砍到第一个 | [graph.py:421-426](../src/rag/graph.py#L421-L426) | flash/DS | 干净 | 关掉后 ToolNode 并行执行多工具；预算记账 `-1`（[429](../src/rag/graph.py#L429)）须改为 `-len(calls)` |
| **G** | **禁用原生思维链**（控温） | `src/llm`/config（非本文件）；症状留痕 [408-418](../src/rag/graph.py#L408-L418) | 控温 | 独立轴 | 关闭字段 DS 专属，换模型要换字段；开启后原生通道与 `<thinking>` 文本契约打架 |
| **H** | **工具预算 N=6** | [graph.py:505](../src/rag/graph.py#L505)，decrement [429](../src/rag/graph.py#L429) | 通用 | 干净 | 纯参数 |

**正交性小结**：A / B / D / E / F / H 基本正交，可自由组合；**C 必须拆成 C1（别碰）+ C2（可松）**；F 关掉要同步改预算记账；G 是模型侧独立轴。

---

## Part 2 — HarnessProfile：把轴收进一组开关

与 prompt 层已有的 `src/rag/prompts/profiles/`（normal/discuss）同构思路，给 harness 引入**单一 profile 对象**，用独立字段表达每条轴。**一套变体 = 一组取值；控制变量 = 只翻一个字段。**

```python
# 拟放 src/rag/harness_profile.py（或并入 config），字段名待定
@dataclass(frozen=True)
class HarnessProfile:
    guard_mode:      str = "strict"   # A: strict | soft | off
    correction_tone: str = "strict"   # B: strict | neutral
    prefill_level:   str = "full"     # C2: full | light | minimal（C1 协议恒开）
    final_prefill:   str = "full"     # E: full | light
    serial_tools:    bool = True      # F: True=砍到1 | False=允许并行
    budget_n:        int  = 6         # H
    # G（原生思维链）在 llm/config 层，不进此结构；作为实验时的外层变量记录
```

预置两档（起点，非终点）：

- **`FLASH`**（现状基线，全 strict）：`guard_mode=strict, correction_tone=strict, prefill_level=full, final_prefill=full, serial_tools=True`
- **`STRONG`**（松绑候选）：`guard_mode=soft, correction_tone=neutral, prefill_level=light, final_prefill=light, serial_tools=?`（serial 是否松取决于强模型并行工具的正确性，单独测）

### 接线点（改动集中、风险可控）

- `build_agent(user_id)` 签名加 `profile: HarnessProfile`，闭包进 `call_llm` / `thinking_guard`。
- `build_prefill` / `build_final_prefill` / `thinking_guard` 各加一个按 profile 字段分支的轻量开关。
- `_prepare` → `build_agent` 传入 profile（默认 `FLASH`，保证现状不变）。
- **不碰 C1 协议 → 不碰 `_consume_events` → 流式层零改动。**

> 原则：本阶段所有开关**纯开发态**（.env / 硬编码默认，不进 yaml、不暴露前端、不进 reload_config），与 chunker/rerank 配置同一定位——它是实验旋钮，不是用户设置。

---

## Part 3 —（备选，暂缓）降低 C1 协议对流式的耦合

仅当后续实验证明「必须把 prefill 砍到 minimal、连标记指令一起去」才做。方向：让 `_consume_events` 的 `answer_start` 决策**不再依赖 `[TOOL_LOOP: DONE]`**，改为「`</thinking>` 闭合且本轮无 tool_calls 即开正文」。这会牵动 [718-743](../src/rag/graph.py#L718-L743) 的判定与兜底逻辑，属显示层改造，风险高于 Part 2，**默认不做**，留作 C1 松绑的前置。

---

## 决策记录

- ✅ 约束按 6~8 条正交轴拆分，用单一 `HarnessProfile` 表达；变体=取值组合，控制变量=翻单个字段。
- ✅ prefill 拆成 C1（协议，硬耦合流式，不碰）/ C2（强度，可松绑）；第一刀切 C2。
- ✅ 所有开关纯开发态，默认值=现状（`FLASH`），保证不改默认行为。
- ✅ 原生思维链（G）保持关闭（控温决策），作为实验外层变量记录，不进 profile 结构。
- ⏳ 待定：`STRONG` 档 `serial_tools` 取值（需单独测强模型并行工具正确性）。
- ⏳ 待定：字段命名、profile 存放位置（独立模块 vs 并入 config）。

---

## Next-stage TODO（交接下一阶段）

1. **[本阶段①已完成]** 诊断表 + HarnessProfile 设计固化（本文件）。
2. **[② 量具，未开工]** 建「自动 + 行为感知」评测回路——**这是任何松绑实验的前置**：
   - 自动跑 agent 端到端（自动喂问题、抓答案、抓工具日志、组表），替代 eval_framework 的全手动流程。
   - **行为指标**（不是内容打分）：guard 命中率、纠正循环触发次数、空答率、工具调用轮数、是否撞 budget 上限、`[TOOL_LOOP]` 标记吐出率。这些一翻开关就见分晓，不需要 LLM 评委。
   - 给 LLM 评委加空回复兜底/重试（若之后要恢复内容打分）。
3. **[③ 实施，未开工]** 按 Part 2 接线 HarnessProfile（默认 FLASH，零行为变化），先只让 C2/B 可调。
4. **[④ 实验，未开工]** 同一强模型跑 FLASH vs 单变量松绑，比行为指标，验证过约束假设。
5. **[⑤ 备选]** 若需砍到 minimal，再评估 Part 3 的 C1 解耦。

> ⚠️ 跨机提醒：本计划纯代码层，不依赖 `data/`（gitignored）。但②的评测需要一批测试问题——若复用 `eval_framework/test_cases.json`（129KB，已入库）则随 git 走；若另建题库注意它是否 gitignored。

---

## 附录 A — 迷思池（待扩展，不阻塞主线）

> 定位：开发者提出的更大范围 harness 反思。**核心认知：除了 LLM 本身，其余一切（引导/工具/prompt/skill）都是 harness。** 本计划主线（Part 1-3）聚焦「引导」轴；以下是引导之外、值得单独立项的方向。按紧急度排序，**不阻塞主线**，要紧再展开，不要紧留作后续。

### A1. 工具的渐进式披露（Progressive Disclosure）— 紧急度：中

**迷思**：agent 当前看到的工具信息散落在三处——JSON schema、docstring、system prompt——文字量是否合理？三处描述有无冗余？出现时机是否得当？

**初步判断（待核实）**：
- 现状是「全部工具的完整描述一次性全暴露」，与渐进式披露（先给精简签名，用到再展开细节）相反。
- 三处来源确实容易冗余：schema 的 field description、docstring、prompt 里的工具使用说明，可能在重复讲同一件事。
- 值得做一次「工具信息审计」：列出每个工具在 schema/docstring/prompt 三处各说了什么、总 token、重叠部分。
- **与 A4（skill）强相关**：检索降级链若抽成 skill，工具 docstring 就能瘦身（要用了再看）。

**下一步（未开工）**：审计 `src/rag/tools/` 各工具的三处描述 + 统计 token 占用。

### A2. Prompt 各部分的位置/顺序 — 紧急度：低（内容已锤炼，位置未考究）

**迷思**：prompt 内容经多次锤炼，审核不急；但**各模块的排列位置**没认真设计过，想知道业界惯例。

**初步判断（待核实）**：
- 业界常见结构大致是：角色/身份 → 高层目标 → 能力与工具 → 约束/规则 → 输出格式 → （few-shot 示例）→ 动态上下文（history/检索结果）置于末尾靠近 query。
- 长 prompt 有「首尾权重高、中部易被忽略」的倾向，关键约束不宜埋在中段。
- 现有模块化结构（`src/rag/prompts/` 的 profiles/modules）已具备重排的物质基础——见 A3。

**下一步（未开工）**：对照业界结构，审视 `builder.py` 的模块拼装顺序。

### A3. Prompt 模块的可视化启用/排序 GUI（开发态工具）— 紧急度：低（体验痛点，非功能缺陷）

**迷思**：prompt 模块的启用与排序目前手改不方便；不暴露给用户，但想要个**开发态 GUI**，视觉上勾选启用 + 拖拽调序。

**初步判断（待核实）**：
- 与产品无关，是**开发者内部工具**，定位同「实验旋钮」。
- `builder.py` + `plugins.py` 已是模块化拼装 → 若模块清单可被枚举，做一个本地小页面（勾选 + 拖拽 → 输出模块顺序配置）成本不高。
- 可与 Part 2 的 HarnessProfile 开关合并成同一个「开发者控制台」，避免散落多个小工具。

**下一步（未开工）**：确认 prompt 模块是否可程序化枚举 + 是否有稳定的模块 id。

### A4. 把「检索降级链」抽成 Skill — 紧急度：中（与 A1 联动，最有练手价值）

**迷思**：skill 描述「做一件事的步骤 / 何时做 / 怎么做 / 如何验收收手」。现有 prompt 里「何时调哪个工具、三个检索工具的降级链（rag→s2/openalex→arxiv→jina）」正是这个形状——可写成 skill，就不必一次暴露全部工具 docstring，要用了再看。担心大材小用/过度包装。

**初步判断（待核实）**：
- 概念契合度高：检索降级链确实是「一套有条件、有顺序、有收手判据的流程」，正是 skill 的目标形态。
- **关键前提**：本项目是 LangGraph 自建 agent，不是 Claude Code / Agent SDK 运行时——「skill」在这里只能是**借鉴其思想**（把流程性知识从「常驻 prompt + 全量 docstring」挪到「按需加载的独立单元」），而非直接用某个 skill 运行时。需先厘清在自建 harness 里「按需加载」如何实现（工具分层？二级 prompt？延迟注入 docstring？）。
- 「过度包装」的担心成立与否，取决于降级链的复杂度是否够格独立成单元——目前看有 4 个工具 + 条件分支，够格。
- 与 A1 联动：抽出降级链 → 工具 docstring 可精简 → 渐进式披露落地。

**下一步（未开工）**：调研自建 LangGraph agent 里实现「skill 式按需披露」的可行路径；评估降级链抽取的收益/成本。

> 附录维护约定：以上均为**待扩展占位**，展开任何一条前先在此补「现状核实」再动手；展开后升级为独立 Part 或独立 plan 文件。

---

## 附录 B — 给下一个 session 的交接 brief（夯实本计划）

> 背景：本计划在一次 discuss 讨论中较仓促地成形，开发者（自评新手、正在学 harness engineering）对**方向本身**尚无把握，希望下个 session 用充足上下文重读代码后，回答三个「元问题」并夯实计划。**下个 session 请先通读下列文件，再回答三问，不要沿用本附录的初步倾向作结论——那只是防止空手起步的锚点，需独立核实。**

### 要回答的三个元问题

1. **这算 harness engineering 吗？** —— 我们预计的行动（拆约束轴、HarnessProfile、松绑 C2、行为 eval）是否名副其实属于 harness engineering，还是只是普通重构 / prompt 调优？给一个诚实的定性。
2. **从这里入手对吗？** —— 「先拆引导层约束」是不是最该先做的一步？还是说 A1/A4（工具渐进式披露 + skill 化）收益更大更该优先？或者「先造行为 eval 量具（②）」才是真正的第一步（因为没有量具，松绑无法判断好坏）？给一个排序建议 + 理由。
3. **改动风险如何？** —— 按 Part 2 接线 HarnessProfile（默认 FLASH、零行为变化）的实际风险有多大？哪些是真安全（纯参数/纯文本），哪些有暗线（C1 流式耦合、F 预算记账）？给一个风险分级。

### 下个 session 该读的文件（通读，别只 grep）

- `src/rag/graph.py`（本计划主战场，已在 Part 1 标行号，重读确认行号未漂移）
- `src/rag/prompts/`（`builder.py` / `plugins.py` / `profiles/` / `modules/`）—— 回答 A2/A3 与元问题②的前提
- `src/rag/tools/` 全部工具 —— 回答 A1/A4 的前提（看 schema+docstring 实际文字量与冗余）
- `eval_framework/evaluator.py` + `test_cases.json` 结构 —— 确认②量具要从零造多少
- `src/rag/tool_runtime.py`（`USE_MCP`）、`src/llm.py`、`src/config.py` —— G 轴（原生思维链关闭字段）与模型接线的实际位置
- `src/core/trim_thinking.py` —— CoT 裁剪，改 C1 协议会牵动它（CLAUDE.md 明示 CoT 逻辑跨 graph/prompts/trim_thinking 三处）

### 我此刻的初步倾向（**待核实，勿直接采信**）

- 元问题①：**倾向"是"**。拆分「LLM 之外的脚手架」并按模型调其强度，是 harness engineering 的核心动作；不是单纯重构。但要警惕：若最终只改了几句 prompt 措辞，那名头就过大了——定性要看改动是否触及控制流/信息披露结构。
- 元问题②：**倾向"② 量具应先于松绑实验"**，但「拆分诊断（①）」作为纯文档不阻塞、可先行（已做）。真正动代码的第一步该是量具，否则松绑无法度量。A1/A4 可与主线并行，因它们收益独立且练手价值高。
- 元问题③：**倾向"接线风险低"**——默认 FLASH 即现状，A/B/D/E/H 基本纯参数/纯文本；真正的雷只有两颗：**C1（动了流式会空答）**、**F（关串行要同步改 `-len(calls)` 记账）**。只要不碰这两颗、默认值锁死现状，接线本身近乎零风险。

### 夯实产出预期

下个 session 读完后，应当：(a) 在本附录 B 下补「三问的核实结论」；(b) 若结论与 Part 1-3 有出入，直接修订正文并记入决策记录；(c) 若判定方向有误（比如该先做 A4 而非引导层），重排 Next-stage TODO 的优先级。改完再推分支。
