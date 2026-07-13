"""
tests/test_citation.py

想法 2(b) bind-by-id 核心逻辑单测（purpose-built，不依赖旧漂移测试）。
覆盖：extract_candidates（s2/arxiv/openalex/rag 真实返回样例）、parse_refs（含/不含 <zh>）、
enrich_refs（lean→rich）、detect_hallucination、citation_store 落库/读取。

运行方式：
  pytest tests/test_citation.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.rag.citation import (
    Candidate,
    collect_from_tool_results,
    detect_hallucination,
    enrich_refs,
    extract_candidates,
    parse_refs,
)
from src.core import init_SQLite
from src.core import citation_store


# ══════════════════════════════════════════════════════════════════
# extract_candidates：按工具名分派
# ══════════════════════════════════════════════════════════════════


def _s2_paper(pid="abc123", title="Photonic ADC", **over):
    base = {
        "title": title,
        "abstract": "...",
        "has_abstract": True,
        "tldr": "",
        "authors": ["Zhang", "Ma"],
        "year": 2024,
        "publication_date": "2024-01-01",
        "venue": "Nature",
        "publication_types": ["JournalArticle"],
        "fields_of_study": ["Physics"],
        "citation_count": 10,
        "influential_citation_count": 2,
        "s2_paper_id": pid,
        "arxiv_id": "2401.00001",
        "doi": "10.1/xx",
        "open_access_pdf": "https://x.com/a.pdf",
        "s2_url": f"https://www.semanticscholar.org/paper/{pid}",
    }
    base.update(over)
    return base


def test_extract_s2_basic():
    payload = json.dumps({"success": True, "papers": [_s2_paper()]}, ensure_ascii=False)
    cands = extract_candidates("s2_search_tool", payload)
    assert len(cands) == 1
    c = cands[0]
    assert c.source_id == "s2:abc123"
    assert c.ref_type == "s2"
    assert c.title == "Photonic ADC"
    assert c.authors == ["Zhang", "Ma"]
    assert c.venue == "Nature"
    assert c.year == "2024"
    assert c.doi == "10.1/xx"
    assert c.url == "https://www.semanticscholar.org/paper/abc123"


def test_extract_s2_skips_missing_id():
    payload = json.dumps(
        {"success": True, "papers": [_s2_paper(pid=""), _s2_paper(pid="keep")]},
        ensure_ascii=False,
    )
    cands = extract_candidates("s2_search_tool", payload)
    assert len(cands) == 1 and cands[0].source_id == "s2:keep"


def test_extract_arxiv():
    payload = json.dumps(
        {
            "success": True,
            "papers": [
                {
                    "title": "Reservoir Computing",
                    "summary": "...",
                    "authors": ["Lee"],
                    "published": "2023-05-10T00:00:00Z",
                    "updated": "2023-05-11T00:00:00Z",
                    "arxiv_id": "2305.12345",
                    "pdf_url": "https://arxiv.org/pdf/2305.12345",
                    "link": "https://arxiv.org/abs/2305.12345",
                    "primary_category": "physics.optics",
                    "categories": ["physics.optics"],
                }
            ],
        },
        ensure_ascii=False,
    )
    cands = extract_candidates("arxiv_tool", payload)
    assert len(cands) == 1
    c = cands[0]
    assert c.source_id == "arxiv:2305.12345"
    assert c.ref_type == "arxiv"
    assert c.year == "2023"
    assert c.url == "https://arxiv.org/pdf/2305.12345"
    assert c.venue == ""  # arxiv 无 venue


def test_extract_openalex():
    payload = json.dumps(
        {
            "success": True,
            "papers": [
                {
                    "title": "Microwave Photonics",
                    "abstract": "...",
                    "has_abstract": True,
                    "authors": ["Smith"],
                    "year": 2022,
                    "venue": "IEEE",
                    "doi": "10.2/yy",
                    "s2_paper_id": None,
                    "openalex_id": "W123",
                    "openalex_url": "https://openalex.org/W123",
                    "open_access_pdf": "https://y.com/b.pdf",
                }
            ],
        },
        ensure_ascii=False,
    )
    cands = extract_candidates("openalex_tool", payload)
    assert len(cands) == 1
    c = cands[0]
    assert c.source_id == "openalex:W123"
    assert c.ref_type == "openalex"
    assert c.url == "https://openalex.org/W123"


def test_extract_rag_new_format():
    """format_context 改造后串头带 [rag:<doc_id> | 标题, Page N]。"""
    content = (
        "[rag:doc_001 | Photonic Reservoir, Page 3]\nsome text\n\n"
        "---\n\n"
        "[rag:doc_002 | Fiber Laser]\nmore text"
    )
    cands = extract_candidates("rag_tool", content)
    assert len(cands) == 2
    assert cands[0].source_id == "rag:doc_001"
    assert cands[0].title == "Photonic Reservoir"
    assert cands[0].page == "3"
    assert cands[0].doc_id == "doc_001"
    assert cands[1].source_id == "rag:doc_002"
    assert cands[1].page == ""  # 无 Page


def test_extract_rag_dedup_same_doc_page():
    """同 doc_id + page 多 chunk 只收一条。"""
    content = "[rag:doc_x | Title, Page 1]\ntext1\n\n---\n\n[rag:doc_x | Title, Page 1]\ntext2"
    cands = extract_candidates("rag_tool", content)
    assert len(cands) == 1


def test_extract_rag_legacy_no_docid_skipped():
    """doc_id 缺失（旧格式 [标题, Page N]）无 source_id，不收集。"""
    content = "[Photonic Reservoir, Page 3]\nsome text"
    assert extract_candidates("rag_tool", content) == []


def test_extract_unknown_tool_returns_empty():
    assert extract_candidates("lookup_local_paper_id", '{"results":[]}') == []
    assert extract_candidates("jina_tool", '{"url":"x"}') == []


def test_extract_handles_invalid_json():
    assert extract_candidates("s2_search_tool", "not json") == []
    assert extract_candidates("s2_search_tool", "") == []


def test_collect_from_tool_results_dedup_across_tools():
    """同 source_id 跨工具首条胜出。"""
    s2 = json.dumps(
        {"success": True, "papers": [_s2_paper(pid="dup", title="from_s2")]},
        ensure_ascii=False,
    )
    rag = "[rag:doc_a | Title]\ntext"
    merged = collect_from_tool_results([("s2_search_tool", s2), ("rag_tool", rag)])
    ids = {c.source_id for c in merged}
    assert ids == {"s2:dup", "rag:doc_a"}
    # 同 source_id 首条胜出
    s2_again = json.dumps(
        {"success": True, "papers": [_s2_paper(pid="dup", title="from_s2_again")]},
        ensure_ascii=False,
    )
    merged2 = collect_from_tool_results(
        [("s2_search_tool", s2), ("s2_search_tool", s2_again)]
    )
    assert len(merged2) == 1
    assert merged2[0].title == "from_s2"  # 先到的胜出


# ══════════════════════════════════════════════════════════════════
# parse_refs
# ══════════════════════════════════════════════════════════════════


def test_parse_refs_plain_lean():
    lean = (
        '正文 [ref:1]。\n'
        '<ref id="1">\n[s2:abc123] | 摘要片段："result"\n</ref>'
    )
    refs = parse_refs(lean)
    assert len(refs) == 1
    assert refs[0].ref_id == "1"
    assert refs[0].source_id == "s2:abc123"
    assert '摘要片段' in refs[0].excerpt
    assert refs[0].zh == ""


def test_parse_refs_with_zh():
    lean = (
        '<ref id="2">\n[s2:abc123] | 摘要片段："result"\n'
        '<zh>标题译文。译文片段。</zh>\n</ref>'
    )
    refs = parse_refs(lean)
    assert len(refs) == 1
    assert refs[0].source_id == "s2:abc123"
    assert refs[0].zh == "标题译文。译文片段。"
    # excerpt 不含 <zh> 内容
    assert "译文" not in refs[0].excerpt or "译文片段" not in refs[0].excerpt
    assert '摘要片段' in refs[0].excerpt


def test_parse_refs_multiple_ordered():
    lean = (
        '<ref id="2">\n[arxiv:2305.1] | x\n</ref>\n'
        '<ref id="1">\n[rag:doc_1] | y\n</ref>'
    )
    refs = parse_refs(lean)
    assert [r.ref_id for r in refs] == ["2", "1"]
    assert {r.source_id for r in refs} == {"arxiv:2305.1", "rag:doc_1"}


def test_parse_refs_skips_malformed():
    """格式不符（无 [source_id] | 摘抄）的 <ref> 跳过，不抛。"""
    lean = (
        '<ref id="1">Zhang et al. "Title" | excerpt</ref>\n'  # 旧格式，无 [source_id]
        '<ref id="2">[s2:ok] | good</ref>'
    )
    refs = parse_refs(lean)
    assert len(refs) == 1 and refs[0].source_id == "s2:ok"


def test_parse_refs_empty_or_none():
    assert parse_refs("") == []
    assert parse_refs("无引用纯文本") == []


def test_parse_refs_no_excerpt():
    """无可靠原文时退化为只有 id（无 | 摘抄）——此格式无 excerpt 段，跳过（不属 bind-by-id）。"""
    lean = '<ref id="1">[s2:abc]</ref>'
    refs = parse_refs(lean)
    # 无 `|` 分隔 → _REF_SOURCE_RE 不匹配 → 跳过
    assert refs == []


# ══════════════════════════════════════════════════════════════════
# enrich_refs
# ══════════════════════════════════════════════════════════════════


def test_enrich_refs_s2_full():
    lean = '<ref id="1">\n[s2:abc123] | 摘要片段："result"\n</ref>'
    enrich_map = {
        "s2:abc123": {
            "ref_type": "s2",
            "title": "Photonic ADC",
            "authors": ["Zhang", "Ma"],
            "venue": "Nature",
            "year": "2024",
            "doi": "10.1/xx",
            "url": "",
        }
    }
    rich = enrich_refs(lean, enrich_map)
    assert "[s2:abc123]" not in rich  # source_id 被替换
    assert "Photonic ADC" in rich
    assert "Zhang, Ma" in rich
    assert "Nature" in rich
    assert "10.1/xx" in rich
    # 摘抄保留
    assert '摘要片段' in rich


def test_enrich_refs_rag_type_aware():
    """RAG 类：只给 标题 + Page，无作者/venue/url。"""
    lean = '<ref id="1">\n[rag:doc_001] | 原文摘抄\n</ref>'
    enrich_map = {
        "rag:doc_001": {
            "ref_type": "rag",
            "title": "Photonic Reservoir",
            "authors": [],
            "venue": "",
            "year": "",
            "doi": "",
            "url": "",
            "doc_id": "doc_001",
            "page": "3",
        }
    }
    rich = enrich_refs(lean, enrich_map)
    assert "Photonic Reservoir" in rich
    assert "Page 3" in rich
    # 不该出现作者/venue（RAG 无）
    assert "et al" not in rich


def test_enrich_refs_preserves_zh():
    lean = (
        '<ref id="1">\n[s2:abc123] | 摘要片段："result"\n'
        '<zh>标题译文。译文。</zh>\n</ref>'
    )
    enrich_map = {
        "s2:abc123": {
            "ref_type": "s2", "title": "T", "authors": [], "venue": "",
            "year": "", "doi": "", "url": "",
        }
    }
    rich = enrich_refs(lean, enrich_map)
    assert "<zh>标题译文。译文。</zh>" in rich
    assert '摘要片段' in rich  # excerpt 保留


def test_enrich_refs_missing_enrich_keeps_lean():
    """enrichment_map 没这条（幻觉或 sidecar 未写）→ 保留裸 source_id，不崩。"""
    lean = '<ref id="1">\n[s2:missing] | excerpt\n</ref>'
    rich = enrich_refs(lean, {})  # 空 map
    assert rich == lean  # no-op
    rich2 = enrich_refs(lean, {"other:1": {}})
    assert "[s2:missing]" in rich2  # 缺 enrichment 保留裸 id


def test_enrich_refs_no_refs_noop():
    assert enrich_refs("纯文本无引用", {"s2:1": {}}) == "纯文本无引用"
    assert enrich_refs("", {"s2:1": {}}) == ""


def test_enrich_refs_authors_et_al():
    lean = '<ref id="1">\n[s2:abc] | x\n</ref>'
    enrich_map = {
        "s2:abc": {
            "ref_type": "s2", "title": "T",
            "authors": ["A", "B", "C", "D"],  # 4 人 → et al
            "venue": "", "year": "", "doi": "", "url": "",
        }
    }
    rich = enrich_refs(lean, enrich_map)
    assert "A et al." in rich


# ══════════════════════════════════════════════════════════════════
# detect_hallucination
# ══════════════════════════════════════════════════════════════════


def test_detect_hallucination_finds_fabricated():
    from src.rag.citation import ParsedRef
    refs = [
        ParsedRef(ref_id="1", source_id="s2:real", excerpt="x"),
        ParsedRef(ref_id="2", source_id="s2:FAKE", excerpt="y"),
        ParsedRef(ref_id="3", source_id="rag:real_doc", excerpt="z"),
    ]
    candidates = [
        Candidate(source_id="s2:real", ref_type="s2"),
        Candidate(source_id="rag:real_doc", ref_type="rag"),
    ]
    hallu = detect_hallucination(refs, candidates)
    assert hallu == ["s2:FAKE"]


def test_detect_hallucination_none_when_all_real():
    from src.rag.citation import ParsedRef
    refs = [ParsedRef(ref_id="1", source_id="s2:real", excerpt="x")]
    candidates = [Candidate(source_id="s2:real", ref_type="s2")]
    assert detect_hallucination(refs, candidates) == []


def test_detect_hallucination_empty_inputs():
    assert detect_hallucination([], []) == []


# ══════════════════════════════════════════════════════════════════
# citation_store：落库/读取（临时 DB）
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """把 DB_PATH 指向临时文件，init_db 建表。"""
    db_file = tmp_path / "test_app.db"
    monkeypatch.setattr(init_SQLite, "DB_PATH", str(db_file))
    # citation_store 通过 init_SQLite.get_conn 用 DB_PATH，但 get_conn 内 import 的是
    # init_SQLite.DB_PATH 模块属性——确保两者一致
    monkeypatch.setattr("src.core.init_SQLite.DB_PATH", str(db_file))
    init_SQLite.init_db()
    return str(db_file)


def test_store_save_and_load_by_message(tmp_db):
    rows = [
        Candidate(
            source_id="s2:abc", ref_type="s2", title="T1",
            authors=["A", "B"], venue="V", year="2024", doi="10.1/x",
            url="https://s2", raw_meta={"k": "v"},
        ).to_row(100, "conv_1"),
        Candidate(
            source_id="rag:doc1", ref_type="rag", title="T2",
            page="3", doc_id="doc1",
        ).to_row(100, "conv_1"),
    ]
    n = citation_store.save_candidates(100, "conv_1", rows)
    assert n == 2

    enrich = citation_store.load_enrichment_for_message(100)
    assert "s2:abc" in enrich and "rag:doc1" in enrich
    assert enrich["s2:abc"]["title"] == "T1"
    assert enrich["s2:abc"]["authors"] == ["A", "B"]  # 反序列化回 list
    assert enrich["rag:doc1"]["page"] == "3"


def test_store_save_empty_noop(tmp_db):
    assert citation_store.save_candidates(1, "conv", []) == 0


def test_store_load_by_source_cross_message(tmp_db):
    """load_enrichment_map 跨 message 取（同会话多次检索同一篇）。"""
    rows = [
        Candidate(source_id="s2:shared", ref_type="s2", title="Shared").to_row(1, "conv_x"),
    ]
    citation_store.save_candidates(1, "conv_x", rows)
    # 另一条消息也引用同 id
    rows2 = [
        Candidate(source_id="s2:shared", ref_type="s2", title="Shared").to_row(2, "conv_x"),
        Candidate(source_id="s2:other", ref_type="s2", title="Other").to_row(2, "conv_x"),
    ]
    citation_store.save_candidates(2, "conv_x", rows2)

    m = citation_store.load_enrichment_map("conv_x", ["s2:shared", "s2:other", "s2:absent"])
    assert "s2:shared" in m and m["s2:shared"]["title"] == "Shared"
    assert "s2:other" in m
    assert "s2:absent" not in m


def test_store_delete_by_conversation(tmp_db):
    rows = [Candidate(source_id="s2:abc", ref_type="s2", title="T").to_row(1, "conv_d")]
    citation_store.save_candidates(1, "conv_d", rows)
    citation_store.delete_by_conversation("conv_d")
    assert citation_store.load_enrichment_for_message(1) == {}


def test_store_mark_cited(tmp_db):
    """Step 2：候选集写入后，被引用的 source_id 标 is_cited=1。"""
    rows = [
        Candidate(source_id="s2:used", ref_type="s2", title="A").to_row(10, "conv_c"),
        Candidate(source_id="s2:unused", ref_type="s2", title="B").to_row(10, "conv_c"),
    ]
    citation_store.save_candidates(10, "conv_c", rows)
    n = citation_store.mark_cited(10, "conv_c", ["s2:used", "s2:not_in_candidates"])
    assert n == 1  # 只标中 s2:used（s2:not_in_candidates 无行可标）
    enrich = citation_store.load_enrichment_for_message(10)
    assert enrich["s2:used"]["is_cited"] == 1
    assert enrich["s2:unused"]["is_cited"] == 0


def test_store_mark_cited_empty(tmp_db):
    assert citation_store.mark_cited(1, "conv", []) == 0


# ══════════════════════════════════════════════════════════════════
# _persist_and_enrich 端到端（graph 层集成）
# ══════════════════════════════════════════════════════════════════


def test_persist_and_enrich_full_path(tmp_db, caplog, monkeypatch):
    """模拟流式路径：tool_results 有 s2 候选 + lean answer 引用了真 id + 一个编造 id。
    断言：sidecar 写入、done answer 是 rich、幻觉 id 记 warning 日志、is_cited 标记。
    """
    from src.rag.graph import _persist_and_enrich
    import logging

    # 项目 logger propagate=False（见 src/utils/logger.py），caplog 靠 root 传播捕获。
    # 测试期间临时打开 graph logger 的传播，让 caplog 能收到 records。
    graph_logger = logging.getLogger("src.rag.graph")
    monkeypatch.setattr(graph_logger, "propagate", True)

    s2_payload = json.dumps(
        {"success": True, "papers": [_s2_paper(pid="real1", title="Real Paper")]},
        ensure_ascii=False,
    )
    lean = (
        '正文 [ref:1] [ref:2]。\n'
        '<ref id="1">\n[s2:real1] | 摘要片段："real"\n</ref>\n'
        '<ref id="2">\n[s2:FAKE] | 编造\n</ref>'
    )
    result = {"tool_results": [("s2_search_tool", s2_payload)]}

    with caplog.at_level(logging.WARNING, logger="src.rag.graph"):
        rich = _persist_and_enrich("conv_e2e", 200, lean, result, enrich=True)

    # 1. done answer 是 rich：real id 被替换成完整引用，FAKE 保留裸 id
    assert "Real Paper" in rich
    assert "[s2:real1]" not in rich
    assert "[s2:FAKE]" in rich  # 无 enrichment，保留裸 id

    # 2. sidecar 写入候选
    enrich = citation_store.load_enrichment_for_message(200)
    assert "s2:real1" in enrich  # s2:FAKE 不是候选（候选集只含工具返回的）

    # 3. is_cited 标记：s2:real1 被标 1（候选里有的被引用）
    assert enrich["s2:real1"]["is_cited"] == 1

    # 4. 幻觉 id 记 warning 日志
    assert "s2:FAKE" in caplog.text and "幻觉" in caplog.text


def test_persist_and_enrich_no_tools_noop(tmp_db):
    """无工具结果 → 无候选、无 ref → no-op 返回原 lean。"""
    from src.rag.graph import _persist_and_enrich
    lean = "纯文本回答，无引用"
    result = {"tool_results": []}
    assert _persist_and_enrich("conv_n", 300, lean, result, enrich=True) == lean
    assert citation_store.load_enrichment_for_message(300) == {}


def test_persist_and_enrich_non_stream_no_enrich(tmp_db):
    """非流式路径 enrich=False：sidecar 仍写、幻觉仍检测，但返回 lean 不 merge。"""
    from src.rag.graph import _persist_and_enrich
    s2_payload = json.dumps(
        {"success": True, "papers": [_s2_paper(pid="x1", title="X")]},
        ensure_ascii=False,
    )
    lean = '<ref id="1">\n[s2:x1] | excerpt\n</ref>'
    result = {"tool_results": [("s2_search_tool", s2_payload)]}
    out = _persist_and_enrich("conv_ns", 400, lean, result, enrich=False)
    assert out == lean  # 非 enrich 返回原 lean
    # sidecar 仍写
    assert "s2:x1" in citation_store.load_enrichment_for_message(400)
