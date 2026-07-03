#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Harness 行为量具（阶段② behavior runner）。

喂 eval_framework/test_cases.json 里的问题 → 端到端跑 agent → 只输出**行为指标**
（不含 LLM 评委、不含内容打分）。是 harness 松绑实验（④ FLASH vs STRONG）的前置量具，
翻一个开关重跑即可比行为差异。

设计要点：
- **绕开 chat()/regenerate()**：那两条路径会写 SQLite/memory。本量具内联复刻 _prepare 的
  initial_state（空 history），直跑 agent.ainvoke，零持久化。
- **指标从最终 transcript 推导**：guard 注入的假 ToolMessage（哨兵串「未检测到必要的
  <thinking>」）与预算兜底哨兵（「已达最大调用次数上限」）都留在 state，无需脆弱的
  流式增量重建。单次 ainvoke 即可。
- **复用不 fork**：build_agent/build_prompt/_detect_marker/process_llm_output 全部从
  src.rag.graph import。

风险提示：
- **真成本**：每题 = 一次完整 agent 跑（LLM + s2/arxiv/jina 真实网络）。用 --only 限子集。
- **语料依赖**：rag_tool 读 data/chroma_db（gitignored）。无语料机器上 RAG 空召回，*内容
  相关*行为漂移；但 guard/marker/correction/budget 指标基本语料无关，正是④要比的。
- **MCP**：若设了 PS_USE_MCP=true，需 MCP server 在跑（否则 build_agent 取不到工具）。

用法：
    python scripts/harness_probe.py                      # 全部 20 题
    python scripts/harness_probe.py --only Q01 Q05       # 指定题
    python scripts/harness_probe.py --label STRONG --mode discuss
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# 让脚本从仓库根可直接 `python scripts/harness_probe.py` 运行（src 可 import）
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from src.rag.graph import (  # noqa: E402
    build_agent,
    _detect_marker,
)
from src.rag.prompts import build_prompt, CITATION_DEFAULT  # noqa: E402
from src.core.trim_thinking import process_llm_output  # noqa: E402

TEST_CASES = REPO_ROOT / "eval_framework" / "test_cases.json"
OUT_DIR = REPO_ROOT / "eval_framework" / "results" / "behavior"

# thinking_guard 注入的「工具调用已被取消」假 ToolMessage 的稳定子串（graph.py:184）
GUARD_SENTINEL = "未检测到必要的 <thinking>"
# final_answer 达预算上限时注入的取消哨兵（graph.py:249）
BUDGET_SENTINEL = "已达最大调用次数上限"


def load_questions(only: list[str] | None) -> list[dict]:
    """读题库，只取 id+question。文件是合法 UTF-8。"""
    with open(TEST_CASES, encoding="utf-8") as f:
        cases = json.load(f)
    items = [{"id": c["id"], "question": c["question"]} for c in cases]
    if only:
        want = set(only)
        items = [it for it in items if it["id"] in want]
        missing = want - {it["id"] for it in items}
        if missing:
            print(f"⚠️ 题库中找不到：{sorted(missing)}", file=sys.stderr)
    return items


def build_initial_state(question: str, user_id: str, mode: str) -> dict:
    """内联复刻 _prepare 的 initial_state（空 history）。"""
    system_prompt = build_prompt(
        mode="normal" if mode == "normal" else "discuss",
        history="",
        citation_plugin=CITATION_DEFAULT,
        debug=False,
    )
    return {
        "messages": [
            SystemMessage(content=system_prompt),
            HumanMessage(content=question),
        ],
        "conv_id": "harness_probe",
        "user_id": user_id,
        "translation": False,
        "remaining_calls": 6,
        "next_prefill": None,
    }


def collect_metrics(result: dict) -> dict:
    """从最终 state 的 transcript 推导行为指标。"""
    messages = result.get("messages", [])
    remaining = result.get("remaining_calls", None)

    guard_hits = 0
    tool_rounds = 0
    budget_forced = False
    tool_turns = 0          # 带 tool_calls 的 AIMessage 轮数
    marker_emitted = 0      # 其中吐出了 [TOOL_LOOP: DONE/PENDING] 的轮数

    for m in messages:
        if isinstance(m, ToolMessage):
            content = m.content or ""
            if GUARD_SENTINEL in content:
                guard_hits += 1
            elif BUDGET_SENTINEL in content:
                budget_forced = True
            else:
                tool_rounds += 1
        elif isinstance(m, AIMessage):
            if getattr(m, "tool_calls", None):
                tool_turns += 1
                if _detect_marker(m.content or "") is not None:
                    marker_emitted += 1

    # 最终答案取末条消息内容，过 process_llm_output 判空
    final_content = messages[-1].content if messages else ""
    answer = process_llm_output(final_content, context="harness_probe")

    budget_hit = budget_forced or (remaining is not None and remaining <= 0)
    marker_rate = round(marker_emitted / tool_turns, 3) if tool_turns else None

    return {
        "guard_hits": guard_hits,
        "correction_loops": guard_hits,  # 每次 guard 违规=一次纠正循环
        "tool_rounds": tool_rounds,
        "tool_turns": tool_turns,
        "marker_emit_rate": marker_rate,
        "budget_hit": budget_hit,
        "remaining_calls": remaining,
        "empty_answer": not bool(answer),
        "answer_chars": len(answer),
    }


async def run_one(item: dict, user_id: str, mode: str) -> dict:
    """跑单题，返回 {id, metrics..., latency, error}。"""
    agent = build_agent(user_id)
    state = build_initial_state(item["question"], user_id, mode)
    t0 = time.perf_counter()
    try:
        result = await agent.ainvoke(state)
        metrics = collect_metrics(result)
        error = None
    except Exception as e:  # 网络/模型异常：记录，继续下一题
        metrics = {}
        error = f"{type(e).__name__}: {e}"
    latency = round(time.perf_counter() - t0, 2)
    return {"id": item["id"], "latency_s": latency, "error": error, **metrics}


COLUMNS = [
    ("id", "id", 6),
    ("guard_hits", "guard", 6),
    ("correction_loops", "corr", 5),
    ("tool_rounds", "tools", 6),
    ("marker_emit_rate", "mark%", 6),
    ("budget_hit", "budget", 7),
    ("empty_answer", "empty", 6),
    ("remaining_calls", "rem", 4),
    ("latency_s", "lat_s", 7),
]


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "Y" if v else "."
    return str(v)


def print_table(rows: list[dict]) -> None:
    header = "  ".join(f"{label:>{w}}" for _, label, w in COLUMNS)
    print(header)
    print("-" * len(header))
    for r in rows:
        if r.get("error"):
            print(f"{r['id']:>6}  ERROR  {r['error'][:70]}")
            continue
        line = "  ".join(f"{_fmt(r.get(key)):>{w}}" for key, _, w in COLUMNS)
        print(line)


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r.get("error")]
    n = len(ok)
    if not n:
        return {"n_ok": 0, "n_error": len(rows)}

    def avg(key):
        vals = [r[key] for r in ok if isinstance(r.get(key), (int, float))]
        return round(sum(vals) / len(vals), 3) if vals else None

    return {
        "n_ok": n,
        "n_error": len(rows) - n,
        "guard_hits_total": sum(r.get("guard_hits", 0) for r in ok),
        "correction_loops_total": sum(r.get("correction_loops", 0) for r in ok),
        "empty_answer_rate": round(sum(1 for r in ok if r.get("empty_answer")) / n, 3),
        "budget_hit_rate": round(sum(1 for r in ok if r.get("budget_hit")) / n, 3),
        "avg_tool_rounds": avg("tool_rounds"),
        "avg_marker_emit_rate": avg("marker_emit_rate"),
        "avg_latency_s": avg("latency_s"),
    }


async def main_async(args) -> None:
    items = load_questions(args.only)
    if not items:
        print("没有可跑的题目。", file=sys.stderr)
        sys.exit(1)

    print(
        f"harness_probe | label={args.label} | mode={args.mode} | "
        f"user_id={args.user_id} | {len(items)} 题\n"
    )

    rows: list[dict] = []
    for i, item in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {item['id']} 跑中…", file=sys.stderr)
        row = await run_one(item, args.user_id, args.mode)
        rows.append(row)

    print()
    print_table(rows)
    summary = summarize(rows)
    print("\n== 汇总 ==")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"behavior_{args.label}_{stamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "label": args.label,
                "mode": args.mode,
                "user_id": args.user_id,
                "timestamp": stamp,
                "summary": summary,
                "rows": rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n→ 存档：{out_path}")


def main() -> None:
    p = argparse.ArgumentParser(description="Harness 行为量具（阶段②）")
    p.add_argument("--only", nargs="*", help="只跑指定题目 id，如 Q01 Q05")
    p.add_argument("--label", default="FLASH", help="本次运行标签（如 FLASH/STRONG），入存档名")
    p.add_argument("--mode", default="normal", choices=["normal", "discuss"])
    p.add_argument("--user-id", dest="user_id", default="default", help="rag_tool 语料所属 user_id")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
