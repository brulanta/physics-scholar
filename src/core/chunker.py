import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src import config
from src.utils.logger import get_logger

logger = get_logger(__name__)

# CJK 区间（与 registry.smart_match 一致）与英文词
_CJK = re.compile(r"[一-鿿]")
_WORD = re.compile(r"[a-zA-Z0-9]+")

# 统一分隔符，颗粒度由粗到细：段落 → 行 → 句 → 子句 → 词 → 字符。
# 原列表把句号 "。/." 置于 "\n" 之前，方向反了（先切句再切段），是 bug，此处纠正。
# 末尾 "" 允许硬切超长无分隔片段（原列表缺失，潜在 bug）。中英文分隔符合并为一套即可。
_SEPARATORS = [
    "\n\n",
    "\n",
    "。",
    ".",
    "！",
    "!",
    "？",
    "?",
    "；",
    ";",
    "，",
    ",",
    " ",
    "",
]

# 未校准告警只打一次
_warned_uncalibrated = False


def is_chinese(text: str, threshold: float = 0.10) -> bool:
    """检测是否为中文文本：中文字符占比超过阈值即判为中文。

    英文论文中文字符数趋近 0，阈值取 0.10 足以区分。
    """
    if not text:
        return False
    return len(_CJK.findall(text)) / max(len(text), 1) >= threshold


def estimate_tokens(text: str) -> int:
    """本地估算 bge-m3 token 数（切片时不发请求）。

    公式：tokens ≈ A·中文字数 + B·英文词数 + C，系数由 scripts/calibrate_tokenizer.py
    用真实 prompt_tokens 校准后硬编码进 config 出厂默认。当前默认仅为经验占位。
    """
    cjk = len(_CJK.findall(text))
    words = len(_WORD.findall(text))
    est = config.CHUNK_CALIB_A * cjk + config.CHUNK_CALIB_B * words + config.CHUNK_CALIB_C
    return max(0, int(est))


def chunker(blocks_str: str) -> list[str]:
    """将文本切片。按 token 语义容量度量，中英文分路线选 size/overlap，分隔符统一。"""
    if not blocks_str or not blocks_str.strip():
        return []

    global _warned_uncalibrated
    if not config.CHUNK_CALIBRATED and not _warned_uncalibrated:
        logger.warning(
            "[chunker] 当前使用未校准的占位系数（calibrated=false）。"
            "打包分发前请先运行 scripts/calibrate_tokenizer.py 用真实论文样本拟合，"
            "并把结果硬编码进 config 出厂默认。"
        )
        _warned_uncalibrated = True

    zh = is_chinese(blocks_str)  # 仅用于选 size/overlap（语义容量），分隔符统一
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE_ZH if zh else config.CHUNK_SIZE_EN,
        chunk_overlap=config.CHUNK_OVERLAP_ZH if zh else config.CHUNK_OVERLAP_EN,
        separators=_SEPARATORS,
        length_function=estimate_tokens,
    )
    return splitter.split_text(blocks_str)
