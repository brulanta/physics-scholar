"""
评分结果补全脚本
================
读取一份残缺的 raw_*.json，找出所有 ERROR 条目：
1. 对报错的评委单独重跑打分（限速已内置于call_model）
2. 评委都齐了但 summary 是 ERROR，重跑汇总
3. 补全后覆盖原 raw 文件，并生成新的 report

用法：
  python repair.py                        # 自动选最新的 raw 文件
  python repair.py results/raw/raw_xxx.json  # 指定文件

依赖：pip install openai python-dotenv
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from evaluator import (
    JUDGE_MODELS,
    SUMMARY_MODEL,
    call_model,
    build_judge_prompt,
    build_summary_prompt,
    SUMMARY_SYSTEM,
    TEST_CASES_FILE,
    RAW_DIR,
    REPORT_DIR,
)

# 限速已内置于 evaluator.call_model，此处无需额外处理

# 打分输出必须包含的字段
REQUIRED_SCORE_FIELDS = [
    "D1 内容准确性",
    "D2 回答深度",
    "D3 角色一致性",
    "D4 任务完成度",
    "A1 信息填补异常",
]


def is_error(text: str) -> bool:
    return text.strip().startswith("ERROR:")


def is_incomplete(text: str) -> bool:
    """打分输出不含ERROR但缺少必要字段，视为截断。"""
    if is_error(text):
        return False
    return not all(field in text for field in REQUIRED_SCORE_FIELDS)


def needs_rerun(text: str) -> bool:
    """需要重跑：ERROR或截断都算。"""
    return is_error(text) or is_incomplete(text)


def find_raw_file(path_arg: str | None) -> Path:
    if path_arg:
        p = Path(path_arg)
        if not p.exists():
            raise FileNotFoundError(f"找不到文件：{p}")
        return p
    # 自动选最新
    files = sorted(RAW_DIR.glob("raw_*.json"))
    if not files:
        raise FileNotFoundError(f"在 {RAW_DIR} 找不到任何 raw_*.json 文件")
    return files[-1]


def load_test_cases() -> dict:
    with open(TEST_CASES_FILE, encoding="utf-8") as f:
        cases = json.load(f)
    return {c["id"]: c for c in cases}


async def repair(raw_path: Path):
    print(f"读取文件：{raw_path}")
    with open(raw_path, encoding="utf-8") as f:
        results = json.load(f)

    cases_map = load_test_cases()
    judge_map = {m["name"]: m for m in JUDGE_MODELS}

    total_fixed = 0

    for entry in results:
        qid = entry["id"]
        case = cases_map.get(qid)
        if case is None:
            print(f"  [{qid}] 在 test_cases.json 中找不到，跳过")
            continue

        judge_prompt = build_judge_prompt(case)
        system_judge = "你是一个微波光子学领域的专业评审。请严格按照给定格式输出评分结果，不要添加任何额外内容。"

        # ── 修复各评委的报错或截断输出 ──
        judge_updated = False  # 本题是否有评委成功重新打分且结果完整
        for jr in entry["judge_results"]:
            if not needs_rerun(jr["raw_output"]):
                continue
            if is_incomplete(jr["raw_output"]):
                print(f"  [{qid}] {jr['judge_name']} 输出截断，重新打分")

            judge_name = jr["judge_name"]
            model_cfg = judge_map.get(judge_name)
            if model_cfg is None:
                print(f"  [{qid}] 未知评委 {judge_name}，跳过")
                continue

            print(f"  [{qid}] 重跑评委 {judge_name} ...")
            try:
                output = await call_model(model_cfg, judge_prompt, system_judge)
                print(output)
                if needs_rerun(output):
                    # 重跑后仍然有问题，写回但不标记为updated
                    jr["raw_output"] = output
                    print(
                        f"  [{qid}] {judge_name} ⚠️  重跑后仍截断或异常，留待下次repair"
                    )
                else:
                    jr["raw_output"] = output
                    print(f"  [{qid}] {judge_name} ✅")
                    total_fixed += 1
                    judge_updated = True
            except Exception as e:
                jr["raw_output"] = f"ERROR: {e}"
                print(f"  [{qid}] {judge_name} ❌ 仍然失败：{e}")

        # 有评委成功更新，旧summary已过期，强制失效
        if judge_updated and not is_error(entry.get("summary", "")):
            entry["summary"] = "ERROR: 评委内容已更新，需重新汇总"
            print(f"  [{qid}] 评委有更新，summary 已失效，将重新汇总")

        # ── 检查所有评委是否都齐了 ──
        any_judge_error = any(
            needs_rerun(jr["raw_output"]) for jr in entry["judge_results"]
        )

        # ── 修复 summary ──
        if is_error(entry.get("summary", "")):
            if any_judge_error:
                print(f"  [{qid}] summary 报错，但评委仍有 ERROR，暂不重跑汇总")
                continue

            print(f"  [{qid}] 重跑汇总 ...")
            summary_prompt = build_summary_prompt(case, entry["judge_results"])
            try:
                summary = await call_model(
                    SUMMARY_MODEL, summary_prompt, SUMMARY_SYSTEM, max_tokens=20000
                )
                entry["summary"] = summary
                print(f"  [{qid}] 汇总 ✅")
                total_fixed += 1
            except Exception as e:
                entry["summary"] = f"ERROR: {e}"
                print(f"  [{qid}] 汇总 ❌ 仍然失败：{e}")

    if total_fixed == 0:
        print(f"\n无任何修复，文件未改动。")
        return

    # ── 覆盖写回原 raw 文件 ──
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n原始结果已更新：{raw_path}（修复 {total_fixed} 处）")

    # ── 生成新 report ──
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "timestamp": timestamp,
        "source_raw": str(raw_path.name),
        "total_cases": len(results),
        "judge_models": [m["name"] for m in JUDGE_MODELS],
        "summary_model": SUMMARY_MODEL["name"],
        "cases": [
            {
                "id": r["id"],
                "question": r["question"],
                "intents": r["intents"],
                "summary": r["summary"],
                "anomaly_flagged": "A1 异常标记：是" in r.get("summary", ""),
                "has_error": (
                    any(needs_rerun(jr["raw_output"]) for jr in r["judge_results"])
                    or is_error(r.get("summary", ""))
                ),
            }
            for r in results
        ],
    }
    report_path = REPORT_DIR / f"report_{timestamp}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"评测报告已保存：{report_path}")

    # ── 最终状态汇报 ──
    remaining_errors = sum(
        1
        for r in results
        if any(is_error(jr["raw_output"]) for jr in r["judge_results"])
        or is_error(r.get("summary", ""))
    )
    if remaining_errors == 0:
        print("\n✅ 所有条目已补全，report 已生成。")
    else:
        print(
            f"\n⚠️  仍有 {remaining_errors} 道题存在 ERROR，可再次运行 repair.py 继续补全。"
        )


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        raw_path = find_raw_file(arg)
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)

    asyncio.run(repair(raw_path))
