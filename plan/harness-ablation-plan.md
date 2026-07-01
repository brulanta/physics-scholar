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
