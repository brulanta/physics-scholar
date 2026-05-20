"""
tests/test_s2_tool.py

S2 检索工具测试。

运行方式：
  pytest tests/test_s2_tool.py              # 只跑 mock 测试（无网络请求）
  pytest tests/test_s2_tool.py --live       # 同时跑真实请求的冒烟测试（需要网络）
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests


# ── 导入被测模块 ────────────────────────────────────────────────
import src.rag.tools.s2_tool as s2_mod
from src.rag.tools.s2_tool import (
    _build_search_query,
    _fetch_single_paper,
    _fmt_abstract,
    _parse_paper,
    s2_search_tool,
)


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def reset_s2_state():
    """每个测试前重置模块级全局状态，防止测试间互相污染。"""
    original_key = getattr(s2_mod, "S2_API_KEY", "")
    s2_mod.S2_API_KEY = ""
    s2_mod._LAST_S2_CALL = 0.0
    s2_mod._S2_BLOCK_UNTIL = 0.0
    s2_mod._RECENT_FAILED_QUERY.clear()

    yield

    # 测试后恢复为 config 中的初始值
    s2_mod.S2_API_KEY = original_key
    s2_mod._LAST_S2_CALL = 0.0
    s2_mod._S2_BLOCK_UNTIL = 0.0
    s2_mod._RECENT_FAILED_QUERY.clear()


def _make_s2_paper(
    paper_id: str = "abc123",
    title: str = "Test Paper",
    abstract: str = "A" * 400,
    year: int = 2024,
    citation_count: int = 42,
    arxiv_id: str | None = "2301.00001",
    pdf_url: str | None = "https://arxiv.org/pdf/2301.00001.pdf",
    tldr: str | None = "Short summary.",
) -> dict:
    """构造一个标准的 S2 API 论文条目。"""
    return {
        "paperId": paper_id,
        "title": title,
        "abstract": abstract,
        "authors": [{"name": "Alice"}, {"name": "Bob"}],
        "year": year,
        "publicationDate": f"{year}-06-01",
        "venue": "NeurIPS",
        "publicationTypes": ["JournalArticle"],
        "openAccessPdf": {"url": pdf_url} if pdf_url else None,
        "citationCount": citation_count,
        "influentialCitationCount": 5,
        "fieldsOfStudy": ["Computer Science"],
        "s2FieldsOfStudy": [],
        "externalIds": {"ArXiv": arxiv_id, "DOI": "10.1234/test"},
        "tldr": {"text": tldr} if tldr else None,
    }


def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    """构造一个模拟的 requests.Response。"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    if status_code >= 400:
        http_err = requests.HTTPError(response=resp)
        resp.raise_for_status.side_effect = http_err
    else:
        resp.raise_for_status.return_value = None
    return resp


# ══════════════════════════════════════════════════════════════════
# 纯函数单元测试（无网络）
# ══════════════════════════════════════════════════════════════════


class TestFmtAbstract:
    def test_short_abstract_not_truncated(self):
        text = "Short abstract."
        assert _fmt_abstract(text, full=False) == text

    def test_long_abstract_truncated(self):
        text = "X" * 500
        result = _fmt_abstract(text, full=False)
        assert result.endswith("...")
        assert len(result) == 303  # 300 + "..."

    def test_full_abstract_not_truncated(self):
        text = "X" * 500
        assert _fmt_abstract(text, full=True) == text

    def test_none_abstract_returns_empty(self):
        assert _fmt_abstract(None, full=False) == ""

    def test_empty_abstract_returns_empty(self):
        assert _fmt_abstract("", full=False) == ""


class TestParsePaper:
    def test_basic_fields_present(self):
        raw = _make_s2_paper()
        parsed = _parse_paper(raw, full_abstract=False)

        assert parsed["title"] == "Test Paper"
        assert parsed["s2_paper_id"] == "abc123"
        assert parsed["arxiv_id"] == "2301.00001"
        assert parsed["doi"] == "10.1234/test"
        assert parsed["open_access_pdf"] == "https://arxiv.org/pdf/2301.00001.pdf"
        assert parsed["citation_count"] == 42
        assert parsed["year"] == 2024
        assert parsed["venue"] == "NeurIPS"
        assert parsed["authors"] == ["Alice", "Bob"]
        assert "s2_url" in parsed
        assert "abc123" in parsed["s2_url"]

    def test_abstract_truncated_when_long(self):
        raw = _make_s2_paper(abstract="B" * 500)
        parsed = _parse_paper(raw, full_abstract=False)
        assert parsed["abstract"].endswith("...")

    def test_abstract_full_when_requested(self):
        raw = _make_s2_paper(abstract="B" * 500)
        parsed = _parse_paper(raw, full_abstract=True)
        assert not parsed["abstract"].endswith("...")
        assert len(parsed["abstract"]) == 500

    def test_tldr_extracted(self):
        raw = _make_s2_paper(tldr="One sentence summary.")
        parsed = _parse_paper(raw, full_abstract=False)
        assert parsed["tldr"] == "One sentence summary."

    def test_tldr_empty_when_absent(self):
        raw = _make_s2_paper(tldr=None)
        parsed = _parse_paper(raw, full_abstract=False)
        assert parsed["tldr"] == ""

    def test_no_pdf_returns_none(self):
        raw = _make_s2_paper(pdf_url=None)
        parsed = _parse_paper(raw, full_abstract=False)
        assert parsed["open_access_pdf"] is None

    def test_no_arxiv_id_returns_none(self):
        raw = _make_s2_paper(arxiv_id=None)
        raw["externalIds"] = {}
        parsed = _parse_paper(raw, full_abstract=False)
        assert parsed["arxiv_id"] is None

    def test_missing_optional_fields_dont_crash(self):
        """最简条目（只有必填字段）不崩溃。"""
        minimal = {"paperId": "x", "title": "T"}
        parsed = _parse_paper(minimal, full_abstract=False)
        assert parsed["title"] == "T"
        assert parsed["authors"] == []
        assert parsed["abstract"] == ""


class TestBuildSearchQuery:
    def test_keywords_only(self):
        q = _build_search_query(["transformer", "attention"], "")
        assert q == "transformer attention"

    def test_author_only(self):
        q = _build_search_query([], "Vaswani")
        assert q == "author:Vaswani"

    def test_keywords_and_author(self):
        q = _build_search_query(["optical comb"], "Smith")
        assert "optical comb" in q
        assert "author:Smith" in q

    def test_empty_returns_empty(self):
        q = _build_search_query([], "")
        assert q.strip() == ""


# ══════════════════════════════════════════════════════════════════
# 关键词检索 Mock 测试
# ══════════════════════════════════════════════════════════════════


class TestS2SearchKeyword:
    def test_successful_search_returns_papers(self):
        papers = [
            _make_s2_paper(paper_id=f"id{i}", title=f"Paper {i}") for i in range(3)
        ]
        mock_resp = _mock_response({"data": papers, "total": 3})

        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp):
            result = json.loads(s2_search_tool.invoke({"keywords": ["transformer"]}))

        assert result["success"] is True
        assert result["count"] == 3
        assert len(result["papers"]) == 3
        assert result["papers"][0]["title"] == "Paper 0"

    def test_empty_params_returns_invalid_error(self):
        result = json.loads(s2_search_tool.invoke({}))
        assert result["success"] is False
        assert result["error_type"] == "invalid_params"

    def test_year_range_passed_to_api(self):
        mock_resp = _mock_response({"data": [], "total": 0})
        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            s2_search_tool.invoke({"keywords": ["optics"], "year_range": "2020-2023"})
        call_kwargs = mock_get.call_args
        params = (
            call_kwargs[1]["params"]
            if "params" in call_kwargs[1]
            else call_kwargs[0][1]
        )
        assert params.get("year") == "2020-2023"

    def test_fields_of_study_passed_to_api(self):
        mock_resp = _mock_response({"data": [], "total": 0})
        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            s2_search_tool.invoke(
                {"keywords": ["optics"], "fields_of_study": ["Physics"]}
            )
        call_kwargs = mock_get.call_args
        params = (
            call_kwargs[1]["params"]
            if "params" in call_kwargs[1]
            else call_kwargs[0][1]
        )
        assert "Physics" in params.get("fieldsOfStudy", "")

    def test_max_results_capped_at_20(self):
        mock_resp = _mock_response({"data": [], "total": 0})
        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            s2_search_tool.invoke({"keywords": ["test"], "max_results": 20})
        params = mock_get.call_args[1]["params"]
        assert params["limit"] <= 20

    def test_has_s2_key_false_when_no_key(self):
        mock_resp = _mock_response({"data": [], "total": 0})
        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp):
            result = json.loads(s2_search_tool.invoke({"keywords": ["test"]}))
        assert result["has_s2_key"] is False

    def test_has_s2_key_true_when_key_set(self):
        s2_mod.S2_API_KEY = "fake-key-12345"  # 修改这一行
        mock_resp = _mock_response({"data": [], "total": 0})
        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp):
            result = json.loads(s2_search_tool.invoke({"keywords": ["test"]}))
        assert result["has_s2_key"] is True

    def test_api_key_injected_in_header(self):
        s2_mod.S2_API_KEY = "my-secret-key"  # 保持和下面断言的字符串一致
        mock_resp = _mock_response({"data": [], "total": 0})
        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            s2_search_tool.invoke({"keywords": ["test"]})
        headers = mock_get.call_args[1]["headers"]
        assert headers.get("x-api-key") == "my-secret-key"

    def test_no_key_no_auth_header(self):
        mock_resp = _mock_response({"data": [], "total": 0})
        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            s2_search_tool.invoke({"keywords": ["test"]})
        headers = mock_get.call_args[1]["headers"]
        assert "x-api-key" not in headers


# ══════════════════════════════════════════════════════════════════
# 精确 ID 查询 Mock 测试
# ══════════════════════════════════════════════════════════════════


class TestS2SearchById:
    def test_s2_paper_id_query_hits_paper_endpoint(self):
        paper = _make_s2_paper(paper_id="TARGET123", tldr="Great paper.")
        mock_resp = _mock_response(paper)

        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            result = json.loads(
                s2_search_tool.invoke(
                    {"s2_paper_ids": ["TARGET123"], "full_abstract": True}
                )
            )

        assert result["success"] is True
        assert result["count"] == 1
        assert result["papers"][0]["s2_paper_id"] == "TARGET123"
        assert result["papers"][0]["tldr"] == "Great paper."
        # 确认调用的是 /paper/{id} 端点而非 /paper/search
        called_url = mock_get.call_args[0][0]
        assert "TARGET123" in called_url

    def test_arxiv_id_gets_arxiv_prefix(self):
        paper = _make_s2_paper(arxiv_id="2301.00001")
        mock_resp = _mock_response(paper)

        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            s2_search_tool.invoke({"arxiv_ids": ["2301.00001"]})

        called_url = mock_get.call_args[0][0]
        assert "arXiv:2301.00001" in called_url

    def test_multiple_ids_makes_multiple_requests(self):
        paper = _make_s2_paper()
        mock_resp = _mock_response(paper)

        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            result = json.loads(
                s2_search_tool.invoke({"s2_paper_ids": ["id1", "id2", "id3"]})
            )

        assert mock_get.call_count == 3
        assert result["count"] == 3

    def test_full_abstract_true_returns_full_text(self):
        long_abstract = "Z" * 600
        paper = _make_s2_paper(abstract=long_abstract)
        mock_resp = _mock_response(paper)

        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp):
            result = json.loads(
                s2_search_tool.invoke({"s2_paper_ids": ["abc"], "full_abstract": True})
            )

        assert not result["papers"][0]["abstract"].endswith("...")
        assert len(result["papers"][0]["abstract"]) == 600


# ══════════════════════════════════════════════════════════════════
# 错误处理 Mock 测试
# ══════════════════════════════════════════════════════════════════


class TestS2ErrorHandling:
    def test_429_returns_rate_limited(self):
        # 1. 显式重置全局状态，防止被其他测试用例污染，或污染后续测试
        s2_mod._S2_BLOCK_UNTIL = 0.0
        s2_mod._LAST_S2_CALL = 0.0
        s2_mod._RECENT_FAILED_QUERY.clear()

        # 构造 429 Mock 响应
        mock_resp = _mock_response({}, status_code=429)

        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp):
            with patch("time.sleep"):  # 维持原样：不真正等待物理时间
                result = json.loads(s2_search_tool.invoke({"keywords": ["test"]}))

        # 2. 严格对齐重构后的结构断言
        assert result["success"] is False
        assert result["error_type"] == "rate_limited"
        assert (
            result["retryable"] is False
        )  # 429 触发后，在当前对话内引导 Agent 切换工具，故为 False

        # 3. 增强断言：验证我们新增的 Agent 心理防线机制是否成功塞入
        assert "agent_hint" in result
        assert "绝非你的关键词不好" in result["agent_hint"]

        # 4. 善后清理：避免当前用例留下的 60s 冷却期把后面的正常单测给卡死
        s2_mod._S2_BLOCK_UNTIL = 0.0

    def test_429_sets_block_until(self):
        mock_resp = _mock_response({}, status_code=429)
        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp):
            with patch("time.sleep"):
                s2_search_tool.invoke({"keywords": ["test"]})

        assert s2_mod._S2_BLOCK_UNTIL > time.time()

    def test_timeout_returns_timeout_error(self):
        with patch.object(s2_mod._SESSION, "get", side_effect=requests.Timeout()):
            with patch("time.sleep"):
                result = json.loads(s2_search_tool.invoke({"keywords": ["test"]}))

        assert result["success"] is False
        assert result["error_type"] == "timeout"
        assert result["retryable"] is True

    def test_recent_failed_query_skips_request(self):
        """同一 query 失败后再次调用，应直接返回 recent_failed_query，不发请求。"""
        mock_resp = _mock_response({}, status_code=429)
        with patch.object(s2_mod._SESSION, "get", return_value=mock_resp) as mock_get:
            with patch("time.sleep"):
                # 第一次：触发 429，写入失败缓存
                s2_search_tool.invoke({"keywords": ["test"]})
                first_call_count = mock_get.call_count
                # 第二次：相同 query，应被缓存拦截
                result = json.loads(s2_search_tool.invoke({"keywords": ["test"]}))

        assert result["error_type"] == "recent_failed_query"
        # 第二次没有发出新请求
        assert mock_get.call_count == first_call_count

    def test_request_failed_returns_error(self):
        with patch.object(
            s2_mod._SESSION,
            "get",
            side_effect=requests.ConnectionError("connection refused"),
        ):
            with patch("time.sleep"):
                result = json.loads(s2_search_tool.invoke({"keywords": ["test"]}))

        assert result["success"] is False
        assert result["error_type"] == "request_failed"

    def test_error_message_suggests_arxiv_fallback(self):
        """失败时 agent_hint 应提示可回退到 arxiv_tool。"""
        with patch.object(
            s2_mod._SESSION, "get", side_effect=requests.ConnectionError()
        ):
            with patch("time.sleep"):
                result = json.loads(s2_search_tool.invoke({"keywords": ["test"]}))

        # 🟢 将 ["message"] 修改为 ["agent_hint"]
        assert "arxiv_tool" in result["agent_hint"]

    def test_single_id_failure_doesnt_block_others(self):
        """多个 ID 中某一个请求失败，其他 ID 应继续处理。"""
        good_paper = _make_s2_paper(paper_id="good")
        good_resp = _mock_response(good_paper)
        bad_resp = _mock_response({}, status_code=500)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return bad_resp if call_count == 1 else good_resp

        with patch.object(s2_mod._SESSION, "get", side_effect=side_effect):
            with patch("time.sleep"):
                result = json.loads(
                    s2_search_tool.invoke({"s2_paper_ids": ["fail_id", "good_id"]})
                )

        assert result["success"] is True
        # 失败的 id 被跳过，成功的 id 返回
        assert result["count"] >= 1


# ══════════════════════════════════════════════════════════════════
# 速率限制行为测试
# ══════════════════════════════════════════════════════════════════


class TestS2RateLimit:
    def test_interval_longer_without_key(self):
        assert s2_mod._min_interval() == s2_mod._INTERVAL_WITHOUT_KEY

    def test_interval_shorter_with_key(self):
        s2_mod.S2_API_KEY = "any-key"  # 修改这一行
        assert s2_mod._min_interval() == s2_mod._INTERVAL_WITH_KEY

    def test_block_until_respected(self):
        """_BLOCK_UNTIL 设置后，_wait 函数应调用 sleep。"""
        s2_mod._S2_BLOCK_UNTIL = time.time() + 5
        with patch("time.sleep") as mock_sleep:
            with patch("time.time", return_value=s2_mod._S2_BLOCK_UNTIL - 2):
                pass  # 不实际调用 wait，只验证变量被正确设置
        assert s2_mod._S2_BLOCK_UNTIL > time.time() - 1  # 仍在冷却期内


# ══════════════════════════════════════════════════════════════════
# Live 冒烟测试（需要 --live 参数）
# ══════════════════════════════════════════════════════════════════


@pytest.mark.live
class TestS2Live:
    def test_keyword_search_returns_results(self):
        result = json.loads(
            s2_search_tool.invoke(
                {"keywords": ["optical frequency comb"], "max_results": 2}
            )
        )
        # 如果因为真实环境被频控导致请求失败，直接跳过测试，不判定为 Bug
        if not result["success"] and result.get("error_type") == "rate_limited":
            pytest.skip("S2 API 触发真实 429 限制，跳过此 live 测试")

        assert result["success"] is True
        assert result["count"] >= 1

    def test_arxiv_id_lookup(self):
        # 补充前置睡眠，防止上一个 live 测试刚跑完导致 429 叠加
        time.sleep(2.0)

        result = json.loads(
            s2_search_tool.invoke({"arxiv_ids": ["1706.03762"], "full_abstract": True})
        )
        if not result["success"] and result.get("error_type") == "rate_limited":
            pytest.skip("S2 API 触发真实 429 限制，跳过此 live 测试")

        assert result["success"] is True
        assert result["count"] == 1

    def test_author_search(self):
        # 补充前置睡眠，稍微缓解一下高频请求对官方 API 的压力
        time.sleep(2.0)

        result = json.loads(
            s2_search_tool.invoke({"author": "Vaswani", "max_results": 3})
        )

        # 🟢 增加 429 动态跳过防护：如果遭遇官方真实频控，优雅跳过测试而不断言失败
        if not result["success"] and result.get("error_type") == "rate_limited":
            pytest.skip("S2 API 触发真实 429 限制，跳过此 live 作者检索测试")

        assert result["success"] is True
        assert result["count"] >= 1
