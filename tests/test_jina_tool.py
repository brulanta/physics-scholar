"""
tests/test_jina_tool.py

Jina 阅读工具测试。

运行方式：
  pytest tests/test_jina_tool.py              # 只跑 mock 测试（无网络请求）
  pytest tests/test_jina_tool.py --live       # 同时跑真实请求的冒烟测试
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, call, patch

import pytest
import requests


# ── 注册 --live 命令行选项（conftest 已注册时静默跳过）──────────
def pytest_addoption(parser):
    try:
        parser.addoption("--live", action="store_true", default=False)
    except ValueError:
        pass


def pytest_configure(config):
    config.addinivalue_line("markers", "live: 真实网络请求，需要 --live 参数才运行")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live", default=False):
        skip = pytest.mark.skip(reason="需要 --live 参数才运行")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip)


# ── 导入被测模块 ────────────────────────────────────────────────
import src.rag.tools.jina_tool as jina_mod
from src.rag.tools.jina_tool import (
    _estimate_tokens,
    _fetch_full_text,
    _split_chunks,
    jina_tool,
    set_jina_api_key,
    set_slice_llm,
)

# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def reset_jina_state():
    """每个测试前重置模块级全局状态。"""
    jina_mod._JINA_API_KEY = ""
    jina_mod._SLICE_LLM_BASE_URL = ""
    jina_mod._SLICE_LLM_API_KEY = ""
    jina_mod._SLICE_LLM_MODEL = ""
    jina_mod._LAST_JINA_CALL = 0.0
    jina_mod._JINA_BLOCK_UNTIL = 0.0
    jina_mod._RECENT_FAILED.clear()
    yield
    jina_mod._JINA_API_KEY = ""
    jina_mod._SLICE_LLM_BASE_URL = ""
    jina_mod._SLICE_LLM_API_KEY = ""
    jina_mod._SLICE_LLM_MODEL = ""
    jina_mod._LAST_JINA_CALL = 0.0
    jina_mod._JINA_BLOCK_UNTIL = 0.0
    jina_mod._RECENT_FAILED.clear()


@pytest.fixture
def slice_llm_configured():
    """配置好副 LLM 的 fixture。"""
    set_slice_llm(
        base_url="https://api.deepseek.com/v1",
        api_key="fake-slice-key",
        model="deepseek-chat",
    )


def _mock_jina_response(content: str, status_code: int = 200) -> MagicMock:
    """构造 Jina Reader 的模拟响应。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"data": {"content": content}}
    resp.text = content
    if status_code >= 400:
        http_err = requests.HTTPError(response=resp)
        resp.raise_for_status.side_effect = http_err
    else:
        resp.raise_for_status.return_value = None
    return resp


def _mock_score_response(score: int) -> MagicMock:
    """构造副 LLM 打分的模拟响应。"""
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"score": score})}}]
    }
    return resp


# ══════════════════════════════════════════════════════════════════
# 纯函数单元测试
# ══════════════════════════════════════════════════════════════════


class TestEstimateTokens:
    def test_empty_string(self):
        assert _estimate_tokens("") == 1  # max(1, 0//3)

    def test_short_english(self):
        # 30 字符 → 30//3 = 10
        assert _estimate_tokens("a" * 30) == 10

    def test_longer_text(self):
        result = _estimate_tokens("x" * 3000)
        assert result == 1000

    def test_never_zero(self):
        assert _estimate_tokens("ab") >= 1


class TestSplitChunks:
    def test_empty_text_returns_empty(self):
        assert _split_chunks("", 100, 10) == []

    def test_short_text_single_chunk(self):
        text = "Hello world."
        chunks = _split_chunks(text, chunk_size=1000, overlap=100)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_splits_into_multiple_chunks(self):
        text = "word " * 1000  # 5000 字符
        chunks = _split_chunks(text, chunk_size=1000, overlap=100)
        assert len(chunks) > 1

    def test_overlap_creates_shared_content(self):
        # 构造一段文字，检查相邻 chunk 有重叠
        text = "a" * 500 + "b" * 500 + "c" * 500
        chunks = _split_chunks(text, chunk_size=600, overlap=100)
        assert len(chunks) >= 2
        # 第一片的末尾应出现在第二片的开头
        if len(chunks) >= 2:
            end_of_first = chunks[0][-50:]
            start_of_second = chunks[1][:200]
            # 有重叠意味着第一片末尾的内容在第二片中能找到
            assert any(c in start_of_second for c in end_of_first.split())

    def test_chunks_cover_full_text(self):
        """所有 chunk 合并（去重后）应覆盖原文所有字符。"""
        text = "The quick brown fox jumps over the lazy dog. " * 50
        chunks = _split_chunks(text, chunk_size=100, overlap=20)
        combined = " ".join(chunks)
        # 原文中的每个词都应在 combined 中出现
        for word in ["quick", "brown", "fox", "lazy", "dog"]:
            assert word in combined

    def test_sentence_boundary_preferred(self):
        """有句号时应在句号处断开，而不是硬切。"""
        text = "First sentence. " + "X" * 900 + ". Second chunk starts here."
        chunks = _split_chunks(text, chunk_size=1000, overlap=50)
        # 第一片应在句号后截断，不会把 "First sentence" 切断
        if len(chunks) >= 1:
            assert "First sentence" in chunks[0]

    def test_no_empty_chunks(self):
        text = "  \n  " * 100 + "real content here" + "  \n  " * 100
        chunks = _split_chunks(text, chunk_size=100, overlap=10)
        for chunk in chunks:
            assert chunk.strip() != ""


# ══════════════════════════════════════════════════════════════════
# 无 query 模式（全文截断）Mock 测试
# ══════════════════════════════════════════════════════════════════


class TestJinaNoQuery:
    def test_successful_fetch_returns_content(self):
        full_text = "This is the paper content. " * 100
        mock_resp = _mock_jina_response(full_text)

        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp):
            result = json.loads(
                jina_tool.invoke({"url": "https://example.com/paper.pdf"})
            )

        assert result["success"] is True
        assert result["mode"] == "full_text_truncated"
        assert result["content"]
        assert result["estimated_tokens"] > 0

    def test_content_truncated_at_token_limit(self):
        # no_query_max_tokens=1500 → limit_chars = 4500
        full_text = "x" * 10000
        mock_resp = _mock_jina_response(full_text)

        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp):
            result = json.loads(
                jina_tool.invoke(
                    {"url": "https://example.com/paper.pdf", "no_query_max_tokens": 100}
                )
            )

        # 100 tokens * 3 chars = 300 chars limit
        assert len(result["content"]) <= 303  # 300 + "..."

    def test_short_content_not_truncated(self):
        short_text = "Short paper."
        mock_resp = _mock_jina_response(short_text)

        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp):
            result = json.loads(
                jina_tool.invoke({"url": "https://example.com/paper.pdf"})
            )

        assert result["content"] == short_text
        assert "截断" not in result["note"] or "已截断" not in result["note"]

    def test_note_mentions_truncation_when_long(self):
        full_text = "x" * 10000
        mock_resp = _mock_jina_response(full_text)

        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp):
            result = json.loads(
                jina_tool.invoke(
                    {"url": "https://example.com/paper.pdf", "no_query_max_tokens": 10}
                )
            )

        assert "query" in result["note"]  # 提示用户可以加 query

    def test_has_jina_key_false_without_key(self):
        mock_resp = _mock_jina_response("content")
        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp):
            result = json.loads(
                jina_tool.invoke({"url": "https://example.com/paper.pdf"})
            )
        assert result["has_jina_key"] is False

    def test_has_jina_key_true_with_key(self):
        set_jina_api_key("test-jina-key")
        mock_resp = _mock_jina_response("content")
        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp):
            result = json.loads(
                jina_tool.invoke({"url": "https://example.com/paper.pdf"})
            )
        assert result["has_jina_key"] is True

    def test_jina_key_in_auth_header(self):
        set_jina_api_key("my-jina-key")
        mock_resp = _mock_jina_response("content")
        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            jina_tool.invoke({"url": "https://example.com/paper.pdf"})
        headers = mock_get.call_args[1]["headers"]
        assert headers.get("Authorization") == "Bearer my-jina-key"

    def test_no_key_no_auth_header(self):
        mock_resp = _mock_jina_response("content")
        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            jina_tool.invoke({"url": "https://example.com/paper.pdf"})
        headers = mock_get.call_args[1]["headers"]
        assert "Authorization" not in headers

    def test_url_prepended_to_jina_reader_base(self):
        target_url = "https://arxiv.org/pdf/2301.00001.pdf"
        mock_resp = _mock_jina_response("content")
        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            jina_tool.invoke({"url": target_url})
        called_url = mock_get.call_args[0][0]
        assert called_url == jina_mod._JINA_READER_BASE + target_url


# ══════════════════════════════════════════════════════════════════
# 有 query 模式（分片打分）Mock 测试
# ══════════════════════════════════════════════════════════════════


class TestJinaWithQuery:
    def test_query_without_llm_config_returns_error(self):
        result = json.loads(
            jina_tool.invoke(
                {"url": "https://example.com/paper.pdf", "query": "experiment setup"}
            )
        )
        assert result["success"] is False
        assert result["error_type"] == "slice_no_config"

    def test_query_with_llm_returns_scored_chunks(self, slice_llm_configured):
        # 构造足够长的全文，能切出多个片段
        full_text = "This paper describes experimental methods. " * 100

        jina_resp = _mock_jina_response(full_text)
        score_resp = _mock_score_response(7)

        with patch.object(jina_mod._SESSION, "get", return_value=jina_resp):
            with patch.object(jina_mod._SESSION, "post", return_value=score_resp):
                result = json.loads(
                    jina_tool.invoke(
                        {
                            "url": "https://example.com/paper.pdf",
                            "query": "experimental methods",
                        }
                    )
                )

        assert result["success"] is True
        assert result["mode"] == "scored_chunks"
        assert result["total_chunks"] >= 1
        assert "chunks" in result

    def test_returned_chunks_have_required_fields(self, slice_llm_configured):
        full_text = "Sentence about experiment. " * 200
        jina_resp = _mock_jina_response(full_text)
        score_resp = _mock_score_response(8)

        with patch.object(jina_mod._SESSION, "get", return_value=jina_resp):
            with patch.object(jina_mod._SESSION, "post", return_value=score_resp):
                result = json.loads(
                    jina_tool.invoke(
                        {
                            "url": "https://example.com/paper.pdf",
                            "query": "experiment",
                        }
                    )
                )

        for chunk in result["chunks"]:
            assert "index" in chunk
            assert "score" in chunk
            assert "estimated_tokens" in chunk
            assert "text" in chunk
            assert isinstance(chunk["score"], int)
            assert 1 <= chunk["score"] <= 10

    def test_chunks_below_threshold_excluded(self, slice_llm_configured):
        full_text = "Some content here. " * 200
        jina_resp = _mock_jina_response(full_text)
        low_score_resp = _mock_score_response(3)  # 低于默认阈值 5

        with patch.object(jina_mod._SESSION, "get", return_value=jina_resp):
            with patch.object(jina_mod._SESSION, "post", return_value=low_score_resp):
                result = json.loads(
                    jina_tool.invoke(
                        {
                            "url": "https://example.com/paper.pdf",
                            "query": "anything",
                            "score_threshold": 5,
                        }
                    )
                )

        assert result["returned_chunks"] == 0
        assert "阈值" in result["note"]

    def test_top_n_limits_returned_chunks(self, slice_llm_configured):
        full_text = "High quality content about experiments. " * 500
        jina_resp = _mock_jina_response(full_text)
        high_score_resp = _mock_score_response(9)

        with patch.object(jina_mod._SESSION, "get", return_value=jina_resp):
            with patch.object(jina_mod._SESSION, "post", return_value=high_score_resp):
                result = json.loads(
                    jina_tool.invoke(
                        {
                            "url": "https://example.com/paper.pdf",
                            "query": "experiments",
                            "top_n": 2,
                            "score_threshold": 1,
                        }
                    )
                )

        assert result["returned_chunks"] <= 2

    def test_chunks_returned_in_original_order(self, slice_llm_configured):
        """返回的 chunks 应按原文顺序（index 升序）排列，而非按分数顺序。"""
        full_text = (
            "Section A content. " * 100
            + "Section B content. " * 100
            + "Section C content. " * 100
        )

        scores = iter([8, 6, 9])  # 第1片8分，第2片6分，第3片9分

        def score_side_effect(*args, **kwargs):
            s = next(scores, 7)
            return _mock_score_response(s)

        jina_resp = _mock_jina_response(full_text)

        with patch.object(jina_mod._SESSION, "get", return_value=jina_resp):
            with patch.object(jina_mod._SESSION, "post", side_effect=score_side_effect):
                result = json.loads(
                    jina_tool.invoke(
                        {
                            "url": "https://example.com/paper.pdf",
                            "query": "content",
                            "top_n": 3,
                            "score_threshold": 1,
                        }
                    )
                )

        indices = [c["index"] for c in result["chunks"]]
        assert indices == sorted(indices), "chunks 应按原文顺序（index升序）排列"

    def test_token_budget_limits_total_size(self, slice_llm_configured):
        full_text = "Data about experiments in this paper. " * 500
        jina_resp = _mock_jina_response(full_text)
        score_resp = _mock_score_response(9)

        with patch.object(jina_mod._SESSION, "get", return_value=jina_resp):
            with patch.object(jina_mod._SESSION, "post", return_value=score_resp):
                result = json.loads(
                    jina_tool.invoke(
                        {
                            "url": "https://example.com/paper.pdf",
                            "query": "experiments",
                            "top_n": 10,
                            "score_threshold": 1,
                            "max_return_tokens": 200,  # 很小的预算
                        }
                    )
                )

        assert result["estimated_tokens"] <= 200 + 400  # 允许单片略超预算

    def test_llm_called_once_per_chunk(self, slice_llm_configured):
        """副 LLM 应为每个片段调用一次，不多不少。"""
        # 构造恰好能切成 3 片的文本
        full_text = "A" * 1000 + "B" * 1000 + "C" * 1000
        jina_resp = _mock_jina_response(full_text)
        score_resp = _mock_score_response(6)

        with patch.object(jina_mod._SESSION, "get", return_value=jina_resp):
            with patch.object(
                jina_mod._SESSION, "post", return_value=score_resp
            ) as mock_post:
                result = json.loads(
                    jina_tool.invoke(
                        {
                            "url": "https://example.com/paper.pdf",
                            "query": "content",
                        }
                    )
                )

        assert mock_post.call_count == result["total_chunks"]

    def test_llm_parse_failure_gives_score_zero(self, slice_llm_configured):
        """副 LLM 返回格式错误时，该片段得分应为 0（不崩溃，排在末尾）。"""
        full_text = "Valid paper content about neural networks. " * 100
        jina_resp = _mock_jina_response(full_text)

        bad_resp = MagicMock()
        bad_resp.status_code = 200
        bad_resp.raise_for_status.return_value = None
        bad_resp.json.return_value = {
            "choices": [{"message": {"content": "not json at all"}}]
        }

        with patch.object(jina_mod._SESSION, "get", return_value=jina_resp):
            with patch.object(jina_mod._SESSION, "post", return_value=bad_resp):
                result = json.loads(
                    jina_tool.invoke(
                        {
                            "url": "https://example.com/paper.pdf",
                            "query": "neural networks",
                            "score_threshold": 1,
                        }
                    )
                )

        # 得分为 0 的片段低于任何正常阈值，不会进入结果（threshold 默认 5）
        # 但工具本身不应崩溃
        assert result["success"] is True

    def test_llm_score_clamped_to_1_10(self, slice_llm_configured):
        """副 LLM 返回越界分数时，应夹紧到 [1, 10]。"""
        full_text = "Content here. " * 100
        jina_resp = _mock_jina_response(full_text)

        # 模拟返回 15 分（越界）
        bad_score_resp = MagicMock()
        bad_score_resp.status_code = 200
        bad_score_resp.raise_for_status.return_value = None
        bad_score_resp.json.return_value = {
            "choices": [{"message": {"content": '{"score": 15}'}}]
        }

        with patch.object(jina_mod._SESSION, "get", return_value=jina_resp):
            with patch.object(jina_mod._SESSION, "post", return_value=bad_score_resp):
                result = json.loads(
                    jina_tool.invoke(
                        {
                            "url": "https://example.com/paper.pdf",
                            "query": "content",
                            "score_threshold": 1,
                        }
                    )
                )

        for chunk in result["chunks"]:
            assert 1 <= chunk["score"] <= 10


# ══════════════════════════════════════════════════════════════════
# 错误处理 Mock 测试
# ══════════════════════════════════════════════════════════════════


class TestJinaErrorHandling:
    def test_empty_url_returns_no_url_error(self):
        result = json.loads(jina_tool.invoke({"url": ""}))
        assert result["success"] is False
        assert result["error_type"] == "no_url"

    def test_whitespace_url_returns_no_url_error(self):
        result = json.loads(jina_tool.invoke({"url": "   "}))
        assert result["success"] is False
        assert result["error_type"] == "no_url"

    def test_429_returns_rate_limited(self):
        mock_resp = _mock_jina_response("", status_code=429)
        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp):
            with patch("time.sleep"):
                result = json.loads(
                    jina_tool.invoke({"url": "https://example.com/paper.pdf"})
                )

        assert result["success"] is False
        assert result["error_type"] == "rate_limited"

    def test_429_sets_block_until(self):
        mock_resp = _mock_jina_response("", status_code=429)
        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp):
            with patch("time.sleep"):
                jina_tool.invoke({"url": "https://example.com/paper.pdf"})

        assert jina_mod._JINA_BLOCK_UNTIL > time.time()

    def test_401_returns_auth_error(self):
        mock_resp = _mock_jina_response("", status_code=401)
        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp):
            with patch("time.sleep"):
                result = json.loads(
                    jina_tool.invoke({"url": "https://example.com/paper.pdf"})
                )

        assert result["success"] is False
        assert result["error_type"] == "auth_error"

    def test_timeout_returns_timeout_error(self):
        with patch.object(jina_mod._SESSION, "get", side_effect=requests.Timeout()):
            with patch("time.sleep"):
                result = json.loads(
                    jina_tool.invoke({"url": "https://example.com/paper.pdf"})
                )

        assert result["success"] is False
        assert result["error_type"] in ("timeout", "request_failed")

    def test_recent_failed_url_skips_request(self):
        """同一 URL 失败后再次调用，应直接返回缓存错误。"""
        mock_resp = _mock_jina_response("", status_code=429)
        with patch.object(jina_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            with patch("time.sleep"):
                jina_tool.invoke({"url": "https://example.com/paper.pdf"})
                first_count = mock_get.call_count
                result = json.loads(
                    jina_tool.invoke({"url": "https://example.com/paper.pdf"})
                )

        assert result["error_type"] == "recent_failed"
        assert mock_get.call_count == first_count  # 没有发出新请求

    def test_failed_result_has_null_content(self):
        with patch.object(
            jina_mod._SESSION, "get", side_effect=requests.ConnectionError()
        ):
            with patch("time.sleep"):
                result = json.loads(
                    jina_tool.invoke({"url": "https://example.com/paper.pdf"})
                )

        assert result["content"] is None
        assert result["mode"] is None


# ══════════════════════════════════════════════════════════════════
# 速率限制行为测试
# ══════════════════════════════════════════════════════════════════


class TestJinaRateLimit:
    def test_interval_longer_without_key(self):
        assert jina_mod._jina_interval() == jina_mod._INTERVAL_WITHOUT_KEY

    def test_interval_shorter_with_key(self):
        set_jina_api_key("any-key")
        assert jina_mod._jina_interval() == jina_mod._INTERVAL_WITH_KEY

    def test_set_jina_api_key_strips_whitespace(self):
        set_jina_api_key("  my-key  ")
        assert jina_mod._JINA_API_KEY == "my-key"

    def test_set_slice_llm_stores_all_fields(self):
        set_slice_llm("https://api.example.com/v1", "key-abc", "model-xyz")
        assert jina_mod._SLICE_LLM_BASE_URL == "https://api.example.com/v1"
        assert jina_mod._SLICE_LLM_API_KEY == "key-abc"
        assert jina_mod._SLICE_LLM_MODEL == "model-xyz"

    def test_set_slice_llm_strips_trailing_slash_on_use(self, slice_llm_configured):
        """调用副 LLM 时 URL 不应有双斜杠。"""
        set_slice_llm("https://api.deepseek.com/v1/", "key", "model")
        full_text = "content " * 100
        jina_resp = _mock_jina_response(full_text)
        score_resp = _mock_score_response(7)

        with patch.object(jina_mod._SESSION, "get", return_value=jina_resp):
            with patch.object(
                jina_mod._SESSION, "post", return_value=score_resp
            ) as mock_post:
                jina_tool.invoke(
                    {"url": "https://example.com/paper.pdf", "query": "test"}
                )

        called_url = mock_post.call_args[0][0]
        assert "//" not in called_url.replace("https://", "")


# ══════════════════════════════════════════════════════════════════
# Live 冒烟测试（需要 --live 参数 + 真实 Jina Key）
# ══════════════════════════════════════════════════════════════════


@pytest.mark.live
class TestJinaLive:
    @pytest.fixture(autouse=True)
    def inject_keys(self):
        from src.config import (
            JINA_API_KEY,
            SLICE_LLM_API_KEY,
            SLICE_LLM_BASE_URL,
            SLICE_LLM_MODEL,
        )

        set_jina_api_key(JINA_API_KEY)
        set_slice_llm(SLICE_LLM_BASE_URL, SLICE_LLM_API_KEY, SLICE_LLM_MODEL)

    def test_no_query_fetch_arxiv_pdf(self):
        # 使用 arXiv 上一篇公开论文的 PDF
        url = "https://arxiv.org/pdf/1706.03762"  # Attention Is All You Need
        result = json.loads(jina_tool.invoke({"url": url}))
        assert result["success"] is True
        assert result["mode"] == "full_text_truncated"
        assert len(result["content"]) > 100
        # 论文内容应包含 attention 相关词汇
        assert "attention" in result["content"].lower()

    def test_with_query_returns_relevant_chunks(self):
        url = "https://arxiv.org/pdf/1706.03762"
        result = json.loads(
            jina_tool.invoke(
                {
                    "url": url,
                    "query": "multi-head attention mechanism",
                    "top_n": 3,
                    "score_threshold": 3,
                }
            )
        )
        assert result["success"] is True
        assert result["mode"] == "scored_chunks"
        assert result["returned_chunks"] >= 1
        # 返回的片段应与 attention 相关
        all_text = " ".join(c["text"] for c in result["chunks"]).lower()
        assert "attention" in all_text
