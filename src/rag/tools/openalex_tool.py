"""
OpenAlex 论文摘要补全工具

## 定位
s2_search_tool 的下位补全工具，专注解决 S2 缺失摘要的问题。

触发条件（满足其一即可）：
  1. s2_search_tool 返回一篇或多篇目标论文 has_abstract=false 且具备 DOI
     → 用 doi 精确/批量查询 OpenAlex 补全摘要
  2. s2_search_tool 触发 rate_limited 完全不可用
     → 用 keywords 作为应急检索渠道

不适合场景：
  - 常规论文探索（用 s2_search_tool）
  - 预印本最新进展（用 arxiv_tool）
  - 全文阅读（用 jina_tool）

## 认证方式
OpenAlex 使用 polite pool 机制，无传统 API Key。
配置邮箱后进入更稳定的服务器池，速率更高。
未配置时降级为匿名池。

## 速率限制
  polite pool（有邮箱）: 工具取 0.5s/次
  匿名池（无邮箱）     : 10 req/s 官方上限，工具取 1.5s/次
  429 触发后冷却 60s，相同请求失败后 120s 内不再重试
"""

from __future__ import annotations

import json
import time
from threading import Lock
from typing import Optional
import hashlib

import requests
from langchain.tools import tool
from pydantic import BaseModel, Field

from src.config import OPENALEX_EMAIL
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════
# 速率限制
# ══════════════════════════════════════════════════════════════════

_OA_LOCK = Lock()
_LAST_OA_CALL: float = 0.0
_OA_BLOCK_UNTIL: float = 0.0

_INTERVAL_WITH_EMAIL = 0.5  # polite pool
_INTERVAL_WITHOUT_EMAIL = 1.5  # 匿名池：10 req/s，取 1.5s 留余量

MAX_RETRIES = 2
_SESSION = requests.Session()

_RECENT_FAILED: dict[str, float] = {}
FAILED_TTL = 120


def _oa_interval() -> float:
    return _INTERVAL_WITH_EMAIL if OPENALEX_EMAIL else _INTERVAL_WITHOUT_EMAIL


def _wait_for_oa_rate_limit() -> None:
    global _LAST_OA_CALL, _OA_BLOCK_UNTIL
    with _OA_LOCK:
        now = time.time()
        if now < _OA_BLOCK_UNTIL:
            wait = _OA_BLOCK_UNTIL - now
            logger.warning("[openalex] 冷却期，等待 %.1f 秒", wait)
            time.sleep(wait)
            now = time.time()
        delta = now - _LAST_OA_CALL
        interval = _oa_interval()
        if delta < interval:
            time.sleep(interval - delta)
        _LAST_OA_CALL = time.time()


def _cleanup_recent_failed():
    now = time.time()
    expired = [k for k, v in _RECENT_FAILED.items() if now - v >= FAILED_TTL]
    for k in expired:
        _RECENT_FAILED.pop(k, None)


def _is_recently_failed(key: str) -> bool:
    t = _RECENT_FAILED.get(key)
    _cleanup_recent_failed()
    return bool(t and time.time() - t < FAILED_TTL)


def _mark_failed(key: str) -> None:
    _RECENT_FAILED[key] = time.time()
    _cleanup_recent_failed()


def _handle_oa_error(e: requests.RequestException, attempt: int) -> tuple[str, float]:
    global _OA_BLOCK_UNTIL
    error_type = "request_failed"
    if isinstance(e, requests.Timeout):
        error_type = "timeout"
    elif isinstance(e, requests.HTTPError) and e.response is not None:
        if e.response.status_code == 429:
            error_type = "rate_limited"
            _OA_BLOCK_UNTIL = time.time() + 60
        elif e.response.status_code == 404:
            error_type = "not_found"
        elif e.response.status_code in (401, 403):
            error_type = "auth_error"
    backoff = {
        "rate_limited": 30.0 * (attempt + 1),
        "timeout": 5.0 * (2**attempt),
    }.get(error_type, 3.0 * (2**attempt))
    return error_type, backoff


def _error_payload(error_type: str, error: str, retryable: bool) -> str:
    msg_map = {
        "rate_limited": "OpenAlex API 触发速率限制，请勿重复请求。",
        "timeout": "OpenAlex 请求超时。",
        "request_failed": "OpenAlex 请求失败。",
        "not_found": "OpenAlex 中未找到该 DOI 对应的论文。",
        "recent_failed": "相同请求近期失败，120 秒内不再重试。",
        "invalid_params": "doi 和 keywords 不能同时为空。",
    }
    return json.dumps(
        {
            "success": False,
            "error_type": error_type,
            "error": error,
            "retryable": retryable,
            "agent_hint": msg_map.get(error_type, "未知错误。"),
            "papers": [],
        },
        ensure_ascii=False,
    )


# ══════════════════════════════════════════════════════════════════
# OpenAlex 端点与解析
# ══════════════════════════════════════════════════════════════════

_OA_WORKS_URL = "https://api.openalex.org/works"

# 只拉需要的字段，减少响应体积
_SELECT_FIELDS = ",".join(
    [
        "id",
        "doi",
        "title",
        "abstract_inverted_index",
        "authorships",
        "publication_year",
        "publication_date",
        "primary_location",
        "best_oa_location",
        "cited_by_count",
        "ids",
        "type",
    ]
)


def _base_params(extra: dict | None = None) -> dict:
    """构造基础请求参数，polite pool 邮箱自动注入。"""
    p: dict = {"select": _SELECT_FIELDS}
    if OPENALEX_EMAIL:
        p["mailto"] = OPENALEX_EMAIL
    if extra:
        p.update(extra)
    return p


def _reconstruct_abstract(inverted_index: dict | None) -> str:
    """
    OpenAlex 以倒排索引存储摘要：{"word": [pos1, pos2, ...], ...}
    还原为正常字符串。
    """
    if not inverted_index:
        return ""
    pos_word: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            pos_word.append((pos, word))
    pos_word.sort(key=lambda x: x[0])
    return " ".join(w for _, w in pos_word)


def _extract_arxiv_id(ids: dict | None) -> str | None:
    if not ids:
        return None
    arxiv_url = ids.get("arxiv", "")
    if arxiv_url:
        # 格式：https://arxiv.org/abs/2301.00001
        return arxiv_url.rstrip("/").split("/")[-1]
    return None


def _parse_work(work: dict, full_abstract: bool) -> dict:
    """将 OpenAlex work 解析为与 s2_search_tool 字段对齐的格式。"""
    abstract_raw = _reconstruct_abstract(work.get("abstract_inverted_index"))
    if abstract_raw and not full_abstract and len(abstract_raw) > 300:
        abstract = abstract_raw[:300] + "..."
    else:
        abstract = abstract_raw

    authorships = work.get("authorships") or []
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in authorships
        if a.get("author")
    ]

    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    venue = source.get("display_name", "")

    best_oa = work.get("best_oa_location") or {}
    open_access_pdf = best_oa.get("pdf_url")

    doi_raw = work.get("doi") or ""
    doi = doi_raw.replace("https://doi.org/", "").strip() if doi_raw else ""

    ids = work.get("ids") or {}
    arxiv_id = _extract_arxiv_id(ids)

    openalex_raw = work.get("id") or ""
    openalex_id = openalex_raw.replace("https://openalex.org/", "").strip()

    return {
        # ── s2_search_tool 对齐字段 ──
        "title": work.get("title", ""),
        "abstract": abstract,
        "has_abstract": bool(abstract_raw),
        "authors": authors,
        "year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "venue": venue,
        "publication_types": [work.get("type", "")] if work.get("type") else [],
        "citation_count": work.get("cited_by_count"),
        "influential_citation_count": None,  # OpenAlex 不提供
        "s2_paper_id": None,  # OpenAlex 不提供
        "doi": doi,
        "arxiv_id": arxiv_id,
        "open_access_pdf": open_access_pdf,
        "s2_url": None,  # OpenAlex 不提供
        # ── OpenAlex 专有字段 ──
        "openalex_id": openalex_id,
        "openalex_url": openalex_raw,
    }


def _do_request(
    url: str, params: dict, fail_key: str
) -> tuple[dict | None, str | None]:
    """执行带重试的 GET 请求，返回 (json_data, error_type_or_None)。"""
    for attempt in range(MAX_RETRIES):
        try:
            _wait_for_oa_rate_limit()
            resp = _SESSION.get(url, params=params, timeout=(10, 30))
            resp.raise_for_status()
            return resp.json(), None
        except requests.RequestException as e:
            error_type, backoff = _handle_oa_error(e, attempt)
            if error_type in ("rate_limited", "timeout"):
                _mark_failed(fail_key)
            logger.warning(
                "[openalex] 请求失败(type=%s): %s，%.1f 秒后重试 (%d/%d)",
                error_type,
                e,
                backoff,
                attempt + 1,
                MAX_RETRIES,
            )
            # 不可重试的错误直接终止
            if error_type in ("rate_limited", "auth_error", "not_found"):
                return None, error_type
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)

    _mark_failed(fail_key)
    return None, "request_failed"


# ══════════════════════════════════════════════════════════════════
# Tool 参数
# ══════════════════════════════════════════════════════════════════


class OpenAlexRequest(BaseModel):
    doi: str = Field(
        default="",
        description=(
            "DOI 精确查询（单篇模式）：\n"
            "- 仅在只有【一篇】论文需要补全时使用\n"
            "- 来源：s2_search_tool 返回的 doi 字段\n"
            "- 格式：'10.1038/s41586-021-03965-7'（不含 https://doi.org/ 前缀）"
        ),
    )
    dois: list[str] = Field(
        default=[],
        description=(
            "批量 DOI 查询（⭐多篇模式，强烈推荐）：\n"
            "- 只要有多篇论文需要补全摘要，【务必】使用本字段，避免循环调用单 doi 模式，以极大提升效率！\n"
            "- 格式：['10.1038/s41586-021-03965-7', '10.1126/science.abc1234']\n"
            "- 单次最多 50 个，超出部分自动截断\n"
            "- 与 doi 参数二选一，同时填写时 dois 优先"
        ),
    )
    keywords: list[str] = Field(
        default=[],
        description=(
            "关键词检索（备用模式）：\n"
            "- doi 为空时生效\n"
            "- 仅在 s2_search_tool 完全不可用时使用\n"
            "- 每个元素为英文技术关键词（1-3个词）"
        ),
    )
    year_range: str = Field(
        default="",
        description=(
            "发表年份范围（关键词检索时可选）：\n"
            "- 格式：'2020-2024'（区间）、'2020-'（某年至今）\n"
            "- 留空不限制"
        ),
    )
    only_with_abstract: bool = Field(
        default=True,
        description=(
            "是否只返回有摘要的论文（默认True，关键词检索时生效）：\n"
            "- 本工具主要用途是补摘要，建议保持True"
        ),
    )
    max_results: int = Field(
        default=5,
        description="关键词检索返回数量（默认5，最多20）",
        le=20,
    )
    full_abstract: bool = Field(
        default=True,
        description=(
            "是否返回完整摘要（默认True，通常无需修改）。\n"
            "- 关键词批量检索且结果较多时可设为False控制token用量"
        ),
    )


# ══════════════════════════════════════════════════════════════════
# Tool
# ══════════════════════════════════════════════════════════════════


@tool(args_schema=OpenAlexRequest)
def openalex_tool(
    doi: str = "",
    dois: Optional[list[str]] = None,
    keywords: Optional[list[str]] = None,
    year_range: str = "",
    only_with_abstract: bool = True,
    max_results: int = 5,
    full_abstract: bool = True,
) -> str:
    """
        从 OpenAlex 补全论文摘要，作为 s2_search_tool 的下位工具。

        ## 三种模式
        ### 批量 DOI 查询（dois 参数非空）（⭐推荐）
        一次查询多篇 DOI。适合批量补全 s2_search_tool 缺失摘要的论文库。单次最多 50 个。

        ### DOI 精确查询（doi 参数非空且 dois 为空）
        仅补全单篇论文时使用。结果唯一，无歧义。配合 full_abstract=True。

        ### 关键词检索（doi/dois 为空，填写 keywords）
        s2_search_tool 不可用时的降级备用检索渠道。
        遵循与 s2_search_tool 相同的两步逻辑：
        先以 full_abstract=False 关键词检索获取候选列表，筛选目标后再以 DOI 精确查询获取完整摘要。

        ## 返回格式

        ### 成功时
        {
            "success": true,
            "count": 返回论文数,
            "has_polite_pool": 是否配置了邮箱（影响速率）,
            "mode": "doi_lookup" | "batch_doi_lookup" | "keyword_search",
            "missing_dois": 未在 OpenAlex 找到的 DOI 列表（batch_doi_lookup 模式专有）,
            "papers": [
                {
                    字段与 s2_search_tool 对齐，以下字段固定为 null：
                      influential_citation_count, s2_paper_id, s2_url
                    以下字段为 OpenAlex 专有：
                      openalex_id, openalex_url
                },
                ...
            ],
            "agent_hint": 情况说明
        }

        ### 失败时
        {
            "success": false,
            "error_type": "rate_limited" | "timeout" | "request_failed"
                          | "not_found" | "recent_failed" | "invalid_params",
            "error": 错误详情,
            "retryable": true | false,
            "agent_hint": 处理建议,
            "papers": []
        }

    ## 注意
    - 强烈建议：只要存在多个缺失摘要的 DOI，请坚决使用 dois 字段组合查询，绝不要多次调用单一 doi 查询！
    - 关键词检索时保持 full_abstract=False，避免 token 超限；确定目标论文后再用 DOI 精确查询获取完整摘要
    - influential_citation_count / s2_paper_id / s2_url 固定为 null
    """
    dois = dois or []
    keywords = keywords or []

    # 兼容处理：统一清理 DOI 格式
    def _clean_doi(d: str) -> str:
        return d.strip().replace("https://doi.org/", "").replace("http://doi.org/", "")

    doi = _clean_doi(doi)
    dois = [_clean_doi(d) for d in dois if d.strip()]

    if not dois and not doi and not keywords:
        return _error_payload(
            "invalid_params", "dois、doi 和 keywords 不能同时为空", False
        )

    # ════════════════════════════════════════
    # 模式零：批量 DOI 查询（dois 非空时优先）
    # ════════════════════════════════════════
    if dois:
        # 单次最多 50 个
        dois = dois[:50]
        fail_key = "dois::" + hashlib.md5("|".join(sorted(dois)).encode()).hexdigest()
        if _is_recently_failed(fail_key):
            logger.warning("[openalex] 批量 DOI 近期失败，跳过")
            return _error_payload(
                "recent_failed", "Recent batch DOI query failed", False
            )

        filter_str = "doi:" + "|".join(dois)
        params = _base_params(
            {
                "filter": filter_str,
                "per-page": len(dois),  # 结果数不超过请求的 DOI 数
            }
        )

        logger.info("[openalex] 批量 DOI 查询: %d 篇", len(dois))
        data, err = _do_request(_OA_WORKS_URL, params, fail_key)

        if err:
            return _error_payload(err, f"请求失败: {err}", err == "timeout")

        raw_results = (data or {}).get("results", [])
        papers = [_parse_work(w, full_abstract) for w in raw_results]

        found_dois = {p["doi"].lower() for p in papers}
        missing = [d for d in dois if d.lower() not in found_dois]

        no_abstract_papers = [p for p in papers if not p["has_abstract"]]
        hint_parts = [f"批量 DOI 查询：请求 {len(dois)} 篇，找到 {len(papers)} 篇。"]
        if no_abstract_papers:
            no_abstract_titles = [
                p.get("title", doi) for p, doi in zip(no_abstract_papers, dois)
            ]
            hint_parts.append(
                f"以下 {len(no_abstract_papers)} 篇在 OpenAlex 中也无摘要："
                f"{'; '.join(no_abstract_titles[:5])}{'……' if len(no_abstract_papers) > 5 else ''}。"
                "若论文有 arxiv_id，可尝试 arxiv_tool；若有 open_access_pdf，可尝试 jina_tool 读取全文开头。"
            )

        logger.info("[openalex] 批量 DOI 完成：%d/%d 篇找到", len(papers), len(dois))

        return json.dumps(
            {
                "success": True,
                "count": len(papers),
                "has_polite_pool": bool(OPENALEX_EMAIL),
                "mode": "batch_doi_lookup",
                "missing_dois": missing,
                "papers": papers,
                "agent_hint": "".join(hint_parts),
            },
            ensure_ascii=False,
            indent=2,
        )

    # ════════════════════════════════════════
    # 模式一：DOI 精确查询
    # ════════════════════════════════════════
    if doi:
        fail_key = f"doi::{doi}"
        if _is_recently_failed(fail_key):
            logger.warning("[openalex] DOI 近期失败，跳过: %s", doi)
            return _error_payload("recent_failed", f"DOI {doi} 近期请求失败", False)

        url = f"{_OA_WORKS_URL}/doi:{doi}"
        params = _base_params()

        logger.info("[openalex] DOI 精确查询: %s", doi)
        data, err = _do_request(url, params, fail_key)

        if err:
            return _error_payload(err, f"请求失败: {err}", err == "timeout")
        if not data:
            return _error_payload("not_found", f"DOI {doi} 在 OpenAlex 中未找到", False)

        paper = _parse_work(data, full_abstract)
        logger.info("[openalex] DOI 查询完成: has_abstract=%s", paper["has_abstract"])

        if paper["has_abstract"]:
            hint = "摘要获取成功。"
        else:
            hint = (
                "OpenAlex 中该论文也无摘要。"
                "若论文有 arxiv_id，可尝试 arxiv_tool；"
                "若有 open_access_pdf，可尝试 jina_tool 读取全文开头。"
            )

        return json.dumps(
            {
                "success": True,
                "count": 1,
                "has_polite_pool": bool(OPENALEX_EMAIL),
                "mode": "doi_lookup",
                "papers": [paper],
                "agent_hint": hint,
            },
            ensure_ascii=False,
            indent=2,
        )

    # ════════════════════════════════════════
    # 模式二：关键词检索
    # ════════════════════════════════════════
    query = " ".join(keywords)
    fail_key = f"kw::{query}"
    if _is_recently_failed(fail_key):
        logger.warning("[openalex] 相同 query 近期失败，跳过")
        return _error_payload("recent_failed", "Recent identical query failed", False)

    filters: list[str] = []
    if only_with_abstract:
        filters.append("has_abstract:true")
    if year_range:
        # OpenAlex year 过滤格式：publication_year:2020-2024
        filters.append(f"publication_year:{year_range}")

    params = _base_params(
        {
            "search": query,
            "per-page": min(max_results, 20),
            "sort": "cited_by_count:desc",
        }
    )
    if filters:
        params["filter"] = ",".join(filters)

    logger.info("[openalex] 关键词检索 params: %s", params)
    data, err = _do_request(_OA_WORKS_URL, params, fail_key)

    if err:
        return _error_payload(err, f"请求失败: {err}", err == "timeout")

    raw_results = (data or {}).get("results", [])
    papers = [_parse_work(w, full_abstract) for w in raw_results]

    logger.info("[openalex] 关键词检索完成：返回 %d 篇", len(papers))

    return json.dumps(
        {
            "success": True,
            "count": len(papers),
            "has_polite_pool": bool(OPENALEX_EMAIL),
            "mode": "keyword_search",
            "papers": papers,
            "agent_hint": (f"关键词检索返回 {len(papers)} 篇（仅有摘要的论文）。"),
        },
        ensure_ascii=False,
        indent=2,
    )
