import re
from src.utils.logger import get_logger

logger = get_logger(__name__)


def is_tool_call_leak(text: str) -> bool:
    """检测是否包含DS内部tool_call格式泄漏"""
    return "DSML" in text or "<｜｜" in text


def extract_thinking(text: str) -> tuple[list[str], str]:
    """
    解析模型原始输出，返回 (thinking_blocks, answer)
    - thinking_blocks: 所有思维链段落的列表（多轮 tool call 可能有多段）
    - answer: 最后一个 </thinking> 之后的正式回答

    覆盖情况：
    - 正常：一个完整 <thinking>...</thinking> + answer
    - 直出：无任何 thinking 标签，answer = 原文
    - 多段：多个 <thinking>...</thinking>，answer = 最后一段之后的内容
    - 未闭合：有 <thinking> 无 </thinking>，answer = "" 或存在
    - 无回答：有完整 thinking，</thinking> 后内容为空，answer = ""
    - 空输入：全部为空
    """
    text = (text or "").strip()
    if not text:
        return [], ""

    # 优先：提取所有完整的 <thinking>...</thinking> 块
    thinking_blocks = re.findall(r"<thinking>([\s\S]*?)</thinking>", text)
    if thinking_blocks:
        last_close = text.rfind("</thinking>")
        answer = text[last_close + len("</thinking>") :].strip()
        return [t.strip() for t in thinking_blocks], answer

    # 启发式兜底：有 <thinking> 但没有 </thinking>
    # 说明模型没有闭合标签，把 <thinking> 之后的内容全部当作 answer
    if "<thinking>" in text:
        last_open = text.rfind("<thinking>")
        after_tag = text[last_open + len("<thinking>") :].strip()

        # after_tag为空：模型什么都没写（纯prefill）
        if not after_tag:
            return [], ""

        # after_tag非空：当作answer，thinking视为空
        # 注意：不把after_tag放进thinking_blocks，避免误导日志
        return [], after_tag

    # 无任何 thinking 标签：直出
    return [], text


def process_llm_output(text: str, context: str = "") -> str:
    """
    业务层入口：解析 + 打日志 + 返回干净 answer。
    截断和无回答情况返回空字符串，由调用方决定如何处理。
    """
    thinking_blocks, answer = extract_thinking(text)
    prefix = f"[{context}] " if context else ""

    # 检测tool_call格式泄漏
    if is_tool_call_leak(answer):
        logger.warning("%s检测到tool_call格式泄漏，过滤answer", prefix)
        answer = ""

    is_truncated = (
        bool(thinking_blocks) and not answer and "</thinking>" not in (text or "")
    )

    if not thinking_blocks and not answer:
        logger.warning("%s模型输出为空", prefix)

    elif not thinking_blocks and answer:
        # 包含直出和启发式兜底两种情况
        if "<thinking>" in text:
            logger.debug(
                "%s启发式提取answer（无闭合标签），%d chars", prefix, len(answer)
            )
        else:
            logger.debug("%s直出answer（无思维链），%d chars", prefix, len(answer))

    elif is_truncated:
        logger.warning(
            "%s思维链截断，未找到 </thinking>，已捕获内容 %d chars",
            prefix,
            sum(len(t) for t in thinking_blocks),
        )

    elif not answer:
        # 有完整 thinking 但 </thinking> 后为空
        logger.warning(
            "%s思维链完整但 answer 为空，%d 段 thinking，共 %d chars",
            prefix,
            len(thinking_blocks),
            sum(len(t) for t in thinking_blocks),
        )

    else:
        # 正常情况
        if len(thinking_blocks) > 1:
            logger.debug("%s多段思维链，共 %d 段", prefix, len(thinking_blocks))
        for i, block in enumerate(thinking_blocks):
            logger.debug("%sThinking[%d] (%d chars):\n%s", prefix, i, len(block), block)
        logger.debug("%sAnswer (%d chars)", prefix, len(answer))

    return answer
