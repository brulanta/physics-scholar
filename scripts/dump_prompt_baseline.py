"""
dump_prompt_baseline.py — 生成 prompt 字节级基线 fixtures

改造前跑一次，把 normal/discuss 的 build_prompt 输出存成黄金基线。
改造后 test_prompt_byte_equivalence.py 断言输出 == 基线，验证零行为变化。

用法:
    python scripts/dump_prompt_baseline.py

输出:
    tests/fixtures/prompt_baseline_normal.txt
    tests/fixtures/prompt_baseline_discuss.txt
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.rag.prompts import build_prompt, CITATION_DEFAULT
from src.rag.prompts.plugins import TOOL_DECISION_PLUGIN

FIXTURES_DIR = ROOT / "tests" / "fixtures"

# 固定输入：非空 history 触发 {history} 注入路径，确保基线覆盖动态注入。
HISTORY = "测试历史：用户问过微波光子学的基础问题。"
CITATION_PLUGIN = CITATION_DEFAULT
TOOL_PLUGIN = TOOL_DECISION_PLUGIN


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    for mode in ("normal", "discuss"):
        prompt = build_prompt(
            mode=mode,
            history=HISTORY,
            citation_plugin=CITATION_PLUGIN,
        )
        out = FIXTURES_DIR / f"prompt_baseline_{mode}.txt"
        out.write_text(prompt, encoding="utf-8")
        print(f"[dump] {mode}: {len(prompt)} chars -> {out.relative_to(ROOT)}")

    print("\n基线 fixtures 已生成。改造后跑 test_prompt_byte_equivalence.py 验证字节相等。")


if __name__ == "__main__":
    main()
