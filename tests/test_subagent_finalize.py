"""单测 T2 子 agent 终止机制 + 候选源冒泡（离线，无网络、无 LLM）。

Stage 1 核心：
  - `after_guard` 的 terminator（return_findings）路由 + budget_route（子 agent 预算耗尽走 finalize）
  - `_subagent_finalize` 三路径：按 selection 抠索引 / 预算耗尽兜底全拼 / 索引越界兜底全拼
  - `_extract_findings` 取 state['findings']，缺失时兜底
  - `_consume_events` 的 on_tool_end artifact 分支：retrieve 壳冒泡的子 agent 工具结果进 tool_results

retrieve 壳真跑子图（build_subagent().ainvoke）的端到端留 Stage 4 probe / dev 实跑——这里只测
纯逻辑层（finalize 不调 LLM；artifact 冒泡用合成 on_tool_end 事件）。
"""

import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.rag.graph import (
    _annotate_tool_results,
    _consume_events,
    _extract_findings,
    _iter_tool_items,
    _select_items,
    _subagent_finalize,
    after_guard,
)


# ---- after_guard：terminator + budget_route 路由 --------------------------


def _state_with_last(last_msg, remaining=5):
    return {
        "messages": [last_msg],
        "remaining_calls": remaining,
        "thinking_retry_count": 0,
        "is_thinking_correction": False,
    }


def test_after_guard_return_findings_routes_finalize():
    """子 agent 调 return_findings → 路由 finalize（先于预算检查，不走 tool_node）。"""
    last = AIMessage(
        content="",
        tool_calls=[{
            "name": "return_findings",
            "args": {"selection": [], "summary": "ok"},
            "id": "c1",
            "type": "tool_call",
        }],
    )
    assert after_guard(_state_with_last(last), terminator="return_findings") == "finalize"


def test_after_guard_normal_tool_routes_tool_node():
    """子 agent 调普通检索工具（非 terminator）→ 路由 tool_node。"""
    last = AIMessage(
        content="",
        tool_calls=[{
            "name": "s2_search_tool", "args": {"query": "x"}, "id": "c1",
            "type": "tool_call",
        }],
    )
    assert after_guard(_state_with_last(last), terminator="return_findings") == "tool_node"


def test_after_guard_budget_route_subagent_vs_main():
    """预算耗尽：子 agent → finalize（拼工具结果），主 agent → final_answer（LLM 兜底）。"""
    last = AIMessage(
        content="",
        tool_calls=[{
            "name": "s2_search_tool", "args": {}, "id": "c1", "type": "tool_call",
        }],
    )
    # 子 agent（budget_route=finalize）
    assert after_guard(
        _state_with_last(last, remaining=-1), terminator="return_findings",
        budget_route="finalize",
    ) == "finalize"
    # 主 agent（budget_route=final_answer，terminator=None）
    assert after_guard(
        _state_with_last(last, remaining=-1), terminator=None,
        budget_route="final_answer",
    ) == "final_answer"


def test_after_guard_main_agent_unaffected_without_terminator():
    """主 agent（terminator=None）永不命中 finalize 分支，行为字节级不变。"""
    last = AIMessage(
        content="",
        tool_calls=[{
            "name": "retrieve", "args": {"query": "x"}, "id": "c1",
            "type": "tool_call",
        }],
    )
    assert after_guard(_state_with_last(last), terminator=None) == "tool_node"
    assert after_guard(_state_with_last(AIMessage(content="纯文本回答")), terminator=None) == "__end__"


# ---- _subagent_finalize：三路径 -----------------------------------------


def _tc(name, args, tid):
    return {"name": name, "args": args, "id": tid, "type": "tool_call"}


def _ai_tool(name, args, tid):
    return AIMessage(content="", tool_calls=[_tc(name, args, tid)])


def _tm(content, name, tid):
    return ToolMessage(content=content, tool_call_id=tid, name=name)


def test_finalize_picks_selected_indices():
    """正常收敛：按 return_findings 的 result_index 抠选中工具结果 + summary。"""
    messages = [
        HumanMessage(content="Q"),
        _ai_tool("s2_search_tool", {"query": "x"}, "c1"),
        _tm('{"papers":[{"s2_paper_id":"A","title":"PaperA"}]}', "s2_search_tool", "c1"),
        _ai_tool("rag_tool", {"query": "y"}, "c2"),
        _tm("[rag:d1 | 论文A, Page 3]\n本地 chunk 正文", "rag_tool", "c2"),
        AIMessage(content="", tool_calls=[_tc("return_findings", {
            "selection": [
                {"result_index": 1, "reason": "外部主证"},
                {"result_index": 2, "reason": "本地佐证"},
            ],
            "summary": "覆盖了原理与本地实验",
        }, "c3")]),
    ]
    result = asyncio.run(_subagent_finalize({"messages": messages}))
    findings = result["findings"]

    assert "覆盖了原理与本地实验" in findings  # summary 进 findings
    assert "检索结果 #1（s2_search_tool）" in findings
    assert "检索结果 #2（rag_tool）" in findings
    assert "PaperA" in findings  # s2 结果原文（不转写，原样吐）
    assert "本地 chunk 正文" in findings  # rag 结果原文


def test_finalize_skips_unselected_index():
    """只选 #0 时 #1 不进 findings（子 agent 的 item 级——这里是工具调用级——筛选）。"""
    messages = [
        HumanMessage(content="Q"),
        _ai_tool("s2_search_tool", {"query": "x"}, "c1"),
        _tm('{"papers":[{"s2_paper_id":"A"}]}', "s2_search_tool", "c1"),
        _ai_tool("arxiv_tool", {"query": "y"}, "c2"),
        _tm('{"papers":[{"arxiv_id":"B"}]}', "arxiv_tool", "c2"),
        AIMessage(content="", tool_calls=[_tc("return_findings", {
            "selection": [{"result_index": 1, "reason": "只要这个"}],
            "summary": "s",
        }, "c3")]),
    ]
    findings = asyncio.run(_subagent_finalize({"messages": messages}))["findings"]
    assert "检索结果 #1" in findings
    assert "arxiv_id" not in findings  # #2 未选，不进
    assert "检索结果 #2" not in findings


def test_finalize_budget_exhausted_picks_all():
    """预算耗尽（无 return_findings args）→ 兜底全拼已有工具结果。"""
    messages = [
        HumanMessage(content="Q"),
        _ai_tool("s2_search_tool", {"query": "x"}, "c1"),
        _tm("s2 结果原文", "s2_search_tool", "c1"),
        # 末条 AIMessage 想调工具但预算耗尽（after_guard 已路由 finalize，无 return_findings）
        _ai_tool("rag_tool", {"query": "y"}, "c2"),
    ]
    findings = asyncio.run(_subagent_finalize({"messages": messages}))["findings"]
    assert "预算耗尽兜底" in findings
    assert "s2 结果原文" in findings  # 已有结果全拼


def test_finalize_out_of_range_indices_fallback_all():
    """selection 索引全部越界 → 兜底全拼，不丢检索成果。"""
    messages = [
        HumanMessage(content="Q"),
        _ai_tool("s2_search_tool", {"query": "x"}, "c1"),
        _tm("s2 结果", "s2_search_tool", "c1"),
        AIMessage(content="", tool_calls=[_tc("return_findings", {
            "selection": [{"result_index": 99, "reason": "数错了"}],
            "summary": "s",
        }, "c2")]),
    ]
    findings = asyncio.run(_subagent_finalize({"messages": messages}))["findings"]
    # 越界兜底：仍含已有结果
    assert "s2 结果" in findings


def test_finalize_empty_tools():
    """子 agent 一个工具没调就 return_findings → findings 占位，不崩。"""
    messages = [
        HumanMessage(content="Q"),
        AIMessage(content="", tool_calls=[_tc("return_findings", {
            "selection": [], "summary": "无需检索",
        }, "c1")]),
    ]
    findings = asyncio.run(_subagent_finalize({"messages": messages}))["findings"]
    assert "无需检索" in findings
    assert "检索结果" not in findings  # 无工具结果段


def test_finalize_truncates_overlong():
    """findings 超 MAX_FINDINGS_LEN 截断兜底。"""
    import src.rag.graph as g
    long_blob = "X" * (g.MAX_FINDINGS_LEN + 500)
    messages = [
        HumanMessage(content="Q"),
        _ai_tool("jina_tool", {"url": "u"}, "c1"),
        _tm(long_blob, "jina_tool", "c1"),
        AIMessage(content="", tool_calls=[_tc("return_findings", {
            "selection": [{"result_index": 1, "reason": "r"}], "summary": "s",
        }, "c2")]),
    ]
    findings = asyncio.run(_subagent_finalize({"messages": messages}))["findings"]
    assert len(findings) <= g.MAX_FINDINGS_LEN + 200  # 截断 + 截断标记
    assert "已截断" in findings


# ---- _extract_findings --------------------------------------------------


def test_extract_findings_prefers_state_field():
    """优先 finalize 写的 state['findings']。"""
    result = {"findings": "## 检索摘要\n...", "messages": []}
    assert _extract_findings(result) == "## 检索摘要\n..."


def test_extract_findings_fallback_to_tool_results():
    """finalize 没写 findings（异常路径）→ 兜底拼工具结果。"""
    result = {
        "findings": "",
        "messages": [_tm("s2 结果", "s2_search_tool", "c1")],
    }
    findings = _extract_findings(result)
    assert "s2 结果" in findings
    assert "兜底" in findings


def test_extract_findings_empty():
    """无 findings 无工具结果 → 占位。"""
    assert _extract_findings({"findings": "", "messages": []}) == "(检索未返回结果)"
    assert _extract_findings({}) == "(检索未返回结果)"


# ---- _consume_events：retrieve 壳 artifact 冒泡 --------------------------


class _ToolOut:
    """on_tool_end 的 data.output：带 content + artifact + status。"""

    def __init__(self, content="", artifact=None, status="success"):
        self.content = content
        self.artifact = artifact
        self.status = status


class _Msg:
    def __init__(self, content):
        self.content = content


class _FakeRequest:
    async def is_disconnected(self):
        return False


class _FakeAgent:
    def __init__(self, events):
        self._events = events

    async def astream_events(self, state, version=None):
        for ev in self._events:
            yield ev


def _run_consume(events):
    from src.rag.graph import _consume_events
    result = {}
    frames = []

    async def _drive():
        async for f in _consume_events(_FakeAgent(events), {}, _FakeRequest(), result):
            frames.append(f)

    asyncio.run(_drive())
    return result


def test_consume_events_retrieve_artifact_bubbles_to_tool_results():
    """retrieve 壳的 artifact（子 agent 工具结果）冒泡进 result['tool_results']；
    retrieve 自身的 content=findings 不进（artifact 分支优先，不走 elif）。"""
    events = [
        {"event": "on_chain_start", "run_id": "root", "metadata": {}},
        {"event": "on_tool_start", "name": "retrieve", "run_id": "t1"},
        {"event": "on_tool_end", "name": "retrieve", "run_id": "t1", "data": {
            "output": _ToolOut(
                content="## 检索结果\nFINDINGS 正文",  # 给主 agent LLM
                artifact=[
                    ("s2_search_tool", '{"papers":[{"s2_paper_id":"A","title":"P"}]}'),
                    ("rag_tool", "[rag:d1 | T, Page 1]\nchunk"),
                ],
            ),
        }},
        {"event": "on_chain_end", "run_id": "root", "metadata": {}, "data": {
            "output": {"messages": [_Msg("最终答案")]},
        }},
    ]
    result = _run_consume(events)

    assert result["tool_results"] == [
        ("s2_search_tool", '{"papers":[{"s2_paper_id":"A","title":"P"}]}'),
        ("rag_tool", "[rag:d1 | T, Page 1]\nchunk"),
    ]
    # findings 正文不进候选收集（artifact 分支已消费，不走 elif）
    contents = [c for _, c in result["tool_results"]]
    assert not any("FINDINGS 正文" in c for c in contents)


def test_consume_events_plain_tool_uses_content_when_no_artifact():
    """常规工具（artifact=None）走 elif：content 进 tool_results（行为与重构前一致）。"""
    events = [
        {"event": "on_chain_start", "run_id": "root", "metadata": {}},
        {"event": "on_tool_end", "name": "rag_tool", "run_id": "t1", "data": {
            "output": _ToolOut(content="[rag:d2 | T2]\n正文", artifact=None),
        }},
        {"event": "on_chain_end", "run_id": "root", "metadata": {}, "data": {
            "output": {"messages": [_Msg("答案")]},
        }},
    ]
    result = _run_consume(events)
    assert result["tool_results"] == [("rag_tool", "[rag:d2 | T2]\n正文")]


# ---- item 级精度（Stage 4.5）：_iter_tool_items / _select_result_body / finalize 切片 / 标注计数


# _iter_tool_items：按工具返回顺序切 item
def test_iter_items_s2_papers():
    """s2 JSON papers 数组 → 每篇重包成 {"papers":[p]} 一条，保序。"""
    content = json.dumps({"success": True, "papers": [
        {"s2_paper_id": "A", "title": "PA"},
        {"s2_paper_id": "B", "title": "PB"},
        {"s2_paper_id": "C", "title": "PC"},
    ]})
    items = _iter_tool_items("s2_search_tool", content)
    assert len(items) == 3
    assert json.loads(items[0]) == {"papers": [{"s2_paper_id": "A", "title": "PA"}]}
    assert json.loads(items[2]) == {"papers": [{"s2_paper_id": "C", "title": "PC"}]}


def test_iter_items_rag_chunks():
    """rag 多 chunk（\\n\\n---\\n\\n 拼）→ 切成 chunk 列表，剔空。"""
    content = "[rag:d1 | T1, Page 1]\nbody1\n\n---\n\n[rag:d2 | T2, Page 3]\nbody2\n\n---\n\n[rag:d1 | T1, Page 5]\nbody3"
    items = _iter_tool_items("rag_tool", content)
    assert len(items) == 3
    assert "body1" in items[0]
    assert "body2" in items[1]
    assert "body3" in items[2]


def test_iter_items_single_item_tools():
    """lookup/jina/未知工具 → [content] 单条整体（不可切）。"""
    assert _iter_tool_items("jina_tool", "整篇全文 blob") == ["整篇全文 blob"]
    assert _iter_tool_items("lookup_local_paper_id", '{"doc_id":"x"}') == ['{"doc_id":"x"}']
    assert _iter_tool_items("unknown_tool", "xxx") == ["xxx"]


def test_iter_items_parse_failure_falls_back_whole():
    """JSON 解析失败 → [content] 兜底（不抛进 agent）。"""
    assert _iter_tool_items("s2_search_tool", "not json") == ["not json"]


def test_iter_items_empty():
    assert _iter_tool_items("s2_search_tool", "") == []


# _select_items：按 item_index 抠 per-item 列表（findings 文本 join + 候选收集逐条收共用）
def test_select_items_none_returns_whole():
    """wanted 含 None（item_index 缺省）→ [整条 content]（向后兼容 call-level 路径）。"""
    tm = _tm('{"papers":[{"s2_paper_id":"A"},{"s2_paper_id":"B"}]}', "s2_search_tool", "c1")
    assert _select_items(tm, [None]) == [tm.content]


def test_select_items_picks_single():
    """wanted=[2] → [第 2 篇重包]，长度 1（per-item，不拼接）。"""
    tm = _tm(json.dumps({"papers": [
        {"s2_paper_id": "A"}, {"s2_paper_id": "B"}, {"s2_paper_id": "C"},
    ]}), "s2_search_tool", "c1")
    items = _select_items(tm, [2])
    assert len(items) == 1
    assert json.loads(items[0]) == {"papers": [{"s2_paper_id": "B"}]}


def test_select_items_picks_multiple():
    """wanted=[1,3] → [第 1 篇, 第 3 篇]（per-item 列表，候选收集逐条收，每条合法 JSON）。"""
    tm = _tm(json.dumps({"papers": [
        {"s2_paper_id": "A"}, {"s2_paper_id": "B"}, {"s2_paper_id": "C"},
    ]}), "s2_search_tool", "c1")
    items = _select_items(tm, [1, 3])
    assert len(items) == 2
    assert json.loads(items[0]) == {"papers": [{"s2_paper_id": "A"}]}
    assert json.loads(items[1]) == {"papers": [{"s2_paper_id": "C"}]}


def test_select_items_out_of_range_whole():
    """wanted 全越界 → [整条 content] 兜底（不丢检索成果）。"""
    tm = _tm('{"papers":[{"s2_paper_id":"A"}]}', "s2_search_tool", "c1")
    assert _select_items(tm, [99]) == [tm.content]


def test_select_items_single_item_tool_whole():
    """单条工具（jina）→ [整条 content]（item_index 无意义）。"""
    tm = _tm("整篇全文", "jina_tool", "c1")
    assert _select_items(tm, [1]) == ["整篇全文"]


# _subagent_finalize：item_index 端到端
def test_finalize_picks_specific_item():
    """selection 带 item_index=2 → findings 只含该结果第 2 篇，不含第 1/3 篇。"""
    messages = [
        HumanMessage(content="Q"),
        _ai_tool("s2_search_tool", {"query": "x"}, "c1"),
        _tm(json.dumps({"papers": [
            {"s2_paper_id": "A", "title": "PA"},
            {"s2_paper_id": "B", "title": "PB"},
            {"s2_paper_id": "C", "title": "PC"},
        ]}), "s2_search_tool", "c1"),
        AIMessage(content="", tool_calls=[_tc("return_findings", {
            "selection": [{"result_index": 1, "item_index": 2, "reason": "只要 PB"}],
            "summary": "s",
        }, "c2")]),
    ]
    result = asyncio.run(_subagent_finalize({"messages": messages}))
    findings = result["findings"]
    assert "检索结果 #1（s2_search_tool）" in findings
    assert "PB" in findings
    assert "PA" not in findings  # 第 1 篇未选，不进
    assert "PC" not in findings  # 第 3 篇未选，不进
    # selected_tool_results：仅选中 item（per-item，候选收集只收这一条）
    sel = result["selected_tool_results"]
    assert len(sel) == 1
    assert sel[0][0] == "s2_search_tool"
    assert json.loads(sel[0][1]) == {"papers": [{"s2_paper_id": "B", "title": "PB"}]}


def test_finalize_item_index_absent_backward_compat():
    """selection 无 item_index（缺省）→ findings + selected 都是整条（与 Stage 4 行为一致，零回归）。"""
    messages = [
        HumanMessage(content="Q"),
        _ai_tool("s2_search_tool", {"query": "x"}, "c1"),
        _tm(json.dumps({"papers": [
            {"s2_paper_id": "A", "title": "PA"},
            {"s2_paper_id": "B", "title": "PB"},
        ]}), "s2_search_tool", "c1"),
        AIMessage(content="", tool_calls=[_tc("return_findings", {
            "selection": [{"result_index": 1, "reason": "整条都要"}],
            "summary": "s",
        }, "c2")]),
    ]
    result = asyncio.run(_subagent_finalize({"messages": messages}))
    findings = result["findings"]
    assert "PA" in findings  # 整条=两篇都在
    assert "PB" in findings
    # selected_tool_results：整条（缺省 item_index，向后兼容）
    sel = result["selected_tool_results"]
    assert len(sel) == 1
    assert "PA" in sel[0][1] and "PB" in sel[0][1]


def test_finalize_selected_excludes_unselected_results():
    """selected_tool_results 只含被选中的结果——未选中的结果不进候选收集（存储精度核心）。"""
    messages = [
        HumanMessage(content="Q"),
        _ai_tool("s2_search_tool", {"query": "x"}, "c1"),
        _tm(json.dumps({"papers": [{"s2_paper_id": "A", "title": "PA"}]}), "s2_search_tool", "c1"),
        _ai_tool("arxiv_tool", {"query": "y"}, "c2"),
        _tm(json.dumps({"papers": [{"arxiv_id": "B", "title": "TB"}]}), "arxiv_tool", "c2"),
        AIMessage(content="", tool_calls=[_tc("return_findings", {
            "selection": [{"result_index": 1, "reason": "只要 s2 的"}],
            "summary": "s",
        }, "c3")]),
    ]
    sel = asyncio.run(_subagent_finalize({"messages": messages}))["selected_tool_results"]
    # 只收 result #1（s2），不收 result #2（arxiv 未选 → 不进候选）
    assert len(sel) == 1
    assert sel[0][0] == "s2_search_tool"
    assert "PA" in sel[0][1]
    assert not any("TB" in c for _, c in sel)


def test_finalize_budget_exhausted_selected_all_raw():
    """预算耗尽兜底（无 return_findings）→ selected_tool_results = 全部 raw（未经甄选，全收）。"""
    messages = [
        HumanMessage(content="Q"),
        _ai_tool("s2_search_tool", {"query": "x"}, "c1"),
        _tm("s2 结果原文", "s2_search_tool", "c1"),
        _ai_tool("rag_tool", {"query": "y"}, "c2"),  # 预算耗尽，未执行
    ]
    sel = asyncio.run(_subagent_finalize({"messages": messages}))["selected_tool_results"]
    assert sel == [("s2_search_tool", "s2 结果原文")]


# _annotate_tool_results：item 计数标注
def test_annotate_includes_item_count_for_multi_item():
    """多 item 结果（s2 3 篇）→ 标注带「共 3 条候选，按返回顺序 #1..#3」。"""
    state = {"messages": []}
    tool_result = {"messages": [_tm(json.dumps({"papers": [
        {"s2_paper_id": "A"}, {"s2_paper_id": "B"}, {"s2_paper_id": "C"},
    ]}), "s2_search_tool", "c1")]}
    out = _annotate_tool_results(state, tool_result)
    ann = out["messages"][-1]
    assert "共 3 条候选" in ann.content
    assert "#1..#3" in ann.content


def test_annotate_no_item_note_for_single_item():
    """单条工具（jina）→ 标注无 item 计数（保持原 minimal 标注）。"""
    state = {"messages": []}
    tool_result = {"messages": [_tm("整篇全文", "jina_tool", "c1")]}
    out = _annotate_tool_results(state, tool_result)
    ann = out["messages"][-1]
    assert ann.content == "第 1 次工具调用结果 · 工具 jina_tool"
