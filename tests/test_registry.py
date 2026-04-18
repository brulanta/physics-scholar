import pytest
from datetime import datetime
from src.core.registry import (
    PaperMeta,
    register_paper,
    is_duplicate,
    update_after_index,
    remove_paper,
    search_by_keyword,
)

TEST_USER = "test_user"
TEST_DOC_ID = "test_doc_id_registry_001"


@pytest.fixture(autouse=True)
def cleanup():
    # 每个测试前后都清理，保证测试互不影响
    remove_paper(TEST_DOC_ID, TEST_USER)
    yield
    remove_paper(TEST_DOC_ID, TEST_USER)


@pytest.fixture
def sample_meta():
    return PaperMeta(
        doc_id=TEST_DOC_ID,
        title="胶原蛋白测试论文",
        author="张三, 李四",
        year="2024",
        file_name="test.pdf",
        upload_time=datetime.now().isoformat(),
        source_type="user",
        user_id=TEST_USER,
    )


# ── 注册 & 查重 ──────────────────────────────────────────


def test_register_success(sample_meta):
    result = register_paper(sample_meta)
    assert result["success"] is True


def test_is_duplicate_after_register(sample_meta):
    register_paper(sample_meta)
    assert is_duplicate(TEST_DOC_ID, TEST_USER) is True


def test_is_not_duplicate_before_register():
    assert is_duplicate(TEST_DOC_ID, TEST_USER) is False


# ── 更新状态 ─────────────────────────────────────────────


def test_update_after_index(sample_meta):
    register_paper(sample_meta)
    result = update_after_index(
        TEST_DOC_ID, chunk_count=42, page_count=8, user_id=TEST_USER
    )
    assert result["success"] is True


def test_update_nonexistent_doc():
    # 更新一个不存在的doc，应该返回失败而不是报错
    result = update_after_index(
        "nonexistent_id", chunk_count=10, page_count=3, user_id=TEST_USER
    )
    assert result["success"] is False


# ── 删除 ─────────────────────────────────────────────────


def test_remove_paper(sample_meta):
    register_paper(sample_meta)
    remove_paper(TEST_DOC_ID, TEST_USER)
    assert is_duplicate(TEST_DOC_ID, TEST_USER) is False


def test_remove_nonexistent_is_safe():
    # 删除不存在的doc不应该报错
    result = remove_paper("nonexistent_id", TEST_USER)
    assert result["success"] is True


# ── 关键词检索 ────────────────────────────────────────────


def test_search_hit(sample_meta):
    register_paper(sample_meta)
    results = search_by_keyword(["胶原蛋白"], TEST_USER)
    assert len(results) > 0
    doc_ids = [r["doc_id"] for r in results]
    assert TEST_DOC_ID in doc_ids


def test_search_miss():
    results = search_by_keyword(["完全不存在的词xyzabc"], TEST_USER)
    assert results == []


def test_search_score_order(sample_meta):
    # 命中更多关键词的排在前面
    register_paper(sample_meta)
    results = search_by_keyword(["胶原蛋白", "测试论文"], TEST_USER)
    assert len(results) > 0
    assert results[0]["score"] >= 1
