# src/api/dev_routes.py
"""开发态调试路由组（dev-only）——Prompt 模块调控 GUI 的后端。

**铁门（双层）**：
1. main.py 仅在 `config.IS_DEV` 时 import 并挂载本 router（frozen 下路由不存在 = 404
   by construction，同 LangSmith dev-only 先例）；
2. router 级 `_dev_guard` dependency 再拦一道（保险带，可单测）。

frozen 下 profiles 落在 _MEIPASS（只读解压目录），写 yaml 本就无意义——路由不挂载，
此路径天然不可达。

**scope**：只管 `profiles/{normal,discuss}.yaml`（mode 的开关/顺序/内容覆盖）。debug.yaml
是手动调试 profile 非 mode，不在管辖内；子 agent prompt（subagent_prompt.py）刻意不走
模块系统，同样不在管辖内。

**v2 内容编辑语义**（builder.apply_config 的 content 字段扩展之上）：
- 已有模块条目带 `content` → 覆盖 .py 默认内容（GUI 内容编辑）
- 未注册名带 `content` → yaml 定义新模块（GUI 新增；不经 .py）
- yaml 定义模块从 yaml 删除 = 真删（GUI 删除）；.py 模块条目删除 = 回退 .py 默认+禁用
  （GUI 里 .py 模块「仅可禁用」）

**生效语义**：graph.py 每次 chat 重建 agent、build_prompt 每次读 profiles/{mode}.yaml，
故保存后下一问即生效（含内容改动），无需重启。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from src.config import IS_DEV
from src.rag.prompts.builder import PromptBuilder, PromptModule
from src.rag.prompts.modules import MODES, get_mode_modules, get_shared_modules
from src.rag.prompts.plugins import CITATION_DEFAULT, TOOL_DECISION_PLUGIN

router = APIRouter()

# profiles 目录（与 build_prompt 的查找逻辑同源：builder.py 按 __file__ 定位）
_PROFILES_DIR = Path(__file__).resolve().parents[1] / "rag" / "prompts" / "profiles"

# 预览时占位符填的可见 marker——防空 history 静默隐藏含占位符模块的内容
_PREVIEW_HISTORY = "(预览：此处注入对话历史)"
_PREVIEW_VARS = {
    "history": _PREVIEW_HISTORY,
    "citation_plugin": CITATION_DEFAULT,
    "tool_decision_plugin": TOOL_DECISION_PLUGIN,
}

# 模块名格式（对齐既有命名约定 UPPER_SNAKE；防 yaml 键/注入层面的怪名字）
_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _dev_guard() -> None:
    """router 级保险带：非 dev 环境一律 404（正常 frozen 下路由根本没挂载）。"""
    if not IS_DEV:
        raise HTTPException(status_code=404, detail="Not Found")


class ModuleItem(BaseModel):
    name: str
    enabled: bool
    # v2：内容（不传=None=不改）。已注册模块传 = 覆盖 .py 默认；未注册名传 = yaml 定义新模块
    content: str | None = None


class PromptConfigRequest(BaseModel):
    # mode 真相源 = modules/_MODE_MODULES（与 routes.AskRequest.mode 注释同口径）
    mode: Literal["normal", "discuss"]
    modules: list[ModuleItem]

    @field_validator("modules")
    @classmethod
    def _no_duplicate_names(cls, v: list[ModuleItem]) -> list[ModuleItem]:
        """名字重复在 pydantic 层即拒（422），不必等到业务校验。"""
        names = [it.name for it in v]
        if len(names) != len(set(names)):
            from collections import Counter

            dup = next(n for n, c in Counter(names).items() if c > 1)
            raise ValueError(f"模块重复出现: {dup}")
        return v


# ── 内部工具 ──────────────────────────────────────────────


def _py_modules(mode: str) -> list:
    """经 pkgutil 注册的模块（.py 定义）：shared + mode 专属。

    ⚠ 返回的是 pkgutil 扫描缓存里的**同一批对象引用**——builder.apply_config 会原地
    改写它们的 content/enabled/order（yaml 覆盖直接污染「.py 默认值」）。凡需要
    「.py 原始值」的场合（GET 的 default_content/overridden 判定、save 的内容收敛）
    必须先深拷贝快照、再 apply_config。_snapshot_py_defaults() 是配套工具。
    """
    return get_shared_modules() + get_mode_modules(mode)


def _snapshot_py_defaults(mode: str) -> dict[str, PromptModule]:
    """深拷贝快照 .py 模块的原始状态（apply_config 之前调用）。"""
    import copy

    return {m.name: copy.copy(m) for m in _py_modules(mode)}


def _profile_path(mode: str) -> Path:
    return _PROFILES_DIR / f"{mode}.yaml"


def _validate_mode(mode: str) -> None:
    if mode not in MODES:
        raise HTTPException(status_code=404, detail=f"未知 mode: {mode}")


def _extract_placeholders(content: str) -> set[str]:
    """复用 builder 的探测（含 ValueError 降级——字面 { 当无占位符）。"""
    from src.rag.prompts.builder import _extract_placeholders as _ep

    return _ep(content)


def _validate_items(mode: str, items: list[ModuleItem]) -> None:
    """校验候选配置。不信任客户端，preview/save 共用。

    - 未注册名必须带 content（= yaml 定义新模块），否则 400
    - 新模块名须匹配 UPPER_SNAKE，且不得撞 .py 已注册名
    - content 里的 {占位符} 必须全部在已知变量集内——防 build 期 KeyError 崩溃
    """
    py_names = {m.name for m in _py_modules(mode)}
    known_vars = set(_PREVIEW_VARS.keys())
    for item in items:
        if item.name not in py_names:
            if item.content is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"未注册的模块: {item.name}（新模块必须提供 content）",
                )
            if not _NAME_RE.match(item.name):
                raise HTTPException(
                    status_code=400,
                    detail=f"新模块名 {item.name} 不合格式（须 UPPER_SNAKE，如 MY_MODULE）",
                )
        if item.content is not None:
            unknown = _extract_placeholders(item.content) - known_vars
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"模块 {item.name} 含未知占位符: {sorted(unknown)}"
                        f"（可用: {sorted(known_vars)}）"
                    ),
                )


def _apply_candidate(mode: str, items: list[ModuleItem]) -> PromptBuilder:
    """把候选配置套到 fresh builder 上（列表序即真相，order 重编号 (i+1)*10）。
    dry-run 专用，不碰任何文件。v2：content 非 None 时覆盖/注册。

    ⚠ 注册的是 .py 模块对象的**深拷贝**（pkgutil 缓存是进程级共享引用，toggle/
    set_order/set_content 原地改写会跨请求泄漏 dry-run 状态——生产 build_prompt 每次
    apply_config 三字段全量覆盖故无感，这里防御性隔离）。
    """
    import copy

    builder = PromptBuilder()
    for m in _py_modules(mode):
        builder.register(copy.copy(m))
    for i, item in enumerate(items):
        if item.name not in builder._modules:
            # v2 校验已保证：到这里的新名字必带 content
            builder.register(
                PromptModule(
                    name=item.name, content=item.content, enabled=item.enabled, order=(i + 1) * 10
                )
            )
        else:
            builder.toggle(item.name, item.enabled).set_order(item.name, (i + 1) * 10)
            if item.content is not None:
                builder.set_content(item.name, item.content)
    builder.set_vars(**_PREVIEW_VARS)
    return builder


def _yaml_block(s: str, indent: str = "      ") -> str:
    """content 值渲染成 yaml 标量（多行用 literal block，单行用双引号标量）。

    返回值以 "|" 或 '"' 开头，调用方拼接在 "content: " 之后；literal 块的续行
    已带 indent 前缀（yaml 要求块内容比键多缩进）。
    """
    if "\n" not in s.rstrip("\n"):
        # 单行：双引号标量（json.dumps 产合法 yaml 双引号标量，转义最稳）
        import json

        return json.dumps(s, ensure_ascii=False)
    # 多行：literal block。PyYAML 语义实测：| 对物理末行不追加换行（源块末行即内容末行），
    # |- 是 strip 一个尾换行。故：带尾换行 → rstrip 后靠 |- 不对——直接用 | + 物理末行；
    # 简化等价实现：无尾换行用 |（内容原样），带尾换行用 | 并在末行后加一个物理空行不
    # 可行（会成内容空行）——改用「无尾换行 → |；带尾换行 → rstrip 后 |- 的反转」不可靠。
    # 最稳做法：所有多行内容先统一 rstrip("\n")，chomp 用 |-；带尾换行的差异（恰好一个
    # 尾换行）由 GUI 层约定「模块内容尾换行无语义」吸收（build join 不受影响）。
    lines = s.rstrip("\n").split("\n")
    out = ["|-"]
    for ln in lines:
        out.append(indent + ln if ln else "")
    return "\n".join(out)


def _render_yaml(mode: str, items: list[dict]) -> str:
    """手写渲染 profile yaml（yaml.dump 会丢头注释、重排 key，故手写）。

    items: [{name, enabled, content}] 全量有序列表；content=None 不写该键。
    order 按序重编号 (i+1)*10，与 GUI 屏幕列表严格镜像、天然无 order 碰撞。
    """
    lines = [
        f"# {mode.capitalize()}模式的模块配置（由 Prompt 调控 GUI 生成）",
        "# enabled: false 可以临时关闭某个模块（调试用）",
        "# order: 控制拼装顺序，数字越小越靠前",
        "# content: 可选，覆盖 .py 默认内容（yaml 定义模块必带）",
        "",
        "modules:",
    ]
    for i, it in enumerate(items):
        lines.append(f"  - name: {it['name']}")
        lines.append(f"    enabled: {'true' if it['enabled'] else 'false'}")
        if it.get("content") is not None:
            lines.append(f"    content: {_yaml_block(it['content'])}")
        lines.append(f"    order: {(i + 1) * 10}")
    return "\n".join(lines) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    """同目录 tmp + os.replace 原子替换——graph.py 每请求 open()，只见旧或新、不见半个。"""
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── 路由 ──────────────────────────────────────────────────


@router.get("/prompt/{mode}", dependencies=[Depends(_dev_guard)])
async def get_prompt_config(mode: str):
    """模块清单 + 当前 yaml 生效状态 + 内容（生效版 + .py 默认版）+ 覆盖/来源标记。

    关键：enabled/order/content 必须在 apply_config(真实 yaml) **之后**读——
    post-apply 读才能反映 yaml 覆盖与新定义模块。
    """
    _validate_mode(mode)
    import copy

    py_defaults = _snapshot_py_defaults(mode)  # 先快照原始值（apply_config 会原地改写）
    builder = PromptBuilder()
    for m in _py_modules(mode):
        builder.register(copy.copy(m))  # 深拷贝注册：apply 原地改写不污染 pkgutil 全局
    path = _profile_path(mode)
    if path.exists():
        builder.apply_config(str(path))

    py_modules = py_defaults
    shared_names = {m.name for m in get_shared_modules()}
    modules = []
    for m in sorted(builder._modules.values(), key=lambda x: x.order):
        py = py_modules.get(m.name)
        modules.append(
            {
                "name": m.name,
                "source": (
                    "yaml" if py is None else ("shared" if m.name in shared_names else "mode")
                ),
                # yaml 定义模块可删；.py 模块只能禁用（删条目=回退默认+禁用）
                "deletable": py is None,
                "overridden": py is not None and py.content != m.content,
                "enabled": m.enabled,
                "order": m.order,
                "content": m.content,  # 生效版（可能是 yaml 覆盖后的）
                "default_content": py.content if py is not None else None,  # .py 原版（恢复默认用）
            }
        )
    return {"mode": mode, "modules": modules}


@router.post("/prompt/preview", dependencies=[Depends(_dev_guard)])
async def preview_prompt(req: PromptConfigRequest):
    """dry-run 拼装：不碰任何文件。注意是结构预览（占位符填 marker），非逐字节生产 prompt。"""
    _validate_mode(req.mode)
    _validate_items(req.mode, req.modules)
    prompt = _apply_candidate(req.mode, req.modules).build()
    return {
        "prompt": prompt,
        "char_count": len(prompt),
        "active_count": sum(1 for it in req.modules if it.enabled),
    }


@router.post("/prompt/save", dependencies=[Depends(_dev_guard)])
async def save_prompt_config(req: PromptConfigRequest):
    """校验 → 手写渲染 yaml → 写前 sanity 门（重解析回放须与 preview 一致）→ 原子落盘。

    v2 增量：content 字段（覆盖/新模块）；内容等于 .py 默认时自动省略 content 键
    （「恢复默认」= 传回 .py 原文，落盘自动收敛，不留冗余覆盖）。
    """
    _validate_mode(req.mode)
    _validate_items(req.mode, req.modules)

    # 快照 .py 原始值：内容收敛比较用（直接引用会被 apply_config/此前保存污染）
    py_defaults = _snapshot_py_defaults(req.mode)
    import copy  # sanity 回放的注册也用深拷贝（见 _apply_candidate 注释）

    # 候选表：GUI 传的序为准；未传到的 .py 模块按「缺席=禁用」补到末尾
    # （yaml 定义模块缺席 = 真删，不补）。正常 GUI 总发全量；此兜底防 curl 漏发静默丢模块。
    by_name = {it.name: it for it in req.modules}
    ordered: list[dict] = [
        {"name": it.name, "enabled": it.enabled, "content": it.content} for it in req.modules
    ]
    for m in _py_modules(req.mode):
        if m.name not in by_name:
            ordered.append({"name": m.name, "enabled": False, "content": None})

    # 内容等于 .py 默认 → 省略 content 键（恢复默认的收敛路径）
    for entry in ordered:
        py = py_defaults.get(entry["name"])
        if entry["content"] is not None and py is not None and entry["content"] == py.content:
            entry["content"] = None

    text = _render_yaml(req.mode, ordered)

    # 写前 sanity 门：渲染文本重解析 → fresh builder 回放，须不抛且 build 输出与
    # 候选配置的 build 一致。任何不一致都不碰文件（500）。
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=f".{req.mode}.yaml", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        sanity_builder = PromptBuilder()
        for m in _py_modules(req.mode):
            sanity_builder.register(copy.copy(m))
        # 复用 apply_config 语义（写出去的 yaml 就是给 apply_config 读的）
        sanity_builder.apply_config(tmp)
        sanity_builder.set_vars(**_PREVIEW_VARS)
        candidate_output = _apply_candidate(
            req.mode,
            [
                ModuleItem(name=e["name"], enabled=e["enabled"], content=e["content"])
                for e in ordered
            ],
        ).build()
        if sanity_builder.build() != candidate_output:
            raise ValueError("sanity 回放输出与候选配置不一致")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"写前校验失败，未落盘: {e}"
        )
    finally:
        os.unlink(tmp)

    path = _profile_path(req.mode)
    _atomic_write(path, text)
    # 仓库相对路径（展示用）；不在仓库内（如测试 monkeypatch 到 tmp）时退回绝对路径
    repo_root = Path(__file__).resolve().parents[2]
    try:
        shown = str(path.relative_to(repo_root))
    except ValueError:
        shown = str(path)
    return {
        "success": True,
        "message": "已保存，下一问生效（无需重启）",
        "path": shown,
    }
