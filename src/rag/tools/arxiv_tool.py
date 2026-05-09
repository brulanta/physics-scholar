from pydantic import BaseModel, Field
import feedparser
import requests
from datetime import datetime, timedelta, timezone
import time
from langchain.tools import tool
import json
from src.utils.logger import get_logger
from threading import Lock

logger = get_logger(__name__)
_ARXIV_LOCK = Lock()
_LAST_ARXIV_CALL = 0.0

MIN_ARXIV_INTERVAL = 3.0
MAX_RETRIES = 3


def wait_for_arxiv_rate_limit():
    global _LAST_ARXIV_CALL

    with _ARXIV_LOCK:
        now = time.time()
        delta = now - _LAST_ARXIV_CALL

        if delta < MIN_ARXIV_INTERVAL:
            sleep_time = MIN_ARXIV_INTERVAL - delta

            logger.info(
                "[arxiv] rate limit保护：等待 %.1f 秒",
                sleep_time,
            )

            time.sleep(sleep_time)

        _LAST_ARXIV_CALL = time.time()


class ArxivRequest(BaseModel):
    keywords: list[str] = Field(
        default=[],
        description=(
            "检索关键词列表：\n"
            "- 每个元素应为英文技术关键词（1-3个词），如 'transformer', 'optical comb'\n"
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
            "- 若已知论文的 arXiv ID（如从对话历史或用户输入中获得），直接填写，无需关键词检索\n"
            "- 格式示例：['2602.09408', '2604.18499']\n"
            "- 填写后将忽略 keywords、author、category、recent_days 等参数\n"
            "- 建议配合 full_summary=True 使用，以获取完整摘要"
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

    category: list[str] = Field(
        default=[],
        description=(
            "arXiv 分类列表（可选）：\n"
            "可选值：eess.SP, physics.optics, physics.app-ph, "
            "eess.IV, eess.AS, cond-mat.mtrl-sci, cond-mat.mes-hall, quant-ph\n"
            "- 多个分类之间为 OR 关系\n"
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
        default=5,
        description=(
            "返回论文数量（默认5，最多20）：\n"
            "- 关键词批量检索时建议保持默认值\n"
            "- 使用 arxiv_ids 时无需设置，返回数量由 ID 列表长度决定"
        ),
        le=20,
    )

    full_summary: bool = Field(
        default=False,
        description=(
            "是否返回完整摘要（默认False，截断至300字符）：\n"
            "- 仅在使用 arxiv_ids 精确查询少量论文时设为True\n"
            "- 关键词批量检索时请保持False，避免token超限"
        ),
    )


def build_arxiv_params(
    keywords: list[str] = [],
    arxiv_ids: list[str] = [],
    author: str = "",
    category: list[str] = [],
    recent_days: int = 0,
    max_results: int = 5,
):
    # 有arxiv_ids时走id_list模式，忽略其他参数
    if arxiv_ids:
        return {"id_list": ",".join(arxiv_ids), "max_results": len(arxiv_ids)}

    # 没有则走原来的search_query模式

    parts = []

    def normalize_kw(kw: str):
        return f'"{kw}"' if " " in kw else kw

    if keywords:
        kw_query = " OR ".join([normalize_kw(k) for k in keywords])
        parts.append(f"all:({kw_query})")

    if author:
        parts.append(f'au:"{author}"')

    if category:
        cat_query = " OR ".join([normalize_kw(c) for c in category])
        parts.append(f"cat:{cat_query}")

    search_query = " AND ".join(parts)

    fetch_n = min(max_results * 3, 40) if recent_days > 0 else max_results

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
    keywords: list[str] = [],
    arxiv_ids: list[str] = [],
    author: str = "",
    category: list[str] = [],
    recent_days: int = 0,
    max_results: int = 5,
    full_summary: bool = False,
) -> str:
    """
    在 arXiv 上检索学术论文。

    ## 两种查询模式

    ### 模式一：关键词检索（广撒网）
    适用于：探索某一研究方向、获取最新进展、浏览相关论文
    - 填写 keywords，可选填 author、category、recent_days
    - 返回论文列表，包含标题、截断摘要、作者、分类标签、arXiv ID 和 PDF 链接
    - 建议根据返回的 title 和 categories 判断相关度，筛选目标论文的 arxiv_id

    ### 模式二：ID 精确查询（精读）
    适用于：已知 arXiv ID 时获取论文详情
    - 填写 arxiv_ids，忽略其他检索参数
    - 建议配合 full_summary=True 获取完整摘要
    - 对话历史中已出现的 arXiv ID 视为可信，直接使用，无需关键词二次确认

    ## 推荐工作流（当需要深入了解某方向时）
    1. 关键词检索（full_summary=False）→ 获取论文列表
    2. 根据 title、categories 筛选高相关论文，记录其 arxiv_id
    3. ID 精确查询（full_summary=True）→ 获取完整摘要进行深入分析

    ## 返回格式

    ### 成功时
    {
        "success": true,
        "count": 实际返回论文数量,
        "papers": [
            {
                "title": 论文标题,
                "summary": 摘要（full_summary=False 时截断至300字符）,
                "authors": 作者列表,
                "published": 发布时间,
                "updated": 更新时间,
                "arxiv_id": arXiv ID（可用于后续精确查询）,
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
        "error_type": "rate_limited" | "timeout" | "request_failed",
        "error": 错误详情,
        "retryable": true | false,
        "message": 处理建议,
        "papers": []
    }
    - retryable=true：API 暂时异常，不代表相关论文不存在
    - retryable=false：非临时性错误，重试无意义

    ## 注意
    - 关键词批量检索时保持 full_summary=False，避免 token 超限
    - 检索结果按相关度排序，前10个结果通常已涵盖核心信息；超过20条后边际效用显著下降，噪音增加
    - 若工具多次返回 rate_limited，应停止尝试，基于已有信息回答
    """
    last_error = None
    params = build_arxiv_params(
        keywords, arxiv_ids, author, category, recent_days, max_results
    )
    logger.info("[arxiv] params: %s", params)
    url = "http://export.arxiv.org/api/query"
    headers = {"User-Agent": "PhysicsScholar/0.1 (13159331923@163.com)"}

    for attempt in range(MAX_RETRIES):
        try:
            wait_for_arxiv_rate_limit()

            response = requests.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            break
        except requests.RequestException as e:
            last_error = str(e)
            error_type = "request_failed"

            if isinstance(e, requests.Timeout):
                error_type = "timeout"

            elif isinstance(e, requests.HTTPError):
                if e.response is not None:
                    if e.response.status_code == 429:
                        error_type = "rate_limited"

            backoff = 2**attempt

            logger.warning(
                "[arxiv] 请求失败: %s，%.1f秒后重试 (%d/%d)",
                e,
                backoff,
                attempt + 1,
                MAX_RETRIES,
            )

            time.sleep(backoff)
    else:
        logger.error("[arxiv] 两次请求均失败，返回空结果")
        return json.dumps(
            {
                "success": False,
                "error_type": error_type,
                "error": str(last_error),
                "retryable": error_type in ["timeout", "rate_limited"],
                "message": (
                    "Arxiv API is temporarily unavailable. "
                    "Avoid repeated retries with similar queries."
                ),
                "papers": [],
            },
            ensure_ascii=False,
        )

    feed = feedparser.parse(response.text)
    papers = []
    for entry in feed.entries:
        tags = getattr(entry, "tags", [])
        papers.append(
            {
                "title": entry.title,
                "summary": entry.summary
                if full_summary
                else (
                    entry.summary[:300] + "..."
                    if len(entry.summary) > 300
                    else entry.summary
                ),
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

    logger.info("[arxiv] 检索完成: 命中%d篇，返回%d篇", len(papers), len(result))

    return json.dumps(
        {
            "success": True,
            "count": len(result),
            "papers": result,
        },
        ensure_ascii=False,
        indent=2,
    )
