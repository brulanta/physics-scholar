# scripts/probe_rerank.py
#
# 一次性离线探针（非生产代码、不打包）：验证"重排能把 A 的召回抬多少"。
#
# 背景：二次根因定位发现 A 召回低的主因是「大 chunk 稀释 + hubness」——
# query 对孤立答案句强命中(dist≈0.225)，但对包含该句的 384-token 大 chunk 弱(>0.35)，
# 被 hub chunk 挤出 top-10。cross-encoder 重排直接对 (query, chunk) 打分，理应能救。
#
# 本探针：复用现有 eval_fixed collection + testset.json（不重入库、不重合成），
#   - 对每题稠密过取 FETCH 个候选；
#   - 调硅基流动 /rerank（与 Part 3 同契约：复用 EMBEDDING_BASE_URL/KEY）重排到 top-10；
#   - 对比 A(纯稠密) vs A+rerank 的 Recall@K / MRR；
#   - 额外报「候选池命中率」= 任一候选 is_relevant 的题占比 = 重排的天花板。
#     若该天花板 << 1，说明稠密过取没把 gold 捞进池子，重排无能为力 → 需 BM25/更小切片。
#
# 用法：python scripts/probe_rerank.py            # 默认 FETCH 档位 [10,20,50]
#       不动生产代码，不消耗嵌入额度以外的写操作。

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 让 import eval_retrieval 生效

import json  # noqa: E402

import requests  # noqa: E402
from langchain_chroma import Chroma  # noqa: E402
from langchain_openai import OpenAIEmbeddings  # noqa: E402

# 复用 eval 脚本的判定口径与常量，保证与分层评测同一把尺
from eval_retrieval import (  # noqa: E402
    COLL_FIXED,
    K_LIST,
    OUT_DIR,
    _EVAL_FILTER,
    is_relevant,
)
from src.config import (  # noqa: E402
    CHROMA_DIR,
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_MODEL,
)

RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_TIMEOUT = 30
FETCH_SETTINGS = [10, 20, 50]  # 候选池大小档位（稠密过取多少再重排）
TOPK = max(K_LIST)


def _get_vs() -> Chroma:
    emb = OpenAIEmbeddings(
        api_key=EMBEDDING_API_KEY, base_url=EMBEDDING_BASE_URL, model=EMBEDDING_MODEL
    )
    return Chroma(
        collection_name=COLL_FIXED,
        embedding_function=emb,
        persist_directory=str(CHROMA_DIR),
        collection_metadata={"hnsw:space": "cosine"},
    )


def _rerank(query: str, docs: list, top_n: int) -> list:
    """调硅基流动 /rerank，返回按相关度降序重排后的 docs（截到 top_n）。失败抛出。"""
    r = requests.post(
        f"{EMBEDDING_BASE_URL}/rerank",
        json={
            "model": RERANK_MODEL,
            "query": query,
            "documents": [d.page_content for d in docs],
            "top_n": min(top_n, len(docs)),
            "return_documents": False,
        },
        headers={"Authorization": f"Bearer {EMBEDDING_API_KEY}"},
        timeout=RERANK_TIMEOUT,
    )
    r.raise_for_status()
    order = [it["index"] for it in r.json()["results"]]
    return [docs[i] for i in order][:top_n]


def _hit_rank(docs: list, item: dict) -> int | None:
    """返回首个命中的名次(1-based)，无命中返回 None。"""
    for rank, d in enumerate(docs, 1):
        if is_relevant(d.page_content, item["gold_text"], d.metadata.get("doc_id"), item["doc_id"]):
            return rank
    return None


def _score(ranks: list) -> dict:
    """ranks: 每题首个命中名次(None=未命中)；算 Recall@K / MRR。"""
    n = len(ranks)
    recall = {k: sum(1 for r in ranks if r and r <= k) / n for k in K_LIST}
    mrr = sum((1.0 / r) for r in ranks if r) / n
    return {"recall": recall, "mrr": mrr}


def main():
    testset = json.loads((OUT_DIR / "testset.json").read_text(encoding="utf-8"))
    print(f"测试集 {len(testset)} 题；候选池档位 {FETCH_SETTINGS}\n")
    vs = _get_vs()
    max_fetch = max(FETCH_SETTINGS)

    # 每题稠密过取 max_fetch 候选（一次嵌入查询，各档位切片复用）
    cand_by_item = []
    for it in testset:
        docs = vs.similarity_search(it["query"], k=max_fetch, filter=_EVAL_FILTER)
        cand_by_item.append(docs)

    # A：纯稠密（候选按稠密序，截 top-10）
    a_ranks = [_hit_rank(docs[:TOPK], it) for docs, it in zip(cand_by_item, testset)]

    # 各 FETCH 档：候选池天花板 + 重排后名次
    ceiling = {}   # fetch -> 候选池命中率（任一候选 is_relevant 的题占比）
    rr_ranks = {}  # fetch -> 每题重排后首命中名次
    for fetch in FETCH_SETTINGS:
        ranks, ceil_hits = [], 0
        for docs, it in zip(cand_by_item, testset):
            pool = docs[:fetch]
            if _hit_rank(pool, it) is not None:
                ceil_hits += 1
            try:
                reranked = _rerank(it["query"], pool, TOPK)
                ranks.append(_hit_rank(reranked, it))
            except Exception as e:
                print(f"    ⚠ rerank 失败(fetch={fetch}, q={it['query'][:30]!r}): {e}")
                ranks.append(_hit_rank(pool[:TOPK], it))  # 降级用稠密序
            time.sleep(0.15)
        ceiling[fetch] = ceil_hits / len(testset)
        rr_ranks[fetch] = ranks

    # ── 报表 ──
    cols = ["A(dense)"] + [f"rerank@{f}" for f in FETCH_SETTINGS]
    results = {"A(dense)": _score(a_ranks)}
    for f in FETCH_SETTINGS:
        results[f"rerank@{f}"] = _score(rr_ranks[f])

    w = 12
    print("\n========== A(纯稠密) vs A+rerank（同一 testset / 同一判定口径）==========")
    print("metric".ljust(8) + "".join(c.ljust(w) for c in cols))
    print("-" * (8 + w * len(cols)))
    for k in K_LIST:
        row = f"R@{k}".ljust(8)
        for c in cols:
            row += f"{results[c]['recall'][k]:.4f}".ljust(w)
        print(row)
    row = "MRR".ljust(8)
    for c in cols:
        row += f"{results[c]['mrr']:.4f}".ljust(w)
    print(row)

    print("\n候选池命中率（重排天花板，任一候选含 gold 的题占比）：")
    for f in FETCH_SETTINGS:
        print(f"  fetch={f:>3}: {ceiling[f]:.4f}")
    print(
        "\n解读：rerank@F 受 fetch=F 的天花板封顶。"
        "若天花板已高而 A(dense) 低 → 重排能救（gold 在池中只是排序靠后）；"
        "若天花板本身就低 → 稠密过取没捞到 gold，需 BM25/更小切片，rerank 无能为力。"
    )


if __name__ == "__main__":
    main()
