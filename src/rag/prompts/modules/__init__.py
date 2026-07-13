"""
模块注册入口 —— pkgutil 目录扫描

每个模块文件负责定义自己的 PromptModule 对象（导出名为 `module`）。
这里用 pkgutil 遍历 shared/normal/discuss 子包，自动收集所有 `module`，
供 builder.py 的 build_prompt() 调用。

新增模块：在对应子包目录下放一个 .py，导出 `module = PromptModule(...)` 即可，
无需改本文件（YAML 引用即生效）。

frozen 兼容：pkgutil.iter_modules 依赖模块 .py 文件落盘。physics_scholar.spec
把 src 作为 datas 打包（_MEIPASS/src/rag/prompts/modules/<sub>/下真有 .py），
故 frozen 下可正常扫描。若未来 spec 改为把 src 打进 PYZ，扫描会静默返回空 ——
_scan 里的「扫到 0 个即抛」断言是兜底，防止静默失效导致 prompt 变空。

新增 mode 子包：① 建 modules/<new>/ 子包（含 __init__.py）② _MODE_MODULES 加键
③ spec hiddenimports 加 src.rag.prompts.modules.<new>。
"""
from __future__ import annotations

import importlib
import pkgutil

from ..builder import PromptModule
from . import shared, normal, discuss


def _scan(pkg, pkg_name: str) -> list[PromptModule]:
    """遍历子包目录，收集每个模块文件导出的 `module`（PromptModule）。

    pkgutil.iter_modules 不保证顺序，模块顺序由 PromptModule.order 决定
    （build() 按 order 升序拼装）。扫到 0 个即抛 —— 防 pkgutil 静默失效。
    """
    out: list[PromptModule] = []
    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        m = importlib.import_module(f".{pkg_name}.{name}", __name__)
        mod = getattr(m, "module", None)
        if isinstance(mod, PromptModule):
            out.append(mod)
    if not out:
        raise RuntimeError(
            f"pkgutil 在 {pkg_name} 扫到 0 个模块，疑似扫描异常"
            f"（frozen 下检查 spec 是否仍把 src 作 datas 落盘）"
        )
    return out


# 共用模块：所有 mode 都注册
SHARED_MODULES: list[PromptModule] = _scan(shared, "shared")

# mode → 该 mode 专用模块。_MODE_MODULES 是 mode 枚举的唯一真相源。
_MODE_MODULES: dict[str, list[PromptModule]] = {
    "normal": _scan(normal, "normal"),
    "discuss": _scan(discuss, "discuss"),
}

# mode 列表（供 builder/routes 引用，数据驱动）
MODES = tuple(_MODE_MODULES.keys())


def get_shared_modules() -> list[PromptModule]:
    return SHARED_MODULES


def get_mode_modules(mode: str) -> list[PromptModule]:
    if mode not in _MODE_MODULES:
        raise ValueError(f"Unknown mode: '{mode}'. Must be one of {MODES}")
    return _MODE_MODULES[mode]
