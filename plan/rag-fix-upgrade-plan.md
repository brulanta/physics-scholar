# RAG 工具修复与升级计划

## Context（为什么做这件事）

PhysicsScholar 的本地 RAG 召回链存在一个入库期 bug，并缺少现代召回所需的两个环节：

1. **切片 bug**：`src/core/chunker.py` 的 `RecursiveCharacterTextSplitter` 未传 `length_function`，默认按字符 `len()` 计长。当前 `chunk_size=350` 字符对中英文意味着差异巨大的语义容量（英文 ~90 token、中文 ~230 token），导致英文切片承载内容远小于中文，召回质量不均衡。
2. **召回单一**：当前 `rag_tool` 仅纯向量余弦检索，缺少全文/关键词通道（学术检索里精确术语、公式名、缩写很依赖字面命中）。
3. **无重排**：候选直接返回 LLM，未用 cross-encoder 精排。

目标产出：修复切片计长（改为**按 token 语义容量、中英文分路线**），引入**多路召回（向量 + BM25，RRF 融合）**，加入 **bge-reranker-v2-m3 重排**，并提供一个**可分层对比的独立召回评测脚本**，用 Recall@K / MRR 量化每一步的增益（baseline → A → B → C）。

关键约束（已核实）：项目是**离线优先**的 PyInstaller exe，`physics_scholar.spec` 刻意 `excludes` 了 `torch/transformers/tokenizers/sentence_transformers` 等；`tiktoken` 已打包但其 BPE 词表是首次使用时联网下载（离线首跑有风险）；`numpy` 已打包；嵌入与重排同源走硅基流动 API（`EMBEDDING_API_KEY` / `https://api.siliconflow.cn/v1`）。

已确认的决策：
- 切片 length_function = **校准的字符/词数公式**（零依赖、离线安全；用免费 bge-m3 的 `usage.prompt_tokens` 作 ground-truth 拟合系数）。
- BM25 档位 = **rank_bm25 + 轻量分词**（英文 `\w+`，中文字二元组），不引 jieba。
- 重排 = 硅基流动 **BAAI/bge-reranker-v2-m3**（`/rerank` 端点），**与 embedding 同一套开放逻辑**：url/model 定死、**复用 `EMBEDDING_API_KEY`**（已据官方文档确认硅基流动同一 key 可调 `/rerank`），**不新增前端配置**；`top_n`/超时等参数走 .env/硬编码默认。
- 多路召回与重排的开关/调参（`RAG_HYBRID_ENABLED`、`RAG_FETCH_MULTIPLIER`、`RERANK_*`）一律 **纯开发态**（只读 .env > 硬编码默认，**不进 yaml、不暴露前端、不进 `reload_config`**），与 chunker 配置同一定位。
- 评测 = **分层对比 + 文本重叠判定，~15–25 题**，独立放 `scripts/eval_retrieval.py`，不并入 `eval_framework`。

---

## Part 1 — 切片 bug 修复 + 中英文分路线 + 校准计长

**改动文件**：`src/core/chunker.py`，新增 `scripts/calibrate_tokenizer.py`，`src/config.py`（+ `reload_config`）。

### 1.1 校准计长公式（零依赖、离线）
单一线性公式天然兼容中英文混排（英文文档 cjk≈0，中文文档 words≈很小）：

```python
import re
_CJK  = re.compile(r"[一-鿿]")      # 复用 registry.smart_match 已有的 CJK 区间
_WORD = re.compile(r"[a-zA-Z0-9]+")

def estimate_tokens(text: str) -> int:
    cjk   = len(_CJK.findall(text))
    words = len(_WORD.findall(text))
    return int(CALIB_A * cjk + CALIB_B * words + CALIB_C)  # 系数来自 config
```

### 1.2 语言识别 + 分路线切片（签名不变，`chunker(text)` 调用方无需改）

> 分路线**只作用于 size/overlap**（让中英文语义容量相近）；分隔符中英文合并为一套统一列表、按颗粒度由粗到细排序。

```python
def is_chinese(text: str, threshold: float = 0.10) -> bool:
    if not text: return False
    return len(_CJK.findall(text)) / max(len(text), 1) >= threshold

# 统一分隔符，颗粒度由粗到细：段落 → 行 → 句 → 子句 → 词 → 字符。
# 原列表把句号 "。/." 置于 "\n" 之前，方向反了（先切句再切段），是 bug，此处纠正。
# 末尾 "" 允许硬切超长无分隔片段（原列表缺失，潜在 bug）。中英文分隔符合并为一套即可。
_SEPARATORS = [
    "\n\n", "\n",
    "。", ".",
    "！", "!",
    "？", "?",
    "；", ";",
    "，", ",",
    " ",
    "",
]

def chunker(blocks_str: str) -> list[str]:
    if not blocks_str or not blocks_str.strip():
        return []                                   # 当前对空 reference 也会建 splitter，顺手加空判
    zh = is_chinese(blocks_str)                     # 语言识别仅用于选 size/overlap（语义容量），分隔符统一
    splitter = RecursiveCharacterTextSplitter(
        chunk_size   = CHUNK_SIZE_ZH if zh else CHUNK_SIZE_EN,
        chunk_overlap= CHUNK_OVERLAP_ZH if zh else CHUNK_OVERLAP_EN,
        separators   = _SEPARATORS,
        length_function = estimate_tokens,
    )
    return splitter.split_text(blocks_str)
```

**暂定默认（token 语义容量，留待召回测试 bp 微调）**：中英文统一目标约 `size=384 / overlap=76`，分别可配。因为计长已归一到 token，单一预算即可让两种语言语义容量相近；仍保留中英文独立配置以便单独调。

### 1.3 校准脚本 `scripts/calibrate_tokenizer.py`（一次性，纯开发工具，不打包进 exe）
- 仿 `scripts/verify_rag.py` 的 bootstrap（`sys.path` 插入后从 `src` import）。
- 解析 `data/pdfs/` 下论文，按句子累积成不同长度文本段（中英文混合、覆盖长度谱）。
- 每段算特征 `(cjk, words)`，调 bge-m3 嵌入 API 取真实 `usage.prompt_tokens`（已确认硅基流动 embedding 返回该字段；RPM 2000 充裕）。
- `numpy.linalg.lstsq` 拟合 `tokens ≈ a·cjk + b·words + c`，报告 R²/平均绝对误差。
- **只打印**拟合结果与可粘贴到 `src/config.py` 出厂默认的片段（不写 yaml、不写 .env）。

### 1.4 配置定位：chunker 为纯开发态、出厂硬编码（按用户对齐修订）
- chunker.* **不进 yaml、不暴露前端**（yaml 是前端配置源，这些是内部调参）。读取顺序为 **.env（开发期临时 bp 调参）> `src/config.py` 硬编码出厂默认**，不进 `reload_config`。
- 出厂默认系数当前仅为经验占位（中文 ≈1.05 token/字、英文 ≈1.3 token/词），`CHUNK_CALIBRATED` 默认 `False`，`chunker.py` 未校准时打一次性 WARNING。
- 流程：**打包分发前**跑一次 `calibrate_tokenizer.py`，确认 R² 达标后把 A/B/C 硬编码进 config 出厂默认、并将 `CHUNK_CALIBRATED` fallback 改为 `True`，提交。此后生产用户不接触这些参数。

---

## Part 2 — 多路召回（向量 + BM25，RRF 融合）

**改动文件**：`src/rag/tools/rag_tool.py`（抽出纯函数供 eval 复用），`src/config.py`，`requirements.txt` + `physics_scholar.spec`（加 `rank_bm25`）。

### 2.1 轻量分词（中文字二元组，英文按词；零额外打包）
```python
def _bm25_tokenize(text: str) -> list[str]:
    low = text.lower()
    words = _WORD.findall(low)
    cjk_runs = _CJK.findall(low)                       # 单字列表
    bigrams = [cjk_runs[i]+cjk_runs[i+1] for i in range(len(cjk_runs)-1)]
    return words + cjk_runs + bigrams                  # 字 + 二元组，兼顾召回与精度
```

### 2.2 抽出纯检索函数（eval 与 rag_tool 共用同一份代码）
```python
from rank_bm25 import BM25Okapi

def hybrid_search(vs, query, *, user_id, section, doc_id="", k=5, fetch_k=20):
    flt = build_filter(user_id, section, doc_id)                 # 复用现有多租户过滤
    vec_docs = vs.similarity_search(query, k=fetch_k, filter=flt)
    corpus = vs._collection.get(where=flt,
                                include=["documents", "metadatas"], limit=5000)  # 同过滤，保证租户隔离
    bm25 = BM25Okapi([_bm25_tokenize(d) for d in corpus["documents"]])
    scores = bm25.get_scores(_bm25_tokenize(query))
    top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:fetch_k]
    bm25_docs = [Document(page_content=corpus["documents"][i],
                          metadata=corpus["metadatas"][i]) for i in top]
    return _rrf_merge([vec_docs, bm25_docs])[:fetch_k]           # 去重 + 融合，按 chunk 唯一键
```

唯一键 = `(doc_id, section, chunk_index)`（与 chroma id `f"{doc_id}_{chunk_index}"` 对应，metadata 里都有）。

### 2.3 RRF 融合（无需校准不同分数量纲）
```python
def _rrf_merge(ranked_lists, c=60):
    score, keep = {}, {}
    for docs in ranked_lists:
        for rank, doc in enumerate(docs):
            key = (doc.metadata["doc_id"], doc.metadata["section"], doc.metadata["chunk_index"])
            score[key] = score.get(key, 0) + 1.0/(c + rank)
            keep[key]  = doc
    return [keep[key] for key in sorted(score, key=score.get, reverse=True)]
```

### 2.4 接入 `rag_tool`
- 保留模块级 `vs = get_vectorstore()` 单例（不要重建）。
- 工具对外签名 `rag_tool(query, k, section, doc_id)` **不变**（agent prompt 无需改）。
- `fetch_k = k * RAG_FETCH_MULTIPLIER`；`RAG_HYBRID_ENABLED=false` 时回退到当前纯向量路径（kill switch）。
- 性能：本地单用户库通常数百~数千 chunk，逐查询建 BM25 可接受；如增长可加按 filter 键的 LRU（入库时失效），列为可选优化。

---

## Part 3 — 重排（硅基流动 bge-reranker-v2-m3）

**改动文件**：`src/rag/tools/rag_tool.py`（加 `_rerank`），`src/config.py`（仅加纯开发态常量，**不进 `reload_config`**）。

**凭证复用（关键）**：重排**复用 embedding 的 url/key**，且在**调用时**直接引用 `config.EMBEDDING_BASE_URL` / `config.EMBEDDING_API_KEY`（而非 import 期快照），这样前端改了 embedding key、`reload_config` 刷新后重排自动跟随，无需把 key 纳入 reload。故**不新增** `RERANK_BASE_URL` / `RERANK_API_KEY` 常量。

```python
import requests                 # 已是依赖，无 torch
from src import config          # 调用时取 EMBEDDING_*，跟随 reload_config

def _rerank(query, docs, top_n):
    if not config.RERANK_ENABLED or not docs:
        return docs[:top_n]
    try:
        r = requests.post(
            f"{config.EMBEDDING_BASE_URL}/rerank",                 # 复用 embedding base_url
            json={"model": config.RERANK_MODEL, "query": query,
                  "documents": [d.page_content for d in docs],
                  "top_n": min(top_n, len(docs)), "return_documents": False},
            headers={"Authorization": f"Bearer {config.EMBEDDING_API_KEY}"},  # 复用 embedding key
            timeout=config.RERANK_TIMEOUT)
        r.raise_for_status()
        order = [it["index"] for it in r.json()["results"]]   # results 已按 relevance_score 降序
        return [docs[i] for i in order][:top_n]
    except Exception as e:
        logger.warning("[RAG] rerank 失败，回退融合顺序: %s", e)
        return docs[:top_n]                                   # 优雅降级，绝不抛进 agent
```

接入顺序（`rag_tool` 内）：`hybrid_search(...)` 过取候选 → `_rerank(query, 候选, k)` → `format_context`。

新增 config（**纯开发态：.env > 硬编码默认，不进 yaml/前端/`reload_config`**）：`RERANK_ENABLED=true`、`RERANK_MODEL="BAAI/bge-reranker-v2-m3"`、`RERANK_TIMEOUT=20`。（`top_n` 即工具的 `k`；候选池大小由 `RAG_FETCH_MULTIPLIER` 控。）

### Part 3 附：硅基流动 `/rerank` 契约（据官方文档核实，供压缩上下文后查阅）
- **端点**：`POST {EMBEDDING_BASE_URL}/rerank`（即 `https://api.siliconflow.cn/v1/rerank`）。
- **鉴权**：`Authorization: Bearer {EMBEDDING_API_KEY}`（与 embedding 同账号同 key，已确认可用）。
- **请求体**：`model`（必填，`BAAI/bge-reranker-v2-m3`）、`query`（必填，len≥1）、`documents`（必填，字符串数组，≥1 条）、`top_n`（返回条数，≥1）、`return_documents`（默认 `false`，置 false 只回 index）、`max_chunks_per_doc`（仅 bge-reranker-v2-m3 等支持，默认 1024）、`overlap_tokens`（0–80）。
- **响应体**：`{id, results: [{index, relevance_score, ...}], meta}`；`results` 已按相关度降序，用 `index` 回映射原 `documents` 顺序即可。

---

## Part 4 — 召回评测脚本 `scripts/eval_retrieval.py`（新建，独立）

仿 `verify_rag.py` bootstrap；产物写 `scripts/eval_out/`（`testset.json` / `results_<layer>.json` / `summary.json`）。**不并入 `eval_framework`**。

### 4.1 双 collection 解决"baseline 与修复后切片边界不同"
- `eval_baseline`：脚本内自带 `_legacy_chunker`（精确复刻当前 350/55 字符切片），保证生产 `chunker.py` 干净升级。
- `eval_fixed`：新 token 切片。A/B/C 三层都读 `eval_fixed`，仅算法不同。
- 直接 `Chroma(collection_name=...)` 建库（**不要**复用 `get_vectorstore()`，它锁死 `rag_langchain`）；`user_id="eval"`。入库一次后持久化，非空则跳过，除非 `--reingest`（再入库会消耗付费嵌入额度）。

### 4.2 跨 collection 可比性：文本重叠判定（非 chunk_index）
- 测试集从 `eval_fixed` 的 chunk 合成，但 baseline 的 `chunk_index` 不同 → 命中判定**只看文本**：检索片段 `doc_id` == gold `doc_id` 且与 gold 文本 token 重叠 ≥ 0.6（gold token 命中率）即记命中。两个 collection 因此可比。

### 4.3 测试集合成（main_llm，RPM≈5）
- 每篇文档从 `eval_fixed` 采 ~3–5 个 body chunk；让 main_llm 据该 chunk 出一个自然问题；记录 `(query, doc_id, gold_text)`。
- 健壮性：`tenacity` 指数退避（空回复 / 解析失败 / 429，封顶 ~4 次）+ `time.sleep(~13s)` 节流压在 5 RPM 内 + JSON/正则双解析兜底（仿 `jina_tool._score_chunk`）+ 空/垃圾跳过并记录。
- 立即落 `testset.json`（可断点续跑，重跑各层不重复合成）；规模 ~15–25 题，脚本头部注明"方向性参考，非统计严谨"。

### 4.4 各层跑同一份生产代码
- `baseline` = 纯向量 over `eval_baseline`
- `A` = 纯向量 over `eval_fixed`
- `B` = `hybrid_search(...)` over `eval_fixed`（复用 Part 2，不重排）
- `C` = `hybrid_search` + `_rerank`（Part 3），过取 `k*N` 再重排到 `k`
- 指标：Recall@{1,3,5,10} 与 MRR，按题平均 → `summary.json`（层 × 指标 表）。

---

## 配置与打包改动汇总

`src/config.py` 新增 `_get_typed`（支持 int/float/bool；`_get` 仅返回 str）。**全部新增常量均为纯开发态**：只读 .env > 硬编码出厂默认，**不进 yaml、不暴露前端、不进 `reload_config`**（与 chunker 同一定位，故 `reload_config` 无需新增任何项）。
- `chunker.*`（**已落地**）：`CHUNK_SIZE_ZH/EN`、`CHUNK_OVERLAP_ZH/EN`、`CHUNK_CALIB_A/B/C`、`CHUNK_CALIBRATED`。
- `rag.*`（Part 2）：`RAG_HYBRID_ENABLED`、`RAG_FETCH_MULTIPLIER`。
- `rerank.*`（Part 3）：`RERANK_ENABLED`、`RERANK_MODEL`、`RERANK_TIMEOUT`。**不设** `RERANK_BASE_URL` / `RERANK_API_KEY`——`_rerank` 调用时直接引用 `config.EMBEDDING_BASE_URL` / `config.EMBEDDING_API_KEY`，使前端改 embedding key 后（经 `reload_config` 刷新）重排自动跟随。

打包：`requirements.txt` 加 `rank_bm25==0.2.2`；`physics_scholar.spec` 的 `hiddenimports` 加 `rank_bm25`（numpy 已打包，无编译扩展，无需 datas）。**运行期代码（src/ 内）严禁 import torch/transformers/tokenizers/sentence_transformers**（spec 已 exclude，会导致 exe 崩溃）。

---

## 实施顺序

1. **Part 1**（切片修复 + 校准脚本）先落地——每一层评测数字都依赖切片。
2. **Part 4 脚手架**（双 collection 入库 + 测试集合成 + baseline/A）——先拿到 baseline 与 A，验证"修 bug 的增益"。
3. **Part 2**（混合检索）→ 跑 B。
4. **Part 3**（重排）→ 跑 C。
每步跑一遍评测，逐层确认增益归因。

---

## 进度 / 状态（截至当前会话，便于上下文压缩后续接）

- **Part 1 切片修复**：✅ 已实现并提交 `4107baf`；计划文档对齐提交 `14f34f5`。`chunker.py`/`config.py`/`calibrate_tokenizer.py`/测试均已落地，`CHUNK_CALIBRATED` 仍为 `False`（待打包前跑校准脚本拟合后硬编码）。
- **Part 4 评测脚手架（baseline/A）**：✅ 已实现并提交 `847caf2`（`scripts/eval_retrieval.py`，双 collection + LLM 合成测试集 + Recall@K/MRR）。用户正在本机配好凭证后跑 baseline/A 取数。
- **Part 2 多路召回**：🚧 进行中。**已做（未提交）**：`.gitignore` 加 `.claude/`；`requirements.txt`（UTF-16）加 `rank_bm25==0.2.2`。**待做**：`physics_scholar.spec` 的 `hiddenimports` 加 `'rank_bm25'`；`config.py` 加 `RAG_HYBRID_ENABLED`/`RAG_FETCH_MULTIPLIER`；`rag_tool.py` 加 `hybrid_search`/`_rrf_merge`/`_bm25_tokenize`、`vs` 改惰性、按开关回退；`eval_retrieval.py` 接入 `B` 层（复用生产 `hybrid_search`）；装 `rank_bm25` 后离线验证 RRF/分词；提交。
- **Part 3 重排**：⬜ 未开始（契约与配置定位已在上文敲定）。
- **注意**：`src/config.py` 有用户本地未提交改动（在 `reload_config` 补了 `OPENALEX_API_KEY`），属用户有意改动，勿回退；在其基础上叠加 Part 2 常量即可。

---

## 验证方式

- **单测**：扩展 `tests/test_parser_chunker.py`——验证 `chunker` 输出按 token 语义容量、中/英文分别走对 size/overlap、统一分隔符按粗→细生效、空输入返回 `[]`。
- **校准**：`python scripts/calibrate_tokenizer.py` 跑真实样本，检查 R²（应 >0.95）与平均误差；确认 `calibrated: true` 已写入。
- **召回评测**：`python scripts/eval_retrieval.py`，对照 `summary.json` 看 baseline→A→B→C 的 Recall@K/MRR 是否单调（B 若不优于 A，说明 BM25 未发挥作用，需查分词/融合）。
- **降级**：临时清空 `RERANK_API_KEY`，确认 `_rerank` 走 warning 回退、不抛错；`RAG_HYBRID_ENABLED=false` 确认回退纯向量。
- **端到端**：`uvicorn src.main:app --reload` 起服务，前端发问，确认 `rag_tool` 返回合理、日志无异常。

---

## 风险

- `reload_config()` 必须覆盖每个新常量——逐项重读，漏配会在保存配置后静默用旧值。
- tiktoken 首跑联网下载词表的坑——本方案选了校准字符/词数公式**已彻底规避**（运行期不依赖 tiktoken）。
- 自定义 BM25 风险靠选用 `rank_bm25` 成熟库规避；中文字二元组分词的检索效果由 B 层评测兜底验证。
- 重排/重入库消耗付费/限频 API：重排有 kill switch + 超时降级；eval 双 collection 入库加 `--reingest` 门控并持久化。
- 切片参数变更会改变 `chunk_count` 与 chroma id：**已入库旧文档不会自动重切**（可接受；评测用全新独立 collection 规避对比污染）。
