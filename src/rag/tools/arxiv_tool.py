from pydantic import BaseModel, Field
import feedparser
import requests
from datetime import datetime, timedelta, timezone
import time
from langchain.tools import tool
import json
from src.utils.logger import get_logger

logger = get_logger(__name__)


class ArxivRequest(BaseModel):
    keywords: list[str] = Field(
        ...,
        description=(
            "检索关键词列表（必填）：\n"
            "- 每个元素应为英文技术关键词（1-3个词），如 'transformer', 'optical comb'\n"
            "- 不要使用完整句子或中文\n"
            "- 不要包含无意义词（如 paper, study, method）\n"
            "- 多个关键词之间为 OR 关系"
        ),
    )

    author: str = Field(
        default="",
        description=(
            "作者姓名（可选）：\n"
            "- 仅当用户明确指定作者时填写\n"
            "- 只填写人名（如 'Vaswani'），不要附加其他词\n"
            "- 否则留空"
        ),
    )

    category: str = Field(
        default="",
        description=(
            "arXiv分类（可选，仅可选一个）：\n"
            "可选值：eess.SP, physics.optics, physics.app-ph, "
            "eess.IV, eess.AS, cond-mat.mtrl-sci, cond-mat.mes-hall, quant-ph\n"
            "\n"
            "- 仅在用户明确涉及特定方向时填写\n"
            "- 不确定则留空\n"
            "- 不要生成列表之外的值"
        ),
    )
    recent_days: int = Field(
        default=0,
        description=(
            "时间范围（可选）：\n"
            "- 表示只关注最近多少天的论文（如 1=最近一天，7=最近一周）\n"
            "- 0 表示不限制"
        ),
    )
    max_results: int = Field(
        default=5, description="返回论文数量，默认5，最多20", le=20
    )


def build_arxiv_params(
    keywords: list[str],
    author: str = "",
    category: str = "",
    recent_days: int = 0,
    max_results: int = 5,
):
    parts = []

    def normalize_kw(kw: str):
        return f'"{kw}"' if " " in kw else kw

    if keywords:
        kw_query = " OR ".join([normalize_kw(k) for k in keywords])
        parts.append(f"all:({kw_query})")

    if author:
        parts.append(f'au:"{author}"')

    if category:
        parts.append(f"cat:{category}")

    search_query = " AND ".join(parts)

    fetch_n = max_results * 3 if recent_days > 0 else max_results

    params = {
        "search_query": search_query,
        "max_results": fetch_n,
    }

    if recent_days > 0:
        params["sortBy"] = "submittedDate"
        params["sortOrder"] = "descending"

    return params


def filter_by_recent_days(papers, recent_days: int):
    if recent_days <= 0:
        return papers

    cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)

    def parse_time(t: str):
        return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    return [p for p in papers if parse_time(p["published"]) >= cutoff]


@tool(args_schema=ArxivRequest)
def arxiv_tool(
    keywords: list[str],
    author: str = "",
    category: str = "",
    recent_days: int = 0,
    max_results: int = 5,
) -> str:
    """
    在 arXiv 上检索学术论文。

    该工具用于获取某一研究方向的论文列表，适用于：
    - 获取某个领域的最新研究进展
    - 浏览某个主题的相关论文
    - 查找特定作者的论文

    返回结果为论文的结构化信息，包括标题、摘要、作者、发布时间和链接。

    注意：
    - 返回检索结果列表，不是单篇论文
    - 默认按相关性或时间排序（可通过参数控制）
    - 若未检索到论文，将返回空列表
    - 若检索失败，将返回 {"success": False, "error": "...", "papers": []}
    """
    params = build_arxiv_params(keywords, author, category, recent_days, max_results)
    logger.info("[arxiv] params: %s", params)
    url = "http://export.arxiv.org/api/query"

    for _ in range(2):
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            break
        except requests.RequestException:
            time.sleep(1)
    else:
        return json.dumps(
            {"success": False, "error": "request failed", "papers": []},
            ensure_ascii=False,
        )

    feed = feedparser.parse(response.text)
    papers = []
    for entry in feed.entries:
        tags = getattr(entry, "tags", [])
        papers.append(
            {
                "title": entry.title,
                "summary": entry.summary[:300] + "..."
                if len(entry.summary) > 300
                else entry.summary,
                "authors": [a.name for a in getattr(entry, "authors", [])],
                "published": entry.published,
                "updated": entry.updated,
                "arxiv_id": entry.id.split("/")[-1],
                "pdf_url": next(
                    (l.href for l in entry.links if l.type == "application/pdf"),
                    None,
                ),
                "link": entry.link,
                "tags": tags,
                "primary_category": tags[0]["term"] if tags else None,
                "categories": [t["term"] for t in tags],
                "comment": getattr(entry, "arxiv_comment", None),
            }
        )
    filtered = filter_by_recent_days(papers, recent_days)
    result = filtered[:max_results]
    return json.dumps(result, ensure_ascii=False, indent=2)
