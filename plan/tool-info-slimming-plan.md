# 工具信息瘦身 + 分层披露（A1+A4）— 轻量计划

> 派生自 [harness-ablation-plan.md](./harness-ablation-plan.md) 附录 A1（工具渐进式披露）+ A4（检索降级链抽 skill）。二者是「同一块骨头的两面」，合并为本子项目。
> **定位**：唯一真正指向「让 agent 答得更好」而非「机器转不转」的续作，独立于 harness 松绑线。

## 一句话目标

砍掉 s2/arxiv/jina 三个重工具在 **docstring ↔ pydantic Field ↔ tool_usage.py prompt** 三处的重叠教学，靠一条「谁该讲什么」的分工规则，把每次调用都随 system prompt 发出的几 k 冗余 token 压下去——**不动任何代码逻辑，只动 LLM 可见文本**。

## 复杂度判定

- **机械上 trivial**：只碰叶子文件的散文（docstring / `Field(description=...)` / `tool_usage.py`）。零控制流、零 schema-as-contract、零流式。
- **判断上不 trivial**：冗余里**混着承重的防呆规则**，盲删会伤 arg-fill 正确性（= 最看重的「行为」）。故要**有分寸地切**，不是批量删。

## 三条铁律（核心产出——「谁该讲什么」）

三处现状都在讲「模式 / 何时用 / 降级链」。分工规则：

| 面 | **应独占** | **应删（与他处重复）** |
|---|---|---|
| `tool_usage.py`（prompt） | **WHEN** — 跨工具编排、降级链路、模式*选择*、调用时机 | 已很干净，作为**唯一真相源**保留 |
| docstring | **WHAT it returns** — 返回 schema（唯一讲它的地方）+ 一行定位 | 模式教学、调用时机（与 prompt 重复）→ 砍 |
| `Field.description` | **单参数格式 + 防呆规则** | 模式选择再教学（属 prompt）→ 砍 |

**承重红线（勿删）**：Field 里的防呆规则是**防具**不是废话——`s2_tool.py`「严禁包含年份数字，年份填 year_range」「别把标题拆成碎词」「已知论文用完整标题作唯一元素」等。删它们=直接放任 arg-fill 出错。**保留全部防呆句，只删模式/编排类重复。**

## 验证方案（两层：行为门在前、任务效率在后）

现有量具的错配：`harness_probe` 量的是 guard/marker/tool_rounds = **harness 合规**；A1/A4 改的是**工具 arg-fill 质量**，两者不重叠；content eval 又有意未建。故需专门两层——

### Tier 1 — 基础行为门（objective、便宜、已用）
- **判据**：工具函数不抛错——agent 填的参数合法、调用成功（非错误报文）。量具已扩展：probe 分 `tool_err_request`（进门）vs `tool_err_transient`（旁观），并落 `tool_errors=[[kind,error_type],…]` 明细。
- **门 = 非回归**（不是零）：`errR_after ≤ errR_before`、`empty_after ≤ before`、marker/guard/noThk 逐格不回归。绿了才有资格进 Tier 2。

### Tier 2 — 任务完成/效率测（贵，**当前挂起**，见「未来待办」）
- **判据**：给一个**唯一可辨识、训练库答不出**的目标，强制真检索；指标为**是否达成 + rounds-to-goal**（客观代理，绕开内容打分主观 + 未建的盲区）。
- **为何挂起**：n=1 是噪声（④教训），须每侧多跑比分布——贵。更根本地，其**判别力全在弱模型侧**（强模型不看防呆也填对，gemini-only 下 Tier-2 信息增量 ≈ Tier-1，为它显不出的信号付贵测量费）。故与「弱模型地板」一并挂起。

> user本人注： 弱模型与防呆的关系 —— 弱模型大概率依赖防呆才能表现出正常行为，所以删改前后、想要探测是否误删防呆内容，最好用弱模型做before/after探测；如果想删去所有防呆，也需要弱模型做下限测试，即弱模型可以不读（也能正常工作）的防呆、强模型天然也可以略去。

## 当前决策：gemini-only 单模型基线（2026-07-06）

三选一（① gemini-only / ② 先修 flash 乱码 / ③ 搁置）**选 ①**。定案理由——不止「省事」，而是**当前没有合法地板**：

- **地板必须站得住**。"weak-as-floor" 偷偷假设单调性（强模型每轴弱优于弱模型），但强模型会**换一种方式**失败，且一个在任务上**坏掉**的模型不是地板、是噪声。实测两个 flash 渠道在此 harness 上都不能跑（压缩自 07-05 取证）：
  - **SiliconFlow v4-flash**：Q03 ok，但 Q18 三连空答（0 工具调用 / 505s / `error=None`，flash 在部分工具题上本就边缘）。
  - **DS 官方 v4-flash**（harness 原始调校渠道）：Q03 直接空答——根因是 flash 把 `tool_call` 序列化成乱码标签（`</思维DSMLparameter>` 等）吐进 content，**未产出合法 `tool_calls` 结构**（C1 协议输出层失败），与工具文本冗余无关。裸调用取证确认 harness 的 `DEEPSEEK_EXTRA_BODY` 禁思维字段在该渠道有效（非接线问题），是模型侧 `bind_tools` 兼容退化。
  - ∴ 两渠道都混入「pre-existing flash×harness 不兼容」噪声，**不存在合法地板** → gemini-only 不是妥协、是此刻唯一诚实选项。
- **代价（诚实记下）**：A1/A4 无开关，一份文本发所有模型；弃 flash = 丢了上文「活 A 哨兵」，删过头无自动传感器；「活 B 防呆估值」本就不在保守范围（不 care）。
- **缓解 = 用「不删防呆」替代「flash 门」**：哨兵（活 A）没了，安全就从「地板抓」搬到「删除策略」——只砍编排/模式重复，返回 schema + 防呆句全留，规避「删过头伤弱模型」**靠根本不删，而非靠地板抓**。这是弱模型缺席下的等价替代，不是退让。
- **找补**：今天的弱模型会被淘汰，未来模型平均水位大概率高过现在预设下限，"失去弱覆盖" 的代价随时间自我衰减。

### 元决策立足点（矫正层 vs 契约层，adopted 2026-07-06）
harness 分**矫正层**（guard/prefill 催眠/乱码兜底，拟合单模型失败模式、天生刻舟求剑）与**契约层**（工具集/降级链/引用格式/C1 显示协议，跨模型稳定）。目标：矫正层默认关、契约层承重，使 harness 随模型变强**自然退化成无害死重而非主动错误**。既然 LLM 非平稳，**不校准最优点、只校准梯度**（`FLASH…STRONG…MINIMAL` 是斜率不是三个孤立设置）；真正的资产是**便宜的重测回路（behavior probe）**——换模型时重测+翻 profile，不重新设计。这把「模型会变」从威胁转成设计约束。

### 量具卫生（within-ID 降智防线）
gemini 自己也可能被供应商悄悄量化（正是本轮 DS v4 系列「降智」的启发）。单渠道 before/after diff 会被「模型在脚下变了」污染，防线：pin model id + 日期（`behavior_*.json` 已带）；before/after **交错跑、时间靠近**（别先跑完一批 before 再跑 after，否则漂移只砸一侧）；任何单渠道结果标 provisional。

## 进度

### 第一刀已落地（2026-07-06，gemini-only，Tier-1 门通过）— commit `115bc71` / `a903b67`

三工具 docstring/Field 去编排重复，纯文本、零逻辑改动。

| 工具 | doc | fld | sum | 削减 |
|---|---|---|---|---|
| s2 | 1893→1513 | 2111→2062 | 4004→3575 | −429 (−10%) |
| arxiv | 1060→973 | 637→637 | 1697→1610 | −87 (−5%) |
| jina | 1612→1399 | 659→511 | 2271→1910 | −361 (−15%) |
| **合计** | | | **7972→7095** | **−877 (−11%)** |

Tier-1 门（gemini Q03/Q11/Q18，FLASH，before 11:08 / after 11:26 交错紧跑）：`errR` 0→0、`empty` 0→0、`marker/guard/noThk` 全 1.0/0/0 逐格无回归、`budget_hit` 0→0。after 侧 request 桶空（Q11 仅 1 条 `recent_failed_query`=transient 旁观）。
- **诚实标注**：`avg_tool_rounds` 3.33→2.0 **是噪声不是信号**（before-Q03 5 轮含 transient 触发的重试，n=1）。真信号只有「门绿」。
- **门的边界**：gemini-only 下 `errR` 本就恒 0，故本门证的是「**没删过头**」，**非**「防呆无用」——防呆真值在弱模型侧，本轮观测不到（gemini-only 固有代价）。存档 `behavior_gemBASE_{before,after}_20260706_*.json`。
> user本人注： 本门证的是 —— 没有删得太过头、删到强模型都无法正常工作。但无法证明 —— 没有误删防呆、防呆无用。

### 第二刀已落地（2026-07-06，gemini-only，Tier-1 门通过）

压 docstring 返回 schema 的逐字段 gloss：保留字段名 + 非显然语义（abstract 截断规则 / tldr 何时为空 / venue 预印本为空 / content_warning），删自证 gloss（`"title": 论文标题`）+ 跨工具编排泄漏（`open_access_pdf` 的「可传给 jina_tool」，按铁律移交 prompt）。**fld（防呆）一格未动、arg-fill 输入面零触碰**。

| 工具 | doc | fld(未动) | sum | 本刀 | 累计(原始起) |
|---|---|---|---|---|---|
| s2 | 1513→906 | 2062 | 3575→2968 | −607 | 4004→2968 |
| arxiv | 973→589 | 637 | 1610→1226 | −384 | 1697→1226 |
| jina | 1399→924 | 511 | 1910→1435 | −475 | 2271→1435 |
| **合计** | | | **7095→5629** | **−1466** | **7972→5629（−29%）** |

Tier-1 门（gem2_before 12:27 / gem2_after 12:33 交错紧跑）：`errR` 0→0、`empty` 0→0、`marker/guard/noThk` 全 1.0/0/0 逐格无回归、`budget_hit` 0→0。`errT` 3→2（全 transient 旁观桶，不进门）。
- **诚实标注**：`avg_tool_rounds` 4.33→3.0 **是噪声**（transient 触发的工具重试波动，n=1）。真信号只有「门绿」。
- 第二刀只削 docstring 的**输出面**，arg-fill 的**输入面**（Field/防呆）一格未动 → `errR` 恒 0 符合预期；门证「没删过头」比第一刀更强（连可能扰动 arg-fill 的模式选择段都没碰）。存档 `behavior_gem2_{before,after}_20260706_*.json`。
- ⚠️ **errR 门对第二刀近乎「结构性空转」，别把绿当强证据**：既然输入面一格没动，`errR≡0` 是**必然**、不是测出来的好消息（换任何模型都 0）。第二刀真正该看的是**输出面 gloss 砍掉后 agent 有没有误读返回结构** → 靠**已落地的 transcript spot-check**（见下「spot-check」段），不是靠 errR。errR 门在这里只排除了「手滑连带删了输入面」这一种事故。

### spot-check 已落地（2026-07-06，gemini-only，定性通过）

待办 #1 的前提「现有 after 存档已在手」**不成立**——`harness_probe.py` 原本只落指标计数、不落 transcript（4 份 gemini 存档里没有可翻的 transcript）。故先给 runner 加 `--dump-transcript` 开关（把每题 `result["messages"]` 序列化到 `results/behavior/transcripts/{label}_{stamp}/{id}.json`，缺省关闭、不污染指标存档），再重跑 gem2 after 三题落 transcript。存档 `behavior_gem2_after_spotcheck_20260706_164730.json` + `transcripts/gem2_after_spotcheck_20260706_163233/`。

人眼翻 Q03/Q11/Q18 transcript，核验三大关切（plan 量具卫生：仅 after 一侧、紧贴第二刀文本）：
- **① 选工具/降级链**：Q18 撞 S2 429 → 正确降级 openalex→arxiv→openalex（换词）→arxiv，每步 Q3 附「与上轮差异」理由；budget 剩 1 果断 `[TOOL_LOOP: DONE]`。Q03/Q11 用 s2+openalex 即满足即收手。**无退化**。
- **② 读返回字段**（第二刀核心关切）：agent 在 thinking 里精确引用返回内容——"前4篇是关于6G/遥感/太赫兹综述，只有第5篇 Shilong Pan & Yamei Zhang 的 Microwave Photonic Radars (2020)"、"返回论文集中在生命体征监测/高分辨率成像"、"北大王兴军团队2022 Fully on-chip microwave photonics system (arXiv:2202.11495)"。这些 title/authors/year/arxiv_id 均从工具返回 `papers[]` 读出 → **gloss 砍后 agent 仍正确读出 abstract/title/venue/authors/open_access_pdf 等字段并据内容决策，无误读返回结构**。还观察到正向元认知：[10] 诊断 openalex 长短语检索「被拆词匹配到高引用无关论文」→ 主动改拆分关键词重搜。
- **③ arg-fill**：所有 tool_calls args 合法、防呆生效（s2 年份走 `year_range` 不混入 query、`publication_types` 列表、`sort` 合法；openalex `full_abstract/keywords/year_range` 全对）。`errR=0` 与行为一致，**无 arg-fill 退化**。

- ⚠️ **覆盖盲区（诚实标注，不阻塞收口）**：本次 after 三题**一次都没调 jina_tool**（Q03/Q11 用 s2+openalex 即足，Q18 走 s2→openalex→arxiv 链）。故第二刀砍掉的 `open_access_pdf`「可传给 jina_tool」编排泄漏，其下游「agent 是否仍知道把 `open_access_pdf` 传给 jina」**未在本次观测到**。已确认 openalex 返回里 `open_access_pdf` 字段实存可读，只是 jina 传递链没被触发——属「gemini-only 固有盲区」同类（没测到 ≠ 退化），留待弱模型矩阵 / 触发 jina 的题出现时补。

## 待办

### ✅ 已完成（gemini-only 可独立推进部分）
1. **transcript 定性 spot-check**：runner 加 `--dump-transcript` 开关 + 重跑 gem2 after 落盘 + 人眼核验，结论「无退化」（见上「spot-check 已落地」段）。
2. **第三刀 / 收口**：spot-check 无退化 + docstring 已 −29%、剩余可削面只剩承重防呆（红线全留）与定位一行（低收益）→ **无更多低风险可削处，A1/A4 静态瘦身里程碑收口**。`--dump-transcript` 开关随收口留存（未来弱模型矩阵/Tier-2 复用）。

### 未来（⛔ blocked，等依赖到位再回来重启）
- **合法弱模型地板** = 本 plan 的核心阻塞依赖。以下两件都卡在「当前时点、能在**当前 harness** 上跑起来的合法弱模型」缺失上：
  1. **模型矩阵验收**：`{弱, gemini} × {before, after}`，门收在弱模型侧——验「保留防呆」是否真在保护弱模型（gemini-only 观测不到的那半）。
   > user本人注： 验是否误删防呆内容
  2. **Tier-2 任务完成测**：判别力在弱模型侧，同上。bespoke 定制题（钉死 S2→openalex / S2→jina 路径）的坑已想清：真值漂移（S2 会回填摘要 → 刻舟求剑一层下）、钉路径违背 Tier-2「达成而非路径」判据、建题自身烧 token；届时优先**小批 diverse 真检索题 + 多跑比分布**，别钉单条脆路径。
- **触发条件**：找到这样的弱模型（渠道满血、能吐合法 `tool_calls`、任务上不空答）→ 回到本节重启矩阵 + Tier-2。在此之前，gemini-only 结论一律标 provisional。
> user本人注： 本质是找到一个在当前时间下、当前harness的版本能兼容运行的本体性能最弱的模型，其表现天然具有探针价值（是否是良性的删除 可以与 agent前后表现对比 直接挂钩，观测后者自然得出前者结果），因此目前 tier 1-2 的结果都只具备较弱的参考意义（但随着时间流逝价值会上升 —— 时间够久，最终市面上模型的性能都会高于harness的兼容最底线）

## 明确不做

- **不动代码逻辑 / 返回结构 / 降级实现**——纯文本。
- **不搞「运行时按需注入 schema」**：本项目 `bind_tools` 一次性全量暴露（graph.py:374），无 skill 运行时，「延迟注入」需改 agent 装配，超轻量范围。本线只做**静态瘦身**（删重复），动态分层披露留作后续独立立项。
- **不动 rag_tool / lookup_local_paper_id**：已很克制（附录 A1 核实），非目标。

## 关联

- 上游：[harness-ablation-plan.md](./harness-ablation-plan.md) 附录 A1/A4。
- 量具：[harness-behavior-runner-plan.md](./harness-behavior-runner-plan.md)（probe + tool_log）。
- 邻接决策：[profile-selection-decision.md](./profile-selection-decision.md)（content eval 有意未建，正是本计划验证盲区的根源）。
