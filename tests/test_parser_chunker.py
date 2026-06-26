import pytest
from src.core.parser import parse_pdf
from src.core.chunker import chunker, estimate_tokens, is_chinese
from src.config import CHUNK_SIZE_EN, CHUNK_SIZE_ZH


# ── parser ────────────────────────────────────────────────


def test_output_keys(english_pdf):
    result = parse_pdf(english_pdf)
    assert "body" in result
    assert "reference" in result
    assert "page_count" in result


def test_body_not_empty(english_pdf):
    result = parse_pdf(english_pdf)
    assert len(result["body"].strip()) > 0


def test_body_not_empty_chinese(chinese_pdf):
    result = parse_pdf(chinese_pdf)
    assert len(result["body"].strip()) > 0


def test_reference_extracted(english_pdf):
    # 含reference的论文，reference不应为空
    result = parse_pdf(english_pdf)
    assert len(result["reference"].strip()) > 0


def test_page_count_positive(english_pdf):
    result = parse_pdf(english_pdf)
    assert result["page_count"] > 0


def test_body_and_reference_no_overlap(english_pdf):
    # body和reference不应该有大段重复内容
    result = parse_pdf(english_pdf)
    # 取reference开头100个字符，不应该出现在body的后半段
    if result["reference"]:
        ref_start = result["reference"][:50].strip()
        assert ref_start not in result["body"][-500:]


# ── chunker（纯单元，无需 PDF 夹具）──────────────────────────


def test_empty_input_returns_empty_list():
    assert chunker("") == []
    assert chunker("   \n  ") == []


def test_is_chinese_detection():
    assert is_chinese("这是一篇中文论文的摘要，研究了储备池计算。")
    assert not is_chinese("This is an English abstract about reservoir computing.")
    assert not is_chinese("")


def test_estimate_tokens_monotonic():
    # 文本越长，估算 token 越多
    short = "reservoir computing"
    long = "reservoir computing " * 50
    assert estimate_tokens(short) < estimate_tokens(long)
    # 注：校准公式含非零截距 C（CHUNK_CALIB_C≈3.19），空串估算为一个小常数而非 0。
    # 这是回归校准的固有行为；生产路径 chunker() 已对空输入提前 return []，
    # estimate_tokens 不会在空串上被实际依赖，故此处只断言「空 < 有内容」的单调性。
    assert estimate_tokens("") < estimate_tokens(short)


def test_long_text_splits_into_multiple_chunks():
    # 长文本（带分隔符）应被切成多段，且每段在 token 预算内
    text = ("Photonic reservoir computing exploits optical nonlinearity. " * 200)
    chunks = chunker(text)
    assert len(chunks) > 1
    for c in chunks:
        assert estimate_tokens(c) <= CHUNK_SIZE_EN * 1.5


# ── chunker（依赖 PDF 夹具）─────────────────────────────────


def test_returns_list(english_pdf):
    blocks = parse_pdf(english_pdf)
    chunks = chunker(blocks["body"])
    assert isinstance(chunks, list)


def test_not_empty(english_pdf):
    blocks = parse_pdf(english_pdf)
    chunks = chunker(blocks["body"])
    assert len(chunks) > 0


def test_chunk_token_length_reasonable(english_pdf):
    blocks = parse_pdf(english_pdf)
    chunks = chunker(blocks["body"])
    for chunk in chunks:
        # 按估算 token 度量，允许 50% 余量（强制切分边界可能略超）
        assert estimate_tokens(chunk) <= CHUNK_SIZE_EN * 1.5, (
            f"chunk 估算 token 过长: {estimate_tokens(chunk)}"
        )


def test_chunk_not_too_short(english_pdf):
    blocks = parse_pdf(english_pdf)
    chunks = chunker(blocks["body"])
    # 过滤掉最后一个chunk（可能天然较短），其余不应该过短
    if len(chunks) > 1:
        for chunk in chunks[:-1]:
            assert len(chunk) > 5, f"chunk过短，可能切分异常: {repr(chunk)}"


def test_chinese_chunker(chinese_pdf):
    blocks = parse_pdf(chinese_pdf)
    chunks = chunker(blocks["body"])
    assert len(chunks) > 0
    for chunk in chunks:
        assert estimate_tokens(chunk) <= CHUNK_SIZE_ZH * 1.5
