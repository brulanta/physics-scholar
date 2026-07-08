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
- **soft 档合规真值**（`missing_thinking_calls`）：`guard_hits` 数的是 **strict 哨兵**——
  soft/off 档 guard 不注入哨兵、只打日志放行，故 `guard_hits` 在 soft 恒 0，是**假阴性**
  （⑤ 暴露的量具缺口）。guard 的违规谓词只是「带 tool_calls 却缺 <thinking>」，而违规的
  AIMessage 在 strict/soft/off **每种模式都留在 transcript**；故直接数它，profile 无关，
  才是 soft 档真合规度。strict 档二者应一致（每次驳回对应一条违规 AIMessage）。
- **Tier 1 工具错误门**（`tool_err_request`）：A1/A4 工具信息瘦身的验收门（见
  plan/tool-info-slimming-plan.md）。把工具执行分成 request（参数/校验失败=agent arg-fill
  退化，**门信号**）/ transient（429/超时=上游噪声，旁观）/ other（auth/not_found，旁观）。
  瘦身若删掉承重的防呆字段描述 → agent 填错参 → `tool_err_request` 抬头。**429 主噪声被隔到
  transient 桶，不污染门**（④ 教训）。分类纯逻辑、离线可测（tests/test_harness_probe_metrics.py）。
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
    python scripts/harness_probe.py --only Q03 Q11 Q18 --label gem2_after_spotcheck \
        --dump-transcript                                # A1/A4 验收：落 transcript 供人眼 spot-check
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
from src.rag.harness_profile import PRESETS  # noqa: E402
from src.core.trim_thinking import process_llm_output  # noqa: E402

TEST_CASES = REPO_ROOT / "eval_framework" / "test_cases.json"
OUT_DIR = REPO_ROOT / "eval_framework" / "results" / "behavior"


def _content_to_jsonable(content) -> object:
    """把 message.content 规整成 json 可序列化。str 原样；list/dict 尝试 json 往返，
    失败（含非可序列化对象）退化为 repr——transcript 仅供人眼 spot-check，保真即可。"""
    if isinstance(content, str):
        return content
    try:
        json.dumps(content)
        return content
    except (TypeError, ValueError):
        return repr(content)


def serialize_message(m) -> dict:
    """把一条 langchain message 序列化成人眼可读的 dict（供 transcript 落盘 spot-check）。

    保留 spot-check 关心的全部信号：type / content / tool_calls（选了哪个工具+填了什么参）
    / tool_call_id / status（工具执行框架层成败）/ additional_kwargs。gloss 砍后 agent 是否
    仍认得返回字段、arg 有无退化，全靠这里落下来的真实交互可读。
    """
    entry: dict = {
        "type": m.type,
        "content": _content_to_jsonable(m.content),
    }
    name = getattr(m, "name", None)
    if name:
        entry["name"] = name
    if isinstance(m, AIMessage):
        tc = getattr(m, "tool_calls", None)
        if tc:
            entry["tool_calls"] = [
                {"name": t.get("name"), "args": t.get("args"), "id": t.get("id")}
                for t in tc
            ]
        itc = getattr(m, "invalid_tool_calls", None)
        if itc:
            entry["invalid_tool_calls"] = itc
    if isinstance(m, ToolMessage):
        entry["tool_call_id"] = getattr(m, "tool_call_id", None)
        entry["status"] = getattr(m, "status", None)
    akwargs = getattr(m, "additional_kwargs", None)
    if akwargs:
        try:
            json.dumps(akwargs)
            entry["additional_kwargs"] = akwargs
        except (TypeError, ValueError):
            entry["additional_kwargs"] = repr(akwargs)
    return entry


def dump_transcript(dump_dir: Path, item: dict, row: dict, messages: list) -> Path:
    """把单题 transcript 落盘：question + 行为指标 row + 逐条序列化消息。返回写入路径。"""
    payload = {
        "id": item["id"],
        "question": item["question"],
        "run": row,
        "messages": [serialize_message(m) for m in messages],
    }
    out = dump_dir / f"{item['id']}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out

# thinking_guard 注入的「工具调用已被取消」假 ToolMessage 的稳定子串（graph.py:184）
GUARD_SENTINEL = "未检测到必要的 <thinking>"
# final_answer 达预算上限时注入的取消哨兵（graph.py:249）
BUDGET_SENTINEL = "已达最大调用次数上限"

# Tier 1 工具错误分类（error_type 词表来自 src/rag/tools/*.py 实测枚举）：
# - transient = 上游/网络/限流，**非 agent 的 arg-fill 问题**（④ 实证 429 是主噪声源，
#   工具报文自己都写「绝非你的关键词不好」）→ 不作为 arg-fill 门的判据，仅旁观计数。
# - request  = 参数/校验类，**归因于 agent 填错**（含框架层 status=="error"，即 pydantic
#   校验/异常）→ 这才是 Tier 1 门要盯的信号；A1/A4 瘦身若删过头，此计数会抬头。
# - 其余 success:false（auth/access/not_found 等）= 环境或语义，归 other，旁观不作门。
TRANSIENT_ERROR_TYPES = {
    "rate_limited", "timeout", "request_failed", "server_error", "recent_failed_query",
}
REQUEST_ERROR_TYPES = {
    "bad_request", "invalid_arguments", "invalid_params", "parse_error",
}


def classify_tool_message(m: ToolMessage) -> tuple[str, str | None]:
    """把一条 ToolMessage 归类，返回 (kind, error_type)。

    kind ∈ {guard, budget, ok, err_transient, err_request, err_other}；
    error_type 为工具自报的 error_type 字符串（ok/guard/budget 时为 None），供 diff 诊断
    「是哪个字段/哪类错误」——A1/A4 before/after 验收的可操作信号。

    双信号：① 框架层 `status=="error"`（ToolNode handle_tool_errors 捕获的异常/参数校验，
    graph.py:426）→ 归 request；② 5 个工具自报的 `{"success": false, "error_type": ...}`
    正常返回（status 仍为 success）→ 按 error_type 词表分桶。rag_tool 返回纯文本（无 success
    字段）→ 解析不出 → 视为 ok（其空召回是语料问题，属 Tier 2 任务达成域，非 Tier 1 错误）。
    """
    content = m.content or ""
    if GUARD_SENTINEL in content:
        return "guard", None
    if BUDGET_SENTINEL in content:
        return "budget", None
    # ① 框架层硬错误（异常 / pydantic 校验失败）——直接归 agent 侧 request
    if getattr(m, "status", None) == "error":
        return "err_request", "framework_error"
    # ② 工具自报失败：解析 JSON 的 success/error_type
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict) and data.get("success") is False:
        et = str(data.get("error_type", "") or "").lower() or None
        kind_et = (et or "").lower()
        if kind_et in TRANSIENT_ERROR_TYPES:
            return "err_transient", et
        if kind_et in REQUEST_ERROR_TYPES:
            return "err_request", et
        return "err_other", et
    return "ok", None

# 题级重试之间的退避（秒）——RPM=5 下给限流窗口喘息
RETRY_BACKOFF_S = 8.0


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


def build_initial_state(question: str, user_id: str, mode: str, profile) -> dict:
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
        "remaining_calls": profile.budget_n,
        "next_prefill": None,
    }


def collect_metrics(result: dict) -> dict:
    """从最终 state 的 transcript 推导行为指标。"""
    messages = result.get("messages", [])
    remaining = result.get("remaining_calls", None)

    guard_hits = 0
    tool_rounds = 0
    budget_forced = False
    tool_turns = 0              # 带 tool_calls 的 AIMessage 轮数
    marker_emitted = 0          # 其中吐出了 [TOOL_LOOP: DONE/PENDING] 的轮数
    missing_thinking_calls = 0  # 其中缺 <thinking>…</thinking> 的轮数（合规真值，profile 无关）
    tool_err_request = 0        # Tier 1 门信号：参数/校验类失败（归因 agent arg-fill）
    tool_err_transient = 0      # 上游/限流失败（旁观，非门）
    tool_err_other = 0          # auth/not_found 等（旁观，非门）
    # 每次失败调用的 (kind, error_type) 列表——A1/A4 before/after diff 的可操作诊断：
    # 「errR 2→3」不说明哪个字段退化，本列表答「是 invalid_arguments 还是 bad_request」。
    tool_errors: list[list[str]] = []

    for m in messages:
        if isinstance(m, ToolMessage):
            kind, et = classify_tool_message(m)
            if kind == "guard":
                guard_hits += 1
            elif kind == "budget":
                budget_forced = True
            else:
                # ok / err_* 都是真实工具执行轮（哨兵已在上面排除）
                tool_rounds += 1
                if kind == "err_request":
                    tool_err_request += 1
                    tool_errors.append([kind, et or ""])
                elif kind == "err_transient":
                    tool_err_transient += 1
                    tool_errors.append([kind, et or ""])
                elif kind == "err_other":
                    tool_err_other += 1
                    tool_errors.append([kind, et or ""])
        elif isinstance(m, AIMessage):
            if getattr(m, "tool_calls", None):
                tool_turns += 1
                ai_content = m.content or ""
                if _detect_marker(ai_content) is not None:
                    marker_emitted += 1
                # 合规真值：guard 的违规谓词就是「带 tool_calls 却缺 <thinking>」，
                # 而违规的 AIMessage 在 strict/soft/off **每种模式都留在 transcript**。
                # guard_hits（strict 哨兵计数）在 soft/off 恒 0——是假阴性；本计数直接
                # 数违规 AIMessage，profile 无关，才是 soft 档合规度的真信号（⑤ 量具缺口）。
                if "<thinking>" not in ai_content or "</thinking>" not in ai_content:
                    missing_thinking_calls += 1

    # 最终答案取末条消息内容，过 process_llm_output 判空
    final_content = messages[-1].content if messages else ""
    answer = process_llm_output(final_content, context="harness_probe")

    budget_hit = budget_forced or (remaining is not None and remaining <= 0)
    marker_rate = round(marker_emitted / tool_turns, 3) if tool_turns else None
    compliance_rate = (
        round(1 - missing_thinking_calls / tool_turns, 3) if tool_turns else None
    )

    return {
        "guard_hits": guard_hits,
        "correction_loops": guard_hits,  # 每次 guard 违规=一次纠正循环（strict 机构活动）
        "missing_thinking_calls": missing_thinking_calls,  # profile 无关的违规真值
        "thinking_compliance_rate": compliance_rate,       # 1 - 违规率；soft 档真合规度
        "tool_rounds": tool_rounds,
        "tool_err_request": tool_err_request,      # Tier 1 门：应为 0
        "tool_err_transient": tool_err_transient,  # 旁观：429/超时等上游噪声
        "tool_err_other": tool_err_other,          # 旁观：auth/not_found 等
        "tool_errors": tool_errors,                # [[kind, error_type], …] 诊断明细
        "tool_turns": tool_turns,
        "marker_emit_rate": marker_rate,
        "budget_hit": budget_hit,
        "remaining_calls": remaining,
        "empty_answer": not bool(answer),
        "answer_chars": len(answer),
    }


async def run_one(
    item: dict,
    user_id: str,
    mode: str,
    profile,
    timeout: float,
    retries: int,
    keep_transcript: bool = False,
) -> tuple[dict, list]:
    """跑单题，返回 ({id, metrics..., latency, error, attempts}, transcript_messages)。

    针对 gemini-3.1-pro（RPM=5 + Google GLI 上游抖动）的兜底：
    - **硬超时**：`ainvoke` 无 request_timeout，上游挂起会无限 stall。用 asyncio.wait_for
      给每题一个墙钟上限，超时即判失败进入重试（不 stall 整轮）。
    - **题级重试**：对「超时 / 抛异常 / 空答」重试 `retries` 次。空答在强模型的工具题上
      多半是上游 200-空 body 的哑火，值得重试；attempts 如实记录，最后一次仍空则 empty_answer=True。
    每次重试重建 agent+state，避免脏状态复用。

    keep_transcript=True 时把**最后一次 ainvoke 的完整 messages** 带回（供 --dump-transcript
    落盘 spot-check）；失败/空答/重试链上只保留最后那条结果的 transcript（重试本身不记历史）。
    """
    error = None
    metrics: dict = {}
    transcript: list = []
    t0 = time.perf_counter()
    attempts = 0
    for attempt in range(retries + 1):
        attempts = attempt + 1
        agent = build_agent(user_id, profile)
        state = build_initial_state(item["question"], user_id, mode, profile)
        try:
            result = await asyncio.wait_for(agent.ainvoke(state), timeout=timeout)
            metrics = collect_metrics(result)
            error = None
            if keep_transcript:
                transcript = list(result.get("messages", []))
        except asyncio.TimeoutError:
            metrics = {}
            error = f"TimeoutError: 超过 {timeout}s 无响应"
            transcript = []
        except Exception as e:  # 网络/模型异常
            metrics = {}
            error = f"{type(e).__name__}: {e}"
            transcript = []

        # 干净非空结果 → 收工；否则（异常 / 超时 / 空答）还有配额就重试
        if error is None and not metrics.get("empty_answer", True):
            break
        if attempt < retries:
            reason = error or "空答"
            print(
                f"    ↳ {item['id']} 第 {attempts} 次不理想（{reason}），重试…",
                file=sys.stderr,
            )
            await asyncio.sleep(RETRY_BACKOFF_S)

    latency = round(time.perf_counter() - t0, 2)
    return {"id": item["id"], "latency_s": latency, "error": error, "attempts": attempts, **metrics}, transcript


COLUMNS = [
    ("id", "id", 6),
    ("guard_hits", "guard", 6),
    ("missing_thinking_calls", "noThk", 6),
    ("tool_rounds", "tools", 6),
    ("tool_err_request", "errR", 5),
    ("tool_err_transient", "errT", 5),
    ("marker_emit_rate", "mark%", 6),
    ("budget_hit", "budget", 7),
    ("empty_answer", "empty", 6),
    ("remaining_calls", "rem", 4),
    ("attempts", "try", 4),
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
        # guard_hits 只在 strict 档非零（哨兵计数）；soft/off 恒 0（假阴性）。
        "guard_hits_total": sum(r.get("guard_hits", 0) for r in ok),
        # missing_thinking_calls 是 profile 无关的合规真值——soft/off 档看这个，不看 guard。
        "missing_thinking_total": sum(r.get("missing_thinking_calls", 0) for r in ok),
        "tool_turns_total": sum(r.get("tool_turns", 0) for r in ok),
        # Tier 1 门：request 类工具错误总数——应为 0（arg-fill 无退化）。
        "tool_err_request_total": sum(r.get("tool_err_request", 0) for r in ok),
        "tool_err_transient_total": sum(r.get("tool_err_transient", 0) for r in ok),  # 旁观
        "avg_thinking_compliance_rate": avg("thinking_compliance_rate"),
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

    profile = PRESETS[args.profile]
    label = args.label or args.profile  # 未显式指定 label 时用 profile 名

    print(
        f"harness_probe | profile={args.profile} | label={label} | mode={args.mode} | "
        f"user_id={args.user_id} | {len(items)} 题\n"
        f"  {profile}\n"
    )

    # --dump-transcript：把每题完整 transcript 落到子目录，供人眼 spot-check 选工具/读字段/arg。
    # 缺省关闭——只落行为指标计数，行为不变。落盘目录随本轮 timestamp 绑定 label，避免覆盖。
    dump_dir: Path | None = None
    if args.dump_transcript:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dump_dir = OUT_DIR / "transcripts" / f"{label}_{stamp}"
        dump_dir.mkdir(parents=True, exist_ok=True)
        print(f"  → transcript 落盘到：{dump_dir}\n")

    rows: list[dict] = []
    for i, item in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {item['id']} 跑中…", file=sys.stderr)
        row, transcript = await run_one(
            item, args.user_id, args.mode, profile, args.timeout, args.retries,
            keep_transcript=args.dump_transcript,
        )
        rows.append(row)
        if dump_dir is not None and transcript:
            p = dump_transcript(dump_dir, item, row, transcript)
            print(f"    ↳ transcript → {p}", file=sys.stderr)
        # RPM=5：题间静置，避免下一题开头就撞限流
        if i < len(items) and args.pace > 0:
            await asyncio.sleep(args.pace)

    print()
    print_table(rows)
    summary = summarize(rows)
    print("\n== 汇总 ==")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"behavior_{label}_{stamp}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "label": label,
                "profile": args.profile,
                "profile_fields": vars(profile),
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
    p.add_argument(
        "--profile",
        default="FLASH",
        choices=list(PRESETS.keys()),
        help="HarnessProfile 预置：FLASH（现状基线）| STRONG（松绑候选）",
    )
    p.add_argument("--label", default=None, help="运行标签（入存档名）；缺省=profile 名")
    p.add_argument("--mode", default="normal", choices=["normal", "discuss"])
    p.add_argument("--user-id", dest="user_id", default="default", help="rag_tool 语料所属 user_id")
    p.add_argument(
        "--timeout", type=float, default=360.0,
        help="每题墙钟上限（秒）；上游挂起超时即判失败进入重试。默认 360",
    )
    p.add_argument(
        "--retries", type=int, default=1,
        help="题级重试次数（对超时/异常/空答）。默认 1（即最多跑 2 次）",
    )
    p.add_argument(
        "--pace", type=float, default=5.0,
        help="题间静置秒数，缓解 RPM=5 限流。默认 5",
    )
    p.add_argument(
        "--dump-transcript", action="store_true",
        help="把每题完整 transcript 落到 results/behavior/transcripts/{label}_{stamp}/，"
             "供人眼 spot-check 选工具/读字段/arg 有无退化（A1/A4 验收用）。缺省只落指标计数",
    )
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
