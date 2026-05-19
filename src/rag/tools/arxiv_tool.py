"""
arXiv 论文检索工具

## 定位
备用检索渠道 / 预印本优先场景的专用工具。

主力检索请优先使用 s2_search_tool（收录更广、字段更丰富）。
仅在以下情况使用本工具：
  1. 用户明确要查找 arXiv 上刚上传的预印本（S2 对预印本的收录存在数周延迟）
  2. s2_search_tool 返回 rate_limited 错误，作为应急回退

## 输出用途
本工具只负责检索，返回论文的标题、截断/完整摘要、作者、分类、
arXiv ID 和 PDF 链接。

若需要深入阅读某篇论文的 PDF 全文，请将返回的 pdf_url 传给
jina_tool 做语义片段召回，不要在本工具内处理全文。

## 速率限制
arXiv API 无官方 key 机制，请求间隔保持 ≥5s。
429 触发后强制冷却 60s，相同 query 失败后 120s 内不再重试。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from threading import Lock

import feedparser
import requests
from langchain.tools import tool
from pydantic import BaseModel, Field

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ──────────────────────────────────────────────
# 速率限制
# ──────────────────────────────────────────────
_ARXIV_LOCK = Lock()
_LAST_ARXIV_CALL: float = 0.0
_ARXIV_BLOCK_UNTIL: float = 0.0

MIN_ARXIV_INTERVAL = 3.5  # 官方要求 3s/次，取 3.5s 留余量
MAX_RETRIES = 2
_SESSION = requests.Session()

_RECENT_FAILED_QUERY: dict[str, float] = {}
FAILED_QUERY_TTL = 120


def _wait_for_arxiv_rate_limit() -> None:
    global _LAST_ARXIV_CALL, _ARXIV_BLOCK_UNTIL

    with _ARXIV_LOCK:
        now = time.time()

        if now < _ARXIV_BLOCK_UNTIL:
            sleep_time = _ARXIV_BLOCK_UNTIL - now
            logger.warning("[arxiv] 处于429冷却期，等待 %.1f 秒", sleep_time)
            time.sleep(sleep_time)
            now = time.time()

        delta = now - _LAST_ARXIV_CALL
        if delta < MIN_ARXIV_INTERVAL:
            sleep_time = MIN_ARXIV_INTERVAL - delta
            logger.info("[arxiv] rate limit 保护：等待 %.1f 秒", sleep_time)
            time.sleep(sleep_time)

        _LAST_ARXIV_CALL = time.time()


def _is_recently_failed(query_key: str) -> bool:
    failed_at = _RECENT_FAILED_QUERY.get(query_key)
    return bool(failed_at and time.time() - failed_at < FAILED_QUERY_TTL)


def _mark_failed(query_key: str) -> None:
    _RECENT_FAILED_QUERY[query_key] = time.time()


def _error_payload(error_type: str, error: str, retryable: bool) -> str:
    return json.dumps(
        {
            "success": False,
            "error_type": error_type,
            "error": error,
            "retryable": retryable,
            "message": (
                "arXiv API 暂时不可用或触发速率限制。"
                "请勿在同一对话内反复重试相同查询。"
                "如需继续检索，可尝试 s2_search_tool。"
            ),
            "papers": [],
        },
        ensure_ascii=False,
    )


# ──────────────────────────────────────────────
# 参数构造
# ──────────────────────────────────────────────


def _build_arxiv_params(
    keywords: list[str],
    arxiv_ids: list[str],
    author: str,
    category: list[str],
    recent_days: int,
    max_results: int,
) -> dict:
    if arxiv_ids:
        return {
            "id_list": ",".join(arxiv_ids),
            "max_results": len(arxiv_ids),
        }

    parts = []

    def normalize_kw(text: str) -> str:
        text = text.strip()
        return f'"{text}"' if " " in text else text

    if keywords:
        kw_parts = []
        for kw in keywords:
            nk = normalize_kw(kw)
            kw_parts.append(f"ti:{nk}")
            kw_parts.append(f"abs:{nk}")
        parts.append(f"({' OR '.join(kw_parts)})")

    if author:
        parts.append(f'au:"{author}"')

    if category:
        cat_parts = [f"cat:{c}" for c in category]
        parts.append(f"({' OR '.join(cat_parts)})")

    fetch_n = min(max_results * 3, 30) if recent_days > 0 else max_results

    params: dict = {
        "search_query": " AND ".join(parts),
        "max_results": fetch_n,
    }
    if recent_days > 0:
        params["sortBy"] = "submittedDate"
        params["sortOrder"] = "descending"

    return params


def _filter_by_recent_days(papers: list[dict], recent_days: int) -> list[dict]:
    if recent_days <= 0:
        return papers
    cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)

    def parse_time(t: str) -> datetime:
        return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

    return [p for p in papers if parse_time(p["published"]) >= cutoff]


# ──────────────────────────────────────────────
# Tool
# ──────────────────────────────────────────────


class ArxivRequest(BaseModel):
    keywords: list[str] = Field(
        default=[],
        description=(
            "检索关键词列表：\n"
            "- 每个元素为英文技术关键词（1-3个词），如 'transformer', 'optical comb'\n"
            "- 不要使用完整句子或中文\n"
            "- 不要包含无意义词（如 paper, study, method）\n"
            "- 多个关键词之间为 OR 关系\n"
            "- 使用 arxiv_ids 时可留空"
        ),
    )
    arxiv_ids: list[str] = Field(
        default=[],
        description=(
            "arXiv ID 列表（可选）：\n"
            "- 若已知论文的 arXiv ID，直接填写，跳过关键词检索\n"
            "- 格式示例：['2602.09408', '2604.18499']\n"
            "- 填写后将忽略 keywords、author、category、recent_days"
        ),
    )
    author: str = Field(
        default="",
        description=(
            "作者姓名（可选）：\n"
            "- 仅当用户明确指定作者时填写\n"
            "- 只填写人名（如 'Vaswani'），不要附加其他词"
        ),
    )
    category: list[str] = Field(
        default=[],
        description=(
            "arXiv 分类列表（可选）：\n"
            "可选值：eess.SP, physics.optics, physics.app-ph, "
            "eess.IV, eess.AS, cond-mat.mtrl-sci, cond-mat.mes-hall, quant-ph\n"
            "- 多个分类之间为 OR 关系\n"
            "- 不确定则留空，不要生成列表之外的值"
        ),
    )
    recent_days: int = Field(
        default=0,
        description=(
            "时间范围（可选）：最近多少天的论文（1=最近一天，7=最近一周）。\n"
            "0 表示不限制。"
        ),
    )
    max_results: int = Field(
        default=5,
        description="返回论文数量（默认5，最多20）",
        le=20,
    )
    full_abstract: bool = Field(
        default=False,
        description=(
            "是否返回完整摘要（默认False，截断至300字符）：\n"
            "- 批量关键词检索时保持False，避免token超限\n"
            "- 使用 arxiv_ids 精确查询少量论文时可设为True"
        ),
    )


@tool(args_schema=ArxivRequest)
def arxiv_tool(
    keywords: list[str] = [],
    arxiv_ids: list[str] = [],
    author: str = "",
    category: list[str] = [],
    recent_days: int = 0,
    max_results: int = 5,
    full_abstract: bool = False,
) -> str:
    """
    在 arXiv 上检索学术论文。

    ## 使用时机
    本工具是备用检索渠道，请优先使用 s2_search_tool。
    仅在以下两种情况下调用本工具：
      1. 用户明确要查找 arXiv 上刚上传的最新预印本
         （S2 对新预印本的收录存在数周延迟）
      2. s2_search_tool 返回了 rate_limited 错误

    ## 两种查询模式

    ### 模式一：关键词检索（广撒网）
    适用于探索某研究方向或获取最新预印本。
    填写 keywords，可选填 author、category、recent_days。
    返回论文列表，包含标题、截断摘要、作者、分类标签、arXiv ID 和 PDF 链接。

    ### 模式二：ID 精确查询
    已知 arXiv ID 时直接获取论文信息，填写 arxiv_ids，忽略其他检索参数。
    可配合 full_abstract=True 获取完整摘要。

    ## 返回格式

    ### 成功时
    {
        "success": true,
        "count": 实际返回论文数量,
        "papers": [
            {
                "title": 论文标题,
                "summary": 摘要（full_abstract=False 时截断至300字符）,
                "authors": 作者列表,
                "published": 发布时间（ISO 8601）,
                "updated": 更新时间（ISO 8601）,
                "arxiv_id": arXiv ID,
                "pdf_url": PDF 直接下载链接,
                "link": arXiv 页面链接,
                "categories": 所属分类列表,
                "primary_category": 主分类
            },
            ...
        ]
    }

    ### 失败时
    {
        "success": false,
        "error_type": "rate_limited" | "timeout" | "request_failed" | "recent_failed_query",
        "error": 错误详情,
        "retryable": true | false,
        "message": 处理建议（含是否应回退至 s2_search_tool 的提示）,
        "papers": []
    }

    ## 下游工具
    若需深入阅读某篇论文的 PDF 全文，请将返回的 pdf_url 传给 jina_tool。
    本工具不处理全文内容。

    ## 注意
    - 批量检索时保持 full_abstract=False，避免 token 超限
    - 若工具多次返回 rate_limited，停止尝试，切换到 s2_search_tool
    - 相同 query 失败后 120 秒内不会重复请求
    """
    if arxiv_ids:
        recent_days = 0
        max_results = len(arxiv_ids)

    params = _build_arxiv_params(
        keywords, arxiv_ids, author, category, recent_days, max_results
    )
    query_key = json.dumps(params, sort_keys=True)

    if _is_recently_failed(query_key):
        logger.warning("[arxiv] 相同 query 近期失败，跳过重复请求")
        return json.dumps(
            {
                "success": False,
                "error_type": "recent_failed_query",
                "error": "Recent identical query failed",
                "retryable": False,
                "message": "相同的 arXiv 查询近期失败，请勿重复尝试。如需继续检索请使用 s2_search_tool。",
                "papers": [],
            },
            ensure_ascii=False,
        )

    logger.info("[arxiv] params: %s", params)

    url = "https://export.arxiv.org/api/query"
    headers = {"User-Agent": "PhysicsScholar/0.1 (13159331923@163.com)"}
    last_error = ""
    error_type = "request_failed"

    for attempt in range(MAX_RETRIES):
        try:
            _wait_for_arxiv_rate_limit()
            response = _SESSION.get(
                url, params=params, headers=headers, timeout=(10, 30)
            )
            response.raise_for_status()
            break
        except requests.RequestException as e:
            last_error = str(e)
            error_type = "request_failed"

            if isinstance(e, requests.Timeout):
                error_type = "timeout"
            elif isinstance(e, requests.HTTPError) and e.response is not None:
                if e.response.status_code == 429:
                    error_type = "rate_limited"
                    global _ARXIV_BLOCK_UNTIL
                    _ARXIV_BLOCK_UNTIL = time.time() + 60

            if error_type in ("rate_limited", "timeout"):
                _mark_failed(query_key)

            if error_type == "rate_limited":
                backoff = 30.0 * (attempt + 1)
            elif error_type == "timeout":
                backoff = 5.0 * (2**attempt)
            else:
                backoff = 3.0 * (2**attempt)

            logger.warning(
                "[arxiv] 请求失败(type=%s): %s，%.1f 秒后重试 (%d/%d)",
                error_type,
                e,
                backoff,
                attempt + 1,
                MAX_RETRIES,
            )

            if error_type == "rate_limited" and attempt >= 1:
                break

            time.sleep(backoff)
    else:
        logger.error("[arxiv] 请求失败，返回空结果")
        return _error_payload(error_type, last_error, error_type == "timeout")

    feed = feedparser.parse(response.text)
    papers = []
    for entry in feed.entries:
        tags = getattr(entry, "tags", [])
        summary = entry.summary
        papers.append(
            {
                "title": entry.title,
                "summary": summary
                if full_abstract
                else (summary[:300] + "..." if len(summary) > 300 else summary),
                "authors": [a.name for a in getattr(entry, "authors", [])],
                "published": entry.published,
                "updated": entry.updated,
                "arxiv_id": entry.id.split("/")[-1],
                "pdf_url": next(
                    (l.href for l in entry.links if l.type == "application/pdf"), None
                ),
                "link": entry.link,
                "primary_category": tags[0]["term"] if tags else None,
                "categories": [t["term"] for t in tags],
            }
        )

    filtered = _filter_by_recent_days(papers, recent_days)
    logger.info("[arxiv] 时间过滤前 %d 篇，过滤后 %d 篇", len(papers), len(filtered))

    result = filtered if arxiv_ids else filtered[:max_results]
    logger.info("[arxiv] 检索完成：命中 %d 篇，返回 %d 篇", len(papers), len(result))

    return json.dumps(
        {"success": True, "count": len(result), "papers": result},
        ensure_ascii=False,
        indent=2,
    )
