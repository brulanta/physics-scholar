# scripts/eval_retrieval.py
#
# 召回评测脚本（纯开发工具，非自动测试，不打包进 exe）。
#
# 目的：分层量化 RAG 召回改动的增益。每层各跑 Recall@K / MRR：
#   baseline = 现有向量检索（旧字符切片，含 bug）
#   A        = 修复切片后的向量检索
#   B        = A + 多路召回（Part 2 落地后接入）
#   C        = B + 重排（Part 3 落地后接入）
# 本版先交付 baseline / A 脚手架；B/C 留好接入点。
#
# 设计要点：
# - 双 collection：eval_baseline（旧字符切片）/ eval_fixed（新 token 切片），
#   独立于生产 collection（rag_langchain），user_id="eval"。
# - 跨 collection 可比：测试集从 eval_fixed 合成，命中判定只看 doc_id + 文本重叠
#   （非 chunk_index），故两个切片边界不同的库可比。
# - 测试集用主 LLM 合成（RPM≈5，节流 + tenacity 重试 + 空/截断兜底），落盘可断点续跑。
#
# 用法：
#   python scripts/eval_retrieval.py                  # 入库(跳过已存在) + 合成/复用测试集 + 跑 baseline/A
#   python scripts/eval_retrieval.py --reingest       # 强制重建两个 eval collection（重算嵌入，耗额度）
#   python scripts/eval_retrieval.py --rebuild-testset# 重新合成测试集
#   python scripts/eval_retrieval.py --queries 20     # 目标测试集题数（默认 20）
#
# 依赖：需配好 EMBEDDING_*（入库/检索）与 MAIN_LLM_*（合成测试集）凭证，且 data/pdfs/ 有论文。

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_chroma import Chroma  # noqa: E402
from langchain_core.documents import Document  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # noqa: E402
from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402
from tenacity import retry, stop_after_attempt, wait_exponential  # noqa: E402

from src.config import (  # noqa: E402
    CHROMA_DIR,
    DEEPSEEK_EXTRA_BODY,
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_MODEL,
    MAIN_LLM_API_KEY,
    MAIN_LLM_BASE_URL,
    MAIN_LLM_MODEL,
    PDF_DIR,
)
from src import config  # noqa: E402
from src.core import hash_file, parser  # noqa: E402
from src.core.chunker import _CJK, _WORD  # noqa: E402
from src.core.chunker import chunker as new_chunker  # noqa: E402
from src.rag.tools.rag_tool import _rerank  # noqa: E402  复用生产重排，保证各层跑同一份代码

OUT_DIR = Path(__file__).resolve().parent / "eval_out"
EVAL_USER = "eval"
K_LIST = [1, 3, 5, 10]
TOPK = max(K_LIST)
OVERLAP_THRESHOLD = 0.6  # 检索片段对 gold 答案句 token 的覆盖率（按 gold 归一）达此比例即判命中
SPAN_VERBATIM_MIN = 0.6  # 合成时校验：LLM 摘录的答案句须有此比例 token 真出自源片段，否则视为改写丢弃
LLM_INTERVAL = 13.0  # 秒，压在 RPM≈5 内

COLL_BASELINE = "eval_baseline"
COLL_FIXED = "eval_fixed"

# 命中判定的过滤条件（与生产 build_filter 同构）
_EVAL_FILTER = {"$and": [{"user_id": EVAL_USER}, {"section": "body"}]}


# ── 旧切片器（精确复刻 bug 版，仅本脚本用，保证生产 chunker 干净升级）─────
def _legacy_chunker(text: str):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=350,
        chunk_overlap=55,
        separators=["。", ".", "！", "!", "？", "?", "，", ",", "\n", " "],
    )
    return splitter.split_text(text)


# ── ChromaDB ───────────────────────────────────────────────
_emb = None


def _get_emb():
    global _emb
    if _emb is None:
        _emb = OpenAIEmbeddings(
            api_key=EMBEDDING_API_KEY,
            base_url=EMBEDDING_BASE_URL,
            model=EMBEDDING_MODEL,
        )
    return _emb


def _get_collection(name: str) -> Chroma:
    return Chroma(
        collection_name=name,
        embedding_function=_get_emb(),
        persist_directory=str(CHROMA_DIR),
        collection_metadata={"hnsw:space": "cosine"},
    )


def ingest_into(collection_name: str, chunk_fn, reingest: bool) -> Chroma:
    vs = _get_collection(collection_name)
    count = vs._collection.count()
    if count and not reingest:
        print(f"  • {collection_name}: 已有 {count} 条，跳过入库（--reingest 可强制重建）")
        return vs
    if count:
        vs._collection.delete(where={"user_id": EVAL_USER})
        print(f"  • {collection_name}: 已清空旧数据")

    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"❌ {PDF_DIR} 下没有 PDF。")
        sys.exit(1)

    total = 0
    for pdf in pdfs:
        data = pdf.read_bytes()
        doc_id = hash_file.get_pdf_hash(data)
        try:
            blocks = parser.parse_pdf(str(pdf))
        except Exception as e:
            print(f"    ⚠ 解析失败，跳过 {pdf.name}: {e}")
            continue
        chunks = chunk_fn(blocks.get("body", ""))
        if not chunks:
            continue
        docs = [
            Document(
                page_content=c,
                metadata={
                    "doc_id": doc_id,
                    "user_id": EVAL_USER,
                    "title": pdf.name,
                    "section": "body",
                    "chunk_index": i,
                },
            )
            for i, c in enumerate(chunks)
        ]
        ids = [f"{doc_id}_{i}" for i in range(len(docs))]
        vs.add_documents(documents=docs, ids=ids)
        total += len(docs)
        print(f"    ✓ {pdf.name}: {len(docs)} 片")
    print(f"  • {collection_name}: 入库 {total} 片")
    return vs


# ── 测试集合成（主 LLM）─────────────────────────────────────
_SYNTH_PROMPT = (
    "你是论文检索测试集构造助手。阅读下面的论文片段，完成两件事：\n"
    "1. 提出一个**具体的、该片段能直接回答**的问题（与片段同语言；不要照抄原句，用提问方式）。\n"
    "2. 从片段中**逐字原样摘录** 1 句最能回答该问题的原文（必须是片段里出现过的连续原句，"
    "不要改写、不要合并多句、不要翻译）。\n"
    '只输出严格 JSON：{"question": "...", "answer": "..."}，不要任何额外文字或代码块标记。\n\n'
    "片段：\n{chunk}"
)


def _make_llm():
    return ChatOpenAI(
        model=MAIN_LLM_MODEL,
        api_key=MAIN_LLM_API_KEY,
        base_url=MAIN_LLM_BASE_URL,
        temperature=0.3,
        extra_body=DEEPSEEK_EXTRA_BODY,
        max_retries=1,  # 重试交给 tenacity 统一管
    )


SKIP_HEAD_CHUNKS = 2  # 跳过每篇开头 N 个 chunk（标题/作者/单位/摘要首句，元数据主导，不适合做 content gold）


def _collect_gold_chunks(vs_fixed: Chroma, target: int):
    """从 eval_fixed 取 body 片段作为 gold 来源。

    避开文档开头块（元数据主导、检索口径不公平），每篇在「中段」均匀取样，
    再跨文档凑够 target，保证多样性。
    """
    got = vs_fixed._collection.get(where=_EVAL_FILTER, include=["documents", "metadatas"])
    by_doc: dict[str, list[dict]] = {}
    for text, meta in zip(got["documents"], got["metadatas"]):
        if len(text) < 200:  # 太短的片段难以提出有意义问题
            continue
        by_doc.setdefault(meta["doc_id"], []).append({"text": text, "meta": meta})
    if not by_doc:
        return []

    doc_ids = sorted(by_doc.keys())
    # 每篇按 chunk_index 排序后跳过开头块，再在剩余片段里均匀取 per_doc 个
    per_doc = max(1, -(-target // len(doc_ids)))  # ceil
    by_doc_picks: dict[str, list[dict]] = {}
    for d in doc_ids:
        items = sorted(by_doc[d], key=lambda x: x["meta"].get("chunk_index", 0))
        body = items[SKIP_HEAD_CHUNKS:] or items  # 文档太短则退回全部
        if len(body) <= per_doc:
            by_doc_picks[d] = list(body)
        else:
            step = len(body) / per_doc
            by_doc_picks[d] = [body[int(i * step)] for i in range(per_doc)]

    # 跨文档轮转展平，凑够 target（保持各篇均衡）
    picked = []
    cursors = {d: 0 for d in doc_ids}
    while len(picked) < target:
        progressed = False
        for d in doc_ids:
            if cursors[d] < len(by_doc_picks[d]):
                picked.append(by_doc_picks[d][cursors[d]])
                cursors[d] += 1
                progressed = True
                if len(picked) >= target:
                    break
        if not progressed:
            break
    return picked


def _parse_qa(raw: str) -> tuple[str, str]:
    """解析 LLM 的 {"question","answer"} 输出；JSON 优先，失败再正则兜底。"""
    raw = (raw or "").strip()
    obj = None
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
        except Exception:
            obj = None
    if obj is None:  # 正则兜底
        mq = re.search(r'"question"\s*:\s*"(.*?)"\s*,', raw, re.S)
        ma = re.search(r'"answer"\s*:\s*"(.*?)"\s*}', raw, re.S)
        if mq and ma:
            obj = {"question": mq.group(1), "answer": ma.group(1)}
    if not obj:
        raise ValueError(f"无法解析 JSON: {raw[:120]!r}")
    q = (obj.get("question") or "").strip()
    a = (obj.get("answer") or "").strip()
    if len(q) < 5 or len(a) < 5:
        raise ValueError(f"q/a 过短: q={q!r} a={a!r}")
    return q, a


def build_testset(vs_fixed: Chroma, target: int, rebuild: bool):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "testset.json"
    existing = []
    if path.exists() and not rebuild:
        existing = json.loads(path.read_text(encoding="utf-8"))
        # 旧版 gold 为整块（无 source_chunk 字段），与新版答案句口径不兼容，强制重建
        if existing and any("source_chunk" not in it for it in existing):
            print("  • 检测到旧版测试集（gold 为整块），改用答案句口径，强制重建")
            existing = []
        elif len(existing) >= target:
            print(f"  • 复用已有测试集 {len(existing)} 题（--rebuild-testset 可重建）")
            return existing[:target]
        elif existing:
            print(f"  • 已有 {len(existing)} 题，继续补到 {target}")

    golds = _collect_gold_chunks(vs_fixed, target)
    if not golds:
        print("❌ 无可用 gold 片段（库为空或片段过短）。")
        sys.exit(1)

    llm = _make_llm()

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, max=30))
    def synth(chunk_text: str) -> tuple[str, str]:
        # 注意：_SYNTH_PROMPT 内含 JSON 示例字面量 {"question":...}，不能用 str.format
        # （会把示例的花括号当成替换字段触发 KeyError），故用 replace 只替 {chunk} 占位。
        prompt = _SYNTH_PROMPT.replace("{chunk}", chunk_text[:1500])
        resp = llm.invoke([HumanMessage(content=prompt)])
        return _parse_qa(resp.content)

    testset = list(existing)
    seen = {(it["doc_id"], it["source_chunk"]) for it in testset}
    for i, g in enumerate(golds, 1):
        key = (g["meta"]["doc_id"], g["text"])
        if key in seen:
            continue
        try:
            q, ans = synth(g["text"])
        except Exception as e:
            print(f"    ⚠ [{i}/{len(golds)}] 合成失败，跳过: {e}")
            continue
        # 校验答案句确实逐字出自源片段（防 LLM 改写/翻译导致 token 重叠失真）
        src, gold = _token_set(g["text"]), _token_set(ans)
        if not gold or len(gold & src) / len(gold) < SPAN_VERBATIM_MIN:
            print(f"    ⚠ [{i}/{len(golds)}] 答案句非原文摘录，跳过: {ans[:40]!r}")
            continue
        testset.append(
            {
                "query": q,
                "doc_id": g["meta"]["doc_id"],
                "title": g["meta"].get("title", ""),
                "gold_text": ans,          # 答案句（最小命中单元）
                "source_chunk": g["text"],  # 出处 chunk（调试/复现用）
            }
        )
        seen.add(key)
        path.write_text(json.dumps(testset, ensure_ascii=False, indent=2), encoding="utf-8")  # 落盘续跑
        print(f"    ✓ [{len(testset)}/{target}] {q[:50]}")
        if len(testset) >= target:
            break
        time.sleep(LLM_INTERVAL)
    return testset[:target]


# ── 命中判定与指标 ─────────────────────────────────────────
def _token_set(text: str) -> set:
    low = text.lower()
    return set(_WORD.findall(low)) | set(_CJK.findall(low))


def is_relevant(retrieved_text: str, gold_text: str, doc_id_r: str, doc_id_g: str) -> bool:
    """命中 = 同 doc 且检索片段覆盖了 gold 答案句的足够 token（按 gold 归一，跨粒度对称）。

    gold_text 现为最小答案句：覆盖率 = |检索 ∩ gold| / |gold|，不再用 min 归一，
    故小 chunk 不能再靠"自身碎片几乎被大 gold 包含"虚高。
    """
    if doc_id_r != doc_id_g:
        return False
    a, b = _token_set(retrieved_text), _token_set(gold_text)
    if not a or not b:
        return False
    inter = len(a & b)
    return inter / len(b) >= OVERLAP_THRESHOLD


def eval_layer(name: str, retrieve_fn, testset: list) -> dict:
    recall = {k: 0 for k in K_LIST}
    mrr_sum = 0.0
    for item in testset:
        docs = retrieve_fn(item["query"], TOPK)
        first_rel = None
        for rank, d in enumerate(docs, 1):
            if is_relevant(d.page_content, item["gold_text"], d.metadata.get("doc_id"), item["doc_id"]):
                first_rel = rank
                break
        for k in K_LIST:
            if first_rel and first_rel <= k:
                recall[k] += 1
        mrr_sum += (1.0 / first_rel) if first_rel else 0.0
    n = len(testset)
    return {
        "n": n,
        "recall_at_k": {str(k): round(recall[k] / n, 4) for k in K_LIST},
        "mrr": round(mrr_sum / n, 4),
    }


def vector_retrieve(vs: Chroma):
    def fn(query: str, topk: int):
        return vs.similarity_search(query, k=topk, filter=_EVAL_FILTER)

    return fn


def vector_rerank_retrieve(vs: Chroma):
    """稠密过取 RAG_FETCH_MULTIPLIER*topk 候选 → 生产 _rerank 精排到 topk。

    与生产 rag_tool 同一过取/重排逻辑（候选池越大重排天花板越高，见 probe_rerank.py）。
    """
    def fn(query: str, topk: int):
        fetch_k = max(topk, config.RAG_FETCH_MULTIPLIER * topk)
        cands = vs.similarity_search(query, k=fetch_k, filter=_EVAL_FILTER)
        return _rerank(query, cands, topk)

    return fn


# ── 只读抽检：把"分数"背后的真实内容打印出来，定位 H1(评测器)/H2(检索)──────
def _overlap_score(retrieved_text: str, gold_text: str):
    """返回 (inter, |retrieved set|, |gold set|, inter/|gold|)；与 is_relevant 同口径（gold 覆盖率）。"""
    a, b = _token_set(retrieved_text), _token_set(gold_text)
    if not a or not b:
        return 0, len(a), len(b), 0.0
    inter = len(a & b)
    return inter, len(a), len(b), inter / len(b)


def _chunk_len_stats(vs: Chroma):
    got = vs._collection.get(where=_EVAL_FILTER, include=["documents"])
    lens = sorted(len(t) for t in got["documents"])
    if not lens:
        return (0, 0, 0, 0)
    n = len(lens)
    median = lens[n // 2]
    return (n, lens[0], median, lens[-1])


def debug_cases(vs_baseline: Chroma, vs_fixed: Chroma, testset: list, n: int):
    a_retrieve = vector_retrieve(vs_fixed)
    b_retrieve = vector_retrieve(vs_baseline)

    for vs, label in ((vs_fixed, "eval_fixed"), (vs_baseline, "eval_baseline")):
        cnt, mn, med, mx = _chunk_len_stats(vs)
        print(f"\n[{label}] chunk 字符长度  n={cnt}  min={mn}  median={med}  max={mx}")

    for qi, item in enumerate(testset[:n], 1):
        gold = item["gold_text"]
        src = item.get("source_chunk", "")
        g_set = _token_set(gold)
        print("\n" + "=" * 78)
        print(f"Q{qi}  doc_id={item['doc_id'][:12]}  gold_chars={len(gold)}  gold_tokens={len(g_set)}")
        print(f"  query: {item['query']}")
        print(f"  gold(答案句): {gold[:120].replace(chr(10), ' ')}")

        def _report(docs, layer):
            # 命中 = 检索片段覆盖 gold 答案句；rank = 第一个命中的名次
            hit_rank = next(
                (r for r, d in enumerate(docs, 1)
                 if is_relevant(d.page_content, gold, d.metadata.get("doc_id"), item["doc_id"])),
                None,
            )
            print(f"\n  [{layer}] 覆盖 gold 答案句的首个命中名次(top-{TOPK}): "
                  f"{hit_rank if hit_rank else '未命中'}")
            for r, d in enumerate(docs[:5], 1):
                inter, na, nb, score = _overlap_score(d.page_content, gold)
                same = d.metadata.get("doc_id") == item["doc_id"]
                is_src = d.page_content == src
                hit = is_relevant(d.page_content, gold, d.metadata.get("doc_id"), item["doc_id"])
                print(f"    {r}. doc{'✓' if same else '✗'} "
                      f"{'[SRC]' if is_src else '     '} "
                      f"inter={inter} |ret|={na} cover={score:.2f} hit={hit}")
                print(f"        {d.page_content[:100].replace(chr(10), ' ')}")

        _report(a_retrieve(item["query"], TOPK), "A / eval_fixed")
        _report(b_retrieve(item["query"], TOPK), "baseline / eval_baseline")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reingest", action="store_true", help="强制重建两个 eval collection")
    ap.add_argument("--rebuild-testset", action="store_true", help="重新合成测试集")
    ap.add_argument("--queries", type=int, default=20, help="测试集目标题数（默认 20）")
    ap.add_argument("--debug-cases", type=int, default=0,
                    help="只读抽检前 N 题：打印 gold/检索内容与重叠分数，不跑分层评测")
    args = ap.parse_args()

    if not EMBEDDING_API_KEY:
        print("❌ EMBEDDING_API_KEY 为空，无法入库/检索。")
        sys.exit(1)

    print("\n========== 1) 入库（双 collection）==========")
    vs_baseline = ingest_into(COLL_BASELINE, _legacy_chunker, args.reingest)
    vs_fixed = ingest_into(COLL_FIXED, new_chunker, args.reingest)

    n_docs = len({m["doc_id"] for m in vs_fixed._collection.get(where=_EVAL_FILTER, include=["metadatas"])["metadatas"]})
    if n_docs < 3:
        print(f"  ⚠ 当前仅 {n_docs} 篇论文，评测偏弱；建议多放几篇中英文论文再跑。")

    print("\n========== 2) 合成/复用测试集 ==========")
    if not MAIN_LLM_API_KEY:
        print("❌ MAIN_LLM_API_KEY 为空，无法合成测试集。")
        sys.exit(1)
    testset = build_testset(vs_fixed, args.queries, args.rebuild_testset)
    print(f"  测试集共 {len(testset)} 题")

    if args.debug_cases:
        print(f"\n========== 抽检（前 {args.debug_cases} 题，只读，不跑评测）==========")
        debug_cases(vs_baseline, vs_fixed, testset, args.debug_cases)
        return

    print("\n========== 3) 分层评测 ==========")
    layers = {
        "baseline": vector_retrieve(vs_baseline),       # 旧切片 + 纯向量（测量偏置，见 plan，仅纵向参考）
        "A": vector_retrieve(vs_fixed),                 # 新切片 + 纯向量
        "A+rerank": vector_rerank_retrieve(vs_fixed),   # 新切片 + 稠密过取 + cross-encoder 重排（Part 3）
        # "B": Part 2 落地后接入 hybrid_search(vs_fixed, ...)
        # "C": Part 2+3 落地后接入 hybrid_search + _rerank
    }
    summary = {}
    for name, fn in layers.items():
        summary[name] = eval_layer(name, fn, testset)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"results_{name}.json").write_text(
            json.dumps(summary[name], ensure_ascii=False, indent=2), encoding="utf-8"
        )

    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n========== 结果（层 × 指标）==========")
    header = "layer".ljust(10) + "".join(f"R@{k}".ljust(9) for k in K_LIST) + "MRR"
    print(header)
    print("-" * len(header))
    for name, m in summary.items():
        row = name.ljust(10) + "".join(str(m["recall_at_k"][str(k)]).ljust(9) for k in K_LIST)
        row += str(m["mrr"])
        print(row)
    print(f"\n（n={len(testset)} 题，方向性参考，非统计严谨。结果写入 {OUT_DIR}）")


if __name__ == "__main__":
    main()
