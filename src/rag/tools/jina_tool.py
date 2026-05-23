"""
Jina 网页/PDF 阅读工具

## 定位
独立的网页与 PDF 全文阅读工具，与上游检索工具解耦。
本工具不负责"找到哪些论文"，只负责"读取某个 URL 的内容并按需切片"。

## 典型调用链
  s2_search_tool  →  (取 open_access_pdf)  →  jina_tool
  arxiv_tool      →  (取 pdf_url)          →  jina_tool
  用户直接提供 URL                          →  jina_tool

## 工作模式

### 无 query（全文截断模式）
  调用 r.jina.ai 清洗目标 URL，返回全文开头至 no_query_max_tokens 估算 token
  上限的内容。适合快速了解全文结构，不消耗副 API。

### 有 query（分片打分模式）
  1. 调用 r.jina.ai 获取完整清洗全文（不截断）
  2. 按 chunk_size / overlap 将全文切片
  3. 逐片单独调副 LLM，要求对"该片段与 query 的相关度"打分（1-10 整数）
  4. 按分数降序排列，同分按原文顺序（片段索引升序）
  5. 累计返回片段，直到估算 token 数达到 max_return_tokens 阈值为止
  副 LLM 只负责打分，不摘录、不改写，避免幻觉。
  返回给调用方的是原始片段文本，附带各片分数。

## 速率限制（依据 Jina 官方标准）
  r.jina.ai 无 Key : 20 RPM → 工具取 3.5s/次
  r.jina.ai 有 Key : 500 RPM → 工具取 1.0s/次
  429 触发后强制冷却 60s，相同 URL 失败后 120s 内不再重试
"""

from __future__ import annotations

import json
import time
from threading import Lock

import requests
from langchain.tools import tool
from pydantic import BaseModel, Field

from src.utils.logger import get_logger
import re
from langchain_core.messages import SystemMessage, HumanMessage
from src.llm import sub_llm

# ══════════════════════════════════════════════════════════════════
# 外部配置注入
# ══════════════════════════════════════════════════════════════════


from src.config import (
    JINA_API_KEY,
    SUB_LLM_API_KEY,
    SUB_LLM_BASE_URL,
    SUB_LLM_MODEL,
)

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════
# Jina Reader 速率限制
# ══════════════════════════════════════════════════════════════════

_JINA_LOCK = Lock()
_LAST_JINA_CALL: float = 0.0
_JINA_BLOCK_UNTIL: float = 0.0

_INTERVAL_WITH_KEY = 1.0  # 500 RPM 官方上限，取 1.0s 留余量
_INTERVAL_WITHOUT_KEY = 3.5  # 20 RPM = 3s/次，取 3.5s 留余量

MAX_RETRIES = 2
_SESSION = requests.Session()

_RECENT_FAILED: dict[str, float] = {}
FAILED_TTL = 120


def _jina_interval() -> float:
    return _INTERVAL_WITH_KEY if JINA_API_KEY else _INTERVAL_WITHOUT_KEY


def _wait_for_jina_rate_limit() -> None:
    global _LAST_JINA_CALL, _JINA_BLOCK_UNTIL
    with _JINA_LOCK:
        now = time.time()
        if now < _JINA_BLOCK_UNTIL:
            wait = _JINA_BLOCK_UNTIL - now
            logger.warning("[jina] 冷却期，等待 %.1f 秒", wait)
            time.sleep(wait)
            now = time.time()
        delta = now - _LAST_JINA_CALL
        interval = _jina_interval()
        if delta < interval:
            time.sleep(interval - delta)
        _LAST_JINA_CALL = time.time()


def _is_recently_failed(key: str) -> bool:
    t = _RECENT_FAILED.get(key)
    return bool(t and time.time() - t < FAILED_TTL)


def _mark_failed(key: str) -> None:
    _RECENT_FAILED[key] = time.time()


def _handle_jina_error(e: requests.RequestException, attempt: int) -> tuple[str, float]:
    global _JINA_BLOCK_UNTIL
    error_type = "request_failed"
    if isinstance(e, requests.Timeout):
        error_type = "timeout"
    elif isinstance(e, requests.HTTPError) and e.response is not None:
        status = e.response.status_code
        if status == 429:
            error_type = "rate_limited"
            _JINA_BLOCK_UNTIL = time.time() + 60
        elif status in (401, 402, 403):
            error_type = "auth_error"
    backoff = {
        "rate_limited": 30.0 * (attempt + 1),
        "timeout": 5.0 * (2**attempt),
    }.get(error_type, 3.0 * (2**attempt))
    return error_type, backoff


# ══════════════════════════════════════════════════════════════════
# Token 估算（无需精确，用于控制返回量级）
# ══════════════════════════════════════════════════════════════════


def _estimate_tokens(text: str) -> int:
    """
    粗略估算 token 数：英文约 4 字符/token，中文约 2 字符/token。
    混合文本取保守值 3 字符/token。
    """
    return max(1, len(text) // 3)


# ══════════════════════════════════════════════════════════════════
# Jina Reader：获取清洗全文
# ══════════════════════════════════════════════════════════════════

_JINA_READER_BASE = "https://r.jina.ai/"


def _jina_reader_headers() -> dict[str, str]:
    h: dict[str, str] = {
        "Accept": "application/json",
        "X-Return-Format": "text",
        "X-With-Generated-Alt": "true",
        "X-Retain-Images": "none",
    }
    if JINA_API_KEY:
        h["Authorization"] = f"Bearer {JINA_API_KEY}"
    return h


def _fetch_full_text(url: str, fail_key: str) -> tuple[str, str | None]:
    """
    调用 r.jina.ai 获取干净全文（不截断）。
    返回 (full_text, error_type_or_None)。
    """
    reader_url = _JINA_READER_BASE + url
    for attempt in range(MAX_RETRIES):
        try:
            _wait_for_jina_rate_limit()
            resp = _SESSION.get(
                reader_url,
                headers=_jina_reader_headers(),
                timeout=(20, 120),
            )
            resp.raise_for_status()
            try:
                text = resp.json().get("data", {}).get("content", "") or resp.text
            except Exception:
                text = resp.text
            text = text.strip()
            logger.info("[jina] Reader 完成：全文 %d 字符", len(text))
            return text, None
        except requests.RequestException as e:
            error_type, backoff = _handle_jina_error(e, attempt)
            if error_type in ("rate_limited", "timeout"):
                _mark_failed(fail_key)
            logger.warning(
                "[jina] Reader 失败(type=%s): %s，%.1f 秒后重试 (%d/%d)",
                error_type,
                e,
                backoff,
                attempt + 1,
                MAX_RETRIES,
            )
            if error_type in ("rate_limited", "auth_error"):
                return "", error_type
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)

    _mark_failed(fail_key)
    return "", "request_failed"


# ══════════════════════════════════════════════════════════════════
# 文本切片
# ══════════════════════════════════════════════════════════════════


def _split_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    按字符数切片，相邻片段有 overlap 字符的重叠。
    尽量在句子边界（'. '）处截断，避免切断完整语义。
    """
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_size, text_len)

        # 若不是最后一片，尝试在句子边界截断
        if end < text_len:
            boundary = text.rfind(". ", start + chunk_size // 2, end)
            if boundary != -1:
                end = boundary + 1  # 包含句号

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= text_len:
            break
        start = end - overlap

    return chunks


# ══════════════════════════════════════════════════════════════════
# 副 LLM 打分
# ══════════════════════════════════════════════════════════════════

# System prompt 占位符，由外部（prompt 工程师）填写后注入
# 要求副 LLM：
#   - 只输出 JSON：{"score": 整数 1-10}
#   - 评估维度：该片段是否包含能回答 query 的信息（信息相关性）
#   - 不因片段长/专业/含共同词汇而虚高打分
#   - 打分前在内部推理，不输出推理过程
SLICE_SYSTEM_PROMPT: str = (
    "You are a relevance scoring assistant for academic RAG retrieval.\n"
    "Given a QUERY and a TEXT PASSAGE, score how much the passage helps answer the query.\n\n"
    "Scoring scale (integer 1–10):\n"
    "  1–2 : Completely irrelevant.\n"
    "  3–4 : Tangentially related but minimal useful information.\n"
    "  5–6 : Partially relevant; addresses the query indirectly.\n"
    "  7–8 : Clearly relevant; directly addresses the query.\n"
    "  9–10: Highly relevant; densely answers the query.\n\n"
    "Scoring rules:\n"
    "  - Judge informational relevance only—ignore length, writing style, and academic tone.\n"
    "  - Keyword overlap is NOT semantic relevance; judge meaning, not surface form.\n"
    "  - Reason internally; output the score only.\n\n"
    'Output: JSON only. Example: {"score": 7}'
)


def set_slice_system_prompt(prompt: str) -> None:
    """允许外部注入经过专门设计的打分 system prompt，覆盖默认值。"""
    global SLICE_SYSTEM_PROMPT
    SLICE_SYSTEM_PROMPT = prompt.strip()


def _score_chunk(chunk: str, query: str) -> int:
    """调用副 LLM 对单个片段打分。"""
    user_message = f"QUERY: {query}\n\nTEXT PASSAGE:\n{chunk}"

    messages = [
        SystemMessage(content=SLICE_SYSTEM_PROMPT),
        HumanMessage(content=user_message),
    ]

    # 临时绑定较低的 token 和 json 格式
    score_llm = sub_llm.bind(response_format={"type": "json_object"}, max_tokens=128)

    try:
        res = score_llm.invoke(messages)
        content = res.content.strip()

        # 兼容处理
        clean_content = content.replace("```json", "").replace("```", "").strip()
        try:
            score = int(json.loads(clean_content)["score"])
        except Exception:
            match = re.search(r'(?i)"score"\s*:\s*(\d+)', content)
            if match:
                score = int(match.group(1))
            else:
                raise ValueError(f"无法从文本中解析分数: {content}")

        return max(1, min(10, score))

    except Exception as e:
        logger.warning(
            "[jina] 副 LLM 打分失败: %s | 返回: %s",
            e,
            repr(locals().get("content", "")),
        )
        return 0


# ══════════════════════════════════════════════════════════════════
# 错误响应构造
# ══════════════════════════════════════════════════════════════════


def _error_payload(error_type: str, error: str, retryable: bool, url: str) -> str:
    msg_map = {
        "rate_limited": "Jina API 触发速率限制，请勿重复请求同一 URL。",
        "auth_error": "Jina API Key 无效或额度不足，请检查 Key 设置。",
        "timeout": "Jina 请求超时，目标页面可能无法访问或响应过慢。",
        "recent_failed": "相同 URL 近期请求失败，120 秒内不再重试。",
        "no_url": "未提供有效的 URL。",
        "slice_no_config": "有 query 但副 LLM 未配置，无法执行分片打分。",
        "request_failed": "Jina 请求失败，请稍后重试。",
    }
    return json.dumps(
        {
            "success": False,
            "url": url,
            "error_type": error_type,
            "error": error,
            "retryable": retryable,
            "agent_hint": msg_map.get(error_type, "未知错误。"),
            "mode": None,
            "content": None,
        },
        ensure_ascii=False,
    )


# ══════════════════════════════════════════════════════════════════
# Tool 参数
# ══════════════════════════════════════════════════════════════════


class JinaRequest(BaseModel):
    url: str = Field(
        description=(
            "目标网页或 PDF 的完整 URL（必填）：\n"
            "- 支持任意公开可访问的网页或 PDF 链接\n"
            "- 典型来源：\n"
            "    s2_search_tool 返回的 open_access_pdf 字段\n"
            "    arxiv_tool 返回的 pdf_url 字段\n"
            "    用户直接提供的链接\n"
            "- 不支持需要登录才能访问的页面"
        )
    )
    query: str = Field(
        default="",
        description=(
            "语义检索问题（可选）：\n"
            "- 填写后：对全文分片，逐片用副 LLM 打分，返回最相关的片段\n"
            "- 留空时：返回全文开头截断内容，不消耗副 LLM\n"
            "- 建议用英文描述用户真正关心的研究问题，如实验细节、方法原理等\n"
            "- 仅在摘要无法满足用户追问时才填写此参数"
        ),
    )
    top_n: int = Field(
        default=3,
        description=(
            "最多返回的高分片段数量上限（默认3）：\n"
            "- 实际返回数量还受 max_return_tokens 控制，先到者优先\n"
            "- 仅在有 query 时生效"
        ),
        le=10,
    )
    score_threshold: int = Field(
        default=5,
        description=(
            "分数阈值（默认5，范围1-10）：\n"
            "- 低于此分数的片段不返回，即使数量未达到 top_n\n"
            "- 仅在有 query 时生效"
        ),
        ge=1,
        le=10,
    )
    max_return_tokens: int = Field(
        default=3000,
        description=(
            "有 query 时，返回片段的估算 token 总预算（默认3000）：\n"
            "- 按分数从高到低累加，超出预算后停止添加新片段\n"
            "- token 为粗略估算值（3字符≈1token），用于控制量级"
        ),
    )
    no_query_max_tokens: int = Field(
        default=1500,
        description=(
            "无 query 时，返回全文头部的估算 token 上限（默认1500）：\n"
            "- 超出部分截断，适合快速了解全文整体结构"
        ),
    )
    chunk_size: int = Field(
        default=1000,
        description="分片大小（字符数，默认1000）。过小会增加副 LLM 调用次数，过大会降低定位精度。",
    )
    chunk_overlap: int = Field(
        default=150,
        description="相邻片段的重叠字符数（默认150），避免语义在片段边界处断裂。",
    )


# ══════════════════════════════════════════════════════════════════
# Tool
# ══════════════════════════════════════════════════════════════════


@tool(args_schema=JinaRequest)
def jina_tool(
    url: str,
    query: str = "",
    top_n: int = 3,
    score_threshold: int = 5,
    max_return_tokens: int = 3000,
    no_query_max_tokens: int = 1500,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
) -> str:
    """
    读取指定 URL 的网页或 PDF 内容，支持基于副 LLM 的语义片段召回。

    ## 两种模式

    ### 无 query：全文截断模式
    返回全文开头 no_query_max_tokens 估算 token 的内容。
    不消耗副 LLM，适合快速了解文章结构。

    ### 有 query：分片打分模式
    对全文完整分片，逐片调副 LLM 打分（1-10），
    按分数从高到低累积返回，直到达到 max_return_tokens 预算或 top_n 上限。
    返回原始片段文本，附带各片分数。

    ## 返回格式

    ### 无 query 成功时
    {
        "success": true,
        "url": 请求的 URL,
        "mode": "full_text_truncated",
        "has_jina_key": 是否使用了 Jina API Key,
        "content": 截断后的全文文本,
        "estimated_tokens": 估算 token 数,
        "agent_hint": 情况详释
    }

    ### 有 query 成功时
    {
        "success": true,
        "url": 请求的 URL,
        "mode": "scored_chunks",
        "has_jina_key": 是否使用了 Jina API Key,
        "query": 传入的 query,
        "total_chunks": 全文切片总数,
        "returned_chunks": 实际返回片段数,
        "estimated_tokens": 返回内容的估算 token 总数,
        "chunks": [
            {
                "index": 片段在全文中的原始顺序（0起），
                "score": 副 LLM 打分（1-10），
                "estimated_tokens": 该片段估算 token 数,
                "text": 原始片段文本
            },
            ...
        ],
        "agent_hint": 情况详释
    }

    ### 失败时
    {
        "success": false,
        "url": 请求的 URL,
        "error_type": "rate_limited" | "timeout" | "request_failed"
                      | "auth_error" | "recent_failed" | "no_url" | "slice_no_config",
        "error": 错误详情,
        "retryable": true | false,
        "agent_hint": 情况详释与处理建议,
        "mode": null,
        "content": null
    }

    ## 注意
    - 无法访问需要登录的页面（付费期刊正文等）
    - 有 query 时副 LLM 调用次数 = 切片数，长文档会消耗较多资源
    - 副 LLM 未配置时，有 query 的请求直接返回错误
    - 相同 URL 失败后 120 秒内不会重复请求
    - 不要对同一 URL 反复调用；如需多次讨论同一篇文章，建议用户将其下载入库
    """
    # ── 基础校验 ──
    url = url.strip()
    if not url:
        return _error_payload("no_url", "URL 为空", False, url)

    fail_key = url  # Jina 失败缓存只按 URL 区分，与 query 无关
    if _is_recently_failed(fail_key):
        logger.warning("[jina] 相同 URL 近期失败，跳过: %s", url)
        return _error_payload(
            "recent_failed", "Recent request to this URL failed", False, url
        )

    # ── 有 query：提前校验副 LLM 配置，避免拿完全文才发现无法打分 ──
    if query:
        # 从 LangChain 实例中安全获取真实的 API Key
        api_key = (
            sub_llm.openai_api_key.get_secret_value() if sub_llm.openai_api_key else ""
        )

        if not api_key:
            logger.warning("[jina] 有 query 但副 LLM 未配置")
            return _error_payload(
                "slice_no_config",
                "query 参数需要副 LLM 打分，但尚未配置有效的 API Key",
                False,
                url,
            )

    # ── 获取全文 ──
    logger.info("[jina] 开始读取: %s", url)
    full_text, err = _fetch_full_text(url, fail_key)
    if err:
        return _error_payload(err, f"Jina Reader 失败: {err}", err == "timeout", url)

    # 【新增点】防范假 200 OK（反爬/登录墙校验）
    text_lower = full_text.lower()
    if len(full_text) < 1000 and any(
        kw in text_lower
        for kw in [
            "please enable javascript",
            "verify you are human",
            "checking your browser",
            "log in to view",
        ]
    ):
        return json.dumps(
            {
                "success": False,
                "url": url,
                "error_type": "access_denied",
                "error": "Hit a paywall or anti-bot challenge",
                "retryable": False,
                "agent_hint": "该链接存在反爬虫验证或要求登录（付费墙），Jina 无法读取有效内容。请放弃读取该链接，尝试寻找这篇论文的 arXiv 预印本，或向用户说明无法获取全文。",
                "mode": None,
                "content": None,
            },
            ensure_ascii=False,
        )

    has_key = bool(JINA_API_KEY)

    # ════════════════════════════════════════
    # 模式一：无 query → 截断全文返回
    # ════════════════════════════════════════
    if not query:
        limit_chars = no_query_max_tokens * 3  # 粗估：1 token ≈ 3 字符
        truncated = full_text[:limit_chars]
        was_truncated = len(full_text) > limit_chars
        est_tokens = _estimate_tokens(truncated)

        if was_truncated:
            agent_hint = (
                f"全文共 {len(full_text)} 字符，已截断至前 {len(truncated)} 字符"
                f"（约 {est_tokens} token）。如需查找特定内容，请提供 query 参数启用分片打分模式。"
            )
        else:
            agent_hint = (
                f"全文共 {len(full_text)} 字符，完整返回（约 {est_tokens} token）。"
            )

        logger.info("[jina] 全文模式：返回 %d 字符", len(truncated))
        return json.dumps(
            {
                "success": True,
                "url": url,
                "mode": "full_text_truncated",
                "has_jina_key": has_key,
                "content": truncated,
                "estimated_tokens": est_tokens,
                "agent_hint": agent_hint,
            },
            ensure_ascii=False,
            indent=2,
        )

    # ════════════════════════════════════════
    # 模式二：有 query → 分片打分
    # ════════════════════════════════════════
    chunks = _split_chunks(full_text, chunk_size, chunk_overlap)
    total_chunks = len(chunks)
    logger.info("[jina] 切片完成：共 %d 片，开始逐片打分", total_chunks)

    # 逐片打分，记录 (原始索引, 分数, 文本)
    scored: list[tuple[int, int, str]] = []
    for i, chunk in enumerate(chunks):
        score = _score_chunk(chunk, query)
        logger.info("[jina] 片段 %d/%d 分数: %d", i + 1, total_chunks, score)
        scored.append((i, score, chunk))

    # 排序：分数降序，同分按原始索引升序（原文靠前的优先）
    scored.sort(key=lambda x: (-x[1], x[0]))

    # 按 token 预算和 top_n 筛选
    result_chunks: list[dict] = []
    accumulated_tokens = 0

    for idx, score, text in scored:
        if score < score_threshold:
            continue
        if len(result_chunks) >= top_n:
            break
        est = _estimate_tokens(text)
        if accumulated_tokens + est > max_return_tokens and result_chunks:
            # 已有内容且加入此片会超预算，停止
            break
        result_chunks.append(
            {
                "index": idx,
                "score": score,
                "estimated_tokens": est,
                "text": text,
            }
        )
        accumulated_tokens += est

    # 最终按原文顺序重排（方便 agent 阅读）
    result_chunks.sort(key=lambda x: x["index"])

    total_returned_tokens = sum(c["estimated_tokens"] for c in result_chunks)
    logger.info(
        "[jina] 分片打分完成：%d 片中返回 %d 片，估算 %d token",
        total_chunks,
        len(result_chunks),
        total_returned_tokens,
    )

    note_parts = [
        f"全文共 {total_chunks} 个片段，",
        f"返回分数≥{score_threshold} 的前 {len(result_chunks)} 个片段，",
        f"估算共 {total_returned_tokens} token。",
    ]
    if len(result_chunks) == 0:
        note_parts.append(
            "所有片段分数均低于阈值，建议调低 score_threshold 或修改 query。"
        )

    return json.dumps(
        {
            "success": True,
            "url": url,
            "mode": "scored_chunks",
            "has_jina_key": has_key,
            "query": query,
            "total_chunks": total_chunks,
            "returned_chunks": len(result_chunks),
            "estimated_tokens": total_returned_tokens,
            "chunks": result_chunks,
            "agent_hint": "".join(note_parts),
        },
        ensure_ascii=False,
        indent=2,
    )
