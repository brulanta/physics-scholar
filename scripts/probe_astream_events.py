"""probe：钉死 astream_events(v2) 里区分「主 agent 事件 vs 嵌套子 agent 事件」的字段。

为 _consume_events 统一 depth 过滤找口径。跑 main agent（bind_tools=[retrieve]），
对每个 on_tool_start/on_tool_end/on_chat_model_start 事件 dump：
  event | name | run_id | parent_ids | tags | langgraph_node | checkpoint_ns
首个 retrieve on_tool_end 后 break（省时省额度）。
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from src.rag.graph import build_agent  # noqa: E402
from src.rag.prompts.builder import build_prompt  # noqa: E402


def _short(rid: str) -> str:
    return (rid or "")[:8]


async def main() -> int:
    Q = "光子微波信号产生有什么最新进展？找两篇近年的。"  # 短题，逼 retrieve + 至少一次子 agent 工具
    sys_prompt = build_prompt(mode="normal", history="", citation_plugin="", debug=False)
    agent = build_agent("default")
    initial = {
        "messages": [SystemMessage(content=sys_prompt), HumanMessage(content=Q)],
        "conv_id": "probe",
        "user_id": "default",
        "translation": False,
        "remaining_calls": 1,
        "next_prefill": None,
    }

    print("event | name | run_id | parent_ids | tags | lg_node | ckpt_ns")
    print("-" * 100)
    seen_retrieve_end = False
    n = 0
    async for ev in agent.astream_events(initial, version="v2"):
        et = ev.get("event", "")
        if et not in ("on_tool_start", "on_tool_end", "on_chat_model_start"):
            continue
        name = ev.get("name", "")
        run_id = ev.get("run_id", "")
        pids = ev.get("parent_ids") or []
        tags = ev.get("tags") or []
        md = ev.get("metadata") or {}
        lg_node = md.get("langgraph_node")
        ckpt = md.get("langgraph_checkpoint_ns")
        n += 1
        print(
            f"{et:20} | {name:22} | {_short(run_id)} | "
            f"[{','.join(_short(p) for p in pids)}] | {tags} | {lg_node} | {ckpt}"
        )
        # 收到首个 retrieve on_tool_end 即够（嵌套结构已现）
        if et == "on_tool_end" and name == "retrieve":
            seen_retrieve_end = True
            # 多扫一两条确认收尾，然后停
        if seen_retrieve_end and et in ("on_chat_model_start",):
            print(f"... (停于首个 retrieve 后的 chat_model_start，共 {n} 条目标事件)")
            break
    print(f"\n共 {n} 条目标事件")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
