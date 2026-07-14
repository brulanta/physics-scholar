# citation.py
"""引用 bind-by-id 核心逻辑（想法 2(b)）。

三件事：
  1. extract_candidates(tool_name, content) —— 从工具返回串收集候选 enrichment
     （source_id + 完整元信息）。按工具名分派：外部工具解析 JSON，RAG 正则提串头。
  2. parse_refs(lean_answer) —— 解析 model 写的 lean ref `<ref id="N">[source_id] | 摘抄</ref>`。
  3. enrich_refs(lean_answer, enrichment_map) —— lean → rich，把 [source_id] 换成完整引用。

设计见 plan/citation-bind-by-id-plan.md。source_id 带类型前缀（rag:/s2:/arxiv:/openalex:）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ── source_id 前缀 ──────────────────────────────────────────────
PREFIX_RAG = "rag:"
PREFIX_S2 = "s2:"
PREFIX_ARXIV = "arxiv:"
PREFIX_OPENALEX = "openalex:"

# 工具名 → 前缀（graph 里 ToolMessage 不带工具原 name 时用此映射兜底；
# 真相源是工具 .name，此处覆盖现状 6 工具名）
_TOOL_PREFIX = {
    "rag_tool": PREFIX_RAG,
    "s2_search_tool": PREFIX_S2,
    "arxiv_tool": PREFIX_ARXIV,
    "openalex_tool": PREFIX_OPENALEX,
}


@dataclass
class Candidate:
    """一个可引用项的 enrichment。与 citation_store.EnrichmentRow 字段对齐。"""
    source_id: str
    ref_type: str = ""          # rag/s2/arxiv/openalex
    title: str = ""
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    year: str = ""
    doi: str = ""
    url: str = ""
    doc_id: str = ""
    page: str = ""
    raw_meta: dict[str, Any] = field(default_factory=dict)

    def to_row(self, message_id: int, conversation_id: str):
        """转 EnrichmentRow（落库用）。延迟 import 避 core↔rag 循环。"""
        from src.core.citation_store import EnrichmentRow
        return EnrichmentRow(
            message_id=message_id,
            conversation_id=conversation_id,
            source_id=self.source_id,
            ref_type=self.ref_type,
            title=self.title,
            authors=json.dumps(self.authors, ensure_ascii=False) if self.authors else "",
            venue=self.venue,
            year=self.year,
            doi=self.doi,
            url=self.url,
            doc_id=self.doc_id,
            page=self.page,
            raw_meta=json.dumps(self.raw_meta, ensure_ascii=False) if self.raw_meta else "",
            is_cited=0,
        )


# ── 候选收集 ────────────────────────────────────────────────────


def _candidates_from_s2(payload: dict) -> list[Candidate]:
    """s2 返回 {success, papers:[...]}，每篇 paper 字段见 s2_tool._parse_paper。"""
    out = []
    for p in payload.get("papers", []):
        pid = p.get("s2_paper_id")
        if not pid:
            continue
        out.append(Candidate(
            source_id=f"{PREFIX_S2}{pid}",
            ref_type="s2",
            title=p.get("title", ""),
            authors=p.get("authors") or [],
            venue=p.get("venue", ""),
            year=str(p.get("year") or ""),
            doi=p.get("doi") or "",
            url=p.get("s2_url") or p.get("open_access_pdf") or "",
            raw_meta=p,
        ))
    return out


def _candidates_from_arxiv(payload: dict) -> list[Candidate]:
    """arxiv 返回 {success, papers:[...]}，每篇含 arxiv_id/pdf_url/title/summary/authors。"""
    out = []
    for p in payload.get("papers", []):
        aid = p.get("arxiv_id")
        if not aid:
            continue
        out.append(Candidate(
            source_id=f"{PREFIX_ARXIV}{aid}",
            ref_type="arxiv",
            title=p.get("title", ""),
            authors=p.get("authors") or [],
            venue="",  # arxiv 无 venue
            year=(p.get("published", "")[:4] if p.get("published") else ""),
            doi="",
            url=p.get("pdf_url") or p.get("link") or "",
            raw_meta=p,
        ))
    return out


def _candidates_from_openalex(payload: dict) -> list[Candidate]:
    """openalex 返回 {success, papers:[...]}，字段与 s2 对齐 + openalex_id/openalex_url。"""
    out = []
    for p in payload.get("papers", []):
        oid = p.get("openalex_id")
        if not oid:
            continue
        out.append(Candidate(
            source_id=f"{PREFIX_OPENALEX}{oid}",
            ref_type="openalex",
            title=p.get("title", ""),
            authors=p.get("authors") or [],
            venue=p.get("venue", ""),
            year=str(p.get("year") or ""),
            doi=p.get("doi") or "",
            url=p.get("openalex_url") or p.get("open_access_pdf") or "",
            raw_meta=p,
        ))
    return out


# RAG format_context 串头格式：[rag:<doc_id> | 标题, Page N]（见 rag_tool.format_context）
# doc_id 缺失时格式回退到 [标题, Page N]——此时无 source_id，不收集（无绑定可言）。
_RAG_CHUNK_HEADER_RE = re.compile(
    r"\[rag:(?P<doc_id>[^\]\s|]+)\s*\|\s*(?P<title>[^\]]*?)(?:,\s*Page\s*(?P<page>\d+))?\]",
    re.IGNORECASE,
)


def _candidates_from_rag(content: str) -> list[Candidate]:
    """RAG 返回串由多个 chunk 用 `\\n\\n---\\n\\n` 拼接，每 chunk 串头带 `rag:<doc_id>`。
    同 doc_id 可能多 chunk（不同 page），各收一条（page 不同）。"""
    out = []
    seen: set[tuple[str, str]] = set()  # (doc_id, page) 去重
    for m in _RAG_CHUNK_HEADER_RE.finditer(content):
        doc_id = m.group("doc_id").strip()
        title = (m.group("title") or "").strip().rstrip(",").strip()
        page = (m.group("page") or "").strip()
        key = (doc_id, page)
        if key in seen:
            continue
        seen.add(key)
        out.append(Candidate(
            source_id=f"{PREFIX_RAG}{doc_id}",
            ref_type="rag",
            title=title,
            page=page,
            doc_id=doc_id,
            url="",  # RAG 无 url（本地文档）；title/page 够展示
            raw_meta={"doc_id": doc_id, "title": title, "page": page},
        ))
    return out


def extract_candidates(tool_name: str, content: str) -> list[Candidate]:
    """从工具返回串收集候选 enrichment。按工具名分派。

    外部工具返回 JSON（{success, papers:[...]}）；RAG 返回拼接串（串头带 source_id）。
    解析失败/空 papers/无 source_id 的项静默跳过——候选收集是尽力而为，绝不抛进 agent。
    """
    if not content:
        return []

    # RAG 走正则（返回非 JSON）
    if tool_name == "rag_tool":
        try:
            return _candidates_from_rag(content)
        except Exception as e:
            logger.warning("[citation] RAG 候选收集异常: %s", e)
            return []

    # 外部工具走 JSON
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return []

    dispatch = {
        "s2_search_tool": _candidates_from_s2,
        "arxiv_tool": _candidates_from_arxiv,
        "openalex_tool": _candidates_from_openalex,
    }
    fn = dispatch.get(tool_name)
    if fn is None:
        return []  # lookup_local_paper_id / jina_tool 不在 bind-by-id 范围
    try:
        return fn(payload)
    except Exception as e:
        logger.warning("[citation] %s 候选收集异常: %s", tool_name, e)
        return []


def collect_from_tool_results(
    tool_results: Iterable[tuple[str, str]],
) -> list[Candidate]:
    """遍历 (tool_name, content) 列表收集候选，跨工具合并、按 source_id 去重。
    流式路径（_consume_events 累积的 tool_results）与非流式（result['messages'] 的 ToolMessage）
    都喂这里。
    """
    merged: dict[str, Candidate] = {}
    for tool_name, content in tool_results:
        for c in extract_candidates(tool_name, content):
            # 同 source_id 首条胜出（先到的工具结果更靠前）
            merged.setdefault(c.source_id, c)
    return list(merged.values())


# ── ref 解析（lean answer → 结构化） ────────────────────────────

# <ref id="N">[source_id] | 摘抄</ref>，可含 <zh>...</zh> 子标签
# source_id 段：前缀:值，值不含 ] 和空白；摘抄段到 </ref> 前（含 <zh>）
_REF_BLOCK_RE = re.compile(
    r'<ref id="(?P<ref_id>\d+)">\s*(?P<body>[\s\S]*?)</ref>',
    re.IGNORECASE,
)
# body 内：[source_id] | excerpt（excerpt 可含 <zh>）。source_id 不含 ] 和 |
_REF_SOURCE_RE = re.compile(
    r"\[(?P<source_id>[^\]\s|]+:[^\]\s|]*)\]\s*\|\s*(?P<excerpt>[\s\S]*)",
    re.IGNORECASE,
)


@dataclass
class ParsedRef:
    ref_id: str
    source_id: str
    excerpt: str = ""
    zh: str = ""


def parse_refs(lean_answer: str) -> list[ParsedRef]:
    """解析 model 写的 lean refs。返回按正文出现顺序的 ParsedRef 列表。

    兼容 translation 模式：<zh>...</zh> 单独提，excerpt 去掉 <zh> 段。
    格式不符的 <ref>（无 [source_id] | 摘抄）静默跳过——模型格式写错不阻断展示，
    前端照原样 render（markdown.js 不挑格式）。
    """
    if not lean_answer:
        return []
    out: list[ParsedRef] = []
    for m in _REF_BLOCK_RE.finditer(lean_answer):
        ref_id = m.group("ref_id")
        body = m.group("body").strip()

        zh_match = re.search(r"<zh>([\s\S]*?)</zh>", body, re.IGNORECASE)
        zh = zh_match.group(1).strip() if zh_match else ""
        body_no_zh = re.sub(r"<zh>[\s\S]*?</zh>", "", body, flags=re.IGNORECASE).strip()

        src_match = _REF_SOURCE_RE.match(body_no_zh)
        if not src_match:
            # 非法 lean 格式（无 [source_id]），跳过——不算引用，不触发 enrich
            continue
        out.append(ParsedRef(
            ref_id=ref_id,
            source_id=src_match.group("source_id").strip(),
            excerpt=src_match.group("excerpt").strip(),
            zh=zh,
        ))
    return out


# ── enrich（lean → rich 展示态） ────────────────────────────────


def _format_authors(authors: list[str]) -> str:
    """作者列表 → 展示串。超 3 人用 et al."""
    if not authors:
        return ""
    clean = [a for a in authors if a]
    if not clean:
        return ""
    if len(clean) <= 3:
        return ", ".join(clean)
    return f"{clean[0]} et al."


def _enrich_one(ref: ParsedRef, enrich: dict | None) -> str:
    """单个 ref 的 source 段（`[source_id]`）替换成 rich 引用串。摘抄与 <zh> 保留。

    enrich 缺失（候选集没这条 = 幻觉，或 sidecar 还没写）→ 保留 lean 原样 source_id，
    展示降级为裸 id（用户至少能看到 id，不崩）。
    """
    sid = ref.source_id
    if enrich is None:
        return f"[{sid}]"
    ref_type = enrich.get("ref_type", "")
    title = enrich.get("title", "")
    authors = enrich.get("authors") or []
    venue = enrich.get("venue", "")
    year = enrich.get("year", "")
    doi = enrich.get("doi", "")
    url = enrich.get("url", "")
    doc_id = enrich.get("doc_id", "")
    page = enrich.get("page", "")

    auth_str = _format_authors(authors)
    parts: list[str] = []
    if auth_str:
        parts.append(auth_str + ".")
    if title:
        parts.append(f'"{title}"')
    if venue:
        parts.append(venue)
    if year:
        parts.append(year)
    body = " ".join(parts).strip()

    if ref_type == "rag":
        # RAG：标题 + Page（无作者/venue/url，本地文档）
        rag_parts = []
        if title:
            rag_parts.append(title)
        if page:
            rag_parts.append(f"Page {page}")
        if not rag_parts:
            return f"[{sid}]"
        return ", ".join(rag_parts)

    # 论文类（s2/arxiv/openalex）：可点链接。doi 需加 https://doi.org/ 前缀才是合法 URL，
    # 否则前端 markdown 把裸 "10.1364/ol.500356" 渲成纯文本点不动（sidecar 存的是裸 doi）。
    link = ""
    if doi:
        link = f"https://doi.org/{doi}"
    elif url:
        link = url
    if body and link:
        return f"{body}. [{link}]({link})"
    if body:
        return f"{body}."
    if link:
        return f"[{link}]({link})"
    return f"[{sid}]"


def enrich_refs(lean_answer: str, enrichment_map: dict[str, dict]) -> str:
    """lean answer → rich answer：把每个 <ref> 里的 [source_id] 替换成完整引用，
    保留摘抄与 <zh>。enrichment_map = {source_id: fields_dict}（来自 sidecar）。

    只改 source 段，ref 的 id/摘抄/zh 结构不动——前端 markdown.js 照原样 parse。
    无 ref 的纯文本回答：no-op 原样返回。
    """
    if not lean_answer or not enrichment_map:
        return lean_answer

    def _replace_block(m: re.Match) -> str:
        ref_id = m.group("ref_id")
        body = m.group("body")
        # 解析出 source_id（复用 parse_refs 的正则口径）
        zh_match = re.search(r"<zh>([\s\S]*?)</zh>", body, re.IGNORECASE)
        zh_block = zh_match.group(0) if zh_match else ""
        body_no_zh = re.sub(r"<zh>[\s\S]*?</zh>", "", body, flags=re.IGNORECASE).strip()
        src_match = _REF_SOURCE_RE.match(body_no_zh)
        if not src_match:
            return m.group(0)  # 非法格式，原样保留
        sid = src_match.group("source_id").strip()
        excerpt = src_match.group("excerpt").strip()
        rich_source = _enrich_one(
            ParsedRef(ref_id=ref_id, source_id=sid, excerpt=excerpt),
            enrichment_map.get(sid),
        )
        # 重组：<ref id="N"> rich_source | excerpt [zh_block]
        new_body = f"{rich_source} | {excerpt}"
        if zh_block:
            new_body += f"\n{zh_block}"
        return f'<ref id="{ref_id}">\n{new_body}\n</ref>'

    return _REF_BLOCK_RE.sub(_replace_block, lean_answer)


# ── Step 2：幻觉检测 ────────────────────────────────────────────


def detect_hallucination(
    refs: list[ParsedRef], candidates: list[Candidate]
) -> list[str]:
    """返回 model 引用了但候选集没有的 source_id（binding 幻觉信号）。

    候选集 = 工具返回的所有可引用项；被引用集 = model 在 refs 里写的 source_id。
    差集 = model 编了工具没返回的 id。仅返回 source_id 列表（落库标记 + 日志用），
    不阻断回答——与 harness_probe soft-violation 口径一致。
    """
    candidate_ids = {c.source_id for c in candidates}
    cited_ids = {r.source_id for r in refs}
    return sorted(cited_ids - candidate_ids)
