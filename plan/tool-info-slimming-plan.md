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

## Flash 基线实测（2026-07-05，SiliconFlow v4-flash，FLASH profile）

| id | tools | errR | errT | mark% | budget | empty | try | lat |
|---|---|---|---|---|---|---|---|---|
| Q03 | 6 | 0 | 1 | 1.0 | Y | . | 1 | 161s |
| Q11 | 6 | **2** | 1 | 1.0 | Y | . | 1 | 140s |
| Q18 | **0** | 0 | 0 | - | . | **Y** | 3 | 505s |

汇总：`errR=2, errT=2, empty=1/3, budget_hit=2/3, marker=1.0, guard=0`。存档 `eval_framework/results/behavior/behavior_flashBASE_before_20260705_172933.json`。

### 基线暴露三件事，改了上面的验证设计

1. **门是「非回归」不是「零」**：flash 瘦身前就 `errR=2`——「errR 应为 0」是 naive。真门 = `errR_after ≤ errR_before(2)`、`empty_after ≤ 1/3`、`budget_after ≤ 2/3`。flash 是下界正因为它不完美，预期它完美本就错。
2. **Q18 是 flash 的既有失败，与瘦身无关**：3 次尝试 0 工具调用、全空答、505s，`error=None`（非代理/异常，是 run 完了 flash 啥也没产出）。瘦身还没动 → 这是 flash 在 Q18 上**本就垮**。「flash 是下界」比预想更咬人：flash 在部分工具题上**本就边缘**。
3. **量具缺口（actionable）**：probe 计了 `errR=2` 但**没存 error_type**——「flash 填错 2 次参」却不知是哪个字段/哪种 error_type，对 A1/A4 验收不可操作。classifier 已解析出 error_type，只是没写进 row。**「after」跑前须补**：把每次调用的 error_type 列表写进 row，否则 before/after diff 不可读。

### n=1 诚实边界
Q18 的三连空是 1 次运行，505s/3-attempts 模式偏系统性但 n=1 不下定论；Q11 的 errR=2 是真信号（classifier 只抓 bad_request/invalid_arguments 类，不含 429），但同样 n=1。要发表率证据需每题多跑几次比分布。

## 渠道切换与 DS-official flash 现状（2026-07-05，本日收尾发现）

作者提醒：开发时 harness 是基于 **DS 官方 v4-flash** 调出来的，疑 SiliconFlow 渠道的 flash 非满血。故本日切换到 DS 官方 flash 重测 Q03——**结果比 SiliconFlow 更坏，且坏在更底层**：

### 诊断链（逐层隔离，确认非瘦身、非代理、非配置问题）
1. **裸 DS flash 调用**（无 harness）→ `reasoning_content` 非空（694 token），原生思维链在跑。
2. **裸调用 + `extra_body={'thinking':{'type':'disabled'}}`** → `reasoning_content` 清空、content 1801 字、`finish=stop`。→ **harness 的禁用字段在 DS 官方渠道有效**（config.py:98 `DEEPSEEK_EXTRA_BODY` 经 llm.py:19 注入，G 轴接线正常）。
3. **agent 内跑 Q03（FLASH 全 strict）** → 2 次 attempts 全空答、0 工具调用、`error=None`、`remaining_calls=6`（预算未动）。抓 transcript 看到根因：flash 写对了 `<thinking>` 块、计划了工具调用，但**把 tool_call 序列化成乱码标签输出在 content 里**（`</思维DSMLparameter>`、`</invoke>` 等），**未产出合法的 `tool_calls` 结构**。`AIMessage.tool_calls` 为空 → 路由当最终答案 → `process_llm_output` 剥掉 `<thinking>` → 0 字 = 空答。

### 含义（对 A1/A4 的影响）
- **DS 官方 flash 在 Q03 上、未瘦身时就已经坏**——坏在「模型不吐合法 `tool_calls` 结构」（C1 协议的输出层失败），与工具文本冗余无关。
- **两个 flash 渠道对 A1/A4 都已失格**：
  - SiliconFlow flash：Q03 ok、Q18 空答×3。
  - DS 官方 flash：Q03 直接空答（连 Q03 这种「应检索」题都不调工具）。
- 故 **任何 flash 基线都混入「pre-existing flash×harness 不兼容」噪声**，不是瘦身引入的信号。「flash=下界」在此 harness 上**不能成立**——flash 本就边缘/坏掉，不能当 A1/A4 的验收地板。
- 这与原假设方向**相反**：作者疑 SiliconFlow 非满血，实测是 **DS 官方在此 harness 上更坏**（DSML 乱码 vs SiliconFlow 的 Q18 空答）。可能涉及 DS 官方对 `bind_tools` 结构化输出的兼容差异，或 `<thinking>` 文本契约与 DS 原生 thinking 通道的某种残留冲突——**未深挖**（本日收尾，留 G 轴独立 bug）。

### 收尾时的环境状态
- `.env` 已切回 **gemini-3.1-pro-preview**（gcli 渠道）——验证可用的干净渠道。
- 探针 `error_type` 明细补丁已合入并 push（commit `6c5b2d4`）：`classify_tool_message` 返回 `(kind, error_type)`、row 落 `tool_errors` 列表。
- 本日新增 4 个 behavior 存档（`behavior_dsSMOKE_*` / `behavior_flashBASE_*` / `behavior_flashBASE_before_*` / `behavior_flashSMOKE_*`）入 `eval_framework/results/behavior/`，连同本计划更新一起提交。

### 下一会话起点（三选一，未决）
1. **Gemini-only 基线**：放弃 flash 作下界（它本就坏），只在 gemini 上跑 A1/A4 before/after。最短路径得真瘦身结论，但失去弱模型覆盖。
2. **先修 flash 的 tool_call 乱码**（G 轴独立 bug：DS 官方吐 DSML 标签而非结构化 tool_calls），再重基线 flash。绕路，但恢复 flash 下界。
3. **就此搁置 A1/A4**，记录发现、换方向。

> ⚠️ 本日工作未推进到「实际瘦身」——Tier 1 量具加固（error_type 明细）完成、基线尝试暴露出渠道问题而非瘦身问题。下次开工前需先定方向（上面三选一）。

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
