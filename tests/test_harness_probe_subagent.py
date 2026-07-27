"""test_harness_probe_subagent.py — T2 Stage 3：harness_probe 子 agent 探测路径单测。

Stage 3 给 harness_probe 加了 --target sub（直跑子 agent 验 RETRIEVER）+ --model（cross-model），
并修了主 agent 种子 bug（remaining_calls 须为 1，见 _prepare 注释）。本文件复刻
test_harness_probe_metrics.py 的 importlib 文件加载模式，离线验证这些新增逻辑（无网络、无 agent）：

- build_main_state 播 remaining_calls=1（Stage 3 修复的回归门——主 agent 有效预算恒 1，
  after_guard 的 remaining<0 才能在第二次 retrieve 兜底）。
- build_sub_state 形状：system=子 agent prompt（非主 agent prompt）、remaining=profile.budget_n
  （RETRIEVER=6，未被强制）、findings=''、无 next_prefill（忠实复刻 make_retrieve_tool 种子）。
- collect_metrics 在子 agent 风格 transcript 上的行为：guard **strict** 下 GUARD_SENTINEL 是真信号
  （guard_hits 计数，与 missing_thinking_calls 一致——非原计划 guard off 的恒 0 假阴性）、
  return_findings 被 finalize 拦截不产 ToolMessage 故不计 tool_rounds、预算耗尽走 finalize
  （非 final_answer）故无 BUDGET_SENTINEL——budget_hit 仅由 remaining<=0 反映。
"""
import importlib.util
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.rag.harness_profile import PRESETS
from src.rag.prompts.subagent_prompt import build_subagent_prompt

# harness_probe 在 scripts/（非包），按文件路径加载。用区别于 test_harness_probe_metrics 的
# 模块名，避免同会话双加载冲突。
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "harness_probe.py"
_spec = importlib.util.spec_from_file_location("harness_probe_sub_test", _SCRIPT)
harness_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness_probe)

collect_metrics = harness_probe.collect_metrics
build_main_state = harness_probe.build_main_state
build_sub_state = harness_probe.build_sub_state
GUARD_SENTINEL = harness_probe.GUARD_SENTINEL

# 子 agent 的 thinking 不带 [TOOL_LOOP] marker——marker 闸门是主 agent 流式专属，
# 子 agent 非流式不用（故 marker_emit_rate 恒 0/None，是真零非噪声）。
SUB_THINK = (
    "<thinking>\n缺口：摘要不够支撑。下一步：调 s2_search_tool 查 X。\n现有结果：暂无。\n</thinking>"
)


def _ai(content, name="s2_search_tool", cid="c1"):
    return AIMessage(
        content=content,
        tool_calls=[{"name": name, "args": {"query": "x"}, "id": cid, "type": "tool_call"}],
    )


def _rf_ai(cid="rf"):
    """调 return_findings 的 AIMessage（终止意图，finalize 拦截，不产 ToolMessage）。"""
    return AIMessage(
        content=f"{SUB_THINK} 检索够了，收敛。",
        tool_calls=[
            {"name": "return_findings", "args": {"selection": [], "summary": ""}, "id": cid, "type": "tool_call"}
        ],
    )


def _tool_ok(cid="c1", name="s2_search_tool"):
    return ToolMessage(
        content='{"success": true, "papers": [{"s2_paper_id": "abc"}]}',
        tool_call_id=cid,
        name=name,
    )


# --- build_main_state：Stage 3 种子修复回归门 -------------------------------


def test_main_state_seeds_remaining_calls_one():
    """主 agent 有效预算恒 1（build_agent 强制 budget_n=1）；种子须匹配，after_guard 的
    remaining<0 才能在第二次 retrieve 兜底（单次 retrieve 契约的 budget 安全网）。

    Stage 3 修复点——不论传哪个 profile，主 agent 种子都必须是 1（profile 的 budget_n 对主
    agent 无意义，build_agent 会强制改写）。
    """
    for pname in ("FLASH", "STRONG", "MINIMAL"):
        state = build_main_state("q", "u", "normal", PRESETS[pname])
        assert state["remaining_calls"] == 1, f"{pname} 主 agent 种子须为 1（build_agent 强制预算 1）"


# --- build_sub_state：形状正确（忠实复刻 make_retrieve_tool 种子） ------------


def test_sub_state_uses_subagent_prompt_and_unforced_budget():
    """子 agent state：system=子 agent prompt（非主 agent prompt）、remaining=profile.budget_n
    （RETRIEVER=6，未被 build_subagent 强制）、findings=''、无 next_prefill。"""
    profile = PRESETS["RETRIEVER"]
    state = build_sub_state("q", "u", profile)
    sys_content = state["messages"][0].content
    assert sys_content == build_subagent_prompt()        # 子 agent 专属 prompt
    assert "检索系统" in sys_content                     # 身份标识（确认非主 agent prompt）
    assert "return_findings" in sys_content              # 终止协议在场
    assert state["remaining_calls"] == profile.budget_n   # 6，未强制（与主 agent 的强制 1 对照）
    assert state["findings"] == ""
    assert "next_prefill" not in state                   # 子 agent 不用主 agent 的 prefill 槽


# --- collect_metrics：子 agent 风格 transcript ------------------------------


def test_sub_transcript_return_findings_not_a_tool_round():
    """return_findings 被 finalize 拦截、不产 ToolMessage → 不计 tool_rounds；但它仍是带
    tool_calls 的 AIMessage → 计入 tool_turns（marker/compliance 分母）。子 agent 不吐
    [TOOL_LOOP] → marker_emit_rate=0.0（真零，非噪声）。"""
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="q"),
        _ai(f"{SUB_THINK} 查一下。", cid="c1"),
        _tool_ok("c1"),
        _rf_ai(),  # 终止意图（无对应 ToolMessage）
    ]
    m = collect_metrics({"messages": msgs, "remaining_calls": 5})
    assert m["tool_rounds"] == 1    # 仅 s2 真实结果；return_findings 无 ToolMessage
    assert m["tool_turns"] == 2     # s2 调用 + return_findings 调用都带 tool_calls
    assert m["guard_hits"] == 0
    assert m["missing_thinking_calls"] == 0    # 两次调用都带 thinking
    assert m["marker_emit_rate"] == 0.0        # 子 agent 不用 marker 闸门


def test_sub_strict_guard_sentinel_is_real_signal():
    """子 agent guard strict（Stage 2 修订：非原计划的 off）→ 违规会注入 GUARD_SENTINEL。

    guard_hits 是真信号（非 guard off 的恒 0 假阴性），且与 missing_thinking_calls 一致；
    哨兵 ToolMessage 无 name，不计 tool_rounds。
    """
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="q"),
        _ai("直接查（漏 thinking）。", cid="c1"),                        # 违规 AIMessage
        ToolMessage(                                                     # strict guard 哨兵（无 name）
            content=f"[工具调用已被取消：{GUARD_SENTINEL} 块]",
            tool_call_id="c1",
        ),
        _ai(f"{SUB_THINK} 补 thinking 重查。", cid="c2"),
        _tool_ok("c2"),
        _rf_ai(),
    ]
    m = collect_metrics({"messages": msgs, "remaining_calls": 4})
    assert m["guard_hits"] == 1                # strict 真信号
    assert m["missing_thinking_calls"] == 1    # 与 guard_hits 一致（一次违规）
    assert m["tool_rounds"] == 1               # 哨兵不计，仅 s2 真实结果


def test_sub_budget_exhaustion_reflected_via_remaining_not_sentinel():
    """子 agent 预算耗尽走 finalize（非 final_answer）→ transcript 无 BUDGET_SENTINEL。

    budget_forced 内部恒 False（无哨兵可数），但 budget_hit 仍由 remaining<=0 置 True——
    即子 agent 的预算耗尽能被探测到，只是信号源是 remaining 而非哨兵。
    """
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="q"),
        AIMessage(content="子 agent 兜底收敛。"),  # finalize 拼完 findings，无 BUDGET_SENTINEL
    ]
    m = collect_metrics({"messages": msgs, "remaining_calls": -1})
    assert m["budget_hit"] is True     # remaining<=0 路径（无 BUDGET_SENTINEL）
