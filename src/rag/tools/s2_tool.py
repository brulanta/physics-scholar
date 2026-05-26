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
            "agent_hint": (
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
_S2_SEARCH_BULK_URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
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
        "has_abstract": bool(entry.get("abstract")),
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
            "检索关键词列表（核心技术词，极其重要）：\n"
            "- CRITICAL: keyword 会被按 AND 关系联合检索，因此数量必须极简！通常 1-3 个核心词即可。\n"
            "- 推荐使用英文技术关键词或短语，如 ['photonic', 'ADC']、['optical comb']。\n"
            "- 不要写完整句子、论文摘要式描述、长修饰语，错误示例：['different half-wave voltage']。\n"
            "- 严禁把论文标题拆成多个碎词加入 keyword，这会极大降低召回率。\n"
            "- 如果目标是某篇已知论文，请直接将【完整论文标题】作为唯一元素，例如：['An electrooptic analog-to-digital converter']，此时不要再附加其他关键词。\n"
            "- 同一个检索意图如果有多种表达（如 same half-wave voltage 和 equal Vpi），请选择其中一种最通用的。"
            "- 可使用引号包裹固定短语，如 ['\"attention mechanism\"']。\n"
            "- 不要包含无意义泛词，如 paper、study、method、approach。\n"
            "- 严禁包含年份数字（如 1975），年份请填写到 year_range 字段。\n"
            "- 使用 s2_paper_ids 或 arxiv_ids 精确查询时，可留空。"
        ),
    )
    s2_paper_ids: list[str] = Field(
        default=[],
        description=(
            "S2 Paper ID 列表（可选）：\n"
            "- 若已知 S2 Paper ID，直接填写\n"
            "- 填写后将忽略 keywords、author、year_range、fields_of_study\n"
            "- 建议配合 full_abstract=True 获取完整摘要\n"
            "- 可同时传入多个，逐一精确查询"
        ),
    )
    arxiv_ids: list[str] = Field(
        default=[],
        description=(
            "arXiv ID 列表（可选）：\n"
            "- S2 支持以 arXiv ID 直接查询，格式如 '2301.07041'\n"
            "- 填写后将忽略 keywords、author、year_range、fields_of_study\n"
            "- 适用于已从 arxiv_tool 获得 arxiv_id、想在 S2 补充引用数等字段的场景"
        ),
    )
    author: str = Field(
        default="",
        description=(
            "作者姓名（可选）：\n"
            "- 仅在寻找特定学者的工作、或已知作者时填写。\n"
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
    sort: str = Field(
        default="",
        description=(
            "排序方式（可选）：\n"
            "- 填写后将切换至 bulk 检索端点（不再按相关性排序）\n"
            "- 格式：'field:order'，其中 field 可选 citationCount / publicationDate / paperId，"
            "order 可选 asc / desc\n"
            "- 常用示例：'citationCount:desc'（引用数从高到低）、"
            "'publicationDate:desc'（最新论文优先）\n"
            "- 留空则使用相关性排序（默认，推荐关键词检索时使用）"
        ),
    )
    min_citation_count: int = Field(
        default=0,
        description=(
            "最低引用数过滤（可选）：\n"
            "- 只返回引用数 >= 该值的论文\n"
            "- 0 表示不限制\n"
            "- 适合过滤低影响力论文，如设为 50 可只看有一定引用量的工作"
        ),
        ge=0,
    )
    open_access_only: bool = Field(
        default=False,
        description=(
            "是否只返回有开放获取 PDF 的论文（默认False）：\n"
            "- True 时只返回可直接获取全文的论文\n"
            "- 配合后续 jina_tool 读取全文时建议开启"
        ),
    )
    publication_date_range: str = Field(
        default="",
        description=(
            "精确日期范围过滤（可选）：\n"
            "- 格式：'<startDate>:<endDate>'，日期为 YYYY-MM-DD\n"
            "- 两端均可省略，表示开放区间\n"
            "- 示例：'2023-01-01:2024-06-30'、'2024-01-01:'（某日之后）、':2023-12-31'（某日之前）\n"
            "- 与 year_range 互补：需要精确到月/日时用本字段，只需限定年份用 year_range\n"
            "- 注意：部分论文无精确日期，会被视为发表于当年 1 月 1 日"
        ),
    )
    publication_types: list[str] = Field(
        default=[],
        description=(
            "论文类型过滤（可选）：\n"
            "可选值：Review, JournalArticle, CaseReport, ClinicalTrial, Conference, "
            "Dataset, Editorial, LettersAndComments, MetaAnalysis, News, Study, Book, BookSection\n"
            "- 多个类型之间为 OR 关系\n"
            "- 常用组合：['JournalArticle', 'Conference']（正式发表）、['Review']（综述）\n"
            "- 留空不限制"
        ),
    )


def _build_search_query(keywords: list[str], author: str) -> str:
    parts = []
    if keywords:
        # 确保 keywords 里面没有被 LLM 误塞进长句子，将其规范化
        parts.append(" ".join(keywords))
    if author:
        # 去掉 'author:' 伪语法，S2 检索直接拼人名即可，例如 "photonic ADC Taylor"
        parts.append(author.strip())
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
    sort: str = "",
    min_citation_count: int = 0,
    open_access_only: bool = False,
    publication_date_range: str = "",
    publication_types: list[str] = [],
) -> str:
    """
    在 Semantic Scholar (S2) 上检索学术论文。

    ## 三种查询模式

    ### 模式一：关键词检索（广撒网）
    填写 keywords，可选填 author、year_range、publication_date_range、fields_of_study、
    publication_types、min_citation_count、open_access_only。
    返回论文列表，包含标题、截断摘要、引用数、s2_paper_id、open_access_pdf 等。
    建议根据标题和引用数筛选目标论文，记录 s2_paper_id 供后续精确查询。
    默认按相关性排序；填写 sort 字段可改为按引用数或发表日期排序（自动切换至 bulk 端点）。

    ### 模式二：S2 Paper ID 精确查询
    填写 s2_paper_ids，配合 full_abstract=True 获取完整摘要。
    返回额外包含 tldr（S2 AI 生成的一句话总结）。
    对话历史中已出现的 s2_paper_id 视为可信，直接使用。

    ### 模式三：arXiv ID 精确查询
    填写 arxiv_ids，通过 S2 获取该论文的完整元数据（引用数、venue 等）。
    适合在 arxiv_tool 检索后，用 S2 补充元数据的场景。

    ## 返回格式

    ### 成功时
    {
        "success": true,
        "count": 实际返回论文数量,
        "has_s2_key": 是否使用了 API Key（影响速率上限）,
        "agent_hint": 情况详释,
        "papers": [
            {
                "title": 论文标题,
                "abstract": 摘要（full_abstract=False 时截断至300字符）,
                "has_abstract": 是否存在摘要,
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
        "agent_hint": 情况详释与处理建议
        "papers": []
    }

    ## 下游工具
    若需深入阅读论文全文，将 open_access_pdf 字段的值传给 jina_tool。
    本工具不处理全文内容。

    ## 注意
    - 批量检索时保持 full_abstract=False，避免 token 超限
    - 相同 query 失败后 120 秒内不会重复请求
    - 需要排序或精确日期过滤时填写 sort / publication_date_range，工具会自动切换检索端点
    - abstract 为空是 S2 的正常现象，出现频率较高；返回结果中 has_abstract: false 的论文无需再做精确查询，精确查询不会补全摘要。若需要该论文摘要，优先检查arxiv_id 字段是否存在，存在则可用 arxiv_tool 尝试获取；其次检查 open_access_pdf 字段是否存在，存在则可用 jina_tool 尝试读取
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
                if err in ("rate_limited", "timeout", "request_failed"):
                    logger.error("[s2] ID查询遭遇系统级错误 %s: %s", err, request_id)
                    # 【修改点】如果前面已经拿到了部分结果，不要 return error，而是终止循环并返回已有结果
                    if results:
                        break
                    else:
                        return _error_payload(
                            err,
                            f"Failed while fetching ID: {request_id}",
                            retryable=True,
                        )

        # 组装成功报文（统一使用 agent_hint）
        agent_hint = "检索成功。"
        if not results:
            agent_hint = "查询请求已完全成功执行，但在数据库中没有找到对应的 ID 条目（可能已被删除或ID输入错误）。请勿盲目重试，改用关键词搜索模式。"
        elif stat_failed > 0:
            agent_hint = f"部分拉取成功（{len(results)}篇），但有 {stat_failed} 篇因速率限制/网络错误未能拉取。你可以先基于已有内容回答。"

        return json.dumps(
            {
                "success": True,
                "count": len(results),
                "has_s2_key": bool(S2_API_KEY),
                "agent_hint": agent_hint,
                "papers": results,
            },
            ensure_ascii=False,
        )

    # ── 模式一：关键词检索 ──

    # 【新增：防御性清洗逻辑】
    cleaned_keywords = []
    for kw in keywords:
        # 如果 Agent 误把一长串句子当成一个 keyword 传进来，帮它按空格切分
        sub_kws = kw.split()
        for sub_kw in sub_kws:
            # 过滤掉 4 位数字（年份，如 1975），因为年份应当走 year_range 参数
            # 年份混在 query 里在老文献检索中非常容易导致 0 命中
            if sub_kw.isdigit() and len(sub_kw) == 4:
                # 如果当前请求没指定 year_range，顺手帮它把年份补到参数里
                if not year_range:
                    year_range = sub_kw
                continue
            cleaned_keywords.append(sub_kw)

    # 重新把清洗后的列表赋给 keywords
    keywords = cleaned_keywords

    query = _build_search_query(keywords, author)
    if not query.strip():
        return json.dumps(
            {
                "success": False,
                "error_type": "invalid_params",
                "error": "keywords、s2_paper_ids、arxiv_ids 和 author 不能同时为空",
                "retryable": False,
                "agent_hint": "请至少提供 keywords、s2_paper_ids、arxiv_ids 或 author 之一。",
                "papers": [],
            },
            ensure_ascii=False,
        )

    # 根据是否指定 sort 决定使用哪个端点
    use_bulk = bool(sort)
    search_url = _S2_SEARCH_BULK_URL if use_bulk else _S2_SEARCH_URL

    params: dict = {
        "query": query,
        "fields": _SEARCH_FIELDS,
    }
    # search 端点支持 limit 参数做服务端截断；bulk 端点不支持，在本地截断
    if not use_bulk:
        params["limit"] = min(max_results, 20)
    if year_range:
        params["year"] = year_range
    if fields_of_study:
        params["fieldsOfStudy"] = ",".join(fields_of_study)
    # 新增字段
    if sort:
        params["sort"] = sort
    if min_citation_count > 0:
        params["minCitationCount"] = str(min_citation_count)
    if open_access_only:
        params["openAccessPdf"] = ""
    if publication_date_range:
        params["publicationDateOrYear"] = publication_date_range
    if publication_types:
        params["publicationTypes"] = ",".join(publication_types)

    query_key = json.dumps(params, sort_keys=True)
    if _is_recently_failed(query_key):
        logger.warning("[s2] 相同 query 近期失败，跳过重复请求")
        return json.dumps(
            {
                "success": False,
                "error_type": "recent_failed_query",
                "error": "Recent identical query failed",
                "retryable": False,
                "agent_hint": "相同的 S2 查询近期失败，请勿重复尝试。如需继续检索请使用 arxiv_tool。",
                "papers": [],
            },
            ensure_ascii=False,
        )

    logger.info(
        "[s2] 关键词检索 endpoint=%s params=%s",
        "bulk" if use_bulk else "relevance",
        params,
    )

    last_error = ""
    error_type = "request_failed"
    resp_success = False

    for attempt in range(MAX_RETRIES):
        try:
            _wait_for_s2_rate_limit()
            resp = _SESSION.get(
                search_url,
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
    if use_bulk:
        raw_papers = raw_papers[:max_results]  # bulk 端点在本地截断
    papers = [_parse_paper(p, full_abstract) for p in raw_papers]

    # 核心优化：如果请求 200 成功，但是返回了 0 篇论文，给 Agent 打上明确的补丁指南
    agent_hint = ""
    if len(papers) == 0:
        logger.info("[s2] 关键词检索完成：API调用成功，但检索结果为 0 篇 (零命中)")
        agent_hint = (
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
            "agent_hint": agent_hint,
            "papers": papers,
        },
        ensure_ascii=False,
        indent=2,
    )
