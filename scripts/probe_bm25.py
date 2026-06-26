# scripts/probe_bm25.py
#
# 一次性离线探针（非生产代码、不打包、零网络/零嵌入额度）：验证 Part 2 的
# _bm25_tokenize / _rrf_merge / hybrid_search 三个纯函数的逻辑正确性。
#
# 为什么不用 eval_framework 的旧测试：CLAUDE.md 明确旧测试版本漂移、多为陈旧断言；
# 这里现写一份贴合当前架构的小探针，断言可读、失败即真问题。
#
# 覆盖：
#   1) _bm25_tokenize：英文按词、中文单字 + 字二元组、大小写归一。
#   2) BM25 + tokenize 端到端：精确术语 query 应把含该术语的文档排到第一
#      （这正是 BM25 相对稠密检索的靶区——精确字面命中）。
#   3) _rrf_merge：跨路名次融合、去重（同 chunk 多路命中得分累加并只保留一份）、
#      按融合分降序。
#
# 用法：python scripts/probe_bm25.py

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK，避免 ✓/中文 编码错

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.documents import Document  # noqa: E402
from rank_bm25 import BM25Okapi  # noqa: E402

from src.rag.tools.rag_tool import _bm25_tokenize, _chunk_key, _rrf_merge  # noqa: E402

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ✓ {name}")
    else:
        _FAIL += 1
        print(f"  ✗ {name}  {detail}")


def _doc(doc_id, idx, text, section="body"):
    return Document(
        page_content=text,
        metadata={"doc_id": doc_id, "section": section, "chunk_index": idx},
    )


# ── 1) 分词 ────────────────────────────────────────────────
print("\n[1] _bm25_tokenize")
toks = _bm25_tokenize("Microwave Photonics 滤波带宽")
check("英文按词且小写归一", "microwave" in toks and "photonics" in toks, toks)
check("中文单字", all(c in toks for c in ["滤", "波", "带", "宽"]), toks)
check("中文字二元组", "滤波" in toks and "波带" in toks and "带宽" in toks, toks)
check("纯英文无中文残留", _bm25_tokenize("Q value") == ["q", "value"], _bm25_tokenize("Q value"))
check("空串返回空", _bm25_tokenize("") == [], _bm25_tokenize(""))

# ── 2) BM25 端到端：精确术语命中 ───────────────────────────
print("\n[2] BM25 精确术语命中（BM25 的靶区）")
corpus = [
    "本文研究微波光子滤波器的带宽调控方法",
    "reservoir computing 的训练方法与应用",
    "optical frequency comb 用于提升 MPF 的 Q 值与品质因数",
]
bm25 = BM25Okapi([_bm25_tokenize(d) for d in corpus])
scores = bm25.get_scores(_bm25_tokenize("滤波带宽"))
top = max(range(len(scores)), key=lambda i: scores[i])
check("中文术语『滤波带宽』命中第 0 篇", top == 0, f"top={top} scores={[round(s,3) for s in scores]}")

scores2 = bm25.get_scores(_bm25_tokenize("Q value"))
top2 = max(range(len(scores2)), key=lambda i: scores2[i])
check("英文术语『Q value』命中第 2 篇", top2 == 2, f"top={top2} scores={[round(s,3) for s in scores2]}")

# ── 3) RRF 融合 ────────────────────────────────────────────
print("\n[3] _rrf_merge")
# 两路：向量路 [d0, d1, d2]，BM25 路 [d2, d3]。d2 两路都命中 → 应排前。
d0, d1, d2, d3 = _doc("X", 0, "a"), _doc("X", 1, "b"), _doc("X", 2, "c"), _doc("X", 3, "d")
merged = _rrf_merge([[d0, d1, d2], [d2, d3]])
keys = [_chunk_key(d) for d in merged]
check("去重：d2 只出现一次", keys.count(("X", "body", 2)) == 1, keys)
check("并集大小为 4", len(merged) == 4, keys)
# d2: 1/(60+2) + 1/(60+0) = 0.01613+0.01667=0.0328；d0: 1/60=0.01667 → d2 应在 d0 前
check("两路命中的 d2 排在仅向量首位的 d0 之前",
      keys.index(("X", "body", 2)) < keys.index(("X", "body", 0)), keys)
# d3 仅 BM25 rank1 → 1/61=0.0164，应在末尾附近但高于 d1(向量 rank1=1/61 同值)；只验证 d3 在结果中
check("仅单路命中的 d3 也被保留", ("X", "body", 3) in keys, keys)

# 唯一键区分 section / doc_id：同 chunk_index 不同 section 不应被当成同一条
e0 = _doc("X", 0, "ref", section="reference")
merged2 = _rrf_merge([[d0], [e0]])
check("唯一键含 section：body0 与 reference0 不去重", len(merged2) == 2,
      [_chunk_key(d) for d in merged2])

print(f"\n{'='*40}\n  PASS={_PASS}  FAIL={_FAIL}\n{'='*40}")
sys.exit(1 if _FAIL else 0)
