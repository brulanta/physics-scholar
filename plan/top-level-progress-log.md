# PhysicsScholar 总纲与决策落实处

> **这份文档是什么**：项目架构层全景 + 决策落实清单 + 备忘 + 未成熟想法暂存处。
> 先**客观**描述项目有哪些架构层（含近期没被波及的），再把做过的工作映射上去（一句话+指路，细节回子计划查）。
> 不是季度总结、不是 plans 压缩版——是 base 项目总体的决策落实处，随 session 持续演进。
>
> 「近期波及」列的字母 = 工作线编号，见末尾[子计划索引](#子计划索引)的映射。
> 最后更新 2026-07-07。

---

## 一、架构层全景（先客观，后映射）

请求流：`app.py`（托盘+浏览器启动，frozen-aware）→ `src/main.py`（FastAPI，挂 `frontend/dist/`）→ `src/api/routes.py`（全部路由）→ `src/rag/graph.py`（LangGraph agent）→ ChromaDB + SQLite + 外部 API。

按职责客观拆出的层（带文件锚点 + 近期是否被波及）：

| 层 | 职责 | 文件锚点 | 近期波及 |
|---|---|---|---|
| **1. 入口/进程** | 托盘、浏览器启动、frozen 分流、重启、Job Object 孤儿防护、打包 | `app.py`、`scripts/build_release.py`、`physics_scholar.spec` | ✅ D |
| **2. Web 服务/路由** | 全部 HTTP 路由（会话/上传/确认/提问/regenerate/配置/重启/代理） | `src/api/routes.py`、`src/main.py`、`src/service_state.py` | ✅ C/D |
| **3. Agent harness** | graph 控制流（guard/prefill/预算/串行裁剪）、CoT&标记契约、流式状态机、prompt 模块化拼装、profile | `src/rag/graph.py`、`harness_profile.py`、`src/rag/prompts/**`、`trim_thinking.py` | ✅ A/C |
| **4. 工具/检索** | 本地 RAG（多路召回+重排）、外部检索（arXiv/S2/OpenAlex/Jina）+ 降级链、工具 MCP 化、工具信息瘦身 | `src/rag/tools/**`、`src/mcp_servers/**`、`mcp_client.py`、`tool_runtime.py` | ✅ B/D |
| **5. 文档摄入** | PDF 解析→切片→元数据提取→嵌入入库→注册表+去重 | `src/core/{parser,chunker,extractor,ingestor,registry,hash_file}.py` | 🟡 仅 chunker |
| **6. 会话/记忆** | 消息树（regenerate/edit 分支）、会话 CRUD、点赞 | `src/rag/memory.py`、`src/core/init_SQLite.py` | ❌ 未动 |
| **7. 存储** | ChromaDB 向量、SQLite（会话/消息/论文注册表）、data 目录布局 | `data/chroma_db/`、`data/SQLite/app.db` | 🟡 仅 chroma_gen |
| **8. 配置** | .env > user_config.yaml > 硬编码；前端 Settings 写 yaml + reload | `src/config.py`、前端 `ConfigModal.vue`/`SettingsDrawer.vue` | 🟡 增项未重构 |
| **9. LLM 客户端** | main_llm/sub_llm 构造、streaming、DS 专属 extra_body | `src/llm.py` | 🟡 streaming 一行 |
| **10. 前端交互** | 聊天页、消息渲染、时间轴、上传、会话侧栏、markdown、服务遮罩 | `frontend/src/**` | ✅ C/D |
| **11. 评测/量具** | 召回分层评测、行为量具（非内容打分） | `scripts/{eval_retrieval,harness_probe,probe_*}.py`、`tests/test_harness_probe_metrics.py` | ✅ E |

**技术债/死代码**（客观存在但不承重，别误当架构层）：`src/rag/retriever.py`、`src/rag/chain.py`、`src/rag/prompt.py` 全仓无 import，历史遗留，待清理。`tests/`/`scripts/` 大量版本漂移脚本（见 CLAUDE.md 信任边界）。

---

## 二、已做工作（按层映射，一句话 + 指路）

> 自 2026-06-23 起 58 条 commit。每条一两句话，细节回子计划。

- **1 入口/进程**：frozen 托盘重启 breakaway 两条路径全补；打包固化为 `build_release.py` 一键三步。→ `mcp-tool-migration-plan.md`、`startup-restart-refactor-plan.md`
- **2 路由**：`/ask`/`/regenerate` 改 SSE 流式 + 断连检测；`/config/restart` 补 breakaway；删 `update_config` 的 MCP 热重启浪费。→ `real-streaming-sse.md`、`startup-restart-refactor-plan.md`
- **3 Agent harness**：把为弱模型定的强约束拆成 6~8 正交轴 + `HarnessProfile`（A/C2/E/H 可插拔，默认 FLASH=零行为变化）；6 阶段实验结论 settle——**A 轴 guard 对强模型是死重、C2 full→light 免费、light→minimal 砸契约 → STRONG(light)=Pareto 地板**。→ `harness-ablation-plan.md`
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

---

## 四、待办

### ⛔ blocked（等依赖到位）
- **合法弱模型地板**（核心阻塞依赖）：解锁 A1/A4 模型矩阵验收 `{弱,gemini}×{before,after}`（验是否误删防呆）+ Tier-2 任务完成测（达成+rounds-to-goal）。触发条件=找到渠道满血、能吐合法 `tool_calls`、任务上不空答的弱模型。在那之前 gemini-only 结论一律标 provisional。
- **Harness B（correction_tone）/ F（serial_tools）接线**：黄区，改动扩散出 graph.py，各自单独小 PR。非阻塞。

### ⏸ 不急 / 决策已定未编码
- **Profile 产品化**：Settings 2 档开关 + `_prepare`/`build_agent` 读配置映射 FLASH/STRONG + 帮助文案（只承诺快/省）。3 步小改动，无 deadline。
- **cross-model probe**：加 `--model` env override，把 gemini 专用探针升成任意模型探针。
- **chunk-size 扫描**（256/200）：当前证据支持取消、保留重启口；真实用户反馈纯语义检索有术语不匹配短板再议。
- **`answer_reset` 流式硬化**：DONE 后惯性早闭标签致瞬时闪烁（done 覆盖保正确性，不写错库）；投入产出比低。

### 🧹 收尾杂项
- 两份 untracked behavior JSON（`behavior_SMOKE_20260703_154644.json` + `behavior_STRONG_20260703_162902.json`，07-03 阶段②/③ probe 产物）：补提交存档 or 确认废弃删除。
- 死代码 `retriever.py`/`chain.py`/`prompt.py`：择机清理。

---

## 五、未成熟想法（待讨论落 plan）

> 还在考虑、没落实成 plan 的想法暂存处。后续 session 讨论后，或落进上面「决策清单/待办」，或独立成 plan。
>
> *（空——等后续 session 注入）*

---

## 六、跨机交接提醒（gitignored `data/` 相关）

- RAG 评测依赖 `data/chroma_db/` 的 `eval_baseline`/`eval_fixed` 1024 维 collection（换机不存在 → 首跑重入库**消耗嵌入额度**）。
- 生产 chroma 库需 bge-m3/1024 维（曾遇 384 维历史废库报维度错）。
- MCP 实机核验需在装有 exe 的机器上做（Job Object 等依赖真实 Windows 内核行为）。

---

## 子计划索引

| 编号 | 工作线 | 子计划文件 | 状态 |
|---|---|---|---|
| A | harness 过约束诊断 | [harness-ablation-plan.md](./harness-ablation-plan.md) | ✅ 结论 settle |
| B | RAG 召回四件套 + 工具信息瘦身 | [rag-fix-upgrade-plan.md](./rag-fix-upgrade-plan.md) / [tool-info-slimming-plan.md](./tool-info-slimming-plan.md) | ✅ / 🟡 收口 |
| C | SSE 真流式 | [real-streaming-sse.md](./real-streaming-sse.md) | ✅ |
| D | MCP 迁移 + 启动重启 + 打包 | [mcp-tool-migration-plan.md](./mcp-tool-migration-plan.md) / [startup-restart-refactor-plan.md](./startup-restart-refactor-plan.md) | ✅ 封存 / ✅ |
| E | 评测/量具（召回 + 行为 probe） | [harness-behavior-runner-plan.md](./harness-behavior-runner-plan.md) + rag-fix Part4 | ✅ |
| — | profile 产品侧（决策未编码） | [profile-selection-decision.md](./profile-selection-decision.md) | ⏸ |
