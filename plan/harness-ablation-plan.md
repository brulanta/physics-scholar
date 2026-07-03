# Harness 过约束诊断与可插拔化改造计划

> 定位：这是一份**诊断 + 设计靶子文档**，不是立即实施单。产出是「把 `graph.py` 里那坨为弱模型（ds-v4-flash）而生的约束，拆成彼此正交的开关」，为后续「同一模型、两版（或多版）harness 对照实验」提供可控变量。实施与评测量具是后续阶段（见文末 Next-stage TODO）。

## Context（为什么做这件事）

现有 harness（`src/rag/graph.py`）是围绕 **ds-v4-flash** 手工调出来的。flash 对格式遵守极差，于是架构用**重度 prefill 催眠 + thinking_guard 强制驳回 + 威胁性话术**把它逼到「能合法输出思维链、能按规矩调工具、能写答案」。

问题假设（开发者提出，待测）：**这套为弱模型定制的强约束，对更强的模型可能是负担——要么过度限制发挥，要么分散注意力。** 这是 harness engineering 的已知特性：一套 harness 未必跨模型通用。

要验证「松绑对强模型是帮助还是伤害」，前置条件是**把约束拆成可单独插拔的开关**，才能做控制变量实验（只翻一个开关，跑同一批问题，比行为指标）。本计划就是把这个「拆」的方案固化下来。

**这确实属于 harness engineering，不是普通重构/prompt 调优。** 本项目的脚手架异常之重、且重得有代码为证：一整个 `thinking_guard` 节点（假 ToolMessage 注入 + 退款记账 + 3 次重试路由）是**控制流约束**；每轮 `build_prefill` 塞自我催眠 AIMessage 是**推理过程注入**；三态流式状态机靠 `[TOOL_LOOP]` 标记决定何时放正文是**协议层耦合**。拆这些成正交开关、按模型调强度、用行为指标做对照，正是该领域的核心动作。**名实相符的前提**：改动须触及控制流/协议/信息披露结构，而非停在换措辞——拆 A、松 C2、翻 F 都够格；若最后只落地了 B（换语气），则名头过大，故 B 不该是主菜。

### 已确立的关键认知

1. **约束不是一块铁板，是 6~8 条轴**（见下表）。其中 A/D/E/H 是干净单点；B/C/F 各自跨多处，接线时按多点清单改。
2. **prefill（C）是两层**：**协议层 C1**（模型须吐 `<thinking>…</thinking>` + `[TOOL_LOOP: DONE/PENDING]` 标记）+ **强度层 C2**（威胁 + 自我催眠文本）。C1 硬耦合流式显示状态机（`_consume_events` 靠标记决定何时放正文），**是雷区**；C2 才是过约束嫌疑犯。**第一刀切 C2，别碰 C1。** 关键：C1 契约的教学主体在 prompt 层（`plugins.py`），prefill 只是复读加强——故只降级 C2 **不会**动摇契约，C2 松绑安全。
3. **原生思维链已被全局禁用**（开发者为「控温」所做的决策，DS reasoner 忽略 temperature）。这是独立轴 G，关闭字段是 DS 专属（`DEEPSEEK_EXTRA_BODY`），换模型需换字段。因此 `reasoning_content` 通道在 DS 上恒空，[graph.py:408-418](../src/rag/graph.py#L408-L418) 那段误判检测在当前模型下不触发——但换强模型/别家时会复活，属于「关闭开关如何跨模型可靠」的 harness 题。
4. eval_framework **不能**当回归护栏用（内容测试、非 robust、全手动），所以任何松绑实验的前置是先有「自动 + 行为感知」的量具——那是后续阶段（②）。

---

## Part 1 — 约束轴清单（诊断表，带行号靶点）

| 轴 | 约束点 | 代码位置 | 为谁而设 | 插拔面 | 插拔影响（翻掉它牵动什么） |
|---|--------|---------|---------|------------|--------------------------|
| **A** | **thinking_guard 强制**：缺 `<thinking>` 就驳回、为每个 tool_call 塞假 ToolMessage、退款 `remaining+1`、重试 3 次，超限强制 final_answer | [graph.py:150-213](../src/rag/graph.py#L150-L213)、路由 [216-238](../src/rag/graph.py#L216-L238) | flash | 干净单点 | `after_guard` 依赖 `is_thinking_correction`/`thinking_retry_count`；guard 不再 set 时路由仍成立（走 tool_node/END）。可独立关或降级为 soft（只警告不驳回） |
| **B** | **纠正话术语气**：「申请单/被驳回/权限永久关闭」 | graph.py：[194-204](../src/rag/graph.py#L194-L204)（纠正话术）、[184](../src/rag/graph.py#L184)（假 ToolMessage）；prompt 层：[output_format.py:4-10](../src/rag/prompts/modules/shared/output_format.py#L4-L10)、[plugins.py:19](../src/rag/prompts/plugins.py#L19) | flash | **纯文本，跨 4 处** | 无结构影响，与 A 正交的**风格轴**；但语气是跨 graph+prompt 的一致寄存器，只改 graph 会与 prompt 层威胁话术打架。要松绑须同批改这 4 处，否则风格分裂 |
| **C1** | **CoT/标记契约**：模型须产出 `<thinking>…</thinking>` + `[TOOL_LOOP: DONE/PENDING]` | 教学主体在 prompt 层：[plugins.py:18-44 TOOL_DECISION_PLUGIN](../src/rag/prompts/plugins.py#L18-L44) 定义 DONE/PENDING 语义、[output_format.py:27](../src/rag/prompts/modules/shared/output_format.py#L27) 强化；prefill [260-321](../src/rag/graph.py#L260-L321) 每轮复读。消费在 [_detect_marker 116-119](../src/rag/graph.py#L116-L119)、[流式判定 718-724](../src/rag/graph.py#L718-L724) | 显示协议 | **⚠️ 雷区**：硬耦合 `_consume_events` | 砍掉标记指令 → 强模型不吐 `[TOOL_LOOP: DONE]` → 流式永不 `answer_start` → 前端「空回答」（哪怕 content 写满）。[729-743](../src/rag/graph.py#L729-L743) 的 `on_chat_model_end` 兜底只在「无 tool_calls」时补，救不全。真要砍契约须同时删 plugins/output_format 的标记指令 + 改流式层（见 Part 3） |
| **C2** | **prefill 逼迫强度**：`<think>…我必须立刻输出<thinking>…否则…[start]` + 协议 A/B/C/纠正变体 | [build_prefill 260-321](../src/rag/graph.py#L260-L321) | flash | 可松绑（契约由 plugins.py 撑住，不受影响） | **过约束头号嫌疑犯**。可降级为「RUNTIME_STATUS + 一句中性格式提示」，去掉自我催眠。不动 C1 就不动流式层，安全。**注意**：`[start]` 锚点本身也是 prefill/prompt 契约（[output_format.py:37-38](../src/rag/prompts/modules/shared/output_format.py#L37-L38)），降级时须保留 `[start]` 收尾 |
| **D** | **RUNTIME_STATUS 预算块** | [graph.py:278-282](../src/rag/graph.py#L278-L282) | 通用 | 干净单点 | 纯信息，基本无副作用 |
| **E** | **final_answer 兜底 prefill**（终局威胁话术） | [graph.py:324-341](../src/rag/graph.py#L324-L341)，调用于 [253-254](../src/rag/graph.py#L253-L254) | flash | 干净单点 | 独立于 C，可单独换轻量版 |
| **F** | **串行裁剪**：并行 tool_calls 砍到第一个 | graph.py：[421-426](../src/rag/graph.py#L421-L426)（应用层裁剪）+ [429](../src/rag/graph.py#L429)（记账）；API 层第二道闸：[config.py:100 `parallel_tool_calls: False`](../src/config.py#L100) | flash/DS | **双闸联动** | 真正松绑串行须**同时**：① graph.py 去裁剪 ② 记账改 `-len(calls)` ③ config 的 `parallel_tool_calls` 置 True——只改①②，模型仍被 API 层禁止吐并行调用，等于没松。③ 又与 G 共享 `DEEPSEEK_EXTRA_BODY`（DS 专属字段），换模型要换 key |
| **G** | **禁用原生思维链**（控温） | `src/llm`/config（非本文件）；症状留痕 [408-418](../src/rag/graph.py#L408-L418) | 控温 | 独立轴 | 关闭字段 DS 专属，换模型要换字段；开启后原生通道与 `<thinking>` 文本契约打架 |
| **H** | **工具预算 N=6** | [graph.py:505](../src/rag/graph.py#L505)，decrement [429](../src/rag/graph.py#L429) | 通用 | 干净单点 | 纯参数 |

**正交性小结**：干净单点的只有 **A/D/E/H**，可自由组合。**C** 必须拆成 C1（雷区，契约主体在 plugins.py）+ C2（可松、安全）。**B** 是跨 graph+prompt 4 处的一致风格寄存器，松绑须同批改。**F** 是「应用层裁剪 + 记账 + API 层 `parallel_tool_calls`」三处联动，第三处与 G 共用 DS 专属字段。G 是模型侧独立轴。

---

## Part 2 — HarnessProfile：把轴收进一组开关

与 prompt 层已有的 `src/rag/prompts/profiles/`（normal/discuss）同构思路，给 harness 引入**单一 profile 对象**，用独立字段表达每条轴。**一套变体 = 一组取值；控制变量 = 只翻一个字段。**

```python
# 拟放 src/rag/harness_profile.py（或并入 config），字段名待定
@dataclass(frozen=True)
class HarnessProfile:
    guard_mode:      str = "strict"   # A: strict | soft | off（干净单点）
    correction_tone: str = "strict"   # B: strict | neutral（须同步替换 prompt 层 output_format.py:4-10 + plugins.py:19，否则风格分裂）
    prefill_level:   str = "full"     # C2: full | light | minimal（C1 契约由 plugins.py 撑住恒开；本字段只调 prefill 强度，保留 [start] 收尾）
    final_prefill:   str = "full"     # E: full | light（干净单点）
    serial_tools:    bool = True      # F: True=砍到1 | False=允许并行；False 须三处联动：去 graph 裁剪 + 记账改 -len(calls) + config.parallel_tool_calls=True（DS 专属字段，与 G 共用 DEEPSEEK_EXTRA_BODY）
    budget_n:        int  = 6         # H（干净单点）
    # G（原生思维链）在 llm/config 层，不进此结构；作为实验外层变量记录
    # 注意：B 的 prompt 层文案、F 的 config.parallel_tool_calls 不在本 dataclass 内，
    #       接线时要在 build_prompt/llm 构造处按 profile 分支——不是纯 graph.py 局部改动
```

预置两档（起点，非终点）：

- **`FLASH`**（现状基线，全 strict）：`guard_mode=strict, correction_tone=strict, prefill_level=full, final_prefill=full, serial_tools=True`
- **`STRONG`**（松绑候选）：`guard_mode=soft, correction_tone=neutral, prefill_level=light, final_prefill=light, serial_tools=?`（serial 是否松取决于强模型并行工具的正确性，单独测）

### 接线点与风险分级

graph.py 内的接线（改动集中、默认 FLASH 即现状）：

- `build_agent(user_id)` 签名加 `profile: HarnessProfile`，闭包进 `call_llm` / `thinking_guard`。
- `build_prefill` / `build_final_prefill` / `thinking_guard` 各加一个按 profile 字段分支的轻量开关。
- `_prepare` → `build_agent` 传入 profile（默认 `FLASH`）。
- **不碰 C1 协议 → 不碰 `_consume_events` → 流式层零改动。**

按风险分级施工：

- 🟢 **真安全（纯单点，默认锁死即零风险）**：**A / C2 / D / E / H**。改动都在 graph.py 局部，默认 FLASH 就是现状。**首刀 PR 只上这几条。**
- 🟡 **能做但改动扩散出 graph.py（各自单独小 PR）**：
  - **B（correction_tone）**：话术散落 graph + prompt 4 处。建议起步只调 graph 那段，prompt 层留待，并在实验记录注明「B 未完全解耦」；若要彻底解耦，须把 prompt 层威胁段做成 profile 变体（扩散到 build_prompt）。
  - **F（serial_tools）**：`main_llm` 是 `src/llm.py` 模块级单例，`extra_body` import 时定死。按 profile 切换 `parallel_tool_calls` 须重构 llm 为工厂函数、或在 `build_agent` 里 `llm.bind(extra_body=...)` 覆盖——是结构改动，非局部开关。
- 🔴 **雷区（默认不碰）**：**C1**。动了流式会空答，须走 Part 3。

> 原则：本阶段所有开关**纯开发态**（.env / 硬编码默认，不进 yaml、不暴露前端、不进 reload_config），与 chunker/rerank 配置同一定位——它是实验旋钮，不是用户设置。

---

## Part 3 —（备选，暂缓）降低 C1 协议对流式的耦合

仅当后续实验证明「必须把 prefill 砍到 minimal、连标记指令一起去」才做。方向：让 `_consume_events` 的 `answer_start` 决策**不再依赖 `[TOOL_LOOP: DONE]`**，改为「`</thinking>` 闭合且本轮无 tool_calls 即开正文」。

范围是「prompt + 显示层」双改，不止流式层：契约的教学主体在 prompt 层（[plugins.py:18-44](../src/rag/prompts/plugins.py#L18-L44) 的 DONE/PENDING 语义、[output_format.py:27](../src/rag/prompts/modules/shared/output_format.py#L27)），去标记须同时删这些 prompt 指令 + 改 [_consume_events 718-743](../src/rag/graph.py#L718-L743) 的判定与兜底逻辑。风险高于 Part 2，**默认不做**，留作 C1 松绑的前置。

---

## 决策记录

- ✅ 约束按 6~8 条轴拆分，用单一 `HarnessProfile` 表达；变体=取值组合，控制变量=翻单个字段。
- ✅ prefill 拆成 C1（协议，硬耦合流式，不碰）/ C2（强度，可松绑）；第一刀切 C2。C1 契约主体在 plugins.py，故降级 C2 不动摇契约、不碰流式层。
- ✅ 所有开关纯开发态，默认值=现状（`FLASH`），保证不改默认行为。
- ✅ 原生思维链（G）保持关闭（控温决策），作为实验外层变量记录，不进 profile 结构。
- ✅ 接线风险分级：真安全只有 A/C2/D/E/H；B/F 属「改动扩散出 graph.py」的黄区；C1 红区。首刀 PR 只上真安全轴。
- ✅ 优先级：真正动代码的第一步是 **②行为量具**（现有 eval_framework 是内容打分、无法复用），不是接线；**A1/A4 与②同级并行**（正交、独立见效、练手价值最高）。①拆分诊断（本文档）已作为纯文档完成。
- ✅ 已定：profile 存放于独立模块 `src/rag/harness_profile.py`（非并入 config——它是开发态实验旋钮，不进 yaml/前端/reload）。字段名：`guard_mode`/`prefill_level`/`final_prefill`/`budget_n`。
- ✅ 已定（③首刀）：只落地真安全轴的 4 个字段（A/C2/E/H）；**不预置 B/F 的空字段**——「已声明未接线」对实验 rig 是陷阱（翻了没反应）。D（RUNTIME_STATUS）判定为恒开纯信息，不设开关。
- ⏳ 待定：`STRONG` 档 `serial_tools` 取值（需单独测强模型并行工具正确性）——F 落地时补字段。

---

## Next-stage TODO（交接下一阶段）

优先级：**② ≈ A1/A4（可并行）＞ ③接线 ＞ ④实验**；①已完成。

1. **[① 已完成]** 诊断表 + HarnessProfile 设计固化（本文件）。
2. **[② 量具，进行中 — 真正的第一步]** 建「自动 + 行为感知」评测回路，**是任何松绑实验的前置**。**详细实施计划见 [harness-behavior-runner-plan.md](./harness-behavior-runner-plan.md)**：
   - 自动跑 agent 端到端（自动喂问题、抓答案、抓工具日志、组表），替代 eval_framework 的全手动流程。现有 `eval_framework/evaluator.py` 是内容打分（手动喂 answer/tool_log 给 LLM 评委），不自动跑 agent、不产行为指标，**无法复用，基本从零造 runner**（评委宪法/意图模板设计可参考）。
   - **行为指标**（不是内容打分）：guard 命中率、纠正循环触发次数、空答率、工具调用轮数、是否撞 budget 上限、`[TOOL_LOOP]` 标记吐出率。一翻开关就见分晓，不需要 LLM 评委。
   - 题库：复用 `eval_framework/test_cases.json`（20 题，随 git 走）。②如只需问题，读 list→dict 的 `question` 字段即可。
   - 给 LLM 评委加空回复兜底/重试（若之后要恢复内容打分）。
3. **[A1+A4 工具信息瘦身 + 分层披露，未开工 — 与②并行]** 独立于松绑实验、独立见效、练手价值最高：
   - 审计 s2/arxiv/jina 三个重工具的 docstring↔pydantic schema↔TOOL_USAGE prompt 三处冗余，砍重叠。
   - 探索自建 LangGraph agent 里「按需披露」的落地路径（工具信息分层：精简签名常驻 + 详细 schema/降级逻辑延迟注入）。
4. **[③ 接线，首刀已完成]** `src/rag/harness_profile.py`（`HarnessProfile` + `FLASH`/`STRONG`/`PRESETS`）；graph.py 把 profile（默认 FLASH）串进 `thinking_guard`/`build_prefill`/`build_final_prefill`/`final_answer`/`build_agent`/`_prepare`。已接真安全轴 **A（guard_mode strict/soft/off）/ C2（prefill_level full/light/minimal）/ E（final_prefill full/light）/ H（budget_n）**；D 恒开不设开关。`harness_probe.py` 加 `--profile FLASH|STRONG`。离线验证：FLASH 文本与旧硬编码逐分支一致（零行为变化）、STRONG 各轴翻转、guard 三态、graph 编译、生产默认=FLASH；Q01 实机 FLASH/STRONG 均端到端通过。**待办：B（correction_tone）/ F（serial_tools）各自单独小 PR（黄区，改动扩散出 graph.py）。**
5. **[④ 实验，未开工，依赖③]** 同一强模型跑 `--profile FLASH` vs `--profile STRONG`（全 20 题，需含触发工具的题以考验 guard:soft/marker），比行为指标，验证过约束假设。
6. **[⑤ 备选]** 若需砍到 minimal，再评估 Part 3 的 C1 解耦。

> ⚠️ 跨机提醒：本计划纯代码层，不依赖 `data/`（gitignored）。②的评测题库复用已入库的 `eval_framework/test_cases.json`，随 git 走，无额外跨机依赖。

---

## 附录 A — 迷思池（引导轴之外的 harness 反思，不阻塞主线）

> **核心认知：除了 LLM 本身，其余一切（引导/工具/prompt/skill）都是 harness。** 本计划主线（Part 1-3）聚焦「引导」轴；以下是引导之外、值得单独立项的方向。A1/A4 已核实、收益明确（提到与②并行）；A2/A3 仍是待展开占位。

### A1. 工具的渐进式披露（Progressive Disclosure）— 紧急度：中（已核实）

**问题**：agent 当前看到的工具信息散落三处——JSON schema、docstring、system prompt——文字量是否合理？有无冗余？

**核实结论**：假设成立，**s2_tool 是重灾区**。`s2_search_tool` 的 LLM 可见面 = ~70 行 docstring（三种模式 + 完整返回 schema 逐字段）+ 13 个 pydantic Field 的多行 description + prompt 层 TOOL_USAGE 里的 s2 段，三处重复讲「模式/字段/降级」。相比之下 `rag_tool`/`lookup_local_paper_id` 很克制。现状是「全部工具完整描述一次性全暴露」，与渐进式披露相反。**优先砍 s2/arxiv/jina 三个重工具的 docstring↔schema↔prompt 重叠**即可拿到大部分收益，不必全量审计。与 A4 是同一块骨头的两面。

### A2. Prompt 各部分的位置/顺序 — 紧急度：低（内容已锤炼，位置未考究）

**问题**：prompt 内容经多次锤炼，审核不急；但各模块的排列位置没认真设计过，想对照业界惯例。

**待核实的初步判断**：
- 业界常见结构：角色/身份 → 高层目标 → 能力与工具 → 约束/规则 → 输出格式 →（few-shot）→ 动态上下文（history/检索结果）置于末尾靠近 query。
- 长 prompt 有「首尾权重高、中部易被忽略」的倾向，关键约束不宜埋在中段。
- 现有 `normal.yaml` 把 `CONTEXT_BLOCK`（history）放在 order 20（靠前），与「动态上下文置末尾」的惯例相反，值得对照 `builder.py` 的拼装顺序审视。

### A3. Prompt 模块的可视化启用/排序 GUI（开发态工具）— 紧急度：低（体验痛点）

**问题**：prompt 模块的启用与排序目前手改不方便；想要个开发态 GUI，勾选启用 + 拖拽调序。

**待核实的初步判断**：
- 是开发者内部工具，定位同「实验旋钮」。`builder.py` + `plugins.py` 已是模块化拼装（`PromptModule` 有 `name`/`enabled`/`order`，profile 走 yaml），若模块清单可枚举，做本地小页面成本不高。
- 可与 Part 2 的 HarnessProfile 开关合并成同一个「开发者控制台」。

### A4. 把「检索降级链」抽成 Skill — 紧急度：中（与 A1 联动，已核实）

**问题**：skill 描述「做一件事的步骤/何时做/怎么做/如何收手」。现有 prompt 里「检索降级链（rag→s2/openalex→arxiv→jina）」正是这个形状——能否抽成 skill，就不必一次暴露全部工具 docstring？

**核实结论**：概念契合，但**「skill 运行时」在本项目不存在**——这是自建 LangGraph agent，工具通过 `bind_tools` 一次性全量暴露（[graph.py:374](../src/rag/graph.py#L374)），无按需加载机制，降级链当前写死在 TOOL_USAGE prompt 里。「skill 式按需披露」在这里只能借鉴思想，等价于**工具信息分层**（精简签名常驻 + 详细 schema/降级逻辑延迟注入）。降级链有 4 工具 + 条件分支，够格独立成单元。**建议 A1+A4 合并成一个「工具信息瘦身 + 分层披露」子项目**，与②量具并行。

> 附录维护约定：A2/A3 展开前先补「现状核实」再动手；展开后升级为独立 Part 或独立 plan 文件。
