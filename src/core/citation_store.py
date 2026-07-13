# citation_store.py
"""引用元信息 sidecar 持久化（想法 2(b) bind-by-id）。

职责：把 harness 从工具结果捕获的候选 enrichment 落库（关联 agent 消息 id），
展示期按 message_id 或 source_id 查回，供后端 merge 出 rich 引用。

设计见 plan/citation-bind-by-id-plan.md。复用 init_SQLite.get_conn() 的连接模式。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Iterable

from src.core.init_SQLite import get_conn


@dataclass
class EnrichmentRow:
    """ref_enrichment 一行的领域模型。与 Candidate（src.rag.citation）字段对齐，
    这里单独定义避免 rag 层 ↔ core 层循环 import。"""
    message_id: int
    conversation_id: str
    source_id: str
    ref_type: str = ""
    title: str = ""
    authors: str = ""       # JSON list[str] 串存
    venue: str = ""
    year: str = ""
    doi: str = ""
    url: str = ""
    doc_id: str = ""
    page: str = ""
    raw_meta: str = ""      # 完整原始元信息 JSON（兜底，展示/调试用）
    is_cited: int = 0


_INSERT_SQL = """
INSERT INTO ref_enrichment (
    message_id, conversation_id, source_id, ref_type,
    title, authors, venue, year, doi, url,
    doc_id, page, raw_meta, is_cited
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_BY_MSG_SQL = (
    "SELECT source_id, ref_type, title, authors, venue, year, doi, url, "
    "doc_id, page, raw_meta, is_cited FROM ref_enrichment WHERE message_id = ?"
)

_SELECT_BY_SOURCE_SQL = (
    "SELECT message_id, source_id, ref_type, title, authors, venue, year, doi, url, "
    "doc_id, page, raw_meta, is_cited FROM ref_enrichment "
    "WHERE conversation_id = ? AND source_id IN (%s)"
)


def save_candidates(
    message_id: int, conversation_id: str, candidates: Iterable[EnrichmentRow]
) -> int:
    """批量写候选 enrichment。每条候选预填 is_cited=0（候选集语义）。
    返回写入行数。空候选集直接返回 0，不建连接。
    """
    rows = list(candidates)
    if not rows:
        return 0
    with get_conn() as conn:
        cur = conn.cursor()
        for c in rows:
            # 强制 message_id/conversation_id 用入参（候选 row 可能没带）
            cur.execute(
                _INSERT_SQL,
                (
                    message_id,
                    conversation_id,
                    c.source_id,
                    c.ref_type,
                    c.title,
                    c.authors,
                    c.venue,
                    c.year,
                    c.doi,
                    c.url,
                    c.doc_id,
                    c.page,
                    c.raw_meta,
                    c.is_cited or 0,
                ),
            )
        conn.commit()
        return len(rows)


def load_enrichment_for_message(message_id: int) -> dict[str, dict]:
    """按 message_id 取该消息所有候选 enrichment，返回 {source_id: fields_dict}。
    供展示期 enrich_refs 查表。同一 source_id 多行取首行（候选集理论上一条一行）。
    """
    out: dict[str, dict] = {}
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(_SELECT_BY_MSG_SQL, (message_id,))
        for row in cur.fetchall():
            d = dict(row)
            sid = d.pop("source_id")
            # authors 反序列化为 list（落库时是 JSON 串）
            if d.get("authors"):
                try:
                    d["authors"] = json.loads(d["authors"])
                except (json.JSONDecodeError, TypeError):
                    d["authors"] = []
            else:
                d["authors"] = []
            out.setdefault(sid, d)
    return out


def load_enrichment_map(
    conversation_id: str, source_ids: Iterable[str]
) -> dict[str, dict]:
    """按 conversation_id + source_ids 批量取 enrichment。
    跨 message 取（同一会话多次检索同一篇论文），用于落库 enrich 时兜底——
    当前实现优先用本消息 sidecar，本消息缺的再跨消息补。
    """
    sids = [s for s in source_ids if s]
    if not sids:
        return {}
    placeholders = ",".join("?" * len(sids))
    sql = _SELECT_BY_SOURCE_SQL % placeholders
    out: dict[str, dict] = {}
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, (conversation_id, *sids))
        for row in cur.fetchall():
            d = dict(row)
            sid = d.pop("source_id")
            if d.get("authors"):
                try:
                    d["authors"] = json.loads(d["authors"])
                except (json.JSONDecodeError, TypeError):
                    d["authors"] = []
            else:
                d["authors"] = []
            out.setdefault(sid, d)
    return out


def delete_by_conversation(conversation_id: str) -> None:
    """清会话时连带删 sidecar（与 messages 同生命周期）。"""
    with get_conn() as conn:
        conn.cursor().execute(
            "DELETE FROM ref_enrichment WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.commit()


_MARK_CITED_SQL = (
    "UPDATE ref_enrichment SET is_cited = 1 "
    "WHERE message_id = ? AND conversation_id = ? AND source_id IN (%s)"
)


def mark_cited(
    message_id: int, conversation_id: str, source_ids: Iterable[str]
) -> int:
    """把 model 实际引用的候选行标 is_cited=1（Step 2 幻觉检测配套）。
    候选集里命中被引用集的升 1，未命中的留 0（候选未引用）；查无匹配的被引用 id
    由 detect_hallucination 单独记日志（不在此处理，它们没 sidecar 行可标）。
    返回更新行数。
    """
    sids = [s for s in source_ids if s]
    if not sids:
        return 0
    placeholders = ",".join("?" * len(sids))
    sql = _MARK_CITED_SQL % placeholders
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (message_id, conversation_id, *sids))
        conn.commit()
        return cur.rowcount
