# 工具信息瘦身 + 分层披露（A1+A4）— 轻量计划

> 派生自 [harness-ablation-plan.md](./harness-ablation-plan.md) 附录 A1（工具渐进式披露）+ A4（检索降级链抽 skill）。二者是「同一块骨头的两面」，合并为本子项目。
> **定位**：唯一真正指向「让 agent 答得更好」而非「机器转不转」的续作，独立于 harness 松绑线。记于 2026-07-04，未开工。

## 一句话目标

砍掉 s2/arxiv/jina 三个重工具在 **docstring ↔ pydantic Field ↔ tool_usage.py prompt** 三处的重叠教学，靠一条「谁该讲什么」的分工规则，把每次调用都随 system prompt 发出的几 k 冗余 token 压下去——**不动任何代码逻辑，只动 LLM 可见文本**。

## 复杂度判定（为什么是轻量计划）

- **机械上 trivial**：只碰叶子文件的散文（docstring / `Field(description=...)` / `tool_usage.py`）。零控制流、零 schema-as-contract、零流式。
- **判断上不 trivial**：见下「三条铁律」。冗余里**混着承重的防呆规则**，盲删会伤 arg-fill 正确性（= 你最看重的「行为」）。故要**有分寸地切**，不是批量删。

## 实测冗余（LLM 可见面，2026-07-04 量得）

| 工具 | docstring | Field 描述（字段数） | 备注 |
|---|---|---|---|
| **s2_search_tool** | 1893 chars | 2111 chars（13 字段） | **重灾区**；docstring 含「三种模式」教学 + 完整返回 schema；tool_usage.py 再讲一遍 s2 |
| arxiv_tool | 1060 chars | 637 chars（7 字段） | 中等 |
| jina_tool | 1612 chars | 659 chars（8 字段） | 中等；两种模式（有/无 query）教学 |

> 合计 ≈ 8k chars（≈ 数 k token），**每次 LLM 调用都发**，跨 6 轮工具预算重复承载。这是 recurring cost，不是一次性。

## 三条铁律（本项目的核心产出——「谁该讲什么」）

现状问题：三处**都**在讲「模式 / 何时用 / 降级链」。分工规则：

| 面 | **应独占** | **应删（与他处重复）** |
|---|---|---|
| `tool_usage.py`（prompt） | **WHEN** — 跨工具编排、降级链路、模式*选择*、调用时机 | 已很干净，作为**唯一真相源**保留 |
| docstring | **WHAT it returns** — 返回 schema（唯一讲它的地方）+ 一行定位 | 模式教学、调用时机（与 prompt 重复）→ 砍 |
| `Field.description` | **单参数格式 + 防呆规则**（如「年份填 year_range 别进 keyword」） | 模式选择再教学（属 prompt）→ 砍 |

**承重红线（勿删）**：Field 里的防呆规则是**防具**不是废话——
`s2_tool.py:292`「严禁包含年份数字，年份填 year_range」、「别把标题拆成碎词」等。删它们=直接放任 arg-fill 出错。**保留全部防呆句，只删模式/编排类重复。**

## 验证方案（两层，作者设计 2026-07-04）

现有量具的错配：`harness_probe` 量的是 guard/marker/tool_rounds = **harness 合规**；A1/A4 改的是**工具 arg-fill 质量**，两者不重叠；content eval 又有意未建（见 profile-selection-decision）。故本改动需专门的两层检验——**行为门（客观/便宜）在前，任务效率（贵）在后**：

### Tier 1 — 基础行为门（objective，可自动化）
- **判据**：工具函数不抛错 —— agent 填的参数合法、调用成功返回真实结果（非错误报文）。
- **量具扩展（小改）**：`harness_probe` 现在只数 `tool_rounds`，**把「错误调用」和「成功调用」同等计数**。加一个 `tool_error` 计数器（解析 `ToolMessage` 内容里各工具的错误标记 vs 真实结果），本层即从「人眼」升级为**一行指标**。仍留在 behavior harness 内（错误调用属行为，非内容）。
- **意义**：瘦身若删过头（删了承重防呆句 → arg 填错 → 调用报错），本门直接抓到。绿了才进 Tier 2。

### Tier 2 — 任务完成/效率测（作者设计，贵，Tier 1 绿后再跑）
- **判据**：给一个**唯一可辨识、且训练库答不出**的目标（如检索某篇特定论文 / 某论文里独有的可辨识信息）——强制真检索，模型无法用参数化记忆蒙混。
- **控制变量**：同模型、同 profile、同输入 prompt，只翻「瘦身前 / 瘦身后」。
- **指标**（客观、非主观内容打分）：**是否达成（拿到/没拿到）** + **达成所需轮数（rounds-to-goal）**。这是**任务完成**度量，正好绕开「内容打分主观 + 未建」的盲区——是「arg-fill 质量是否退化」的可测代理。
- ⚠️ **n=1 是噪声（④ 的教训）**：temp>0 + S2 429 抖动会让 rounds-to-goal run-to-run 晃动；④ 的「light→更多检索」信号在 n=9 下就蒸发了。故每侧跑**多次**，比分布不比单点。这也是本层贵、排在 Tier 1 之后的原因。

### 模型矩阵：弱模型是下界（作者提醒 2026-07-05）
A1/A4 **无开关**——一份瘦身文本发给所有模型，故**最弱模型是约束的绑定者**，不是最强。逻辑与 harness 线同构：那些防呆字段描述（`严禁包含年份…`、「别把标题拆碎词」）多半是**为弱模型写的**——强模型不看也填对，**flash 才真靠它**。
- **只在 gemini 上测 = 对真风险失明**：删掉承重防呆句，gemini 不会退化（它不需要），但 flash 会。
- ∴ **v4-flash = 下界/压力测试**：门收在「**连 flash 的 `tool_err_request` 与召回都 before==after**」。flash 活，强模型必活。
- **可用配置**（.env，作者供，前两者额度充足可随意测）：Gemini 3.1 Pro（强）/ SiliconFlow v4-flash（弱，**主力下界**）/ DS 官方 v4-flash（弱）。
- **矩阵**：`{v4-flash, gemini} × {before, after}`；flash 先跑、门收在 flash。这也把「保留防呆、只砍编排重复」的规则**锚定为「保护 flash 地板」**——可砍的是跨工具 when/mode 教学（flash 从 prompt 也拿得到），必留的是 per-arg 防呆。

### 推论：第一刀保守
先砍最安全的重复（docstring 里与 prompt 重复的「调用时机」段、模式教学），**返回 schema 与防呆句一律先留**，把 token 削一部分、风险压到最低；Tier 1 绿 + Tier 2 无退化后，再考虑第二刀。

## 落地步骤（保守单 PR）

1. **[本文件] 计划入 `plan/`。**
2. **建改前基线**：`python scripts/harness_probe.py --only Q03 Q18 --profile FLASH`（工具题），存档 `tool_log`/transcript 作为人眼比对基线。
   - ⚠️ 真烧 token + 依赖 gemini RPM=5，见 harness-behavior-runner-plan 的兜底三件套（--timeout/--retries/--pace）。
3. **s2 先行（重灾区，收益最大）**：
   - docstring：删「三种查询模式」里与 `tool_usage.py` 重复的*何时用/怎么选*，**保留返回 schema + 一行定位**。
   - Field：删各字段里的模式选择再教学，**保留防呆规则**。
4. **arxiv / jina 同法**：jina 重点是「有/无 query 两模式选择」——该教学归 prompt 还是 docstring？定为 **prompt 主讲、docstring 只留 WHAT**。
5. **改后复跑同题**，人眼比对 `tool_log`：工具选择对不对、arg 填写有无退化（尤其年份/关键词碎词/模式选择）。**有退化就回退该条删除**。
6. 记 token 削减量（改前/改后 desc+fields char 数）作为 headline win。

## 明确不做

- **不动代码逻辑 / 返回结构 / 降级实现**——纯文本。
- **不搞「运行时按需注入 schema」**：A4 核实结论是本项目 `bind_tools` 一次性全量暴露（graph.py:374），**无 skill 运行时**，「延迟注入」需改 agent 装配，超出轻量范围。本 PR 只做**静态瘦身**（删重复），分层披露的动态版留作后续独立立项。
- **不动 rag_tool / lookup_local_paper_id**：已很克制（附录 A1 核实），非目标。

## 关联

- 上游：[harness-ablation-plan.md](./harness-ablation-plan.md) 附录 A1/A4。
- 量具：[harness-behavior-runner-plan.md](./harness-behavior-runner-plan.md)（步骤 2/5 的 probe + tool_log）。
- 邻接决策：[profile-selection-decision.md](./profile-selection-decision.md)（同为 harness 线收尾后的产品级续作；content eval 有意未建，正是本计划验证盲区的根源）。
