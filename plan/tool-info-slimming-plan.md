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

## 当前决策：gemini-only 单模型基线（2026-07-06）

三选一（① gemini-only / ② 先修 flash 乱码 / ③ 搁置）**选 ①**。定案理由——不止「省事」，而是**当前没有合法地板**：

- **地板必须站得住**。"weak-as-floor" 偷偷假设单调性（强模型每轴弱优于弱模型），但强模型会**换一种方式**失败，且一个在任务上**坏掉**的模型不是地板、是噪声。实测两个 flash 渠道在此 harness 上都不能跑（压缩自 07-05 取证）：
  - **SiliconFlow v4-flash**：Q03 ok，但 Q18 三连空答（0 工具调用 / 505s / `error=None`，flash 在部分工具题上本就边缘）。
  - **DS 官方 v4-flash**（harness 原始调校渠道）：Q03 直接空答——根因是 flash 把 `tool_call` 序列化成乱码标签（`</思维DSMLparameter>` 等）吐进 content，**未产出合法 `tool_calls` 结构**（C1 协议输出层失败），与工具文本冗余无关。裸调用取证确认 harness 的 `DEEPSEEK_EXTRA_BODY` 禁思维字段在该渠道有效（非接线问题），是模型侧 `bind_tools` 兼容退化。
  - ∴ 两渠道都混入「pre-existing flash×harness 不兼容」噪声，**不存在合法地板** → gemini-only 不是妥协、是此刻唯一诚实选项。
- **代价（诚实记下）**：A1/A4 无开关，一份文本发所有模型；弃 flash = 只在「强模型不看防呆也填对」的样本上验证，**承重防呆句的真实价值观测不到**。
- **缓解 = 用「不删防呆」替代「flash 门」**：风险压在删除策略上——只砍编排/模式重复，返回 schema + 防呆句全留（第一刀），规避「删过头伤弱模型」靠根本不删，而非靠地板抓。
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

## 待办

### 即将（gemini-only 可独立推进）
1. **翻 transcript 定性 spot-check**（免费）：现有 after 存档已在手，人眼看 gemini 砍编排/gloss 后是否仍选对工具/模式、arg 无退化。拿到 bespoke 想要的定性判断、零新开销。
2. **（可选）第三刀 / 收口**：docstring 已 −29%，大头（防呆 fld + 定位/模式段）有意保留；若无更多低风险可削处，即记为 A1/A4 里程碑收口。

### 未来（⛔ blocked，等依赖到位再回来重启）
- **合法弱模型地板** = 本 plan 的核心阻塞依赖。以下两件都卡在「当前时点、能在**当前 harness** 上跑起来的合法弱模型」缺失上：
  1. **模型矩阵验收**：`{弱, gemini} × {before, after}`，门收在弱模型侧——验「保留防呆」是否真在保护弱模型（gemini-only 观测不到的那半）。
  2. **Tier-2 任务完成测**：判别力在弱模型侧，同上。bespoke 定制题（钉死 S2→openalex / S2→jina 路径）的坑已想清：真值漂移（S2 会回填摘要 → 刻舟求剑一层下）、钉路径违背 Tier-2「达成而非路径」判据、建题自身烧 token；届时优先**小批 diverse 真检索题 + 多跑比分布**，别钉单条脆路径。
- **触发条件**：找到这样的弱模型（渠道满血、能吐合法 `tool_calls`、任务上不空答）→ 回到本节重启矩阵 + Tier-2。在此之前，gemini-only 结论一律标 provisional。

## 明确不做

- **不动代码逻辑 / 返回结构 / 降级实现**——纯文本。
- **不搞「运行时按需注入 schema」**：本项目 `bind_tools` 一次性全量暴露（graph.py:374），无 skill 运行时，「延迟注入」需改 agent 装配，超轻量范围。本线只做**静态瘦身**（删重复），动态分层披露留作后续独立立项。
- **不动 rag_tool / lookup_local_paper_id**：已很克制（附录 A1 核实），非目标。

## 关联

- 上游：[harness-ablation-plan.md](./harness-ablation-plan.md) 附录 A1/A4。
- 量具：[harness-behavior-runner-plan.md](./harness-behavior-runner-plan.md)（probe + tool_log）。
- 邻接决策：[profile-selection-decision.md](./profile-selection-decision.md)（content eval 有意未建，正是本计划验证盲区的根源）。
