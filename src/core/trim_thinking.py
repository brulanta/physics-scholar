import re


def extract_thinking(text: str) -> str:
    """提取/</thinking/>及其之前的所有内容用于log"""
    match = re.search(r"^[\s\S]*?</thinking>", text)
    if match:
        return match.group(0).strip()
    # 没有</thinking>，说明被截断了，返回全部
    return text.strip() + "  [no closing tag]"


def strip_thinking(text: str) -> str:
    """以/</thinking/>为准，去除它及之前的所有内容"""
    match = re.search(r"</thinking>", text)
    if match:
        return text[match.end() :].strip()
    # 没有</thinking>，说明thinking未结束，全部去掉（异常情况）
    if "<thinking>" in text:
        return ""
    return text.strip()
