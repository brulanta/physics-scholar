# PhysicsScholar 总纲与决策落实处

> **这份文档是什么**：项目架构层全景 + 决策落实清单 + 备忘 + 未成熟想法暂存处。
> 先**客观**描述项目有哪些架构层（含近期没被波及的），再把做过的工作映射上去（一句话+指路，细节回子计划查）。
> 不是季度总结、不是 plans 压缩版——是 base 项目总体的决策落实处，随 session 持续演进。
>
> 「近期波及」列的字母 = 工作线编号，见末尾[子计划索引](#子计划索引)的映射。
> 最后更新 2026-07-13（T0 完成：prompt 模块化 4 硬边界破 + 死代码清理）。

---

## 一、架构层全景（先客观，后映射）

请求流：`app.py`（托盘+浏览器启动，frozen-aware）→ `src/main.py`（FastAPI，挂 `frontend/dist/`）→ `src/api/routes.py`（全部路由）→ `src/rag/graph.py`（LangGraph agent）→ ChromaDB + SQLite + 外部 API。

按职责客观拆出的层（带文件锚点 + 近期是否被波及）：

| 层                   | 职责                                                                                              | 文件锚点                                                                                   | 近期波及          |
| -------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ----------------- |
| **1. 入口/进程**     | 托盘、浏览器启动、frozen 分流、重启、Job Object 孤儿防护、打包                                    | `app.py`、`scripts/build_release.py`、`physics_scholar.spec`                               | ✅ D              |
| **2. Web 服务/路由** | 全部 HTTP 路由（会话/上传/确认/提问/regenerate/配置/重启/代理）                                   | `src/api/routes.py`、`src/main.py`、`src/service_state.py`                                 | ✅ C/D            |
| **3. Agent harness** | graph 控制流（guard/prefill/预算/串行裁剪）、CoT&标记契约、流式状态机、prompt 模块化拼装、profile | `src/rag/graph.py`、`harness_profile.py`、`src/rag/prompts/**`、`trim_thinking.py`         | ✅ A/C/T0         |
| **4. 工具/检索**     | 本地 RAG（多路召回+重排）、外部检索（arXiv/S2/OpenAlex/Jina）+ 降级链、工具 MCP 化、工具信息瘦身  | `src/rag/tools/**`、`src/mcp_servers/**`、`mcp_client.py`、`tool_runtime.py`               | ✅ B/D            |
| **5. 文档摄入**      | PDF 解析→切片→元数据提取→嵌入入库→注册表+去重                                                     | `src/core/{parser,chunker,extractor,ingestor,registry,hash_file}.py`                       | 🟡 仅 chunker     |
| **6. 会话/记忆**     | 消息树（regenerate/edit 分支）、会话 CRUD、点赞                                                   | `src/rag/memory.py`、`src/core/init_SQLite.py`                                             | ❌ 未动           |
| **7. 存储**          | ChromaDB 向量、SQLite（会话/消息/论文注册表）、data 目录布局                                      | `data/chroma_db/`、`data/SQLite/app.db`                                                    | 🟡 仅 chroma_gen  |
| **8. 配置**          | .env > user_config.yaml > 硬编码；前端 Settings 写 yaml + reload                                  | `src/config.py`、前端 `ConfigModal.vue`/`SettingsDrawer.vue`                               | 🟡 增项未重构     |
| **9. LLM 客户端**    | main_llm/sub_llm 构造、streaming、DS 专属 extra_body                                              | `src/llm.py`                                                                               | 🟡 streaming 一行 |
| **10. 前端交互**     | 聊天页、消息渲染、时间轴、上传、会话侧栏、markdown、服务遮罩                                      | `frontend/src/**`                                                                          | ✅ C/D            |
| **11. 评测/量具**    | 召回分层评测、行为量具（非内容打分）                                                              | `scripts/{eval_retrieval,harness_probe,probe_*}.py`、`tests/test_harness_probe_metrics.py` | ✅ E              |

**技术债/死代码**（客观存在但不承重，别误当架构层）：`src/rag/retriever.py`、`src/rag/prompt.py` 已于 T0#2 删除（实测全仓零 import）；`src/rag/chain.py` 保留作非流式兜底（决策 §三.13，仅信任边界外漂移脚本 import）。`tests/`/`scripts/` 大量版本漂移脚本（见 CLAUDE.md 信任边界）。

---

## 二、已做工作（按层映射，一句话 + 指路）

> 自 2026-06-23 起的 commit。每条一两句话，细节回子计划。

- **1 入口/进程**：frozen 托盘重启 breakaway 两条路径全补；打包固化为 `build_release.py` 一键三步。→ `mcp-tool-migration-plan.md`、`startup-restart-refactor-plan.md`
- **2 路由**：`/ask`/`/regenerate` 改 SSE 流式 + 断连检测；`/config/restart` 补 breakaway；删 `update_config` 的 MCP 热重启浪费。→ `real-streaming-sse.md`、`startup-restart-refactor-plan.md`
- **3 Agent harness**：把为弱模型定的强约束拆成 6~8 正交轴 + `HarnessProfile`（A/C2/E/H 可插拔，默认 FLASH=零行为变化）；6 阶段实验结论 settle——**A 轴 guard 对强模型是死重、C2 full→light 免费、light→minimal 砸契约 → STRONG(light)=Pareto 地板**。→ `harness-ablation-plan.md`
  - **T0：prompt 模块化 A 半 + 死代码清理**（2026-07-13 完成）：破 4 硬边界（pkgutil 目录扫描 / mode 数据化 / 占位符自动探测 / `TOOL_DECISION_PLUGIN` 下沉）+ 修 `debug.yaml` 漂移 bug；删死代码 `retriever.py`/`prompt.py`（`chain.py` 保留）。零行为变化字节级验证 + frozen 端到端验证通过。是想法 3 重写主 agent prompt 的地基。→ `prompt-modularization-t0.md`
- **4 工具/检索**：RAG 召回四件套全落地（token 校准切片 + BM25+向量 RRF 融合 + bge-reranker-v2-m3 重排，C 层 R@1/MRR 全 1.0，方向可信幅度偏乐观）；6 工具全 MCP 化（4 项硬伤全修）；三重工具 docstring/Field 瘦身 −29% token（防呆全留，Tier-1 门无回归）。→ `rag-fix-upgrade-plan.md`、`mcp-tool-migration-plan.md`、`tool-info-slimming-plan.md`
- **5 文档摄入**：仅 `chunker.py` 被波及（token 校准切片 + 分隔符顺序修正）；parser/extractor/registry 未动。
- **6 会话/记忆**：未波及。
- **7 存储**：新增 `chroma_gen.py`（跨进程写后读代际令牌）；SQLite schema 零迁移。
- **8 配置**：新增纯开发态常量（chunker/rag/rerank/harness profile 相关，不进 yaml/reload）；`config.ROOT` 加 frozen 分支。
- **9 LLM 客户端**：`main_llm` 加 `streaming=True`；DS extra_body 不变。
- **10 前端**：fetch+ReadableStream 消费 SSE + claude 风竖向时间轴 + AbortController 中止；ConfigModal 强制重启去双跳；ServiceMask 转圈/X 语义分离。
- **11 评测/量具**：召回分层评测（双 collection + 文本重叠判定 + 探针）+ 行为量具 `harness_probe`（兜底三件套 + soft-violation 计数 + `--dump-transcript`）。**两条便宜可重跑回路是本窗口最大方法论资产。**

---

## 三、决策落实清单（活的——已定案、记此防反悔）

> 已拍板的决策，落纸即约束。改动前先回查这里。

1. **Harness 分矫正层 vs 契约层**：矫正层（guard/prefill，拟合单模型失败模式）默认关、契约层（工具集/降级链/引用格式/C1 协议）承重。LLM 非平稳 → **不校准最优点、只校准梯度**；资产是便宜的重测回路。→ 已写入 memory `harness-vs-llm-change-stance`
2. **生产 profile = STRONG(light)**：Pareto 地板。gemini 实证安全净收益。**未产品化**（仍开发态 flag）。
3. **profile 暴露方式**：默认 strict(=FLASH)、light 作 opt-in；Settings 2 档开关「兼容/高性能」；**不做**模型检测、**不做**运行时自动升档。→ `profile-selection-decision.md`（未编码）
4. **RAG 检索流默认**：`RAG_HYBRID_ENABLED=True` + `RERANK_ENABLED=True`；重排复用 embedding 的 url/key（调用时取 `config.EMBEDDING_*` 跟随 reload，不新增 key 常量）。
5. **chunker/rerank/harness profile 全部纯开发态**：.env > 硬编码出厂，不进 yaml/前端/reload_config（profile 产品化是已批准的有意例外，只搬策展 2 档）。
6. **MCP 互斥**：`PS_USE_MCP` 单一总闸，frozen 默认 true / dev 默认 false，绝不同时激活（速率锁会分裂）。
7. **工具名钉死原名**：`to_fastmcp` + `tool_name_prefix=False`，保 graph 透传给 SSE 的 `ev["name"]` 与前端映射不变。
8. **流式正确性下限**：`done` 带权威 `answer`（last-close-wins）覆盖前端累计文本；断连不落库。
9. **打包走 `build_release.py`**：别直接 `pyinstaller physics_scholar.spec`（会 ship 旧前端 + 递归旧 bundle）。
10. **原生思维链保持禁用**（控温决策），作实验外层变量记录，不进 profile 结构。
11. **MCP plan 封存**：闭环迁移记录，不再往里堆新工作；启动/重启相关去 `startup-restart-refactor-plan.md`。
12. **想法 3 开工时机 = 按【五】序排第三**（2026-07-13 定）：B/F 接线与 Profile 产品化推迟到想法 3 后。理由：想法 3 把主 agent 检索循环挪子 agent，循环消失 → B/F 接线对象变（先接=返工）；STRONG(light) 档变历史 → 现在固化进 UI 是给用户承诺即将名不副实的"高性能档"。合 memory `harness-vs-llm-change-stance`——校准点会过时别急着固化，重测回路（`harness_probe`）在想法 3 后存活且搬家更值钱才是资产。→ 执行序见【六】。
13. **chain.py 保留作非流式兜底**（2026-07-13 定）：仅清 `retriever.py`/`prompt.py`（实测全仓零 import 真死代码）；`chain.py` 生产路径已不走、仅信任边界外漂移测试脚本 import，删它要动那些脚本收益低。消解了本 log §一 与 `real-streaming-sse.md:86` 的表述冲突。

---

## 四、待办（raw 汇总，执行序见【六】）

> 本节是 raw 待办素材（从各子计划 + 思考汇总而来），含「已定案未编码」「阻塞项」「收尾杂项」三类。
> **不要按本节顺序开工**——执行排序看【六】（已整合本节 + 【五】成统一开工单，带依赖标注）。
> 本节保留是为追溯每项的来源与决策依据；条目被【六】排进执行单后，开工时再提炼成独立 plan。

### ⛔ blocked（等依赖到位）

- **合法弱模型地板**（核心阻塞依赖）— 来源 [harness-ablation-plan.md](./harness-ablation-plan.md)（附录 A1/A4 + 矩阵框架）/ [tool-info-slimming-plan.md](./tool-info-slimming-plan.md)（§合法弱模型地板，定性+矩阵+Tier-2+触发条件全在此）/ [harness-behavior-runner-plan.md](./harness-behavior-runner-plan.md)（harness_probe 仪器）：解锁 A1/A4 模型矩阵验收 `{弱,gemini}×{before,after}`（验是否误删防呆）+ Tier-2 任务完成测（达成+rounds-to-goal）。触发条件=找到渠道满血、能吐合法 `tool_calls`、任务上不空答的弱模型。在那之前 gemini-only 结论一律标 provisional。
- **Harness B（correction_tone）/ F（serial_tools）接线** — 来源 [harness-ablation-plan.md](./harness-ablation-plan.md)（§接线风险分级 line 79-80/101，B/F 列黄区）：改动扩散出 graph.py，各自单独小 PR。非阻塞。**⚠ 冲撞【五】想法 3**：想法 3 把检索循环挪子 agent 后，B/F 的接线对象（主 agent 检索循环）消失，先接=可能白做。

### ⏸ 不急 / 决策已定未编码

- **Profile 产品化** — 来源 [profile-selection-decision.md](./profile-selection-decision.md)（整篇即此决策）：Settings 2 档开关 + `_prepare`/`build_agent` 读配置映射 FLASH/STRONG + 帮助文案（只承诺快/省）。3 步小改动，无 deadline。**⚠ 冲撞【五】想法 3**：想法 3 让 STRONG(light) 检索循环配置变历史、子 agent 开新配置空间，产品化前需先 settle 档位。
- **cross-model probe** — 来源 [harness-behavior-runner-plan.md](./harness-behavior-runner-plan.md) / [profile-selection-decision.md](./profile-selection-decision.md)（§量具 line 66）：加 `--model` env override，把 gemini 专用探针升成任意模型探针。**⊕ 协同【五】想法 3**：子 agent 需新 probe 轮测时顺带加 `--model`，一次到位。
- **chunk-size 扫描**（256/200）— 来源 [rag-fix-upgrade-plan.md](./rag-fix-upgrade-plan.md)（§chunk-size 扫描降级 line 363-365）：当前证据支持取消、保留重启口；真实用户反馈纯语义检索有术语不匹配短板再议。
- **`answer_reset` 流式硬化** — 来源 [real-streaming-sse.md](./real-streaming-sse.md)（§风险与边界 line 110-113）：DONE 后惯性早闭标签致瞬时闪烁（done 覆盖保正确性，不写错库）；投入产出比低。

### 🧹 收尾杂项

- ~~死代码 `retriever.py`/`prompt.py`~~ — **已于 T0#2 删除**（2026-07-13，实测全仓零 import，spec hiddenimport 一并清）。`chain.py` **保留**作非流式兜底（决策 §三.13：仅 `tests/test_rag_chain.py`+`scripts/verify_rag_chain.py` 等信任边界外漂移脚本 import，生产路径已不走）。此项已闭环。

---

## 五、讨论成果存档（待开工时落实成独立 plan）

> 本节是 raw 讨论结论存档（跨 session 讨论后方案成型、尚未开工的想法），记最终结论不记商榷过程。
> **不要按本节顺序开工**——执行排序看【六】（想法 1/2/3 已排进 T0~T2，带依赖标注）。
> 本节保留是为追溯每个想法的现状关键事实 + 设计成果；某项被【六】排进执行单后，开工时从本节提炼成独立 plan。

> 跨 session 讨论后方案已成型、但尚未开工落实的想法。开工某一项时，从这里提炼成独立 plan 文件。
> 本节记**最终结论**，不记商榷中间过程。三想法独立但都改 `graph.py`/`thinking.py`，有耦合见末尾。

### 想法 1：可视化 Prompt 调控界面

**结论**：现成工具（Langfuse/LangSmith/PromptLayer）模型不匹配，GUI 得自己写；拆两半推进，A 半纯收益先做，B 半看调 prompt 频率再说。

**现状关键事实**：

- `builder.py` 已是声明式拼装引擎（`build()` 过滤 enabled→按 order 升序→join，`builder.py:113-122`），无硬编码 if/else。关模块/调顺序现在就是零代码（改 `profiles/*.yaml`）。
- 真实自由度卡在 **4 个硬边界**：
  1. 模块发现是代码登记表（`modules/__init__.py:13-52` 手动 import + `wrap_module`），非目录扫描——新增模块必须改 .py，YAML 引用未注册名 `KeyError`（`builder.py:98-99`）。
  2. mode 是代码枚举非数据（`_MODE_MODULES` dict + `build_prompt`/API 层 `Literal["normal","discuss"]`）——新增 mode 动 3-4 处代码。
  3. 动态变量注入写死模块名（`builder.py:189-190` 硬编码 `inject("CONTEXT_BLOCK",…)`/`inject("CITATION_FORMAT",…)`）。
  4. `TOOL_DECISION_PLUGIN` 用 f-string 在 import 期焊死在 thinking 模块（`thinking.py:33,45`），游离于模块体系，YAML 看不见关不掉——「plugins 遗漏在模块层级外」的实锤。
- **漂移 bug**：`debug.yaml:25` 引用已删除的 `CITATION_PLUGIN_SLOT`（只剩 `__pycache__` 残骸），切 debug profile 触发 `KeyError`。做 GUI 前必须先修。

**外部工具调研**：三家核心抽象都是「一个 prompt name → 多个 version → label 指定生效版本」，GUI 编辑的是整段 prompt 文本 + `{{变量}}` 占位，运行时 `compile()` 填变量。最小单位是「一整个 prompt」，**没有「模块开关/顺序拖拽/新增模块」这种组合式构造**。它们解决 prompt 版本控制/A-B/灰度，不是模块化可视化组装。Langfuse prompt management 可脱离 tracing 单独用、可 self-host，但仍是整段文本版本管理。

**推进**：

- **A 半（高价值低风险纯收益，先做）**：修 4 个硬边界 + 漂移 bug——模块发现改目录扫描、mode 从枚举改数据、统一占位符协议、`TOOL_DECISION_PLUGIN` 下沉成可注入变量。做完 YAML 真正成可写配置，「自由度不够」痛点消大半。不依赖 GUI。
- **B 半（GUI 界面，看频率）**：A 半做完底层已是声明式可写配置，GUI 只是把 YAML 编辑可视化。先做 A 半，等「改 YAML 烦了」再做 B 半。

### 想法 2：引用实现的选型评估与优化

**结论**：不全盘换 DeepSeek 模式。保持 Model 驱动的编号 + 摘抄（反幻觉护城河），吸纳 harness 校对/填写来源元信息（演化为 bind-by-id + enrichment sidecar）。跨轮次携带全量 raw 经评估弊大于利，不做。

**现状关键事实**：

- Model 驱动，模型独揽三件事：格式记忆（`[ref:N]` + `<ref id="N">来源 | 支撑片段</ref>`，`citation_format.py`）、编号管理（按正文首次出现顺序，**per-answer 重编号**）、原文摘抄（必须逐字、禁止改写，无可靠原文时省略而非概括）。
- 工具层完全不给编号：rag 给 `[标题, Page]`、外部工具给 `s2_paper_id/arxiv_id/doi`，但无 `[1]`。编号/映射/摘抄全压 model，harness 零参与。
- 渲染纯前端后处理（`frontend/src/utils/markdown.js` 正则消费 `[ref:N]`+`<ref>`），模型格式写错跳转就失效，无兜底。
- 跨轮次现状：入库只存纯净 answer（落库前过 `process_llm_output` 剥 thinking），`messages` 表只有 role/content（`init_SQLite.py:14-32`），ToolMessage 不进库；取库只重建 Human/AIMessage（`memory.py:280-288`）；历史拼纯文本进 system prompt（`format_history`）。**turn N+1 看不到 turn N 检索的 raw，但能看到 turn N answer 里的 refs 元信息+摘抄（已注入）。**

**四个正交维度的评估结论**：

- (a) 谁编号 → **保持 model**（编号便宜；harness 编号要解决「模型正文角标与 harness 分配 id 对齐」，反而更绕）。
- (b) 谁填来源信息 → **吸纳 harness**（见下，bind-by-id + enrichment sidecar）。
- (c) 谁摘抄佐证 → **保持 model**（反幻觉核心，绝不下沉；选段是推理 + grounding 强迫功能，harness 代劳则 model 可划水、grounding 信号丢失，且 harness 不知道 model 想用哪段）。
- (d) 跨轮次可见 → **基本不做**。

**跨轮次评估（不做全量 raw 携带的理由）**：

- 现状 per-answer 编号，turn 2 refs 从 1 重新编自包含，「编号对不上」根本不发生。
- unreferenced raw 多半是 model 判定不可用而弃，不保留。
- 全量 raw 跨轮携带弊大于利：①撑大是复利的（raw 比摘抄大一个量级）②制造误导（锚定 + lost-in-the-middle）③raw 按 turn 1 问题检索，对 turn 2 已过时。
- 业界做法 = re-fetch on demand，不携 raw；PhysicsScholar 现状（只重载 Human/AIMessage、ToolMessage 当轮即丢）已是正解。短 session 不必建表，turn 2 直接读 turn 1 answer 文本（已 `format_history` 注入，有 refs 元信息+摘抄），要重取用 id 走工具检索。只有长 session 才考虑 citations 表，作用是「按 id 重取更便宜」的索引，不是「让 turn 2 看到 raw」。
- DS 式：链接给用户点（验证 affordance），agent 每轮重新检索，链接不回流进下一轮 context。

**2(b) 最终设计（bind-by-id + enrichment sidecar）**：

- 诉求演化：harness 校对写元信息 + url 呈现从 model 剥离（默认 reference 帮写 url）+ RAG 来源写 doc_id 便于后续继续使用 + 未来 web 检索工具扩容时 url 归 harness 更稳妥（不抄错）。
- **bind-by-id**：model 按 source_id 绑定，harness 按 id 查 registry 填全部结构化元信息。model 的 ref 输出塌缩成极简 `<ref id="N">[source_id] | 摘抄`，其余（title/authors/venue/year/url 或 doc_id+page）全 harness 填。
- 为什么 bind-by-id 干净：id 转写低错（短、可逐字符抄），title/authors 转写高错（model 漏作者、拼错、编 venue）。让 model 只抄 id，高错部分全交 harness ground truth。
- **enabling edit**：当前 RAG `format_context` 返回串里没有 doc_id（`rag_tool.py:130-137` 只给 `[标题, Page]`，doc_id 在 metadata 没写进串）。bind-by-id 要求 id 对 model 可见，要给 RAG 返回串补 doc_id。论文源 id 本来就在结果 dict 里。
- **bonus**：bind-by-id 自带幻觉检测——model 编了 registry 没有的 id，harness 查无匹配 = binding 幻觉信号，强化反幻觉初衷。
- **持久化双表示**：
  - **lean**（DB `messages.content` 存这个）= model 原文 `[source_id | 摘抄]`，给重展示时后端 merge 出 rich 给前端 + `format_history` 注入下一轮给 model 看。
  - **enrichment sidecar**（新表，按 message+ref_id）= `{source_id, type, title, authors, doi, url, ...}`，给后端展示期 merge 用 + 兼作「按 id 重取」便宜索引。
  - 双表示消解的问题：enrichment 必须持久化（否则页面刷新/会话恢复 url/doi 全没）；model 在 history 看到的 lean 跟被要求写的完全一致，无 drift；model 看不到 harness 填的元信息，无从多余抄袭；不需要 prompt 规避；不剥 raw 里的元信息（工具结果里有用、展示里有用，只是不该进 model 的 ref 输出、不该进 history）。
- **漂亮格式**：双格式——model 向 lean（机器友好 id+摘抄）/展示向 rich（人友好完整引用+可点 url，type-aware：论文给 doi/url，RAG 给 doc_id+page）。
- **scope**：是个有边界的小模块（①工具层补 source_id，RAG 加 doc_id ②enrichment sidecar 表 ③展示期 merge ④prompt 改 lean ref 格式），不是一行改动，但独立于想法 1/3 不挡路。

### 想法 3：解耦 CoT，子 Agent 专注检索

**结论**：轻量可行（主 graph 零改动），走「把子 agent 包成一个工具」路径。成本核算成立。子 agent 内部配置大幅瘦身是核心收益。

**现状关键事实**：

- 主 agent 一把抓：`call_llm→thinking_guard→{tool_node|call_llm|final_answer|END}` 循环，`remaining_calls` max 6（`harness_profile.py:36`），thinking_guard 要求 tool call 前有 `<thinking>` 块否则注 fake ToolMessage + correction 重试 MAX 3（`graph.py:151-227`）。
- 主 agent 注意力负担真实：6 工具 schema 每轮全量绑定（`llm.bind_tools`，`graph.py:425`），粗估 ~2000-3300 token **每轮重复付**，6 轮循环 = 6 次。
- `build_agent` 已是「工具列表+闭包」模式（`graph.py:417-424`），加一个工具只需 tools 列表加一项。

**子 agent 形态（核心认知）**：

- 子 agent = 一张独立编译的图 + 一个调用它的工具函数壳。对主 agent 是工具（透明、轻接、`bind_tools` 里一项），对子 agent 自己是图（有循环、有 guard、有预算）。循环不是手写 while，是 LangGraph 图编译后 `.ainvoke()` 按边路由驱动。两张图物理隔离：各自 State/预算/guard，唯一桥是 run 函数（主图递 query、它跑完子图递回文本）。Claude Code subagent 同款思路。
- 主 graph 零改动：子 agent 那张图独立编译独立存在，被工具函数引用，在主图 tools 列表里以工具身份存在。

**成本核算**：只 call 一次子 agent = 把主 agent 原 6 轮检索循环挪出去，净增 1 次「拉起子 agent」的 LLM 调用。符合「不是无意义加调用」初衷。

**子 agent 内部配置瘦身（解耦 CoT 核心收益）**：

- guard 可关（`guard_mode="off"`，不对用户展示 thinking 契约，省纠正往返）。
- prefill 降到 minimal（非流式不需要 `[TOOL_LOOP]` marker 契约）。注意老实验 minimal 砸契约是因为要流式 marker，子 agent 不流式所以 minimal 安全——这打开了老实验没覆盖的非流式配置空间。
- 预算必须保留（防无限循环），可给独立 `budget_n`。

**ToT/并行不做**：ToT 投入产出比低（任务是「检索补全信息缺口」这种有明确终止条件的线性任务，不需要探索多条推理路径）；并行不做（限速 + 盲打浪费 + 破坏限额逻辑），保持串行。

**子 agent 返回格式（设计成果）**：

- 终止信号做成 tool call 不靠 JSON 解析：子 agent 有检索工具 + 一个 `return_findings` 工具，检索够了就调它，结构化参数 `selection=[{result_index, item_index, reason}], summary`。
- 用顺序索引（harness 边收 ToolMessage 边编号，LLM 只需数数不抄长字符串），harness 拦到 `return_findings` = 子 agent 终止，按索引抠 item 拼接成主 agent 的 ToolMessage。
- 三要点：①子 agent 全程不转写元信息只点索引（解「中间商抄错」+「白花 token 原样吐」）②摘抄归属在主 agent 不在子 agent（子 agent 做 item 级筛选不做 excerpt 级摘抄，它不知道主 agent 最终论点盲摘必次优；harness 传给主 agent 的是被选中 item 的 content raw/限长，主 agent 自己读自己摘抄进 ref，grounding 留主 agent 保反幻觉初衷；子 agent 的 reason 只是筛选理由调试/透明用）③「只拼接不修剪」+ 限长兜底（chunker 已 token 校准、abstract ~200 词有界，纯拼接没问题，唯一风险 jina 长 blob 截断兜底）。

**引用收集（想法 2×3 耦合点，已消解）**：挂在「harness 把子 agent 输出打包成主 agent ToolMessage」那个函数里（桥接层/第三位置），既不挂主 agent 图节点也不挂子 agent 图节点。两层寄生已有流程零额外节点：①候选收集（打包时）harness 此刻有选中项 raw + 元信息，顺手把每个选中项的 enrichment 写进 sidecar（想法 2(b) 元信息 ground truth 几乎免费）②实际引用（主 agent 写完 answer 时）主 agent refs 是选中项子集，harness 拿 ref 里 source_id 查 sidecar enrich 成完整引用。

**harness_probe / prefill 影响（分两层）**：

- **变历史**：6 阶段实验具体调优点（STRONG(light) 给检索循环的 streaming 配置）+ `[TOOL_LOOP]` marker 给主 agent 流式的契约——主 agent 以后只调 1 次子 agent，它的 budget 耗尽路由/prefill 多分支/marker 协议大部分成死代码。
- **存活且搬家**：HarnessProfile 机制（可插拔轴）+ harness_probe 机器（soft-violation 计数 + dump-transcript）——搬到子 agent（它才是新检索循环 owner）。
- **新开空间**：子 agent 非流式可 guard off + prefill minimal，需新 probe 轮测。印证 memory `harness-vs-llm-change-stance`：重测回路是资产、校准点不是；probe 是回路（存活且更值钱），STRONG(light) 是点（变历史）。
- prefill 是「分裂+瘦身」不是「大改」：主 agent `build_prefill` 删死分支，子 agent 拿 trivial 版。

**final_answer 留不留（分两边，留 plan 时定）**：子 agent 内部头铁 LLM 狂调工具不收敛，恰恰最需要兜底（黑盒不收敛=卡死，必须 final_answer 强制收口）；主 agent 工具外包后头铁点挪到「反复 call 子 agent」，但主 agent 调子 agent 有预算约束（只 call 一次），兜底压力小，可能不再需要 final_answer 那种「放弃工具直接答」机制。倾向：主 agent 或许简化掉，子 agent 必须保留（预算耗尽强制返回当前 finding）。

**流式方案（脑测可行，中等改动）**：

- 不变：主 agent 自己的 thinking start/end + answer 流式完全不动（call_llm/final_answer 事件路由照旧）。
- 方案：子 agent 同步阻塞（`ainvoke`，不向上吐 token 流）+ 阶段边界 dispatch custom event。子 agent 不流式 token（避免污染），在 thinking 开始/结束、每个 tool 开始/结束发 custom event（langchain `adispatch_custom_event`，astream_events v2 标准机制）。主 `_consume_events` 加分支认这些 custom event，路由成「检索子任务」事件组给前端，前端渲染成主 tool call 下的嵌套分组。
- 走 custom event 不走 `langgraph_node` 元数据：层级区分。`langgraph_node` 是扁平字符串，不告诉「这是子 agent 第 N 次内部循环 tool 节点嵌在主 agent tool_node 第 2 层」，靠字符串匹配要自维护层级栈——脆。custom event 可显式带 layer/parent 信息一次判明。即便不重名（子图用前缀就不撞）custom event 仍优——结构化层级信号 vs 扁平字符串，重名只放大脆弱性不是根因。
- 可行性估：中等，三处协调改动——子 agent 加 dispatch 调用（小）、`_consume_events` 加 custom event 分支（中）、前端嵌套分组渲染（最磨叽，约 1-2 天聚焦活）。
- 前端等待时间持平或轻微增加：总延迟大体不变（检索工作量没变只挪，但多了拉起子agent的时间）， 6 个独立 tool 阶段 → 现在 1 个检索子任务阶段，接通 phase event 后用户检索期间仍能看到 thinking + 逐 tool 进度。

**`[TOOL_LOOP]` 标记对齐**：标记本质是「压住 answer 流、等工具返回」的闸门，解耦后仍需要（主 agent 调子 agent = 一次 tool call，等返回时 answer 流必须压住）。删的是 prompt 里「工具编排细节」（`tool_usage.py`/`thinking.py` 教主 agent 自己编排 s2→openalex→arxiv→jina 降级链），不是这个流式闸门标记。标记该改名（TOOL_LOOP 不准，解耦后只 call 一次，改 `TOOL_PENDING`/`TOOL_DONE` 或 `RETRIEVAL_PENDING`/`RETRIEVAL_DONE`）。

> 开发注：子agent内也需要保留，也许可以照搬目前现成。具体待开工商榷。

### 三个想法的耦合与推进顺序

- **想法 1 × 想法 3**：都碰 `thinking.py`。想法 1 修 `TOOL_DECISION_PLUGIN` 焊死后，想法 3 重写主 agent prompt（删工具编排细节）更干净。先 1 后 3 顺畅。
- **想法 2 × 想法 3**：都改 `graph.py`。引用收集挂「harness 打包子 agent 输出」函数（桥接层），二元选择被设计消解。2(b) 在现状架构就能做，不必等子 agent；也可与想法 3 一起做。
- **推荐顺序**：①想法 1 的 A 半（纯收益低风险基础设施，含修漂移 bug）→ ②想法 2 的 (b) bind-by-id + enrichment（独立不挡路）→ ③想法 3（轻量加工具，注意流式隔离）。每步落在上一步稳定地基上，无「为新而新」。

---

## 六、全局执行排序（决策已定，逐个开工照此走）

> 2026-07-13 决策落地。整合【四】待办 + 【五】想法成统一开工单。每项开工时从【四】/【五】提炼成独立 plan 文件再动手（见 CLAUDE.md「Plans live in the repo」）。
> 依赖列：硬阻塞标 `blockedBy #N`；软依赖标 `先 #N 更顺`。

### T0 — 纯收益低风险，想法 3 地基 ✅ 已完成（2026-07-13）

> 两项均落地、零行为变化字节级验证通过 + frozen 端到端验证通过。详见 [prompt-modularization-t0.md](./prompt-modularization-t0.md)。

| 序   | 项                                      | 来源         | 依赖 | 开工指引                                                                                                                                                                                                             |
| ---- | --------------------------------------- | ------------ | ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 ✅ | 想法 1 的 A 半：修 4 硬边界 + 漂移 bug  | 【五】想法 1 | 无   | 模块发现改目录扫描、mode 从枚举改数据、统一占位符协议、`TOOL_DECISION_PLUGIN` 下沉成可注入变量；修 `debug.yaml:25` 引用已删 `CITATION_PLUGIN_SLOT` 的漂移 bug。纯收益零行为变化，是想法 3 重写主 agent prompt 的地基 |
| 2 ✅ | 死代码清理 `retriever.py` / `prompt.py` | 【四】收尾   | 无   | 实测全仓零 import（仅 `physics_scholar.spec` 打包列表残留，一并删）；`chain.py` 保留不动（决策 §三.13）                                                                                                              |

### T1 — 独立不挡路，中等收益

| 序  | 项                                            | 来源         | 依赖                         | 开工指引                                                                                                                                                                     |
| --- | --------------------------------------------- | ------------ | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 3   | 想法 2 的 (b) bind-by-id + enrichment sidecar | 【五】想法 2 | 无（可与 #4 合并，也可先做） | 工具层补 source_id（RAG `format_context` 加 doc_id）+ enrichment sidecar 表 + 展示期 merge + prompt 改 lean ref 格式。反幻觉护城河强化，独立于想法 3（耦合点已消解挂桥接层） |

### T2 — 枢纽，大改动

| 序  | 项                                  | 来源         | 依赖       | 开工指引                                                                                                                                                                                                                                                                                                                                                                      |
| --- | ----------------------------------- | ------------ | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4   | 想法 3：解耦 CoT，子 agent 专注检索 | 【五】想法 3 | 先 #1 更顺 | 主 graph 零改动、子 agent 包成工具；子 agent guard off + prefill minimal（非流式新配置空间）+ 独立预算；`return_findings` 工具终止；流式走 `adispatch_custom_event` 阶段边界 dispatch + 前端嵌套分组渲染；删主 agent prompt 工具编排细节，`[TOOL_LOOP]` 改名 `TOOL_PENDING`/`RETRIEVAL_PENDING`。**cross-model probe 的 `--model` 加法并入本项新 probe 轮**（正协同一次到位） |

### T3 — 随想法 3 定（决策 §三.12 落地）

| 序  | 项                                                  | 来源          | 依赖           | 开工指引                                                                                                                                                                                                        |
| --- | --------------------------------------------------- | ------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 5   | Harness B（correction_tone）/ F（serial_tools）接线 | 【四】blocked | `blockedBy #4` | 想法 3 后检索循环挪子 agent，B/F 接线对象变子 agent，先接=返工。B 须同步替换 prompt 层 `output_format.py:4-10`+`plugins.py:19`；F 须重构 `llm.py` 为工厂函数或 `build_agent` 里 `llm.bind(extra_body=...)` 覆盖 |
| 6   | Profile 产品化                                      | 【四】不急    | `blockedBy #4` | 想法 3 后 STRONG(light) 检索循环配置变历史，需在新配置空间重新 settle 档位再搬前端。Settings 2 档开关（兼容/高性能）+ `_prepare`/`build_agent` 读配置映射 + 帮助文案只承诺快/省；不做模型检测/运行时自动升档    |

### 不排期 / 外部触发

| 项                         | 来源          | 状态       | 备注                                                                                 |
| -------------------------- | ------------- | ---------- | ------------------------------------------------------------------------------------ |
| chunk-size 扫描（256/200） | 【四】不急    | 保留重启口 | 等真实用户反馈纯语义检索术语不匹配短板再议；与想法 3 正交（子 agent 用同款检索工具） |
| `answer_reset` 流式硬化    | 【四】不急    | 最低优先   | 投入产出比低；想法 3 主 agent answer 流式不动故不受影响                              |
| 合法弱模型地板             | 【四】blocked | 外部触发   | 等弱模型渠道；与想法 3 无关（子 agent probe 在 gemini-only 下一样 provisional）      |

---

## 七、跨机交接提醒（gitignored `data/` 相关）

- RAG 评测依赖 `data/chroma_db/` 的 `eval_baseline`/`eval_fixed` 1024 维 collection（换机不存在 → 首跑重入库**消耗嵌入额度**）。
- 生产 chroma 库需 bge-m3/1024 维（曾遇 384 维历史废库报维度错）。
- MCP 实机核验需在装有 exe 的机器上做（Job Object 等依赖真实 Windows 内核行为）。

---

## 子计划索引

| 编号 | 工作线                          | 子计划文件                                                                                                                          | 状态                   |
| ---- | ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| A    | harness 过约束诊断              | [harness-ablation-plan.md](./harness-ablation-plan.md)                                                                              | ✅ 结论 settle         |
| B    | RAG 召回四件套 + 工具信息瘦身   | [rag-fix-upgrade-plan.md](./rag-fix-upgrade-plan.md) / [tool-info-slimming-plan.md](./tool-info-slimming-plan.md)                   | ✅ / 🟡 收口           |
| C    | SSE 真流式                      | [real-streaming-sse.md](./real-streaming-sse.md)                                                                                    | ✅                     |
| D    | MCP 迁移 + 启动重启 + 打包      | [mcp-tool-migration-plan.md](./mcp-tool-migration-plan.md) / [startup-restart-refactor-plan.md](./startup-restart-refactor-plan.md) | ✅ 封存 / ✅           |
| E    | 评测/量具（召回 + 行为 probe）  | [harness-behavior-runner-plan.md](./harness-behavior-runner-plan.md) + rag-fix Part4                                                | ✅                     |
| T0   | prompt 模块化 A 半 + 死代码清理 | [prompt-modularization-t0.md](./prompt-modularization-t0.md)                                                                        | ✅ 完成（想法 3 地基） |
| —    | profile 产品侧（决策未编码）    | [profile-selection-decision.md](./profile-selection-decision.md)                                                                    | ⏸                      |
