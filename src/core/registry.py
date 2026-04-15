from src.config import DATA_DIR
import json
from pydantic import BaseModel, Field, field_validator
from typing import Literal
import re
from pathlib import Path


def seed_or_user(user_id: str = "seed") -> Path:
    if user_id == "seed":
        path = DATA_DIR / "registry_seed.json"
    else:
        path = DATA_DIR / "registry_users" / f"{user_id}.json"
    return path


def load_registry(user_id: str = "seed") -> dict:
    path = seed_or_user(user_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        data = {}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
            return {}


def save_registry(registry: dict, user_id: str = "seed"):
    path = seed_or_user(user_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
            return {"success": True}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def is_duplicate(doc_id: str, user_id: str = "seed") -> bool:
    registry = load_registry(user_id)
    return bool(registry.get(doc_id))


class PaperMeta(BaseModel):
    doc_id: str
    title: str
    author: str = Field(default="")  # 预留
    year: str = Field(default="")  # 预留
    file_name: str
    upload_time: str
    source_type: Literal["seed", "user"] = Field(default="user")
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


def register_paper(paper_meta: PaperMeta, user_id: str = "seed"):
    try:
        meta_data = paper_meta.model_dump()
        doc_id = meta_data["doc_id"]
        raw_registry = load_registry(user_id)
        raw_registry[doc_id] = meta_data
        save_registry(raw_registry, user_id)
        return {"success": True}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def update_after_index(
    doc_id: str, chunk_count: int, page_count: int, user_id: str = "seed"
):
    if (
        is_duplicate(doc_id, user_id)
        and chunk_count is not None
        and page_count is not None
    ):
        raw_registry = load_registry(user_id)
        raw_registry[doc_id]["chunk_count"] = chunk_count
        raw_registry[doc_id]["page_count"] = page_count
        raw_registry[doc_id]["status"] = "indexed"
        save_registry(raw_registry, user_id)
        return {"success": True}
    else:
        return {
            "success": False,
            "detail": f'id "{doc_id}" does not exist or chunk_count is illegal',
        }


def remove_paper(doc_id: str, user_id: str = "seed") -> dict:
    """清除注册表记录"""
    raw_registry = load_registry(user_id)
    if doc_id in raw_registry:
        try:
            del raw_registry[doc_id]
            save_registry(raw_registry, user_id)
            return {"success": True}
        except Exception as e:
            return {"success": False, "detail": str(e)}
    return {"success": False, "detail": "论文记录不存在"}


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
