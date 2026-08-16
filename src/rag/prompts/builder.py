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
from string import Formatter
import copy
import yaml
import os

from .plugins import TOOL_DECISION_PLUGIN

# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass
class PromptModule:
    name: str
    content: str
    enabled: bool = False
    order: int = 0


def _extract_placeholders(content: str) -> set[str]:
    """提取字符串里所有合法的 {占位符} 字段名。

    用 string.Formatter.parse 扫描。对含字面 { 但非合法占位符的文本，
    parse 会抛 ValueError —— 此时降级为空集（当作无占位符，保留原样），
    避免未来有人在模块里写字面 { 时炸 build。
    """
    try:
        return {fname for _, fname, _, _ in Formatter().parse(content) if fname}
    except ValueError:
        return set()


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #


class PromptBuilder:
    def __init__(self):
        self._modules: dict[str, PromptModule] = {}
        self._vars: dict[str, str] = {}  # 统一变量池，build 时按各模块 content 的占位符自动取用

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

    def set_content(self, name: str, content: str) -> "PromptBuilder":
        """覆盖模块内容（dev GUI 内容编辑的底层；名字须已注册）。"""
        self._get(name).content = content
        return self

    # ── 变量注入 ──────────────────────────────────────────────────────────── #

    def set_vars(self, **kwargs) -> "PromptBuilder":
        """设置全局变量池。build 时按各模块 content 里出现的 {占位符} 自动取用，
        只替换该模块实际含有的占位符。新增动态变量只需在这里传入即可，无需按模块名逐个 inject。"""
        self._vars.update(kwargs)
        return self

    # ── 从yaml配置批量应用 ────────────────────────────────────────────────── #

    def apply_config(self, config_path: str) -> "PromptBuilder":
        """
        从yaml文件读取模块开关和顺序配置，应用到已注册的模块上。
        yaml格式见 profiles/ 目录。

        条目可选 `content` 字段（dev GUI 内容编辑，v2）：
        - 已注册模块带 content → 覆盖该模块内容（yaml 是运行时真相）
        - 未注册名带 content → 动态注册「yaml 定义模块」（不经 .py，GUI 新增模块）
        - 未注册名且无 content → 仍是 KeyError（与旧语义一致——疏漏/漂移名不做静默宽容）
        """
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        for item in config.get("modules", []):
            name = item["name"]

            if name not in self._modules:
                if "content" in item:
                    # yaml 定义模块：动态注册（order 默认放最末，通常 yaml 会显式给 order）
                    self.register(
                        PromptModule(
                            name=name,
                            content=item["content"],
                            enabled=item.get("enabled", True),
                            order=item.get("order", 1000),
                        )
                    )
                    continue
                raise KeyError(f"Unknown module in yaml: {name}")

            module = self._modules[name]

            # 只要出现在yaml，就自动启用
            module.enabled = item.get("enabled", True)

            if "order" in item:
                module.order = item["order"]

            if "content" in item:
                module.content = item["content"]

        return self

    # ── 构建 ──────────────────────────────────────────────────────────────── #

    def build(self, separator: str = "\n\n---\n\n") -> str:
        active = [m for m in self._modules.values() if m.enabled]
        active.sort(key=lambda m: m.order)
        parts = []
        for m in active:
            content = m.content
            if self._vars:
                # 自动探测该模块 content 里的 {占位符}，只传它实际需要的变量
                needed = _extract_placeholders(content)
                if needed:
                    subset = {k: self._vars[k] for k in needed if k in self._vars}
                    if subset:
                        content = content.format(**subset)
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
    mode: str,
    history: str = "",
    citation_plugin: str = "",
    tool_decision_plugin: str = TOOL_DECISION_PLUGIN,
    debug: bool = False,
) -> str:
    """
    构建完整的system prompt。

    Args:
        mode:           mode 名，须在 modules 的 _MODE_MODULES 中注册（"normal"/"discuss"）
        history:        对话历史字符串，注入到含 {history} 占位符的模块（CONTEXT_BLOCK）
        citation_plugin: 引用插件文本，注入到含 {citation_plugin} 占位符的模块（CITATION_FORMAT）
                         传入 CITATION_DEFAULT 或 CITATION_TRANSLATION
        tool_decision_plugin: 工具调用申请书骨架，注入到含 {tool_decision_plugin} 占位符的
                         模块（THINKING_NORMAL/THINKING_DISCUSS）。默认值=TOOL_DECISION_PLUGIN 常量，
                         保证不传时（如兼容层无参调用）行为与改造前 f-string 内嵌一致。
        debug:          True时打印模块状态

    Returns:
        拼装好的system prompt字符串
    """
    from .modules import get_shared_modules, get_mode_modules

    builder = PromptBuilder()

    # 注册共用模块（拷贝注册：pkgutil 缓存是进程级共享对象，apply_config 的 yaml 覆盖
    # 会原地改写 content/enabled/order——共享注册会让 dev GUI 的 yaml 覆盖污染
    # 「.py 默认值」，跨请求泄漏 dry-run 状态）
    for module in get_shared_modules():
        builder.register(copy.copy(module))

    # 注册模式专用模块
    for module in get_mode_modules(mode):
        builder.register(copy.copy(module))

    # 从profile配置应用开关和顺序
    profile_path = os.path.join(
        os.path.dirname(__file__),
        "profiles",
        f"{mode}.yaml",
    )
    if os.path.exists(profile_path):
        builder.apply_config(profile_path)

    # 注入动态内容：统一变量池，build 时按各模块占位符自动取用
    builder.set_vars(
        history=history,
        citation_plugin=citation_plugin,
        tool_decision_plugin=tool_decision_plugin,
    )

    if debug:
        print(builder.status())

    return builder.build()
