"""单测 graph._consume_events 的三态状态机事件序列（离线，无网络）。

构造四种 LLM 输出场景，断言产出的 SSE 事件类型序列与权威 final_content：
  1. DONE     —— call_llm 内不调工具，直接进入正文
  2. PENDING  —— call_llm 调工具（PENDING 静默）→ 工具 → 再 call_llm（DONE）出正文
  3. 无标记   —— 流式期间未检出 </thinking>，由 on_chat_model_end 用权威 content 兜底
  4. final_answer 节点 —— cur_node==final_answer 必进正文
"""

import asyncio
import json

from src.rag.graph import _consume_events

# ---- 伪事件对象 ----------------------------------------------------------


class FakeChunk:
    def __init__(self, content):
        self.content = content


class FakeModelOutput:
    """on_chat_model_end 的 data.output：带 content + tool_calls。"""

    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeToolOutput:
    """on_tool_end 的 data.output：带 status。"""

    def __init__(self, status="success"):
        self.status = status


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeRequest:
    async def is_disconnected(self):
        return False


class FakeAgent:
    def __init__(self, events):
        self._events = events

    async def astream_events(self, state, version=None):
        for ev in self._events:
            yield ev


# ---- 事件构造助手 --------------------------------------------------------

ROOT = "root-run"


def ev_chain_start_root():
    return {"event": "on_chain_start", "run_id": ROOT, "metadata": {}}


def ev_model_start(node):
    return {"event": "on_chat_model_start", "metadata": {"langgraph_node": node}}


def ev_stream(text):
    return {"event": "on_chat_model_stream", "data": {"chunk": FakeChunk(text)}}


def ev_model_end(content="", tool_calls=None):
    return {
        "event": "on_chat_model_end",
        "data": {"output": FakeModelOutput(content, tool_calls)},
    }


def ev_tool_start(name, run_id):
    return {"event": "on_tool_start", "name": name, "run_id": run_id}


def ev_tool_end(name, run_id, status="success"):
    return {
        "event": "on_tool_end",
        "name": name,
        "run_id": run_id,
        "data": {"output": FakeToolOutput(status)},
    }


def ev_chain_end_root(final_text):
    return {
        "event": "on_chain_end",
        "run_id": ROOT,
        "metadata": {},
        "data": {"output": {"messages": [FakeMessage(final_text)]}},
    }


# ---- 驱动器 --------------------------------------------------------------


def run_consume(events):
    # _consume_events 签名：(agent, initial_state, request, result)
    agent = FakeAgent(events)
    result = {}
    frames = []

    async def _drive():
        async for f in _consume_events(agent, {}, FakeRequest(), result):
            frames.append(f)

    asyncio.run(_drive())

    parsed = [json.loads(f[len("data: ") :]) for f in frames]
    types = [p["type"] for p in parsed]
    return types, parsed, result


# ---- 场景测试 ------------------------------------------------------------


def test_done_direct_answer():
    """场景1：DONE —— call_llm 内不调工具，</thinking> 后直接出正文。"""
    events = [
        ev_chain_start_root(),
        ev_model_start("call_llm"),
        ev_stream("<thinking>\n分析需求\n[TOOL_LOOP: DONE]\n继续推理\n</thinking>"),
        ev_stream("最终答案第一段。"),
        ev_stream("最终答案第二段。"),
        ev_model_end(
            content="<thinking>...</thinking>最终答案第一段。最终答案第二段。"
        ),
        ev_chain_end_root("最终答案第一段。最终答案第二段。"),
    ]
    types, parsed, result = run_consume(events)

    assert types == [
        "thinking_start",
        "thinking_end",
        "answer_start",
        "answer_delta",  # </thinking> 之后的 tail（可能为空被跳过则无）—见下
        "answer_delta",
        "answer_end",
    ] or types == [
        "thinking_start",
        "thinking_end",
        "answer_start",
        "answer_delta",
        "answer_delta",
        "answer_delta",
        "answer_end",
    ]
    # 正文增量拼接应包含两段答案
    answer_text = "".join(p["text"] for p in parsed if p["type"] == "answer_delta")
    assert "最终答案第一段。" in answer_text
    assert "最终答案第二段。" in answer_text
    assert result["final_content"] == "最终答案第一段。最终答案第二段。"


def test_pending_then_tool_then_answer():
    """场景2：PENDING 静默 → 工具 → 再 call_llm（DONE）出正文。"""
    events = [
        ev_chain_start_root(),
        ev_model_start("call_llm"),
        ev_stream("<thinking>\n需要检索\n[TOOL_LOOP: PENDING]\n</thinking>"),
        ev_model_end(
            content="<thinking>...</thinking>",
            tool_calls=[{"name": "rag_tool", "id": "c1"}],
        ),
        ev_tool_start("rag_tool", "t1"),
        ev_tool_end("rag_tool", "t1", status="success"),
        ev_model_start("call_llm"),  # 工具返回后重新进入
        ev_stream("<thinking>\n整合结果\n[TOOL_LOOP: DONE]\n</thinking>"),
        ev_stream("基于检索的最终答案。"),
        ev_model_end(content="<thinking>...</thinking>基于检索的最终答案。"),
        ev_chain_end_root("基于检索的最终答案。"),
    ]
    types, parsed, result = run_consume(events)

    assert types == [
        "thinking_start",
        "thinking_end",
        "tool_start",
        "tool_end",
        "thinking_start",  # 工具打断后重置，重新发
        "thinking_end",
        "answer_start",
        "answer_delta",
        "answer_end",
    ]
    # tool_start/tool_end 的 payload
    ts = next(p for p in parsed if p["type"] == "tool_start")
    te = next(p for p in parsed if p["type"] == "tool_end")
    assert ts["name"] == "rag_tool" and ts["tool_id"] == "t1"
    assert te["ok"] is True
    answer_text = "".join(p["text"] for p in parsed if p["type"] == "answer_delta")
    assert answer_text == "基于检索的最终答案。"
    assert result["final_content"] == "基于检索的最终答案。"


def test_no_marker_fallback_on_model_end():
    """场景3：流式期间无 </thinking>，由 on_chat_model_end 用权威 content 兜底。"""
    events = [
        ev_chain_start_root(),
        ev_model_start("call_llm"),
        ev_stream("<thinking>\n直接思考但分片未闭合\n"),  # 无 </thinking>，静默累积
        ev_model_end(content="<thinking>\n直接思考\n</thinking>\n兜底答案正文。"),
        ev_chain_end_root("兜底答案正文。"),
    ]
    types, parsed, result = run_consume(events)

    assert types == [
        "thinking_start",
        "thinking_end",
        "answer_start",
        "answer_delta",
        "answer_end",
    ]
    answer_text = "".join(p["text"] for p in parsed if p["type"] == "answer_delta")
    assert "兜底答案正文。" in answer_text
    assert result["final_content"] == "兜底答案正文。"


def test_final_answer_node():
    """场景4：final_answer 节点 —— 无论是否有 marker，</thinking> 后必进正文。"""
    events = [
        ev_chain_start_root(),
        ev_model_start("final_answer"),
        ev_stream("<thinking>\n额度耗尽，纯逻辑整合\n</thinking>"),
        ev_stream("兜底节点最终答案。"),
        ev_model_end(content="<thinking>...</thinking>兜底节点最终答案。"),
        ev_chain_end_root("兜底节点最终答案。"),
    ]
    types, parsed, result = run_consume(events)

    assert types == [
        "thinking_start",
        "thinking_end",
        "answer_start",
        "answer_delta",
        "answer_end",
    ]
    answer_text = "".join(p["text"] for p in parsed if p["type"] == "answer_delta")
    assert "兜底节点最终答案。" in answer_text
    assert result["final_content"] == "兜底节点最终答案。"


def test_tool_error_sets_ok_false():
    """补充：on_tool_end status=error 时 tool_end.ok 应为 False。"""
    events = [
        ev_chain_start_root(),
        ev_model_start("call_llm"),
        ev_stream("<thinking>\n检索\n[TOOL_LOOP: PENDING]\n</thinking>"),
        ev_model_end(content="x", tool_calls=[{"name": "s2_search_tool", "id": "c1"}]),
        ev_tool_start("s2_search_tool", "t9"),
        ev_tool_end("s2_search_tool", "t9", status="error"),
        ev_chain_end_root(""),
    ]
    _, parsed, _ = run_consume(events)
    te = next(p for p in parsed if p["type"] == "tool_end")
    assert te["ok"] is False
