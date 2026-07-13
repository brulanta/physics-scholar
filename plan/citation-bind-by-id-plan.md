# 引用 bind-by-id + enrichment sidecar（想法 2(b) / T1 #3）

> 顶层设计出处：`plan/top-level-progress-log.md` 【五】想法 2 的 (b)（line 155-167）+ 【六】T1 #3（line 247-249）。
> 状态：**代码 + 单测完成（2026-07-13）**，端到端 + frozen 验证待跑。

## 实现进度

- ✅ Step 1 + Step 2 全部代码落地，34 个单测 + 7 个 prompt 语义回归全绿。
- ✅ `test_prompt_byte_equivalence.py` 字节等价用例退役（T1 改 prompt 内容，字节不变成反指标），
  换成 lean ref 语义回归（`test_prompt_has_lean_ref_format` / `test_prompt_translation_keeps_zh`）；
  T0 框架回归 3 测试保留。配套删 2 个过时 fixture（`dump_prompt_baseline.py` 留作调试工具）。
- ⏳ 端到端验证（dev server 真实 LLM 跑 RAG+s2 问答，查 sidecar + 看 rich 引用）待跑。
- ⏳ frozen 端到端（`build_release.py` 后确认 spec hiddenimport 生效）待跑。
- 已知不归本计划修：`test_s2_tool` 的 `test_author_only`/`test_keywords_and_author`（既有漂移，代码 2026-05-26 去了 author: 前缀、测试断言旧格式）；`test_rag_chain.py`（import 已删的 `get_or_create_session`，既有漂移）。

## Context（为什么做这个）

现状引用全压 model：格式记忆、编号管理、原文摘抄、来源元信息（作者/标题/venue/year/url）全由 model 独揽，harness 零参与。痛点是 **model 转写元信息高错**——漏作者、拼错、编 venue，且 url 尤其易错。

想法 2(b) 的核心收益：**bind-by-id**——model 只抄短而稳的 `source_id`，harness 按 id 查工具结果填全部结构化元信息（enrichment）。高错部分（title/authors/venue/url）全交 harness ground truth，model 只承担低错的 id 转写 + 反幻觉核心的摘抄。附带 **幻觉检测 bonus**：model 编了工具没返回的 id → 候选集查无匹配 = 幻觉信号。

这是 T1（独立不挡路、中等收益），独立于想法 3（T2 枢纽）。耦合点已在【五】消解——引用收集挂"harness 打包工具输出"层，现状架构对应"落库时回溯工具结果"，不必等子 agent。

## 已核对的关键事实（代码锚点）

- **RAG `format_context` 没把 doc_id 写进串**（`rag_tool.py:130-137`，只给 `[标题, Page]`）→ enabling edit 必须改这里，让 model 看到 `rag:<doc_id>`。一举两得：model 能写 ref + harness 能解析候选。
- **外部工具已自带结构化 id**：s2 `s2_paper_id`/`arxiv_id`/`doi`/`open_access_pdf`/`s2_url`（`s2_tool.py:197-217`）、arxiv `arxiv_id`/`pdf_url`（`arxiv_tool.py:402-418`）、openalex `openalex_id`/`openalex_url`/`doi`（`openalex_tool.py:239-259`）→ **外部工具返回格式零改动**，候选收集直接 json.loads 提取。
- **messages 表只有 role/content**（`init_SQLite.py:14-32`），ToolMessage 不进库 → sidecar 表是纯新增，零迁移。
- **answer 落库唯一钩子**：`chat_stream`/`regenerate_stream` 跑完后 `process_llm_output(final_content)` → `memory.add(AIMessage)` 拿 `agent_msg_id`（`graph.py:873-888` / `938-951`）。sidecar 关联 message_id 必须挂这里。非流式 `chat`/`regenerate` 同构（`graph.py:597-617` / `670-682`）。
- **流式路径拿不到完整 state messages**：`chat_stream` 用 `astream_events`，`result` 只有 `final_content`。但 `_consume_events` 已处理 `on_tool_end`（`graph.py:816-826`）→ 在此累积 `(tool_name, ToolMessage)` 到 `result["tool_results"]`，落库时回溯收集候选。guard 伪造的 ToolMessage 不经工具节点、不触发 on_tool_end，不会被误收集。
- **展示链路**：前端从 `/conversation/{id}/tree` 取 messages（`routes.py:162-170`），`markdown.js` 正则消费 `[ref:N]`+`<ref>`（`buildRefBlockHtml` 解析 `source | excerpt`，source 是 lean 的 `[s2:xxx]` 还是 rich 的完整引用它不在意，照 render）→ **前端 markdown.js 零改**。
- **registry 不存外部论文**（只有本地论文 `source_url`，`registry.py:66-80`）→ 外部 enrichment 必须当轮从工具结果捕获，不能事后查 registry。强化"候选收集层"必要性。
- **prompt 模块化已就位**（T0）：pkgutil 目录扫描 + 统一占位符协议（`builder.py:35-45,127-141`、`modules/__init__.py:28-45`）→ 改 `citation_format.py` 内容 + `plugins.py` 文本即可，**无需新增 mode/模块**。
- **RAG doc_id 是 per-user 隔离**（registry 主键 `(doc_id, user_id)`），外部 id 全局 → source_id 必须带类型前缀防撞 + 供展示期 type-aware 渲染。

## 设计

### source_id 规范（带类型前缀，harness 定、model 抄）

| 来源 | source_id | 备注 |
|---|---|---|
| RAG | `rag:<doc_id>` | format_context 改造后串里可见 |
| S2 | `s2:<s2_paper_id>` | model 从 s2 返回 JSON 读 paperId 自拼 |
| arXiv | `arxiv:<arxiv_id>` | model 从 arxiv 返回读 arxiv_id 自拼 |
| OpenAlex | `openalex:<openalex_id>` | model 从 openalex 返回读 openalex_id 自拼 |
| Jina | **本计划 out-of-scope** | jina 读任意 URL 无稳定论文 id，url 长且高错；维持现状（model 自由写来源）。呼应【五】"未来 web 检索工具扩容时 url 归 harness 更稳妥"，后续再议 |

前缀双重作用：①防 id 空间撞名 ②展示期 type-aware（论文给 doi/url，RAG 给 doc_id+page）③幻觉检测严格匹配。

### enrichment sidecar 表（schema 一步到位，Step 2 不迁移）

```sql
CREATE TABLE IF NOT EXISTS ref_enrichment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,       -- 关联 messages.id（agent 消息）
    conversation_id TEXT NOT NULL,     -- 冗余，便于清会话时连带删
    source_id TEXT NOT NULL,           -- 带前缀，如 s2:abc123
    ref_type TEXT,                     -- rag/s2/arxiv/openalex
    title TEXT, authors TEXT, venue TEXT, year TEXT,
    doi TEXT, url TEXT,                -- 论文可点链接
    doc_id TEXT, page TEXT,            -- RAG 专有
    raw_meta TEXT,                     -- 完整原始元信息 JSON（兜底）
    is_cited INTEGER DEFAULT 0,        -- Step 2：model 是否实际引用（0=候选 1=被引用）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_enrich_msg ON ref_enrichment(message_id);
CREATE INDEX idx_enrich_source ON ref_enrichment(conversation_id, source_id);
```

### 持久化双表示（【五】line 162-166）

- **lean**（DB `messages.content` 存这个）= model 原文 `<ref id="N">[source_id] | 摘抄</ref>`。`format_history` 注入下一轮的也是 lean（天然一致，无 drift，`memory.py:337-344` 不用改）。
- **rich**（展示期 merge 出，不入库）= harness 按 source_id 查 sidecar 填完整引用 + 可点 url，type-aware。

### 候选收集层语义（用户选 A，实现等价且更少侵入）

用户选定"候选收集层（打包时）"——sidecar 存**候选集**（工具返回的所有可引用项，非仅被引用项），为幻觉检测铺路。

实现上**不侵入 graph 节点结构**（不加 collect_candidates 节点、不改 AgentState），而是：
1. `_consume_events` 在 `on_tool_end` 累积 `(tool_name, ToolMessage.content)` 到 `result["tool_results"]`（流式路径）。
2. 落库时（`memory.add` 拿到 `agent_msg_id` 后）遍历 `tool_results`，调 `extract_candidates` 收集候选集，关联 `agent_msg_id` 批量写 sidecar。
3. 非流式 `chat`/`regenerate`：遍历 `result["messages"]` 找 ToolMessage 同样收集。

**语义等价说明**：落库回溯收集的候选集 = 工具回路即时收集的候选集（都是全部 ToolMessage 的候选），区别仅在时机。即时持久化在现状架构有 message_id 时序障碍（agent 消息 id 要到落库时才有），落库回溯绕开此障碍且语义不变。若后续想法 3 把检索挪子 agent，这套收集逻辑原样搬到"打包子 agent 输出"的桥接层——本计划接受这个临时性，plan 里标注。

## Step 划分（本计划内两步走）

### Step 1 — 主体：enrichment + 展示 merge（可见收益、低风险）

1. **RAG `format_context` 加 doc_id**（`rag_tool.py:130-137`）：串格式从 `[标题, Page N]\n内容` 改为 `[rag:<doc_id> | 标题, Page N]\n内容`。model 看到 `rag:<doc_id>` 可写进 ref；harness 候选收集从串正则提 `rag:<doc_id>` 候选（doc_id 在 `doc.metadata`，配合 page/title 一起提）。
2. **sidecar 表 + 持久化模块**：
   - `init_SQLite.py` 加 `ref_enrichment` 表（CREATE TABLE IF NOT EXISTS，幂等）。
   - 新建 `src/core/citation_store.py`：`save_candidates(message_id, conv_id, candidates)`、`load_enrichment_for_message(message_id)`、`load_enrichment_map(conv_id, source_ids)`。复用 `init_SQLite.get_conn()` 模式。
3. **候选收集 + 解析模块**（新建 `src/rag/citation.py`）：
   - `Candidate` dataclass：source_id, ref_type, title, authors, venue, year, doi, url, doc_id, page, raw_meta。
   - `extract_candidates(tool_name, content) -> list[Candidate]`：按工具名分派——s2/arxiv/openalex 解析 JSON 提 papers 列表（每项 id+元信息已结构化）；rag 正则从 format_context 串提 `rag:<doc_id>` + title + page。
   - `parse_refs(lean_answer) -> list[(ref_id, source_id, excerpt)]`：正则解析 `<ref id="N">[source_id] | 摘抄</ref>`（兼容 translation 模式的 `<zh>` 子标签，沿用 `markdown.js:105-132` 同款正则口径）。
   - `enrich_refs(lean_answer, enrichment_map) -> rich_answer`：把 lean ref 的 `[source_id]` 替换成完整引用串（type-aware：论文给 `作者. 标题. venue, year. [doi](doi)`，RAG 给 `标题, Page N`），保留摘抄与 `<zh>`。
4. **prompt 改 lean ref 格式**：
   - `citation_format.py`（CITATION_FORMAT 模块内容）：教 model ref 写 `<ref id="N">[source_id] | 摘抄</ref>`，附 source_id 前缀规则表（rag/s2/arxiv/openalex 各自怎么拼）；明确"来源元信息由系统按 id 自动填充，你只写 id + 摘抄，不要自己写作者/标题/venue/url"。
   - `plugins.py`：`CITATION_DEFAULT`/`CITATION_TRANSLATION` 适配 lean——translation 模式 `<zh>` 标签保留（译标题+摘抄），source_id 不翻译。`{citation_plugin}` 占位符注入机制不变（`builder.py:211-216`）。
5. **展示期 merge（后端 tree 接口，前端零改）**：
   - `routes.py` `/conversation/{id}/tree`：对 role=assistant 的消息，`load_enrichment_for_message(msg_id)` 拿 sidecar → `enrich_refs(content, map)` 转 rich 后返回。role=user 不动。
   - `graph.py` 流式 `chat_stream`/`regenerate_stream`：落库（`memory.add` + `save_candidates`）后、`done` 事件前，调 `enrich_refs` 把 `agent_msg_pure` 转 rich，`done` 的 `answer=rich`（前端覆盖后立即显示完整引用，不必等刷新）。非流式 `chat`/`regenerate` 是测试兜底路径，暂不 merge（返回 lean，测试不关心 rich）——plan 里标此边界。
6. **spec hiddenimports**（`physics_scholar.spec`）：加 `src.rag.citation`、`src.core.citation_store`（沿用 T0 模式，prompt 模块靠 pkgutil datas 扫描不用动）。

### Step 2 — 幻觉检测 bonus（Step 1 稳定后，同计划内）

7. `citation.py` 加 `detect_hallucination(refs, candidates) -> list[str]`：返回 `被引用集 - 候选集`（model 写了工具没返回的 source_id）。
8. `graph.py` 落库时调 `detect_hallucination`，命中的 source_id 记 warning 日志 + sidecar 标记（`is_cited` 字段已预留：候选集=0，被引用集=1，幻觉=查无匹配单独记）。信号接入暂仅日志/计数（不阻断回答），与 `harness_probe` 的 soft-violation 口径一致——后续可升等。

## 改动文件清单

| 文件 | 改动 |
|---|---|
| `src/rag/tools/rag_tool.py` | `format_context` 加 `rag:<doc_id>` source_id 可见性 |
| `src/core/init_SQLite.py` | 加 `ref_enrichment` 表（幂等 CREATE） |
| `src/core/citation_store.py`（新） | sidecar CRUD，复用 `get_conn()` |
| `src/rag/citation.py`（新） | Candidate/extract_candidates/parse_refs/enrich_refs/detect_hallucination |
| `src/rag/graph.py` | `_consume_events` 累积 tool_results；`chat_stream`/`regenerate_stream` 落库后写 sidecar + enrich done answer；非流式 chat/regenerate 落库后写 sidecar（不 enrich） |
| `src/rag/prompts/modules/shared/citation_format.py` | CITATION_FORMAT 改 lean ref 格式 + source_id 前缀规则 |
| `src/rag/prompts/plugins.py` | CITATION_DEFAULT/CITATION_TRANSLATION 适配 lean |
| `src/api/routes.py` | `/conversation/{id}/tree` 对 assistant 消息 enrich merge |
| `physics_scholar.spec` | hiddenimports 加 src.rag.citation / src.core.citation_store |

## 验证

**单测**（`tests/test_citation.py` 新建，purpose-built 不依赖旧漂移测试）：
- `extract_candidates`：对 s2/arxiv/openalex/rag 各一份真实返回样例，断言提取的 source_id + 元信息正确。
- `parse_refs`：对 lean answer（含/不含 translation `<zh>`）断言解析出 (ref_id, source_id, excerpt)。
- `enrich_refs`：lean → rich，断言 source_id 被替换成完整引用、摘抄保留、`<zh>` 保留。
- `detect_hallucination`：候选集 + 被引用集（含一个编造 id）断言返回编造 id。

**端到端**（dev server，真实 LLM）：
- 跑一个会触发 RAG + s2 的问答，查 `ref_enrichment` 表有候选行（source_id 带前缀、元信息齐全）。
- 前端看 answer 的 References 区域显示**完整引用 + 可点 url**（rich），不是裸 `[s2:xxx]`。
- 流式期间显示 lean（短暂），`done` 覆盖后立即 rich，刷新页面（tree 接口）仍 rich——三处一致。

**零行为变化验证**：
- 无引用的纯文本回答：`parse_refs` 返回空、`enrich_refs` no-op、sidecar 不写——回答正常显示。
- translation 模式开关：`<zh>` 标签正常渲染（`markdown.js` 不改）。

**frozen 端到端**：`python scripts/build_release.py` 后跑端到端，确认 spec hiddenimport 生效（`citation`/`citation_store` 正常 import，无 ModuleNotFoundError）。

**cross-machine 提醒**（CLAUDE.md「Closing a session」）：本计划纯代码改动，无 `data/` 依赖；sidecar 表靠 `CREATE TABLE IF NOT EXISTS` 幂等建，换机首跑自动建表，无迁移负担。
