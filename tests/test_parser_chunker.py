import pytest
from src.core.parser import parse_pdf
from src.core.chunker import chunker

CHUNK_SIZE = 300  # 和chunker里保持一致


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


# ── chunker ───────────────────────────────────────────────


def test_returns_list(english_pdf):
    blocks = parse_pdf(english_pdf)
    chunks = chunker(blocks["body"])
    assert isinstance(chunks, list)


def test_not_empty(english_pdf):
    blocks = parse_pdf(english_pdf)
    chunks = chunker(blocks["body"])
    assert len(chunks) > 0


def test_chunk_length_reasonable(english_pdf):
    blocks = parse_pdf(english_pdf)
    chunks = chunker(blocks["body"])
    for chunk in chunks:
        # 允许150%的余量（最后一个chunk可能较短，强制切分时可能略超）
        assert len(chunk) <= CHUNK_SIZE * 1.5, f"chunk过长: {len(chunk)}"


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
        assert len(chunk) <= CHUNK_SIZE * 1.5
