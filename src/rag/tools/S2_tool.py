"""
Semantic Scholar (S2) 论文检索工具

## 定位
主力检索工具。绝大多数论文检索任务优先使用本工具，而非 arxiv_tool。

相比 arxiv_tool 的优势：
  - 收录范围更广（不限于预印本，含已发表期刊/会议论文）
  - 提供引用数、高影响力引用数，便于评估论文影响力
  - 提供 S2 AI 生成的一句话总结（tldr，部分论文有）
  - 提供发表 venue（期刊/会议名称）
  - 支持跨学科 fieldsOfStudy 过滤

劣势（退回 arxiv_tool 的时机）：
  - 对 arXiv 新预印本的收录存在数周延迟；若用户明确要找"最新预印本"，
    请改用 arxiv_tool
  - 本工具触发 rate_limited 时，可回退至 arxiv_tool

## 输出用途
本工具只负责检索，返回论文列表（含标题、摘要、作者、年份、引用数、
s2_paper_id、open_access_pdf 等）。

若需要深入阅读某篇论文的 PDF 全文，请将返回的 open_access_pdf 传给
jina_tool 做语义片段召回，不要在本工具内处理全文。

## 速率限制
  - 有 S2 API Key : 1 req/s（官方限制，工具取 1.1s 留余量）
  - 无 S2 API Key : 100 req/5min，工具保守取 3.5s/次
  - 429 触发后强制冷却 60s，相同 query 失败后 120s 内不再重试
"""

from __future__ import annotations

import json
import time
from threading import Lock

import requests
from langchain.tools import tool
from pydantic import BaseModel, Field

from src.utils.logger import get_logger

# ──────────────────────────────────────────────
# API Key 注入（打包成 exe 后由用户填写）
# ──────────────────────────────────────────────

from src.config import S2_API_KEY

logger = get_logger(__name__)


# ──────────────────────────────────────────────
# 速率限制
# ──────────────────────────────────────────────
_S2_LOCK = Lock()
_LAST_S2_CALL: float = 0.0
_S2_BLOCK_UNTIL: float = 0.0

_INTERVAL_WITH_KEY = 1.1  # 有 key：1.1s/次
_INTERVAL_WITHOUT_KEY = 3.5  # 无 key：3.5s/次（100次/5min 的保守值）

MAX_RETRIES = 2
_SESSION = requests.Session()

_RECENT_FAILED_QUERY: dict[str, float] = {}
FAILED_QUERY_TTL = 120


def _min_interval() -> float:
    return _INTERVAL_WITH_KEY if S2_API_KEY else _INTERVAL_WITHOUT_KEY


def _wait_for_s2_rate_limit() -> None:
    global _LAST_S2_CALL, _S2_BLOCK_UNTIL

    with _S2_LOCK:
        now = time.time()

        if now < _S2_BLOCK_UNTIL:
            sleep_time = _S2_BLOCK_UNTIL - now
            logger.warning("[s2] 处于429冷却期，等待 %.1f 秒", sleep_time)
            time.sleep(sleep_time)
            now = time.time()

        delta = now - _LAST_S2_CALL
        interval = _min_interval()
        if delta < interval:
            sleep_time = interval - delta
            logger.info("[s2] rate limit 保护：等待 %.1f 秒", sleep_time)
            time.sleep(sleep_time)

        _LAST_S2_CALL = time.time()


def _s2_headers() -> dict[str, str]:
    h = {"User-Agent": "PhysicsScholar/0.1 (13159331923@163.com)"}
    if S2_API_KEY:
        h["x-api-key"] = S2_API_KEY
    return h


def _is_recently_failed(query_key: str) -> bool:
    failed_at = _RECENT_FAILED_QUERY.get(query_key)
    return bool(failed_at and time.time() - failed_at < FAILED_QUERY_TTL)


def _mark_failed(query_key: str) -> None:
    _RECENT_FAILED_QUERY[query_key] = time.time()


def _handle_request_error(
    e: requests.RequestException, attempt: int
) -> tuple[str, float]:
    """解析错误类型，设置冷却，返回 (error_type, backoff_seconds)。"""
    global _S2_BLOCK_UNTIL

    error_type = "request_failed"
    if isinstance(e, requests.Timeout):
        error_type = "timeout"
    elif isinstance(e, requests.HTTPError) and e.response is not None:
        if e.response.status_code == 429:
            error_type = "rate_limited"
            _S2_BLOCK_UNTIL = time.time() + 60
        elif e.response.status_code == 404:
            error_type = "not_found"

    if error_type == "rate_limited":
        backoff = 30.0 * (attempt + 1)
    elif error_type == "timeout":
        backoff = 5.0 * (2**attempt)
    else:
        backoff = 3.0 * (2**attempt)

    return error_type, backoff


def _error_payload(error_type: str, error: str, retryable: bool) -> str:
    """系统级/网络级失败返回给 Agent 的报文"""
    return json.dumps(
        {
            "success": False,
            "error_type": error_type,
            "error": error,
            "retryable": retryable,
            "message": (
                "S2 API 暂时不可用、网络超时或触发了服务商的速率限制(429)。"
                "这是系统或网络层面的错误，绝非你的关键词不好！"
                "请勿通过频繁修改关键词来重复尝试此工具。"
                "如需继续检索，评估是否需要启动备用工具 arxiv_tool。"
            ),
            "papers": [],
        },
        ensure_ascii=False,
        indent=2,
    )


# ──────────────────────────────────────────────
# 字段配置
# ──────────────────────────────────────────────

# 批量关键词检索：不含 tldr（search 接口不支持该字段）
_SEARCH_FIELDS = (
    "paperId,externalIds,title,abstract,authors,year,"
    "publicationDate,venue,publicationTypes,"
    "openAccessPdf,citationCount,influentialCitationCount,"
    "fieldsOfStudy,s2FieldsOfStudy"
)

# 精确 ID 查询：额外含 tldr
_DETAIL_FIELDS = _SEARCH_FIELDS + ",tldr"

_S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_S2_PAPER_URL = "https://api.semanticscholar.org/graph/v1/paper/{paper_id}"


# ──────────────────────────────────────────────
# 内部辅助
# ──────────────────────────────────────────────


def _fmt_abstract(abstract: str | None, full: bool) -> str:
    if not abstract:
        return ""
    if full or len(abstract) <= 300:
        return abstract
    return abstract[:300] + "..."


def _parse_paper(entry: dict, full_abstract: bool) -> dict:
    authors = [a.get("name", "") for a in entry.get("authors", [])]
    ext_ids = entry.get("externalIds") or {}
    oa_pdf = entry.get("openAccessPdf") or {}
    paper_id = entry.get("paperId", "")
    return {
        "title": entry.get("title", ""),
        "abstract": _fmt_abstract(entry.get("abstract"), full_abstract),
        "tldr": (entry.get("tldr") or {}).get("text", ""),
        "authors": authors,
        "year": entry.get("year"),
        "publication_date": entry.get("publicationDate"),
        "venue": entry.get("venue", ""),
        "publication_types": entry.get("publicationTypes", []),
        "fields_of_study": entry.get("fieldsOfStudy", []),
        "citation_count": entry.get("citationCount"),
        "influential_citation_count": entry.get("influentialCitationCount"),
        "s2_paper_id": paper_id,
        "arxiv_id": ext_ids.get("ArXiv"),
        "doi": ext_ids.get("DOI"),
        "open_access_pdf": oa_pdf.get("url"),
        "s2_url": (
            f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else None
        ),
    }


def _fetch_single_paper(
    request_id: str, full_abstract: bool
) -> tuple[dict | None, str | None]:
    """通过 /paper/{id} 精确拉取单篇论文。
    返回: (parsed_paper_dict_或者_None, error_type_字符串_或者_None)"""
    query_key = f"paper::{request_id}"
    if _is_recently_failed(query_key):
        logger.warning("[s2] 跳过近期失败的 ID: %s", request_id)
        return None, "recent_failed_query"

    url = _S2_PAPER_URL.format(paper_id=request_id)
    last_error_type = None

    for attempt in range(MAX_RETRIES):
        try:
            _wait_for_s2_rate_limit()
            resp = _SESSION.get(
                url,
                params={"fields": _DETAIL_FIELDS},
                headers=_s2_headers(),
                timeout=(10, 30),
            )
            resp.raise_for_status()
            return _parse_paper(resp.json(), full_abstract), None

        except requests.RequestException as e:
            error_type, backoff = _handle_request_error(e, attempt)

            last_error_type = error_type

            if error_type == "not_found":
                # 404 说明真的没有这篇论文，不需要重试，直接返回
                return None, "not_found"

            if error_type in ("rate_limited", "timeout"):
                _mark_failed(query_key)
            logger.warning(
                "[s2] ID精确查询异常(type=%s): %s，%.1f 秒后重试 (%d/%d)",
                error_type,
                e,
                backoff,
                attempt + 1,
                MAX_RETRIES,
            )
            if error_type == "rate_limited" and attempt >= 1:
                break
            time.sleep(backoff)
        except Exception as e:
            logger.error("[s2] 解析 ID %s 响应严重失败: %s", request_id, e)
            return None, "parse_error"

    return None, last_error_type


# ──────────────────────────────────────────────
# Tool Schema
# ──────────────────────────────────────────────


class S2SearchRequest(BaseModel):
    keywords: list[str] = Field(
        default=[],
        description=(
            "检索关键词列表：\n"
            "- 每个元素为英文技术关键词（1-3个词），如 'transformer', 'optical comb'\n"
            "- 不要使用完整句子或中文\n"
            "- 不要包含无意义词（如 paper, study, method）\n"
            "- 多个关键词之间为 AND 关系（S2 默认行为）\n"
            "- 可用引号包裹短语，如 '\"attention mechanism\"'\n"
            "- 使用 s2_paper_ids 或 arxiv_ids 精确查询时可留空"
        ),
    )
    s2_paper_ids: list[str] = Field(
        default=[],
        description=(
            "S2 Paper ID 列表（可选）：\n"
            "- 若已知 S2 Paper ID（从本工具历史结果或对话历史中获取），直接填写\n"
            "- 填写后将忽略 keywords、author、year_range、fields_of_study\n"
            "- 建议配合 full_abstract=True 获取完整摘要\n"
            "- 可同时传入多个，逐一精确查询"
        ),
    )
    arxiv_ids: list[str] = Field(
        default=[],
        description=(
            "arXiv ID 列表（可选）：\n"
            "- S2 支持以 arXiv ID 直接查询，格式如 '2301.07041'（工具内自动加前缀）\n"
            "- 填写后将忽略 keywords、author、year_range、fields_of_study\n"
            "- 适用于已从 arxiv_tool 获得 arxiv_id、想在 S2 补充引用数等字段的场景"
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
    year_range: str = Field(
        default="",
        description=(
            "发表年份范围（可选）：\n"
            "- 格式：'2020-2024'（区间）、'2023'（单年）、'2020-'（某年至今）\n"
            "- 留空不限制"
        ),
    )
    fields_of_study: list[str] = Field(
        default=[],
        description=(
            "研究领域过滤（可选）：\n"
            "可选值：Computer Science, Physics, Engineering, Mathematics, "
            "Biology, Medicine, Chemistry, Economics\n"
            "- 不确定则留空"
        ),
    )
    max_results: int = Field(
        default=5,
        description=(
            "返回论文数量（默认5，最多20）：\n"
            "- 关键词批量检索时建议保持默认值\n"
            "- 使用精确 ID 查询时无需设置，返回数量由 ID 列表长度决定"
        ),
        le=20,
    )
    full_abstract: bool = Field(
        default=False,
        description=(
            "是否返回完整摘要（默认False，截断至300字符）：\n"
            "- 批量关键词检索时保持False，避免token超限\n"
            "- 精确 ID 查询少量论文时可设为True"
        ),
    )


def _build_search_query(keywords: list[str], author: str) -> str:
    parts = []
    if keywords:
        parts.append(" ".join(keywords))
    if author:
        parts.append(f"author:{author}")
    return " ".join(parts)


@tool(args_schema=S2SearchRequest)
def s2_search_tool(
    keywords: list[str] = [],
    s2_paper_ids: list[str] = [],
    arxiv_ids: list[str] = [],
    author: str = "",
    year_range: str = "",
    fields_of_study: list[str] = [],
    max_results: int = 5,
    full_abstract: bool = False,
) -> str:
    """
    在 Semantic Scholar (S2) 上检索学术论文。

    ## 使用时机
    本工具是主力检索工具，绝大多数论文检索任务优先使用本工具。
    仅在以下情况改用 arxiv_tool：
      1. 用户明确要查找 arXiv 上刚上传的最新预印本（S2 有数周收录延迟）
      2. 本工具返回 rate_limited 错误

    ## 三种查询模式

    ### 模式一：关键词检索（广撒网）
    填写 keywords，可选填 author、year_range、fields_of_study。
    返回论文列表，包含标题、截断摘要、引用数、s2_paper_id、open_access_pdf 等。
    建议根据标题和引用数筛选目标论文，记录 s2_paper_id 供后续精确查询。

    ### 模式二：S2 Paper ID 精确查询
    已知 S2 Paper ID 时，填写 s2_paper_ids，配合 full_abstract=True 获取完整摘要。
    返回额外包含 tldr（S2 AI 生成的一句话总结）。
    对话历史中已出现的 s2_paper_id 视为可信，直接使用，无需关键词二次确认。

    ### 模式三：arXiv ID 精确查询
    已知 arXiv ID 时，填写 arxiv_ids。
    通过 S2 获取该论文的完整信息（引用数、venue 等 arXiv 本身不提供的字段）。
    适合在 arxiv_tool 检索后，用 S2 补充元数据的场景。

    ## 返回格式

    ### 成功时
    {
        "success": true,
        "count": 实际返回论文数量,
        "has_s2_key": 是否使用了 API Key（影响速率上限）,
        "message": 情况详释,
        "papers": [
            {
                "title": 论文标题,
                "abstract": 摘要（full_abstract=False 时截断至300字符）,
                "tldr": S2 AI 一句话总结（精确查询时有，批量检索时为空）,
                "authors": 作者列表,
                "year": 发表年份,
                "publication_date": 精确发表日期（部分论文有）,
                "venue": 发表期刊/会议（预印本通常为空）,
                "publication_types": 论文类型列表,
                "fields_of_study": 研究领域,
                "citation_count": 引用数,
                "influential_citation_count": 高影响力引用数,
                "s2_paper_id": S2 Paper ID,
                "arxiv_id": arXiv ID（若有）,
                "doi": DOI（若有）,
                "open_access_pdf": 开放获取 PDF URL（若有，可传给 jina_tool）,
                "s2_url": S2 论文页面链接
            },
            ...
        ]
    }

    ### 失败时
    {
        "success": false,
        "error_type": "rate_limited" | "timeout" | "request_failed"
                      | "recent_failed_query" | "invalid_params",
        "error": 错误详情,
        "retryable": true | false,
        "message": 情况详释与处理建议
        "papers": []
    }

    ## 下游工具
    若需深入阅读某篇论文的 PDF 全文，请将 open_access_pdf 字段的值传给 jina_tool。
    本工具不处理全文内容。

    ## 注意
    - 批量检索时保持 full_abstract=False，避免 token 超限
    - 若工具多次返回 rate_limited，停止尝试，切换到 arxiv_tool
    - 相同 query 失败后 120 秒内不会重复请求
    - 无 API Key 时速率限制更严格（3.5 秒/次），对话内请合理规划调用次数
    """
    # ── 模式二 & 三：精确 ID 查询 ──
    if s2_paper_ids or arxiv_ids:
        ids_to_fetch: list[str] = []
        for pid in s2_paper_ids:
            ids_to_fetch.append(pid.strip())
        for aid in arxiv_ids:
            ids_to_fetch.append(f"arXiv:{aid.strip()}")

        results = []
        stat_not_found = 0
        stat_failed = 0

        for request_id in ids_to_fetch:
            paper, err = _fetch_single_paper(request_id, full_abstract)
            if err is None and paper is not None:
                results.append(paper)
            elif err == "not_found":
                stat_not_found += 1
            else:
                stat_failed += 1
                # 如果遇到了网络错或 429，为了防止 Agent 误判，直接中断并返回错误 Payload
                if err in ("rate_limited", "timeout", "request_failed"):
                    logger.error("[s2] ID查询过程中断：因遭遇系统级错误 %s", err)
                    return _error_payload(
                        err, f"Failed while fetching ID: {request_id}", retryable=True
                    )

        logger.info(
            "[s2] ID精确查询完成。总请求数: %d | 成功拉取: %d | 确认不存在(404): %d | 内部失败: %d",
            len(ids_to_fetch),
            len(results),
            stat_not_found,
            stat_failed,
        )

        # 组装成功报文（即使 count 为 0，只要 success=True 且没有系统阻断，说明状态可信）
        message = ""
        if not results:
            message = (
                "查询请求已完全成功执行，但在数据库中没有找到对应的 ID 条目（可能已被删除或ID输入错误）。"
                "这不是网络问题，请勿盲目重试。请检查你的 ID 来源，或改用关键词搜索模式。"
            )

        return json.dumps(
            {
                "success": True,
                "count": len(results),
                "has_s2_key": bool(S2_API_KEY),
                "message": message,
                "papers": results,
            },
            ensure_ascii=False,
            indent=2,
        )

    # ── 模式一：关键词检索 ──
    query = _build_search_query(keywords, author)
    if not query.strip():
        return json.dumps(
            {
                "success": False,
                "error_type": "invalid_params",
                "error": "keywords、s2_paper_ids、arxiv_ids 和 author 不能同时为空",
                "retryable": False,
                "message": "请至少提供 keywords、s2_paper_ids、arxiv_ids 或 author 之一。",
                "papers": [],
            },
            ensure_ascii=False,
        )

    params: dict = {
        "query": query,
        "fields": _SEARCH_FIELDS,
        "limit": min(max_results, 20),
    }
    if year_range:
        params["year"] = year_range
    if fields_of_study:
        params["fieldsOfStudy"] = ",".join(fields_of_study)

    query_key = json.dumps(params, sort_keys=True)
    if _is_recently_failed(query_key):
        logger.warning("[s2] 相同 query 近期失败，跳过重复请求")
        return json.dumps(
            {
                "success": False,
                "error_type": "recent_failed_query",
                "error": "Recent identical query failed",
                "retryable": False,
                "message": "相同的 S2 查询近期失败，请勿重复尝试。如需继续检索请使用 arxiv_tool。",
                "papers": [],
            },
            ensure_ascii=False,
        )

    logger.info("[s2] 关键词检索 params: %s", params)

    last_error = ""
    error_type = "request_failed"
    resp_success = False

    for attempt in range(MAX_RETRIES):
        try:
            _wait_for_s2_rate_limit()
            resp = _SESSION.get(
                _S2_SEARCH_URL,
                params=params,
                headers=_s2_headers(),
                timeout=(10, 30),
            )
            resp.raise_for_status()
            resp_success = True

            break
        except requests.RequestException as e:
            last_error = str(e)
            error_type, backoff = _handle_request_error(e, attempt)
            if error_type in ("rate_limited", "timeout"):
                _mark_failed(query_key)
            logger.warning(
                "[s2] 关键词检索失败(type=%s): %s，%.1f 秒后重试 (%d/%d)",
                error_type,
                e,
                backoff,
                attempt + 1,
                MAX_RETRIES,
            )
            if error_type == "rate_limited" and attempt >= 1:
                break
            time.sleep(backoff)

    if not resp_success:
        logger.error("[s2] 关键词检索彻底失败，触发错误回退")

        return _error_payload(error_type, last_error, error_type == "timeout")

    try:
        data = resp.json()
    except Exception as e:
        return _error_payload("parse_error", str(e), False)

    raw_papers = data.get("data", [])
    papers = [_parse_paper(p, full_abstract) for p in raw_papers]

    # 核心优化：如果请求 200 成功，但是返回了 0 篇论文，给 Agent 打上明确的补丁指南
    message = ""
    if len(papers) == 0:
        logger.info("[s2] 关键词检索完成：API调用成功，但检索结果为 0 篇 (零命中)")
        message = (
            "API 检索已完全成功，但是该关键词组合在数据库中【未命中任何论文】。"
            "这不是网络或服务故障，请勿盲目使用完全相同的参数重试。建议行动：1. 减少关键词数量（放宽条件）；"
            "2. 替换过于生僻的词，改用更通用的学术专业名词；3. 检查单词拼写。"
        )
    else:
        logger.info("[s2] 关键词检索完成：成功返回 %d 篇论文", len(papers))

    return json.dumps(
        {
            "success": True,
            "count": len(papers),
            "has_s2_key": bool(S2_API_KEY),
            "message": message,
            "papers": papers,
        },
        ensure_ascii=False,
        indent=2,
    )
