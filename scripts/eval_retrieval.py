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
from src.core import hash_file, parser  # noqa: E402
from src.core.chunker import _CJK, _WORD  # noqa: E402
from src.core.chunker import chunker as new_chunker  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "eval_out"
EVAL_USER = "eval"
K_LIST = [1, 3, 5, 10]
TOPK = max(K_LIST)
OVERLAP_THRESHOLD = 0.5  # gold 与检索片段 token 重叠（按较小集合归一）达此比例即判命中
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
    "你是论文检索测试集构造助手。根据下面的论文片段，提出一个**具体的、该片段能直接回答**的问题。\n"
    "要求：① 问题使用与片段相同的语言（中文片段用中文，英文片段用英文）；"
    "② 不要照抄原句，用提问的方式；③ 只输出问题本身，不要任何解释或前后缀。\n\n"
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


def _collect_gold_chunks(vs_fixed: Chroma, target: int):
    """从 eval_fixed 取 body 片段，按文档轮转挑选足够长的片段作为 gold。"""
    got = vs_fixed._collection.get(where=_EVAL_FILTER, include=["documents", "metadatas"])
    by_doc: dict[str, list[dict]] = {}
    for text, meta in zip(got["documents"], got["metadatas"]):
        if len(text) < 200:  # 太短的片段难以提出有意义问题
            continue
        by_doc.setdefault(meta["doc_id"], []).append({"text": text, "meta": meta})
    if not by_doc:
        return []

    # 每篇内按 chunk_index 排序后均匀抽样，再跨文档轮转，凑够 target
    for items in by_doc.values():
        items.sort(key=lambda x: x["meta"].get("chunk_index", 0))
    picked = []
    doc_ids = sorted(by_doc.keys())
    cursors = {d: 0 for d in doc_ids}
    while len(picked) < target:
        progressed = False
        for d in doc_ids:
            items = by_doc[d]
            if cursors[d] >= len(items):
                continue
            # 均匀跨度抽样
            step = max(1, len(items) // max(1, target // len(doc_ids) + 1))
            idx = cursors[d]
            picked.append(items[idx])
            cursors[d] += step
            progressed = True
            if len(picked) >= target:
                break
        if not progressed:
            break
    return picked


def build_testset(vs_fixed: Chroma, target: int, rebuild: bool):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "testset.json"
    existing = []
    if path.exists() and not rebuild:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if len(existing) >= target:
            print(f"  • 复用已有测试集 {len(existing)} 题（--rebuild-testset 可重建）")
            return existing[:target]
        print(f"  • 已有 {len(existing)} 题，继续补到 {target}")

    golds = _collect_gold_chunks(vs_fixed, target)
    if not golds:
        print("❌ 无可用 gold 片段（库为空或片段过短）。")
        sys.exit(1)

    llm = _make_llm()

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, max=30))
    def synth(chunk_text: str) -> str:
        resp = llm.invoke([HumanMessage(content=_SYNTH_PROMPT.format(chunk=chunk_text[:1500]))])
        q = (resp.content or "").strip()
        q = re.sub(r"^[\s\-*0-9.、)）:：]+", "", q).strip()  # 去除可能的列表/前缀符号
        if len(q) < 5:
            raise ValueError(f"空/过短回复: {q!r}")
        return q

    testset = list(existing)
    seen = {(it["doc_id"], it["gold_text"]) for it in testset}
    for i, g in enumerate(golds, 1):
        key = (g["meta"]["doc_id"], g["text"])
        if key in seen:
            continue
        try:
            q = synth(g["text"])
        except Exception as e:
            print(f"    ⚠ [{i}/{len(golds)}] 合成失败，跳过: {e}")
            continue
        testset.append(
            {
                "query": q,
                "doc_id": g["meta"]["doc_id"],
                "title": g["meta"].get("title", ""),
                "gold_text": g["text"],
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
    if doc_id_r != doc_id_g:
        return False
    a, b = _token_set(retrieved_text), _token_set(gold_text)
    if not a or not b:
        return False
    inter = len(a & b)
    return inter / min(len(a), len(b)) >= OVERLAP_THRESHOLD


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reingest", action="store_true", help="强制重建两个 eval collection")
    ap.add_argument("--rebuild-testset", action="store_true", help="重新合成测试集")
    ap.add_argument("--queries", type=int, default=20, help="测试集目标题数（默认 20）")
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

    print("\n========== 3) 分层评测 ==========")
    layers = {
        "baseline": vector_retrieve(vs_baseline),  # 旧切片 + 纯向量
        "A": vector_retrieve(vs_fixed),            # 新切片 + 纯向量
        # "B": Part 2 落地后接入 hybrid_search(vs_fixed, ...)
        # "C": Part 3 落地后接入 hybrid_search + _rerank
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
