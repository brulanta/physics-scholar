"""真流式实施 — 步骤1 验证脚本（临时，可删）。

验证两件事：
  A. astream_events(version="v2") 能拿到 token 级 on_chat_model_stream，
     且事件带 ev["metadata"]["langgraph_node"] 区分 call_llm / final_answer。
  B. 节点异步化后，旧的同步 agent.invoke() 是否还能跑（计划里的"兜底/测试路径"）。

用法：python -m scripts.verify_astream
"""

import asyncio
from collections import Counter

from langchain_core.messages import SystemMessage, HumanMessage

from src.rag.graph import build_agent

# 极简 system，只为验证流机制，不走完整 thinking protocol
SYSTEM = "你是一个助手。用一句话简短回答，不要调用任何工具。"
USER = "用一句话介绍微波光子学。"


def _initial_state():
    return {
        "messages": [
            SystemMessage(content=SYSTEM),
            HumanMessage(content=USER),
        ],
        "conv_id": "verify",
        "user_id": "default",
        "translation": False,
        "remaining_calls": 6,
        "next_prefill": None,
    }


async def verify_astream():
    print("=" * 60)
    print("A. astream_events(version='v2')")
    print("=" * 60)
    agent = build_agent("default")

    token_events = 0
    nodes_seen = Counter()
    sample_tokens = []
    other_event_types = Counter()

    async for ev in agent.astream_events(_initial_state(), version="v2"):
        etype = ev["event"]
        if etype == "on_chat_model_stream":
            token_events += 1
            node = (ev.get("metadata") or {}).get("langgraph_node")
            nodes_seen[node] += 1
            chunk = ev["data"].get("chunk")
            piece = getattr(chunk, "content", "") if chunk else ""
            if piece and len(sample_tokens) < 12:
                sample_tokens.append(piece)
        else:
            other_event_types[etype] += 1

    print(f"on_chat_model_stream 事件数: {token_events}")
    print(f"langgraph_node 分布: {dict(nodes_seen)}")
    print(f"前若干 token 片段: {sample_tokens!r}")
    print(f"其它事件类型计数: {dict(other_event_types)}")

    ok_tokens = token_events > 1
    ok_node = any(n for n in nodes_seen)  # 至少有一个非 None 的 node 标签
    print()
    print(f"[A] 逐 token 流: {'PASS' if ok_tokens else 'FAIL'}")
    print(f"[A] langgraph_node 标来源: {'PASS' if ok_node else 'FAIL'}")
    return ok_tokens and ok_node


def verify_sync_fallback():
    # 验证选项1的兜底模式：异步节点下 raw agent.invoke() 已不可用，
    # chat()/regenerate() 改用 asyncio.run(agent.ainvoke(...)) 保持同步签名。
    # 这里直接验证该模式（不走 chat() 以免写库副作用）。
    print()
    print("=" * 60)
    print("B. 同步兜底：asyncio.run(agent.ainvoke())（异步节点下）")
    print("=" * 60)
    agent = build_agent("default")
    try:
        result = asyncio.run(agent.ainvoke(_initial_state()))
        last = result["messages"][-1]
        print("[B] 同步兜底 PASS, 末条消息 content 长度 =", len(last.content or ""))
        return True
    except Exception as e:
        print(f"[B] 同步兜底 FAIL: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    # A 在自己的事件循环里跑；B 必须在普通同步上下文里跑（asyncio.run 不能嵌套），
    # 这正是 chat()/regenerate() 真实被调用的场景。
    a = asyncio.run(verify_astream())
    b = verify_sync_fallback()
    print()
    print("=" * 60)
    print(f"结论: A(astream)={'PASS' if a else 'FAIL'}  "
          f"B(sync fallback)={'PASS' if b else 'FAIL'}")
    print("=" * 60)
