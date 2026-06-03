"""
prompt.py — 向后兼容层

原有的 import 方式继续有效：
    from prompt import SYSTEM_PROMPT_NORMAL, SYSTEM_PROMPT_DISCUSS
    from prompt import CITATION_DEFAULT, CITATION_TRANSLATION

新的调用方式（推荐，在chatservice里替换）：
    from prompts import build_prompt, CITATION_DEFAULT, CITATION_TRANSLATION

    system_prompt = build_prompt(
        mode="normal",             # 或 "discuss"
        history=history_text,
        citation_plugin=CITATION_TRANSLATION,
    )
"""

from src.rag.prompts import build_prompt
from src.rag.prompts.plugins import CITATION_DEFAULT, CITATION_TRANSLATION

# 兼容旧有导入：不带动态内容的静态版本
# 注意：history和citation_plugin为空，仅用于不需要动态注入的场景
SYSTEM_PROMPT_NORMAL = build_prompt(mode="normal")
SYSTEM_PROMPT_DISCUSS = build_prompt(mode="discuss")

__all__ = [
    "SYSTEM_PROMPT_NORMAL",
    "SYSTEM_PROMPT_DISCUSS",
    "CITATION_DEFAULT",
    "CITATION_TRANSLATION",
    "build_prompt",
]
