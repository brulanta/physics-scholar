"""
PromptBuilder — 模块化prompt拼装引擎

使用方式：
    from prompts import build_prompt, CITATION_TRANSLATION

    system_prompt = build_prompt(
        mode="normal",           # "normal" | "discuss"
        history="...",
        citation_plugin=CITATION_TRANSLATION,  # 或 CITATION_DEFAULT
    )
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal
import yaml
import os

# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass
class PromptModule:
    name: str
    content: str
    enabled: bool = False
    order: int = 0


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #


class PromptBuilder:
    def __init__(self):
        self._modules: dict[str, PromptModule] = {}
        self._injections: dict[str, dict] = {}  # 新增

    # ── 注册 ──────────────────────────────────────────────────────────────── #

    def register(self, module: PromptModule) -> "PromptBuilder":
        self._modules[module.name] = module
        return self

    def register_many(self, *modules: PromptModule) -> "PromptBuilder":
        for m in modules:
            self.register(m)
        return self

    # ── 开关 ──────────────────────────────────────────────────────────────── #

    def enable(self, *names: str) -> "PromptBuilder":
        for name in names:
            self._get(name).enabled = True
        return self

    def disable(self, *names: str) -> "PromptBuilder":
        for name in names:
            self._get(name).enabled = False
        return self

    def toggle(self, name: str, enabled: bool) -> "PromptBuilder":
        self._get(name).enabled = enabled
        return self

    # ── 顺序 ──────────────────────────────────────────────────────────────── #

    def set_order(self, name: str, order: int) -> "PromptBuilder":
        self._get(name).order = order
        return self

    # ── 变量注入 ──────────────────────────────────────────────────────────── #

    def inject(self, name: str, **kwargs) -> "PromptBuilder":
        """对指定模块做变量替换，例如注入 {history}"""
        module = self._get(name)
        # 不改原对象，存到一个临时的替换表里
        self._injections[name] = kwargs
        return self

    # ── 从yaml配置批量应用 ────────────────────────────────────────────────── #

    def apply_config(self, config_path: str) -> "PromptBuilder":
        """
        从yaml文件读取模块开关和顺序配置，应用到已注册的模块上。
        yaml格式见 profiles/ 目录。
        """
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        for item in config.get("modules", []):
            name = item["name"]

            if name not in self._modules:
                raise KeyError(f"Unknown module in yaml: {name}")

            module = self._modules[name]

            # 只要出现在yaml，就自动启用
            module.enabled = item.get("enabled", True)

            if "order" in item:
                module.order = item["order"]

        return self

    # ── 构建 ──────────────────────────────────────────────────────────────── #

    def build(self, separator: str = "\n\n---\n\n") -> str:
        active = [m for m in self._modules.values() if m.enabled]
        active.sort(key=lambda m: m.order)
        parts = []
        for m in active:
            content = m.content
            if m.name in self._injections:
                content = content.format(**self._injections[m.name])
            parts.append(content)
        return separator.join(parts)

    # ── 调试 ──────────────────────────────────────────────────────────────── #

    def status(self) -> str:
        """打印当前所有模块的状态，用于调试"""
        lines = ["[PromptBuilder status]"]
        sorted_modules = sorted(self._modules.values(), key=lambda m: m.order)
        for m in sorted_modules:
            status = "✓" if m.enabled else "✗"
            lines.append(f"  {status} [{m.order:02d}] {m.name}")
        return "\n".join(lines)

    # ── 内部 ──────────────────────────────────────────────────────────────── #

    def _get(self, name: str) -> PromptModule:
        if name not in self._modules:
            raise KeyError(f"Module '{name}' not registered")
        return self._modules[name]


# --------------------------------------------------------------------------- #
# 工厂函数：一行调用构建完整prompt
# --------------------------------------------------------------------------- #


def build_prompt(
    mode: Literal["normal", "discuss"],
    history: str = "",
    citation_plugin: str = "",
    debug: bool = False,
) -> str:
    """
    构建完整的system prompt。

    Args:
        mode:           "normal" 或 "discuss"
        history:        对话历史字符串，注入到 CONTEXT_BLOCK
        citation_plugin: 引用插件文本，注入到 CITATION_PLUGIN_SLOT
                         传入 CITATION_DEFAULT 或 CITATION_TRANSLATION
        debug:          True时打印模块状态

    Returns:
        拼装好的system prompt字符串
    """
    from .modules import get_shared_modules, get_mode_modules

    builder = PromptBuilder()

    # 注册共用模块
    for module in get_shared_modules():
        builder.register(module)

    # 注册模式专用模块
    for module in get_mode_modules(mode):
        builder.register(module)

    # 从profile配置应用开关和顺序
    profile_path = os.path.join(
        os.path.dirname(__file__),
        "profiles",
        f"{mode}.yaml",
    )
    if os.path.exists(profile_path):
        builder.apply_config(profile_path)

    # 注入动态内容
    builder.inject("CONTEXT_BLOCK", history=history)
    builder.inject("CITATION_FORMAT", citation_plugin=citation_plugin)

    if debug:
        print(builder.status())

    return builder.build()
