import pytest
from datetime import datetime
from src.core import registry
from src.core.ingestor import ingest_pdf, confirm_and_index
from src.core.hash_file import get_pdf_hash

TEST_USER = "test_user"


@pytest.fixture(autouse=True)
def cleanup(english_pdf, chinese_pdf):
    # 测试前清理，防止上次测试残留影响
    for pdf in [english_pdf, chinese_pdf]:
        doc_id = get_pdf_hash(pdf)
        registry.remove_paper(doc_id, TEST_USER)
    yield
    # 测试后再清理一次
    for pdf in [english_pdf, chinese_pdf]:
        doc_id = get_pdf_hash(pdf)
        registry.remove_paper(doc_id, TEST_USER)

    # ── 第一阶段：ingest_pdf ──────────────────────────────────


def test_returns_success(english_pdf):
    result = ingest_pdf(english_pdf, source_type="user", user_id=TEST_USER)
    assert result["success"] is True


def test_returns_paper_meta(english_pdf):
    result = ingest_pdf(english_pdf, source_type="user", user_id=TEST_USER)
    assert "paper_meta" in result
    meta = result["paper_meta"]
    assert meta.doc_id != ""
    assert meta.status == "pending"


def test_title_extracted(english_pdf):
    result = ingest_pdf(english_pdf, source_type="user", user_id=TEST_USER)
    meta = result["paper_meta"]
    assert len(meta.title.strip()) > 0


def test_duplicate_rejected(english_pdf):
    # 第一次上传成功，但不写注册表（pending状态还没confirm）
    # 模拟已经confirm过的情况：手动注册一次
    first = ingest_pdf(english_pdf, source_type="user", user_id=TEST_USER)
    meta = first["paper_meta"]
    meta.status = "indexed"
    registry.register_paper(meta, TEST_USER)

    # 第二次上传同一个文件，应该被拒绝
    second = ingest_pdf(english_pdf, source_type="user", user_id=TEST_USER)
    assert second["success"] is False
    assert "已存在" in second["detail"] or "already exists" in second["detail"]


def test_chinese_pdf(chinese_pdf):
    result = ingest_pdf(chinese_pdf, source_type="user", user_id=TEST_USER)
    assert result["success"] is True
    assert len(result["paper_meta"].title.strip()) > 0


# ── 第二阶段：confirm_and_index 全流程 ────────────────────


def test_full_pipeline_english(english_pdf):
    # 第一阶段
    result = ingest_pdf(english_pdf, source_type="user", user_id=TEST_USER)
    assert result["success"] is True
    meta = result["paper_meta"]

    # 第二阶段：用户确认title（这里模拟用户直接确认，不修改）
    confirmed = confirm_and_index(
        paper_meta=meta,
        pdf_path=english_pdf,
        confirmed_title=meta.title,
        user_id=TEST_USER,
    )
    assert confirmed["success"] is True, confirmed.get("detail")


def test_registry_status_after_index(english_pdf):
    result = ingest_pdf(english_pdf, source_type="user", user_id=TEST_USER)
    meta = result["paper_meta"]

    confirm_and_index(
        paper_meta=meta,
        pdf_path=english_pdf,
        confirmed_title=meta.title,
        user_id=TEST_USER,
    )

    # 检查注册表状态
    reg = registry.load_registry(TEST_USER)
    doc = reg.get(meta.doc_id)
    assert doc is not None
    assert doc["status"] == "indexed"
    assert doc["chunk_count"] > 0
    assert doc["page_count"] > 0


def test_user_can_modify_title(english_pdf):
    result = ingest_pdf(english_pdf, source_type="user", user_id=TEST_USER)
    meta = result["paper_meta"]
    custom_title = "我自己改的标题"

    confirm_and_index(
        paper_meta=meta,
        pdf_path=english_pdf,
        confirmed_title=custom_title,
        user_id=TEST_USER,
    )

    reg = registry.load_registry(TEST_USER)
    doc = reg.get(meta.doc_id)
    assert doc["title"] == custom_title


def test_full_pipeline_chinese(chinese_pdf):
    result = ingest_pdf(chinese_pdf, source_type="user", user_id=TEST_USER)
    assert result["success"] is True
    meta = result["paper_meta"]

    confirmed = confirm_and_index(
        paper_meta=meta,
        pdf_path=chinese_pdf,
        confirmed_title=meta.title,
        user_id=TEST_USER,
    )
    assert confirmed["success"] is True


def test_chroma_has_chunks_after_index(english_pdf):
    from src.core.ingestor import get_vectorstore

    result = ingest_pdf(english_pdf, source_type="user", user_id=TEST_USER)
    meta = result["paper_meta"]

    confirm_and_index(
        paper_meta=meta,
        pdf_path=english_pdf,
        confirmed_title=meta.title,
        user_id=TEST_USER,
    )

    vs = get_vectorstore()
    chroma_result = vs.get(where={"doc_id": meta.doc_id})
    assert len(chroma_result["ids"]) > 0


def test_chroma_similarity_search(english_pdf):
    # 先入库
    result = ingest_pdf(english_pdf, source_type="user", user_id=TEST_USER)
    meta = result["paper_meta"]
    confirm_and_index(
        paper_meta=meta,
        pdf_path=english_pdf,
        confirmed_title=meta.title,
        user_id=TEST_USER,
    )
    from src.core.ingestor import get_vectorstore

    # 用论文相关的词查询，应该能召回
    vs = get_vectorstore()
    docs = vs.similarity_search(
        "reservoir computing optical amplifier", k=3, filter={"doc_id": meta.doc_id}
    )
    assert len(docs) > 0
    # 召回的内容应该和查询语义相关，至少包含论文里的词
    full_text = " ".join([d.page_content for d in docs])
    assert any(
        word in full_text.lower() for word in ["reservoir", "optical", "computing"]
    )
