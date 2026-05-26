import re
from src.utils.logger import get_logger

logger = get_logger(__name__)

# 新增：统一定义兼容标签
THINK_TAG_PATTERN = r"(?:thinking|think)"


def is_tool_call_leak(text: str) -> bool:
    """检测是否包含DS内部tool_call格式泄漏"""
    return "DSML" in text or "<｜｜" in text


def extract_thinking(text: str) -> tuple[list[str], str, str]:
    """
    解析模型原始输出，返回 (thinking_blocks, answer, thinking_raw)
    - thinking_blocks: 所有思维链段落的列表（仅用于分块日志打印）
    - answer: 最后一个 </thinking> 之后的正式回答
    - thinking_raw: 最后一个 </thinking> 之前全部内容的纯文本（去标签后），
                    代表完整思维链，中间裸文本均被保留

    覆盖情况：
    - 正常：一个完整 <thinking>...</thinking> + answer
    - 直出：无任何 thinking 标签，answer = 原文
    - 多段：多个 <thinking>...</thinking>，answer = 最后一段之后的内容
    - 多尾标签：存在多个 </thinking>，以最后一个为准，
                之前全部为 thinking_raw，之后为 answer
    - 未闭合：有 <thinking> 无 </thinking>，answer = "" 或存在
    - 无回答：有完整 thinking，</thinking> 后内容为空，answer = ""
    - 空输入：全部为空
    """
    text = (text or "").strip()
    if not text:
        return [], "", ""

    # 查找最后一个 tag 的位置，作为 thinking 和 answer 的分割线
    last_close_match = list(
        re.finditer(rf"</{THINK_TAG_PATTERN}>", text, re.IGNORECASE)
    )
    last_close = last_close_match[-1].start() if last_close_match else -1
    last_close_tag = last_close_match[-1].group(0) if last_close_match else ""

    if last_close != -1:
        # 存在至少一个 </thinking>
        thinking_part = text[:last_close]  # 最后一个 </thinking> 之前的部分
        answer = text[last_close + len(last_close_tag) :].strip()  # 之后的部分

        # 分块提取（仅用于日志）
        thinking_blocks = re.findall(
            rf"<{THINK_TAG_PATTERN}>([\s\S]*?)</{THINK_TAG_PATTERN}>",
            thinking_part,
            re.IGNORECASE,
        )

        # 生成完整 thinking_raw：去除所有标签后的纯文本
        thinking_raw = re.sub(
            rf"</?{THINK_TAG_PATTERN}>",
            "",
            thinking_part,
            flags=re.IGNORECASE,
        ).strip()

        # 如果 thinking_part 非空但没有提取到块（如只有 <thinking> 没有前面的闭合），
        # 则整个 thinking_part 视为无法解析的思维链残留，不加入 blocks
        if not thinking_blocks and thinking_part.strip():
            # 可能包含无头标签的内容，保守处理：不作为 blocks，但 thinking_raw 已包含
            pass

        return [t.strip() for t in thinking_blocks], answer, thinking_raw

    # 无任何 </thinking>：沿用原有启发式兜底逻辑
    open_matches = list(re.finditer(rf"<{THINK_TAG_PATTERN}>", text, re.IGNORECASE))

    if open_matches:
        last_open_match = open_matches[-1]
        after_tag = text[last_open_match.end() :].strip()

        if not after_tag:
            return [], "", ""

        # 启发式兜底：没有闭合标签，无法确定 thinking 边界
        # 不生成 thinking_raw，仅返回 answer
        return [], after_tag, ""

    # 无任何 thinking 标签：直出
    return [], text, ""


def process_llm_output(text: str, context: str = "") -> str:
    """
    业务层入口：解析 + 打日志 + 返回干净 answer。
    截断和无回答情况返回空字符串，由调用方决定如何处理。
    """
    thinking_blocks, answer, thinking_raw = extract_thinking(text)
    prefix = f"[{context}] " if context else ""

    # 检测tool_call格式泄漏
    if is_tool_call_leak(answer):
        logger.warning("%s检测到tool_call格式泄漏，过滤answer", prefix)
        answer = ""

    # 日志打印
    is_truncated = (
        bool(thinking_blocks)
        and not answer
        and not re.search(rf"</{THINK_TAG_PATTERN}>", text or "", re.IGNORECASE)
    )

    if not thinking_blocks and not thinking_raw and not answer:
        logger.warning("%s模型输出为空", prefix)

    elif not thinking_blocks and not thinking_raw and answer:
        # 包含直出和启发式兜底两种情况
        if re.search(rf"<{THINK_TAG_PATTERN}>", text, re.IGNORECASE):
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
            len(thinking_raw),
        )

    else:
        # 正常情况
        if thinking_raw:
            logger.debug(
                "%s完整思维链 (%d chars):\n%s", prefix, len(thinking_raw), thinking_raw
            )
        if len(thinking_blocks) > 1:
            logger.debug("%s思维链分块，共 %d 段", prefix, len(thinking_blocks))
        for i, block in enumerate(thinking_blocks):
            logger.debug("%sThinking[%d] (%d chars):\n%s", prefix, i, len(block), block)
        logger.debug("%sAnswer (%d chars)", prefix, len(answer))

    return answer
