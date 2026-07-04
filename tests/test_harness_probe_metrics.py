"""单测 harness_probe.collect_metrics 的行为指标推导（离线，无网络、无 agent）。

重点验证 ⑤ 暴露的量具缺口修复：`missing_thinking_calls`（profile 无关的合规真值）。
背景：`guard_hits` 数的是 strict 档注入的哨兵 ToolMessage——soft/off 档 guard 只打日志
放行、不注入哨兵，故 `guard_hits` 在 soft 恒 0，对「模型是否真的漏了 <thinking>」是**假阴性**。
guard 的违规谓词只是「带 tool_calls 却缺 <thinking>」，违规 AIMessage 在每种模式都留在
transcript，故直接数它才是 soft 档真合规度。

场景复刻自 plan ⑤ 实测（gemini-3.1-pro）：
  - STRONG(light)：工具调用均带 <thinking> → missing=0、compliance=1.0、guard=0（真零）
  - MINIMAL     ：工具调用漏 <thinking> → missing>0、compliance<1.0，但 guard=0（假阴性现形）

collect_metrics 只吃 `result` dict（{"messages":[...], "remaining_calls":n}），
故构造合成 transcript 直接断言，无需跑 agent。
"""

import importlib.util
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

# harness_probe 在 scripts/（非包），按文件路径加载
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "harness_probe.py"
_spec = importlib.util.spec_from_file_location("harness_probe", _SCRIPT)
harness_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness_probe)

collect_metrics = harness_probe.collect_metrics
GUARD_SENTINEL = harness_probe.GUARD_SENTINEL
BUDGET_SENTINEL = harness_probe.BUDGET_SENTINEL

THINK = "<thinking>\n[TOOL_LOOP: PENDING] 检索需求…\n</thinking>"


def _tool_call(name="rag_tool", cid="c1"):
    return {"name": name, "args": {"query": "x"}, "id": cid, "type": "tool_call"}


def _ai_with_tools(content, cid="c1"):
    return AIMessage(content=content, tool_calls=[_tool_call(cid=cid)])


def _real_tool_msg(cid="c1"):
    return ToolMessage(content="检索结果：段落若干…", tool_call_id=cid)


def _result(messages, remaining=6):
    return {"messages": messages, "remaining_calls": remaining}


# --- 核心缺口：soft 档 guard 假阴性 vs missing_thinking 真值 --------------------


def test_soft_compliant_all_thinking_present():
    """STRONG(light) 复刻：工具调用都带 <thinking>。missing=0、compliance=1.0、guard=0。"""
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="q"),
        _ai_with_tools(f"{THINK} 我需要检索。", cid="c1"),
        _real_tool_msg("c1"),
        AIMessage(content="最终答案正文。"),
    ]
    m = collect_metrics(_result(msgs))
    assert m["missing_thinking_calls"] == 0
    assert m["thinking_compliance_rate"] == 1.0
    assert m["guard_hits"] == 0          # 真零（合规）
    assert m["tool_turns"] == 1
    assert m["tool_rounds"] == 1


def test_soft_violation_visible_while_guard_hits_stays_zero():
    """MINIMAL 复刻：soft 放行下工具调用漏 <thinking>。

    这是修复的核心：guard_hits 仍 0（soft 不注入哨兵=假阴性），
    但 missing_thinking_calls 抓到违规、compliance<1.0。
    """
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="q"),
        _ai_with_tools("我直接检索（无 thinking）。", cid="c1"),  # 违规
        _real_tool_msg("c1"),
        _ai_with_tools(f"{THINK} 再查一次。", cid="c2"),          # 合规
        _real_tool_msg("c2"),
        AIMessage(content="最终答案。"),
    ]
    m = collect_metrics(_result(msgs))
    assert m["guard_hits"] == 0                    # 假阴性：soft 档看不到
    assert m["missing_thinking_calls"] == 1        # 真值：抓到 1 次违规
    assert m["tool_turns"] == 2
    assert m["thinking_compliance_rate"] == 0.5    # 1 - 1/2
    assert m["tool_rounds"] == 2


# --- strict 档：guard_hits 与 missing_thinking 应一致 -------------------------


def test_strict_guard_sentinel_and_missing_agree():
    """strict 档：一次违规 AIMessage → 一条哨兵 ToolMessage。二计数应一致。"""
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="q"),
        _ai_with_tools("漏了 thinking。", cid="c1"),  # 违规 AIMessage 留在 transcript
        ToolMessage(
            content=f"[工具调用已被取消：{GUARD_SENTINEL} 块，请先完成 [TOOL_LOOP] 再调用工具]",
            tool_call_id="c1",
        ),
        AIMessage(content="纠正后的答案。"),
    ]
    m = collect_metrics(_result(msgs))
    assert m["guard_hits"] == 1
    assert m["missing_thinking_calls"] == 1        # 与 guard_hits 一致
    # 哨兵 ToolMessage 不计入真实工具轮
    assert m["tool_rounds"] == 0


# --- 哨兵分类 & 空答/预算不回归 ----------------------------------------------


def test_sentinels_excluded_from_tool_rounds():
    """guard/budget 哨兵 ToolMessage 不得计入 tool_rounds；预算哨兵置 budget_hit。"""
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="q"),
        _real_tool_msg("c1"),
        ToolMessage(content=f"…{GUARD_SENTINEL}…", tool_call_id="c2"),
        ToolMessage(content=f"…{BUDGET_SENTINEL}…", tool_call_id="c3"),
        AIMessage(content="答案。"),
    ]
    m = collect_metrics(_result(msgs, remaining=0))
    assert m["tool_rounds"] == 1        # 仅那条真实工具消息
    assert m["guard_hits"] == 1
    assert m["budget_hit"] is True


def test_no_tool_turns_yields_none_rates():
    """纯直答（无工具调用）：compliance/marker 率为 None（分母 0），不报错。"""
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="q"),
        AIMessage(content="直接回答，无需工具。"),
    ]
    m = collect_metrics(_result(msgs))
    assert m["tool_turns"] == 0
    assert m["thinking_compliance_rate"] is None
    assert m["marker_emit_rate"] is None
    assert m["missing_thinking_calls"] == 0
    assert m["empty_answer"] is False
