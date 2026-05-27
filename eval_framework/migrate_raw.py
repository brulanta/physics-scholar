"""
raw文件格式转换脚本（一次性使用）
====================================
将旧的4评委raw文件转换为新的3评委格式：
- 移除 gemini-2.5-flash 和 gemini-2.5-pro 的条目
- 检测截断输出（不含完整打分格式）并标为ERROR
- summary如果存在则保留，否则标为ERROR等待repair补全
- 输出新文件，原文件不动

用法：
  python migrate_raw.py                          # 自动选最新raw文件
  python migrate_raw.py results/raw/raw_xxx.json # 指定文件
"""

import json
import sys
import re
from pathlib import Path
from datetime import datetime

BASE_DIR  = Path(__file__).parent
RAW_DIR   = BASE_DIR / "results" / "raw"

# 新的3个评委名称
NEW_JUDGES = {"deepseek-v3", "deepseek-v4-flash", "gemini-3.1-pro"}

# 要移除的旧评委
OLD_JUDGES = {"gemini-2.5-flash", "gemini-2.5-pro"}

# 判断打分输出是否完整：必须同时含有D4和A1
REQUIRED_FIELDS = ["D4 任务完成度", "A1 信息填补异常"]


def is_truncated(text: str) -> bool:
    """检查打分输出是否被截断（缺少必要字段）。"""
    if text.strip().startswith("ERROR:"):
        return False  # 已经是ERROR，不重复标记
    return not all(field in text for field in REQUIRED_FIELDS)


def is_error(text: str) -> bool:
    return text.strip().startswith("ERROR:")


def find_raw_file(arg) -> Path:
    if arg:
        p = Path(arg)
        if not p.exists():
            raise FileNotFoundError(f"找不到文件：{p}")
        return p
    files = sorted(RAW_DIR.glob("raw_*.json"))
    if not files:
        raise FileNotFoundError(f"{RAW_DIR} 下没有找到 raw_*.json")
    return files[-1]


def migrate(raw_path: Path):
    print(f"读取：{raw_path}")
    with open(raw_path, encoding="utf-8") as f:
        results = json.load(f)

    stats = {"removed": 0, "truncated": 0, "kept": 0, "summary_cleared": 0}

    for entry in results:
        qid = entry["id"]
        old_judges = entry.get("judge_results", [])
        new_judges = []

        for jr in old_judges:
            name   = jr["judge_name"]
            output = jr["raw_output"]

            # 移除旧评委
            if name in OLD_JUDGES:
                stats["removed"] += 1
                print(f"  [{qid}] 移除评委 {name}")
                continue

            # 保留的评委：检查是否截断
            if is_truncated(output):
                jr["raw_output"] = f"ERROR: 输出截断（缺少完整打分字段）原始内容：{output[:80]}..."
                stats["truncated"] += 1
                print(f"  [{qid}] {name} 输出截断，已标为ERROR")
            else:
                stats["kept"] += 1

            new_judges.append(jr)

        # gemini-3.1-pro如果之前没有作为评委，补一个空条目等repair填
        existing_names = {jr["judge_name"] for jr in new_judges}
        if "gemini-3.1-pro" not in existing_names:
            new_judges.append({
                "judge_name": "gemini-3.1-pro",
                "raw_output": "ERROR: 新增评委，尚未打分"
            })
            print(f"  [{qid}] 补充空缺评委 gemini-3.1-pro")

        entry["judge_results"] = new_judges

        # summary：如果原来是基于4评委生成的，清掉重来
        # 判断标准：summary存在且不是ERROR，则认为是旧格式，需要重新汇总
        current_summary = entry.get("summary", "")
        if current_summary and not is_error(current_summary):
            entry["summary"] = "ERROR: 评委构成已变更，需重新汇总"
            stats["summary_cleared"] += 1
            print(f"  [{qid}] summary已清除，等待重新汇总")

    # 输出新文件（不覆盖原文件）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name  = f"raw_{timestamp}_migrated.json"
    out_path  = RAW_DIR / out_name

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n转换完成：")
    print(f"  移除旧评委条目：{stats['removed']} 条")
    print(f"  截断输出标ERROR：{stats['truncated']} 条")
    print(f"  正常保留：{stats['kept']} 条")
    print(f"  summary清除：{stats['summary_cleared']} 条")
    print(f"\n新文件已保存：{out_path}")
    print(f"接下来运行：python repair.py {out_path}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        raw_path = find_raw_file(arg)
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)

    migrate(raw_path)
