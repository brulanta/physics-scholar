"""Stage 6 dev-live 验证：子 agent collect-selected 候选落库（probe 绕开的真实路径）。

probe 直跑 ainvoke、绕开 _persist_and_enrich，所以「DB 候选行真降到选中项」一直只有
单测 + state 契约覆盖、非 live 实证。本脚本走 routes.py 唯一生产路径 chat_stream →
_consume_events（读 retrieve 壳的 artifact = selected_tool_results）→ _persist_and_enrich →
save_candidates 落 ref_enrichment，把这块缺口补成实锤。

对比三层（用户 framing raw ⊃ 子 agent 精简版 ⊃ 主 agent 最终使用版本）：
  - raw_count       ：子 agent 全部外部工具结果里的论文数（transcript 的 ToolMessage）
  - selected_count  ：子 agent return_findings 点选的条目数（selection 列表）
  - db_count        ：ref_enrichment 实际落库行数（应 ≈ selected，且 ≪ raw）

用法：.venv/Scripts/python.exe scripts/dev_live_verify_candidates.py
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# UTF-8 stdout（Windows 控制台默认 gbk 会把中文论文标题打成乱码）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# 落子 agent 内层 transcript（retrieve 壳内的工具链 / return_findings / finalize）
os.environ["PS_DUMP_SUBAGENT"] = "1"

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
DB = REPO / "data" / "SQLite" / "app.db"
TRANSCRIPT_DIR = (
    REPO / "eval_framework" / "results" / "behavior" / "transcripts" / "subagent_inner"
)

# Q03（eval_framework/test_cases.json）：多篇外部检索题——逼出 collect-selected 价值
QUESTION = "帮我找3篇2020年之后关于光子微波信号产生的高引综述或重要进展论文。"


def _parse_sse(frame: str) -> dict | None:
    """从 SSE 帧提 JSON。_format_sse 产出形如 `data: {...}\\n\\n`。"""
    s = frame.strip()
    for prefix in ("data:", "event:"):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
            break
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None


def _count_raw_papers(messages: list) -> tuple[list[str], list[str]]:
    """从子 agent transcript 的 ToolMessage 数外部论文 source_id（s2/arxiv/openalex）。"""
    raw_ids: list[str] = []
    tool_names: list[str] = []
    for m in messages:
        if m.get("type") != "tool":
            continue
        name = m.get("name") or ""
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        for p in payload.get("papers", []) or []:
            sid = (
                p.get("s2_paper_id")
                or p.get("arxiv_id")
                or p.get("openalex_id")
            )
            if sid:
                raw_ids.append(f"{sid}")
                tool_names.append(name)
    return raw_ids, tool_names


def _count_selected(messages: list) -> int:
    """从 return_findings 工具调用的 args.selection 数点选条目数。"""
    for m in messages:
        if m.get("type") != "ai":
            continue
        for tc in m.get("tool_calls") or []:
            if tc.get("name") == "return_findings":
                sel = (tc.get("args") or {}).get("selection") or []
                if isinstance(sel, list):
                    return len(sel)
    return 0


async def main() -> int:
    # 迟到 import：上面已设好 env，import graph 触发 config 加载（含 PS_DUMP_SUBAGENT 读取点）
    from src.rag.graph import chat_stream

    conv_id = f"live_verify_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    print(f"[live-verify] conv_id={conv_id}")
    print(f"[live-verify] question={QUESTION}")
    print("[live-verify] driving chat_stream (real production path) ...")

    agent_msg_id = None
    n_frames = 0
    last_err = None
    tool_events: list[str] = []  # 抓 SSE 里 tool_start/tool_end 的 name（查嵌套事件泄漏）
    retrieve_tool_id = None
    subtask_events: list[dict] = []  # Stage 5：抓 subtask 帧（子 agent 内部 trace）
    async for frame in chat_stream(QUESTION, conv_id, request=None, user_id="default"):
        n_frames += 1
        payload = _parse_sse(frame) if isinstance(frame, str) else None
        if not payload:
            continue
        et = payload.get("type")
        if et == "done":
            agent_msg_id = payload.get("agent_msg_id")
        elif et == "error":
            last_err = payload.get("message")
        elif et in ("tool_start", "tool_end"):
            tool_events.append(f"{et}:{payload.get('name')}")
            if et == "tool_start" and payload.get("name") == "retrieve":
                retrieve_tool_id = payload.get("tool_id")
        elif et == "subtask":
            subtask_events.append(payload)

    print(f"[live-verify] stream done: {n_frames} frames, agent_msg_id={agent_msg_id}")
    print(f"[SSE tool events] {' '.join(tool_events) if tool_events else '(none)'}")
    nested = [t for t in tool_events if "retrieve" not in t]
    print(f"[SSE nested leak] {len(nested)} 帧非 retrieve 工具事件（应为 0，>0 = 子 agent 事件泄漏到前端/候选路径）")
    # Stage 5：subtask 帧（retrieve 内部子 agent 步骤）
    print(f"\n[Stage5 subtask] {len(subtask_events)} 帧（retrieve 内部 trace）")
    for s in subtask_events:
        print(f"  kind={s.get('kind'):14} name={s.get('name') or '-':16} ok={s.get('ok')} parent={(s.get('parent_tool_id') or '')[:8]}")
    if subtask_events:
        kinds = [s.get("kind") for s in subtask_events]
        n_think = kinds.count("thinking_start")
        n_tools = kinds.count("tool_start")
        print(f"  → {n_think} 轮思考 + {n_tools} 次工具调用透出")
        bad_parent = [s for s in subtask_events if s.get("parent_tool_id") != retrieve_tool_id]
        print(f"  parent_tool_id 对齐 retrieve({(retrieve_tool_id or '')[:8]}): "
              f"{'✓ 全部对齐' if not bad_parent else f'✗ {len(bad_parent)} 帧错位'}")
    if last_err:
        print(f"[live-verify] !! stream emitted error: {last_err}")
    if agent_msg_id is None:
        print("[live-verify] FAIL: 无 done 帧 / agent_msg_id，落库未发生")
        return 1

    # ── DB 候选行（这是 probe 看不到、本次要验证的落点）──
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT source_id, ref_type, title, is_cited FROM ref_enrichment WHERE message_id=?",
        (agent_msg_id,),
    ).fetchall()
    conn.close()
    db_ids = [r["source_id"] for r in rows]
    print(f"\n[DB] ref_enrichment rows for msg {agent_msg_id}: {len(rows)}")
    for r in rows:
        title = (r["title"] or "")[:46]
        print(f"     {r['source_id']:40} {r['ref_type']:8} cited={r['is_cited']} | {title}")

    # ── 子 agent transcript：raw vs selected ──
    transcripts = sorted(TRANSCRIPT_DIR.glob("sub_*.json")) if TRANSCRIPT_DIR.exists() else []
    raw_ids: list[str] = []
    selected_count = 0
    findings_len = 0
    if transcripts:
        t = json.loads(transcripts[-1].read_text(encoding="utf-8"))
        raw_ids, _tn = _count_raw_papers(t.get("messages", []))
        selected_count = _count_selected(t.get("messages", []))
        findings_len = len(t.get("findings", "") or "")
        print(f"\n[transcript] {transcripts[-1].name}")
        print(f"             status={t.get('status')} hint={t.get('agent_hint')}")
        print(f"             raw papers in tool results: {len(raw_ids)}  {raw_ids}")
        print(f"             return_findings selection count: {selected_count}")
        print(f"             findings body len: {findings_len} chars")
    else:
        print("\n[transcript] 无落盘（PS_DUMP_SUBAGENT 未生效？）")

    # ── 三层对比（归一前缀：raw 串是裸 id，DB 带 s2:/arxiv: 前缀）──
    raw_set = set(raw_ids)
    db_set = set(db_ids)
    db_bare = {sid.split(":", 1)[1] if ":" in sid else sid for sid in db_set}
    print("\n=== 三层对比（raw ⊃ 选中 ⊃ DB）===")
    print(f"  raw_count      = {len(raw_set)}   （子 agent 全部工具结果论文）")
    print(f"  selected_count = {selected_count} （return_findings 点选条目）")
    print(f"  db_count       = {len(db_bare)}   （ref_enrichment 实落）")

    if raw_set:
        leaked = db_bare - raw_set
        print(f"  DB ⊄ raw 的（候选层幻觉/外溢）: {sorted(leaked) if leaked else '无 ✓'}")
    if raw_set and db_bare:
        ratio = len(db_bare) / len(raw_set)
        print(f"  DB/raw 收敛比 = {ratio:.2f}（≪1 才说明 collect-selected 砍掉了噪声）")

    # 判定
    ok = True
    if not db_bare:
        print("\n[判定] FAIL：DB 0 行——候选未落库（artifact 冒泡 / save_candidates 异常？）")
        ok = False
    elif raw_set and db_bare == raw_set and len(raw_set) > 1:
        print("\n[判定] FAIL：DB == raw 全量，collect-selected 未生效（疑似子 agent 事件嵌套泄漏）")
        ok = False
    elif selected_count and len(db_bare) > selected_count:
        print(f"\n[判定] FAIL：DB({len(db_bare)}) > 选中({selected_count})，存了未选中的噪声")
        ok = False
    else:
        print("\n[判定] OK：候选已按选中项落库，collect-selected live 实证通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
