import re
from src.utils.logger import get_logger

logger = get_logger(__name__)


def extract_thinking(text: str) -> tuple[list[str], str]:
    """
    解析模型原始输出，返回 (thinking_blocks, answer)
    - thinking_blocks: 所有思维链段落的列表（多轮 tool call 可能有多段）
    - answer: 最后一个 </thinking> 之后的正式回答

    覆盖情况：
    - 正常：一个完整 <thinking>...</thinking> + answer
    - 直出：无任何 thinking 标签，answer = 原文
    - 多段：多个 <thinking>...</thinking>，answer = 最后一段之后的内容
    - 截断：有 <thinking> 无 </thinking>，answer = ""
    - 无回答：有完整 thinking，</thinking> 后内容为空，answer = ""
    - 空输入：全部为空
    """
    text = (text or "").strip()
    if not text:
        return [], ""

    # 提取所有完整的 <thinking>...</thinking> 块
    thinking_blocks = re.findall(r"<thinking>([\s\S]*?)</thinking>", text)

    if thinking_blocks:
        # 找到最后一个 </thinking>，取其后内容作为 answer
        last_close = text.rfind("</thinking>")
        answer = text[last_close + len("</thinking>") :].strip()
        return [t.strip() for t in thinking_blocks], answer

    # 有 <thinking> 但没有 </thinking>：截断
    if "<thinking>" in text:
        open_pos = text.rfind("<thinking>")
        partial = text[open_pos + len("<thinking>") :].strip()
        return [partial], ""  # 用列表保持返回类型一致，answer 为空

    # 无任何 thinking 标签：直出
    return [], text


def process_llm_output(text: str, context: str = "") -> str:
    """
    业务层入口：解析 + 打日志 + 返回干净 answer。
    截断和无回答情况返回空字符串，由调用方决定如何处理。
    """
    thinking_blocks, answer = extract_thinking(text)
    prefix = f"[{context}] " if context else ""
    is_truncated = (
        bool(thinking_blocks) and not answer and "</thinking>" not in (text or "")
    )

    if not thinking_blocks and not answer:
        logger.warning("%s模型输出为空", prefix)

    elif not thinking_blocks:
        logger.debug("%s直出 answer（无思维链），%d chars", prefix, len(answer))

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
