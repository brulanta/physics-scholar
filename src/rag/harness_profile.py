"""HarnessProfile —— 把 harness 约束轴收进一组**开发态**开关。

定位同 chunker/rerank 配置：**实验旋钮，不是用户设置**。纯开发态——不进 yaml、
不暴露前端、不进 reload_config。一套变体 = 一组取值；控制变量 = 只翻一个字段。
详见 plan/harness-ablation-plan.md（Part 2）。

本模块只落地**真安全轴**（改动全在 graph.py 局部，默认 `FLASH` = 引入 profile
之前的硬编码行为，零变化）：

  A  guard_mode      thinking_guard 强制力度
  C2 prefill_level   build_prefill 逼迫强度
                     （C1 协议契约——`<thinking>` + `[TOOL_LOOP]` 标记——的教学主体在
                      prompts/plugins.py，不在 prefill；故降级本字段不动摇契约、不碰流式层）
  E  final_prefill   final_answer 兜底 prefill 力度
  H  budget_n        工具调用预算

**未纳入本结构**（各自单独 PR / 属外层变量，见计划文档）：
  B  correction_tone 纠正话术语气——散落 graph + prompt 共 4 处，须同批改，属黄区
  F  serial_tools    串行裁剪——graph 裁剪 + 记账 + config.parallel_tool_calls 三处联动，属黄区
  G  原生思维链       在 llm/config 层（DEEPSEEK_EXTRA_BODY），作为实验外层变量记录

> 原则：不在此加「已声明但未接线」的字段——对实验 rig 是陷阱（翻了没反应）。
> B/F 落地时再补对应字段。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HarnessProfile:
    guard_mode: str = "strict"      # A: strict（驳回+纠正）| soft（只警告放行）| off（不检查）
    prefill_level: str = "full"     # C2: full（自我催眠+威胁）| light（中性提示）| minimal（仅 [start]）
    final_prefill: str = "full"     # E: full（终局威胁话术）| light（中性收尾）
    budget_n: int = 6               # H: 每轮用户回合的工具调用上限


# 现状基线：全 strict，等价于引入 profile 之前的硬编码行为。
# _prepare/build_agent 的默认值即此，保证生产路径零行为变化。
FLASH = HarnessProfile()

# 松绑候选（仅真安全轴部分）：guard 放行、prefill 降级。
# serial_tools / correction_tone 属黄区，本 PR 未接线，故不在此表达。
STRONG = HarnessProfile(
    guard_mode="soft",
    prefill_level="light",
    final_prefill="light",
    budget_n=6,
)

# ⑤ 探针（已跑，结论：**不安全，勿上生产**）：STRONG 的单变量再进一步——仅把
# prefill_level 从 light 降到 minimal，隔离「prefill 复读强度」这根轴。
# 实测（gemini-3.1-pro，Q03/Q18/Q19/Q20）：只留 RUNTIME_STATUS + [start]、砍掉那句
# 「按流程：先输出 <thinking>…」后，契约**崩了**——6 次 [guard:soft] 缺 <thinking>、
# marker_emit_rate 1.0→0.0、1 次真空答。即 plugins.py 的标记教学**独木难支**，
# light 里那句残留提醒是**承重**的。故 light(STRONG) 是 Pareto 最优地板：full 只多死重、
# minimal 破契约。本预置保留仅作复现/回归探针，非可发布档。详见 plan ⑤。
MINIMAL = HarnessProfile(
    guard_mode="soft",
    prefill_level="minimal",
    final_prefill="light",
    budget_n=6,
)

# 名字 → 预置，供开发态脚本（如 scripts/harness_probe.py）按 --profile 选择
PRESETS = {"FLASH": FLASH, "STRONG": STRONG, "MINIMAL": MINIMAL}
