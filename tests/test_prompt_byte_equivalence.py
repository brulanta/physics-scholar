"""
test_prompt_byte_equivalence.py — prompt 模块化框架改造的零行为变化回归

断言改造后 build_prompt(mode=normal/discuss, ...) 的输出与改造前 dump 的
黄金基线（tests/fixtures/prompt_baseline_*.txt）字节级相等。

基线生成：python scripts/dump_prompt_baseline.py（改造前已跑过一次）。

配套断言：
- debug.yaml 修复后 apply_config 不再 KeyError
- mode 从 _MODE_MODULES 数据驱动（MODES 与之一致）
- 三个模块子包 pkgutil 扫描成功（frozen 兼容烟雾）
"""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# 与 dump_prompt_baseline.py 保持一致的固定输入
HISTORY = "测试历史：用户问过微波光子学的基础问题。"


def _baseline(mode: str) -> str:
    path = FIXTURES_DIR / f"prompt_baseline_{mode}.txt"
    if not path.exists():
        pytest.skip(f"缺少基线 fixture（先跑 scripts/dump_prompt_baseline.py）：{path.name}")
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["normal", "discuss"])
def test_prompt_byte_identical(mode: str) -> None:
    """改造后 prompt 与改造前基线字节级相等（零行为变化核心断言）。"""
    from src.rag.prompts import build_prompt, CITATION_DEFAULT
    from src.rag.prompts.plugins import TOOL_DECISION_PLUGIN

    result = build_prompt(
        mode=mode,
        history=HISTORY,
        citation_plugin=CITATION_DEFAULT,
        tool_decision_plugin=TOOL_DECISION_PLUGIN,
    )
    baseline = _baseline(mode)
    assert result == baseline, f"{mode} prompt 字节级漂移（长度 {len(result)} vs {len(baseline)}）"


def test_debug_yaml_no_keyerror() -> None:
    """debug.yaml 删除 CITATION_PLUGIN_SLOT 后 apply_config 不再抛 KeyError。"""
    from src.rag.prompts.builder import PromptBuilder
    from src.rag.prompts.modules import get_shared_modules

    builder = PromptBuilder()
    for m in get_shared_modules():
        builder.register(m)
    debug_yaml = Path(__file__).resolve().parents[1] / "src" / "rag" / "prompts" / "profiles" / "debug.yaml"
    # 不应抛 KeyError
    builder.apply_config(str(debug_yaml))


def test_modes_from_data() -> None:
    """mode 列表由 _MODE_MODULES 数据驱动，MODES 与之一致。"""
    from src.rag.prompts.modules import MODES, _MODE_MODULES

    assert set(MODES) == set(_MODE_MODULES.keys())
    assert "normal" in MODES
    assert "discuss" in MODES


def test_module_subpackages_scanned() -> None:
    """三个模块子包 pkgutil 扫描成功（frozen 兼容烟雾：扫空即落盘异常）。"""
    from src.rag.prompts.modules import SHARED_MODULES, _MODE_MODULES

    assert len(SHARED_MODULES) >= 10, f"shared 扫到 {len(SHARED_MODULES)} 个，疑似 pkgutil 扫描异常"
    assert len(_MODE_MODULES["normal"]) >= 3, "normal 扫描异常"
    assert len(_MODE_MODULES["discuss"]) >= 3, "discuss 扫描异常"
