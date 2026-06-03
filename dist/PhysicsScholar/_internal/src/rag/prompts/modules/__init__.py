"""
模块注册入口

每个模块文件负责定义自己的 PromptModule 对象。
这里统一收集，供 builder.py 的 build_prompt() 调用。
"""

from __future__ import annotations
from typing import Literal
from ..builder import PromptModule

# 共用模块
from .shared.role import ROLE_BASE
from .shared.context import CONTEXT_BLOCK
from .shared.tool_usage import TOOL_USAGE
from .shared.thinking_shared import THINKING_SHARED
from .shared.citation_format import CITATION_FORMAT
from .shared.code_rules import CODE_RULES
from .shared.constraints_shared import CONSTRAINTS_SHARED
from .shared.boundary import BOUNDARY_RULES
from .shared.language import LANGUAGE_POLICY
from .shared.output_format import OUTPUT_FORMAT_SHARED

# Normal模式专用
from .normal.role_ext import ROLE_NORMAL_EXT
from .normal.thinking import THINKING_NORMAL
from .normal.constraints import CONSTRAINTS_NORMAL

# Discuss模式专用
from .discuss.role_ext import ROLE_DISCUSS_EXT
from .discuss.thinking import THINKING_DISCUSS
from .discuss.constraints import CONSTRAINTS_DISCUSS


def wrap_module(name: str, module) -> PromptModule:
    if isinstance(module, PromptModule):
        return module
    return PromptModule(name=name, content=module)


_SHARED_MODULES: list[PromptModule] = [
    wrap_module("ROLE_BASE", ROLE_BASE),
    wrap_module("CONTEXT_BLOCK", CONTEXT_BLOCK),
    wrap_module("TOOL_USAGE", TOOL_USAGE),
    wrap_module("THINKING_SHARED", THINKING_SHARED),
    wrap_module("CITATION_FORMAT", CITATION_FORMAT),
    wrap_module("CODE_RULES", CODE_RULES),
    wrap_module("CONSTRAINTS_SHARED", CONSTRAINTS_SHARED),
    wrap_module("BOUNDARY_RULES", BOUNDARY_RULES),
    wrap_module("LANGUAGE_POLICY", LANGUAGE_POLICY),
    wrap_module("OUTPUT_FORMAT_SHARED", OUTPUT_FORMAT_SHARED),
]

_MODE_MODULES: dict[str, list[PromptModule]] = {
    "normal": [
        wrap_module("ROLE_NORMAL_EXT", ROLE_NORMAL_EXT),
        wrap_module("THINKING_NORMAL", THINKING_NORMAL),
        wrap_module("CONSTRAINTS_NORMAL", CONSTRAINTS_NORMAL),
    ],
    "discuss": [
        wrap_module("ROLE_DISCUSS_EXT", ROLE_DISCUSS_EXT),
        wrap_module("THINKING_DISCUSS", THINKING_DISCUSS),
        wrap_module("CONSTRAINTS_DISCUSS", CONSTRAINTS_DISCUSS),
    ],
}


def get_shared_modules() -> list[PromptModule]:
    return _SHARED_MODULES


def get_mode_modules(mode: Literal["normal", "discuss"]) -> list[PromptModule]:
    if mode not in _MODE_MODULES:
        raise ValueError(f"Unknown mode: '{mode}'. Must be 'normal' or 'discuss'")
    return _MODE_MODULES[mode]
