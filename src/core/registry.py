from src.config import DATA_DIR
import json
from pydantic import BaseModel, Field
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
    page_count: int
    chunk_count: int = Field(default=-1)
    upload_time: str
    status: Literal["indexed", "unindexed"] = Field(default="unindexed")
    source_type: Literal["seed", "user"] = Field(default="user")
    user_id: str = Field(default="")


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


def update_after_index(doc_id: str, chunk_count: int, user_id: str = "seed"):
    if is_duplicate(doc_id, user_id) and chunk_count is not None:
        raw_registry = load_registry(user_id)
        raw_registry[doc_id]["chunk_count"] = chunk_count
        raw_registry[doc_id]["status"] = "indexed"
        save_registry(raw_registry, user_id)
        return {"success": True}
    else:
        return {
            "success": False,
            "detail": f'id "{doc_id}" does not exist or chunk_count is illegal',
        }


def smart_match(keyword, title):
    # 之后考虑扩展到涵盖作者名字、年份的搜索
    # title 扩展到 title + author + year 拼接后的字符串
    # 1. 归一化
    keyword = keyword.strip().lower()
    title = title.lower()

    # 2. 判断是否包含中文字符 (使用 Unicode 范围检测)
    has_chinese = re.search(r"[\u4e00-\u9fff]", keyword)

    if has_chinese:
        # 中文逻辑：直接判断子串，因为中文不需要靠空格切词
        return keyword in title
    else:
        # 英文/数字逻辑：使用 \b 保护，防止 AI 匹配到 Mountain
        pattern = rf"\b{re.escape(keyword)}\b"
        return bool(re.search(pattern, title))


def search_by_keyword(query_segments: list[str], user_id: str = "") -> list[dict]:
    """
    输入: query_segments一组关键词或短语。短语优于单个单词。
    Prompt 策略： 让 LLM 提取用户话语中最像论文标题的片段。输入长标题，要切分成短语再调用函数。输入错拼，LLM可自行理解后修正。
    """
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
            if smart_match(q, reg["title"]):
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
    return sorted(hit_result, key=lambda x: x["score"], reverse=True)
    # 按score降序排列，score=0的不返回
