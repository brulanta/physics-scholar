"""
test_prompt_byte_equivalence.py — prompt 模块化框架回归 + 引用格式语义回归

历史：T0（prompt 模块化）时本文件用「字节级黄金基线」断言改造零行为变化。T0 完成
后零行为变化已成事实，字节等价用例退役——T1（想法 2(b) bind-by-id）起改的是 prompt
**内容**（教 model 写 lean ref），「字节不变」从成功判据变成反指标，每次内容变动都要
重 dump 基线，测试退化为仪式。改判据为**语义回归**：断言 prompt 含 lean ref 关键字，
保护「引用格式改动确实落在 prompt 里」，未来想法 3 再改 prompt 时它会红 = 真信号。

保留的框架断言（T0 资产，跨 T1/T2/T3 都该成立）：
- debug.yaml 修复后 apply_config 不再 KeyError
- mode 从 _MODE_MODULES 数据驱动（MODES 与之一致）
- 三个模块子包 pkgutil 扫描成功（frozen 兼容烟雾）

新增的语义断言（T1 资产）：
- build_prompt 输出含 lean ref 关键字（[source_id] / rag:<doc_id> / bind-by-id 规则）
- translation 模式 <zh> 保留、source_id 不翻译口径在
"""
from __future__ import annotations

import pytest


# ── 框架回归（T0 资产，跨变更持续成立） ──────────────────────────


def test_debug_yaml_no_keyerror() -> None:
    """debug.yaml 删除 CITATION_PLUGIN_SLOT 后 apply_config 不再抛 KeyError。"""
    from pathlib import Path

    from src.rag.prompts.builder import PromptBuilder
    from src.rag.prompts.modules import get_shared_modules

    builder = PromptBuilder()
    for m in get_shared_modules():
        builder.register(m)
    debug_yaml = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "rag"
        / "prompts"
        / "profiles"
        / "debug.yaml"
    )
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

    assert len(SHARED_MODULES) >= 9, f"shared 扫到 {len(SHARED_MODULES)} 个，疑似 pkgutil 扫描异常"
    assert len(_MODE_MODULES["normal"]) >= 3, "normal 扫描异常"
    assert len(_MODE_MODULES["discuss"]) >= 3, "discuss 扫描异常"


# ── 引用格式语义回归（T1 资产：保护 lean ref 确实在 prompt 里） ───


# 固定输入：非空 history 触发 {history} 注入路径，确保语义断言覆盖动态注入段。
# 与 scripts/dump_prompt_baseline.py 的输入一致（该脚本现留作手动 dump 调试工具）。
_HISTORY = "测试历史：用户问过微波光子学的基础问题。"


@pytest.mark.parametrize("mode", ["normal", "discuss"])
def test_prompt_has_lean_ref_format(mode: str) -> None:
    """build_prompt 输出含 lean ref 关键字——保护 T1 的 bind-by-id 引用格式确实落在 prompt 里。

    这是语义断言不是字节断言：未来想法 3 再改 prompt 时，若这些关键字消失会红 = 真信号
    （你确实改了 ref 格式相关，去确认是不是想要的）；关键字还在则说明 ref 格式未受波及。
    """
    from src.rag.prompts import CITATION_DEFAULT, build_prompt
    from src.rag.prompts.plugins import TOOL_DECISION_PLUGIN

    prompt = build_prompt(
        mode=mode,
        history=_HISTORY,
        citation_plugin=CITATION_DEFAULT,
        tool_decision_plugin=TOOL_DECISION_PLUGIN,
    )
    # lean ref 核心关键字（任一缺失 = bind-by-id 格式被破坏）
    assert "[source_id]" in prompt, f"{mode} prompt 缺 lean ref [source_id] 标记"
    assert "bind-by-id" in prompt, f"{mode} prompt 缺 bind-by-id 说明"
    assert "rag:<doc_id>" in prompt, f"{mode} prompt 缺 rag: source_id 前缀规则"
    assert "s2:<s2_paper_id>" in prompt, f"{mode} prompt 缺 s2: source_id 前缀规则"
    # 教 model 不自己写元信息（bind-by-id 核心约束）
    assert "系统按 source_id 自动" in prompt or "由系统按 source_id 自动填充" in prompt, (
        f"{mode} prompt 缺「元信息由系统填」约束"
    )


@pytest.mark.parametrize("mode", ["normal", "discuss"])
def test_prompt_translation_keeps_zh(mode: str) -> None:
    """translation 模式：CITATION_TRANSLATION 注入后 prompt 含 <zh> 标签规则。"""
    from src.rag.prompts import CITATION_TRANSLATION, build_prompt
    from src.rag.prompts.plugins import TOOL_DECISION_PLUGIN

    prompt = build_prompt(
        mode=mode,
        history=_HISTORY,
        citation_plugin=CITATION_TRANSLATION,
        tool_decision_plugin=TOOL_DECISION_PLUGIN,
    )
    assert "<zh>" in prompt, f"{mode} translation prompt 缺 <zh> 标签规则"
    # source_id 不翻译口径在（translation 规则里）
    assert "不翻译" in prompt, f"{mode} translation prompt 缺「不翻译 id」口径"

