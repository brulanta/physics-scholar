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

新增的语义断言（T1 资产，Point 2 后收紧）：
- build_prompt 输出含 lean ref「格式」关键字（[source_id] / rag:<doc_id> / s2: 前缀规则）。
  不再断言「告知 LLM 机械化」的指令文本（bind-by-id 说明 / 系统按 source_id 自动填元信息）——
  Point 2 起不向 LLM 注入隐藏机制描述，改由 probe 验行为。
- translation 模式 <zh> 保留、翻译规则（仅翻译支撑片段）在。
"""
from __future__ import annotations

import pytest


# ── 框架回归（T0 资产，跨变更持续成立） ──────────────────────────


def test_debug_yaml_no_keyerror() -> None:
    """debug.yaml 删除 CITATION_PLUGIN_SLOT 后 apply_config 不再抛 KeyError。"""
    import copy
    from pathlib import Path

    from src.rag.prompts.builder import PromptBuilder
    from src.rag.prompts.modules import get_shared_modules

    builder = PromptBuilder()
    for m in get_shared_modules():
        builder.register(copy.copy(m))  # 拷贝注册：apply 原地改写不污染 pkgutil 全局
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


# ── v2：apply_config 的 content 字段扩展（dev GUI 内容编辑/新增模块的底层） ──


def _tmp_yaml(tmp_path, text: str) -> str:
    p = tmp_path / "profile.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_apply_config_content_override(tmp_path) -> None:
    """已注册模块条目带 content → 覆盖内容；占位符仍被注入（覆盖版走同一 build 逻辑）。"""
    import copy

    from src.rag.prompts.builder import PromptBuilder
    from src.rag.prompts.modules import get_mode_modules, get_shared_modules

    b = PromptBuilder()
    for m in get_shared_modules() + get_mode_modules("normal"):
        b.register(copy.copy(m))  # 拷贝注册：apply 原地改写不污染 pkgutil 全局
    cfg = _tmp_yaml(
        tmp_path,
        "modules:\n"
        "  - name: ROLE_BASE\n"
        "    enabled: true\n"
        "    order: 10\n"
        "    content: |\n"
        "      覆盖版身份 {history}\n",
    )
    b.apply_config(cfg)
    b.set_vars(history="HIST")
    out = b.build()
    assert "覆盖版身份 HIST" in out
    assert "## Core Identity" not in out  # 原版被覆盖


def test_apply_config_yaml_defined_module(tmp_path) -> None:
    """未注册名带 content → 动态注册 yaml 定义模块（order 生效）。"""
    import copy

    from src.rag.prompts.builder import PromptBuilder
    from src.rag.prompts.modules import get_mode_modules, get_shared_modules

    b = PromptBuilder()
    for m in get_shared_modules() + get_mode_modules("normal"):
        b.register(copy.copy(m))
    cfg = _tmp_yaml(
        tmp_path,
        "modules:\n"
        "  - name: ROLE_BASE\n"
        "    enabled: true\n"
        "    order: 20\n"
        "  - name: MY_YAML_MOD\n"
        "    enabled: true\n"
        "    order: 5\n"
        "    content: |\n"
        "      新模块内容\n",
    )
    b.apply_config(cfg)
    out = b.build()
    assert "新模块内容" in out
    assert out.index("新模块内容") < out.index("## Core Identity")  # order 5 < 20


def test_apply_config_unknown_name_still_keyerror(tmp_path) -> None:
    """未注册名且无 content → 仍 KeyError（疏漏/漂移名不做静默宽容——旧语义保留）。"""
    import pytest as _pytest

    from src.rag.prompts.builder import PromptBuilder

    b = PromptBuilder()
    cfg = _tmp_yaml(tmp_path, "modules:\n  - name: NO_SUCH\n    enabled: true\n")
    with _pytest.raises(KeyError, match="NO_SUCH"):
        b.apply_config(cfg)


def test_apply_config_no_content_key_zero_change(tmp_path) -> None:
    """现有 yaml（无 content 键）行为零变化：enabled/order 语义与旧版一致。"""
    import copy

    from src.rag.prompts.builder import PromptBuilder
    from src.rag.prompts.modules import get_mode_modules, get_shared_modules

    b = PromptBuilder()
    for m in get_shared_modules() + get_mode_modules("normal"):
        b.register(copy.copy(m))
    cfg = _tmp_yaml(
        tmp_path,
        "modules:\n"
        "  - name: ROLE_BASE\n"
        "    enabled: true\n"
        "    order: 10\n",
    )
    b.apply_config(cfg)
    m = b._modules["ROLE_BASE"]
    assert m.enabled is True
    assert m.order == 10
    assert "## Core Identity" in m.content  # 内容未被触碰


# ── 引用格式语义回归（T1 资产：保护 lean ref 确实在 prompt 里） ───


# 固定输入：非空 history 触发 {history} 注入路径，确保语义断言覆盖动态注入段。
# 与 scripts/dump_prompt_baseline.py 的输入一致（该脚本现留作手动 dump 调试工具）。
_HISTORY = "测试历史：用户问过微波光子学的基础问题。"


@pytest.mark.parametrize("mode", ["normal", "discuss"])
def test_prompt_has_lean_ref_format(mode: str) -> None:
    """build_prompt 输出含 lean ref 格式关键字——保护 bind-by-id 的 lean ref 形态确实落在 prompt 里。

    语义断言：只守 lean ref 的「格式」（source_id 前缀规则 + [source_id] 标记），不守
    「告知 LLM 机械化」的指令文本（Point 2：不再注入「系统按 source_id 自动填元信息」这类
    隐藏机制描述，改 probe 验行为）。未来想法 3 再改 prompt 时，若格式关键字消失会红 = 真信号。
    """
    from src.rag.prompts import CITATION_DEFAULT, build_prompt
    from src.rag.prompts.plugins import TOOL_DECISION_PLUGIN

    prompt = build_prompt(
        mode=mode,
        history=_HISTORY,
        citation_plugin=CITATION_DEFAULT,
        tool_decision_plugin=TOOL_DECISION_PLUGIN,
    )
    # lean ref 格式关键字（source_id 前缀规则 + 标记）——格式未变，仍须在
    assert "[source_id]" in prompt, f"{mode} prompt 缺 lean ref [source_id] 标记"
    assert "rag:<doc_id>" in prompt, f"{mode} prompt 缺 rag: source_id 前缀规则"
    assert "s2:<s2_paper_id>" in prompt, f"{mode} prompt 缺 s2: source_id 前缀规则"


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
    # translation 规则在 prompt 里（Point 2 后口径从「不翻译 X/Y/Z」精简为「仅翻译支撑片段」）
    assert "仅翻译支撑片段" in prompt or "支撑片段译文" in prompt, (
        f"{mode} translation prompt 缺翻译规则"
    )

