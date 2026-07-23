"""test_retrieve_classify.py — retrieve 出口状态分类器（_classify_retrieve_outcome）单测。

T2 Stage 2 修订新增的逻辑：把 retrieve（子 agent）的出口分成 5 类（SUCCESS/NO_MATCH/
INFRA_FAIL/INCOMPLETE/HARD_FAIL），让主 agent 区分「无符合材料」与「上游硬伤」。分类信号
全在子 agent state/messages——本测试构造极小 result/exception 覆盖 5 个分支 + 边界
（guard 哨兵排除、rag 纯文本计成功、部分错误仍有 ok→SUCCESS）。
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from src.rag.graph import _classify_retrieve_outcome


def _rf_call(idx: str = "rf1") -> AIMessage:
    """一条调了 return_findings 的 AIMessage（正常收敛信号）。"""
    return AIMessage(
        content="",
        tool_calls=[{"name": "return_findings", "args": {"selection": [], "summary": ""}, "id": idx}],
    )


def _tool(name: str, content: str, idx: str = "t1") -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id=idx)


# ── 5 个主分支 ──────────────────────────────────────────────


def test_hard_fail_exception() -> None:
    """ainvoke 抛异常 → HARD_FAIL（图级硬伤，非「无文献」）。"""
    status, hint = _classify_retrieve_outcome(None, Exception("network unreachable"))
    assert status == "HARD_FAIL"
    assert "非" in hint and "背景知识" in hint


def test_infra_fail_all_tools_errored() -> None:
    """工具全部报错（ok=0, err>0）→ INFRA_FAIL（管道断了，非「无符合材料」）。"""
    result = {
        "messages": [
            _tool(
                "s2_search_tool",
                '{"success": false, "error_type": "rate_limited"}',
                "1",
            ),
            _tool(
                "arxiv_tool",
                '{"success": false, "error_type": "request_failed"}',
                "2",
            ),
        ],
        "findings": "",
    }
    status, hint = _classify_retrieve_outcome(result, None)
    assert status == "INFRA_FAIL"
    assert "报错" in hint


def test_success_ok_and_converged() -> None:
    """有成功工具结果(ok>0) + 调了 return_findings → SUCCESS。"""
    result = {
        "messages": [
            _tool("s2_search_tool", '{"success": true, "papers": [{"s2_paper_id": "abc"}]}', "1"),
            _rf_call(),
        ],
        "findings": "## 检索结果\n### 检索结果 #0\n...",
    }
    status, _ = _classify_retrieve_outcome(result, None)
    assert status == "SUCCESS"


def test_incomplete_ok_but_budget_exhausted() -> None:
    """有成功结果(ok>0) 但没调 return_findings（预算耗尽兜底）→ INCOMPLETE。"""
    result = {
        "messages": [_tool("rag_tool", "本地检索到的相关文段……")],
        "findings": "## 检索结果（预算耗尽兜底）\n...",
    }
    status, hint = _classify_retrieve_outcome(result, None)
    assert status == "INCOMPLETE"
    assert "预算" in hint or "甄选" in hint


def test_no_match_no_tool_activity_but_converged() -> None:
    """无任何工具结果(ok=0,err=0) + 调了 return_findings → NO_MATCH（语义空）。"""
    result = {"messages": [_rf_call()], "findings": ""}
    status, hint = _classify_retrieve_outcome(result, None)
    assert status == "NO_MATCH"
    assert "无符合" in hint or "语义空" in hint or "暂无" in hint


# ── 边界 ──────────────────────────────────────────────────


def test_guard_sentinel_excluded_from_count() -> None:
    """guard 注入的「驳回」假 ToolMessage 无 name，不计入成败比——不污染分类。

    strict guard 下子 agent 违规会注入这种 ToolMessage；它不该被当成真实工具结果。
    这里 1 条哨兵(无 name) + 1 条 s2 成功 + return_findings → 应判 SUCCESS（哨兵被忽略）。
    """
    sentinel = ToolMessage(
        content="[工具调用已被取消：未检测到必要的 <thinking> 块]",
        tool_call_id="fake1",  # 无 name
    )
    result = {
        "messages": [
            sentinel,
            _tool("s2_search_tool", '{"success": true, "papers": []}', "1"),
            _rf_call(),
        ],
        "findings": "## 检索结果\n...",
    }
    status, _ = _classify_retrieve_outcome(result, None)
    assert status == "SUCCESS"


def test_rag_plain_text_counts_as_ok() -> None:
    """rag_tool 返回纯文本（无 success 字段）→ 计成功，不计失败。"""
    result = {
        "messages": [_tool("rag_tool", "（无 JSON，纯文段）相关内容……"), _rf_call()],
        "findings": "## 检索结果\n...",
    }
    status, _ = _classify_retrieve_outcome(result, None)
    assert status == "SUCCESS"


def test_partial_error_with_some_ok_is_success() -> None:
    """部分工具报错但有成功结果(ok>0) → SUCCESS（有可用材料，不归 INFRA）。"""
    result = {
        "messages": [
            _tool("s2_search_tool", '{"success": false, "error_type": "rate_limited"}', "1"),
            _tool("arxiv_tool", '{"success": true, "papers": [{"arxiv_id": "x"}]}', "2"),
            _rf_call(),
        ],
        "findings": "## 检索结果\n...",
    }
    status, _ = _classify_retrieve_outcome(result, None)
    assert status == "SUCCESS"


def test_framework_error_status_counts_as_err() -> None:
    """框架层 status=='error'（pydantic 校验/异常）→ 计失败。全失败 → INFRA_FAIL。"""
    result = {
        "messages": [
            ToolMessage(content="invalid args", name="s2_search_tool", tool_call_id="1", status="error"),
        ],
        "findings": "",
    }
    status, _ = _classify_retrieve_outcome(result, None)
    assert status == "INFRA_FAIL"
