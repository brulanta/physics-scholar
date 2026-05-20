from src.config import DATA_DIR, DB_PATH
from pydantic import BaseModel, Field, field_validator
from typing import Literal
import re
from pathlib import Path
import sqlite3


def seed_or_user(user_id: str = "seed") -> Path:
    if user_id == "seed":
        path = DATA_DIR / "registry_seed.json"
    else:
        path = DATA_DIR / "registry_users" / f"{user_id}.json"
    return path


PAPER_FIELDS = [
    "doc_id",
    "title",
    "author",
    "year",
    "file_name",
    "upload_time",
    "source_type",
    "user_id",
    "status",
    "page_count",
    "chunk_count",
]

INSERT_PAPER_SQL = """
INSERT OR REPLACE INTO papers (
    doc_id, title, author, year, file_name,
    upload_time, source_type, user_id,
    status, page_count, chunk_count
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
UPDATE_PAPER_SQL = """
UPDATE papers SET status = ? , page_count = ?, chunk_count = ? WHERE doc_id = ? AND user_id = ?
"""
SELECT_PAPER_SQL = """SELECT doc_id, title, author, year, file_name, upload_time, source_type, user_id, status, page_count, chunk_count FROM papers """

DELETE_PAPER_SQL = "DELETE FROM papers WHERE doc_id = ? AND user_id = ?"


def dict_to_tuple(meta: dict, fields: list[str]):
    return tuple(meta.get(f) for f in fields)


def load_registry(user_id: str = "default") -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            SELECT_PAPER_SQL + """WHERE user_id = ?""",
            (user_id,),
        )

        rows = cur.fetchall()

        return {row["doc_id"]: dict(row) for row in rows}


class PaperMeta(BaseModel):
    doc_id: str
    title: str
    author: str = Field(default="")
    year: str = Field(default="")
    file_name: str
    upload_time: str
    source_type: Literal["seed", "user"] = Field(default="user")
    source_url: str = Field(default="")  # 新增
    user_id: str = Field(default="")
    status: Literal["pending", "processing", "indexed", "failed"] = Field(
        default="pending"
    )
    page_count: int = Field(default=-1)
    chunk_count: int = Field(default=-1)

    @field_validator("year", mode="before")
    @classmethod
    def coerce_year(cls, v):
        return str(v) if v is not None else ""


def is_duplicate(doc_id: str, user_id: str = "default") -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM papers WHERE doc_id=? AND user_id=? LIMIT 1",
            (doc_id, user_id),
        )
        res = cur.fetchone()
        return res is not None


def register_paper(paper_meta: PaperMeta) -> dict:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.cursor()

            row = dict_to_tuple(paper_meta.model_dump(), PAPER_FIELDS)

            cur.execute(
                INSERT_PAPER_SQL,
                row,
            )
            return {"success": True}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def update_after_index(
    doc_id: str, chunk_count: int, page_count: int, user_id: str = "default"
):
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()

        cur.execute(
            UPDATE_PAPER_SQL, ("indexed", page_count, chunk_count, doc_id, user_id)
        )
        if cur.rowcount == 0:
            return {
                "success": False,
                "detail": f'id "{doc_id}" does not exist',
            }
        return {"success": True}


def remove_paper(doc_id: str, user_id: str = "default") -> dict:
    """清除注册表记录"""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        try:
            cur.execute(DELETE_PAPER_SQL, (doc_id, user_id))
            if cur.rowcount == 0:
                return {"success": True, "existed": False}
            return {"success": True, "existed": True}
        except Exception as e:
            return {"success": False, "detail": str(e)}


def smart_match(keyword, sentence):
    # 1. 归一化
    keyword = keyword.strip().lower()
    sentence = sentence.lower()

    # 2. 判断是否包含中文字符 (使用 Unicode 范围检测)
    has_chinese = re.search(r"[\u4e00-\u9fff]", keyword)

    if has_chinese:
        # 中文逻辑：直接判断子串，因为中文不需要靠空格分词
        return keyword in sentence
    else:
        # 英文/数字逻辑：使用 \b 保护，防止 AI 匹配到 Mountain
        pattern = rf"\b{re.escape(keyword)}\b"
        return bool(re.search(pattern, sentence))


def search_by_keyword(
    query_segments: list[str], user_id: str = "", top_k=3
) -> list[dict]:

    # 永远加载种子库
    seed_registry = load_registry("seed")
    all_papers = list(seed_registry.values())

    # 如果有当前用户，追加用户库
    if user_id:
        user_registry = load_registry(user_id)
        all_papers += list(user_registry.values())

    # 后面的匹配逻辑不变
    max_score = len(query_segments)
    hit_result = []
    for reg in all_papers:
        score = 0
        for q in query_segments:
            search_text = " ".join(
                [reg.get("title", ""), reg.get("author", ""), reg.get("year", "")]
            )
            if smart_match(q, search_text):
                score += 1
        if score:
            hit_result.append(
                {
                    "doc_id": reg["doc_id"],
                    "title": reg["title"],
                    "score": score,  # 命中几个keyword
                    "max_score": max_score,  # 总共几个keyword
                }
            )
    # 返回结构
    return sorted(hit_result, key=lambda x: x["score"], reverse=True)[:top_k]
    # 按score降序排列，score=0的不返回
