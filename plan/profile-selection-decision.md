# Profile 暴露方式 — 决策note（用户自选 · 症状导向）

> 定位：**纯决策记录，不含代码任务**。派生自 harness ablation 线（见 [harness-ablation-plan.md](./harness-ablation-plan.md) / [harness-behavior-runner-plan.md](./harness-behavior-runner-plan.md)）收尾时的一次设计讨论。回答一个问题：**松绑后的 harness 约束，该给用户 flash 还是 strong？谁来选？**
>
> 记于 2026-07-04。**结论未落地为代码**——当前生产默认仍是 strict（FLASH），本note 决定的正是「保持 strict 为默认 + 加一个用户可选开关」，故落地=零行为变化 + 一个 Settings 开关。不急，见下。

## 背景一句话

harness 松绑实验已 settle：在 gemini-3.1-pro 上，`thinking_guard` 是死重、prefill `full→light` 免费、`light→minimal` 破契约、`STRONG(light)` 是 Pareto 地板。问题转为**产品侧**：这套「哪档约束」该怎么交付给不知道用什么模型的用户。

## 核心决策

1. **默认 strict（= 现状 FLASH，零改动），light 作为 opt-in。**
2. **用户自选，按「症状」框定，不做模型检测。**
3. **只暴露 2 个策展预设**给用户；4 根原始轴 + `MINIMAL` 留作**开发态 rig**，不进前端。
4. **不做**运行时症状自动切换（auto-escalate）——strict-default 下无需。

## 为什么（决策依据，别忘了）

### 依据 A：strict 和 light 的失败模式**不对称**——这是全部决策的地基
- **strict 跑强模型** = 浪费但**安全**。已实证：FLASH-on-gemini 零行为回归。最坏=几百 token 白烧 + guard 空转，用户**无感**。
- **light 跑弱模型** = 可能**直接坏**。soft guard 抓不住违规、light prefill 压不住 → 空答 + 泄露 `<thinking>` **直达用户**，可见故障。
- ∴ strict 是「失败=轻微浪费」的保守档，light 是「失败=输出损坏」的优化档。**home base 必须是安全档** → 默认 strict。新用户拿未知模型，**首跑必成功**；有能力模型的用户**知情地**opt-in light 换速度/成本。

### 依据 B：profile 是「行为存活」旋钮，**无法从模型身份推断**
- 模型叫什么，**不告诉你**它会不会遵守「工具调用前置 `<thinking>`」协议。合规度是 per-model-capability 属性。
- 「vendor X gen Y → profile Z」映射表 = 用**会随每次发版漂移的代理（身份）**去猜**真正在意的东西（合规度）**，且要永久维护。
- **症状导向直接测真变量**：「配好 API 后若经常空答 / 泄露思维链 → 切 strict」。∴ 用户自选 + 症状框定，**不建检测**。

### 依据 C：行为 > 内容，且二者必须正交（用户的原话，采纳）
- 行为合规是**门（gate）**不是**分（score）**：机器坏了，内容根本到不了用户。故行为在独立量具里测（probe 答「机器转不转」），内容留给另一套 eval（答「答得好不好」）。**别在行为 harness 里掺内容打分。**
- 推论：约束足迹越小，对内容扰动越小——这正是 light 的定义（对合规模型的最小可行强制）。
- **注意**：我们只测了 light **行为免费 + 更省**，**没测内容**。有可能重威胁 prefill 让强模型更僵、light 反而答更好——**但未测，不可宣称**。帮助文案只说「更快/更省」，够了。

## 交付形态（落地时照此，勿超纲）

- **用户面**：Settings 一个 **2 档开关**，按收益命名、非模型代号（FLASH/STRONG 是内部黑话，会把非本人的用户绕晕）：
  - **兼容模式 / Compatibility（默认）** = strict —「适配任何模型；若空答或泄露思维链，留在这档」。
  - **高性能模式 / Performance** = light —「强/新模型上更快更省；若见空答或思维链泄露，切回兼容模式」。
- **开发面不变**：`HarnessProfile` 4 轴对象 + `MINIMAL`（已证不安全）仍是**开发态 rig**（probe 的地盘），**不暴露前端**。用户看到的 2 档 = 对 `FLASH`/`STRONG` 两个策展预设的封装。
- **文案诚实**：light 只承诺「更快/更省」，不承诺「答更好」（未测）。

## 明确不做（及原因，防止未来反悔踩坑）

- **不做模型自动检测 / model→profile 映射表** —— 依据 B：猜的是漂移的代理，且要永久维护。
- **不做运行时症状自动升档（auto-escalate to strict）** —— 技术上干净（空答 / 泄露 CoT 症状已在 `trim_thinking` + 空答检测里在带内），但**只在默认 light 时才有意义**（把故障往 strict 升）。既然默认 strict，**没有可升的目标**，问题自动消解。归档为「让 light-default 可行的那个东西——而我们正因不需要 light-default 而不需要它」。**跳过。**
- **不暴露 4 根原始轴 / MINIMAL 给用户** —— rig 比它要做的决策更成熟，暴露=给用户递陷阱。

## 落地清单（未开工，不急——见下）

1. Settings 加 2 档开关（兼容/高性能），存 `user_config.yaml`（走 `save_config_dict` + `reload_config`；注意 README 记载配置改动需**重启**才完全生效）。
2. `_prepare` / `build_agent` 读该配置项 → 映射到 `FLASH`/`STRONG`，缺省 `FLASH`。
   - ⚠️ 与现有「纯开发态、不进 yaml」原则的**张力**：ablation plan 定过「所有开关纯开发态，不进 yaml/前端/reload」。本note 是**有意的例外**——只把**策展的 2 档封装**提升为用户设置，4 根**原始轴**仍纯开发态。落地时别把 4 轴一起搬进 yaml。
3. 帮助文案按上「交付形态」写，只承诺快/省。

## 为什么不急（stop-here 理由）

- 本项目是**开源 + 求职 demo，几乎无活跃用户**（作者判断）。strict-default 已是现状，用户拿不匹配的模型本就达不到预期性能——先发 light 版收益有限。
- 真正把这条线转成「用户可感」的，就是上面 3 步一个小改动；**但没有 deadline**，可与其它工作同批做。
- **rig 已达成其使命**：结论 settle + 可复用 probe 入口留存。harness engineering 线到此**应当收手**；B/F 轴、minimal-via-Part3、补跑样本都是 **rig 加深**（非产品改进），搁置。
- 唯一真指向「答得更好」而非「机器转不转」的后续是 **A1/A4 工具信息瘦身**（s2/arxiv/jina docstring↔schema↔prompt 去冗余），独立于本线，是更真的产品级续作。

## 关联

- 上游结论：[harness-ablation-plan.md](./harness-ablation-plan.md)（Part 2 profile 设计 + ④/⑤ 实验定论）。
- 量具：[harness-behavior-runner-plan.md](./harness-behavior-runner-plan.md)。**cross-model 后续**：probe 的 model 当前来自 config、profile 是 flag；要跑 `model × profile` 矩阵，下次动它时加 `--model`（或 env override），把「gemini 专用探针」升成「任意模型探针」。
