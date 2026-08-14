# src/api/dev_routes.py
"""开发态调试路由组（dev-only）——Prompt 模块调控 GUI 的后端。

**铁门（双层）**：
1. main.py 仅在 `config.IS_DEV` 时 import 并挂载本 router（frozen 下路由不存在 = 404
   by construction，同 LangSmith dev-only 先例）；
2. router 级 `_dev_guard` dependency 再拦一道（保险带，可单测）。

frozen 下 profiles 落在 _MEIPASS（只读解压目录），写 yaml 本就无意义——路由不挂载，
此路径天然不可达。

**scope**：只管 `profiles/{normal,discuss}.yaml`（mode 的开关/顺序）。debug.yaml 是手动
调试 profile 非 mode，不在管辖内；子 agent prompt（subagent_prompt.py）刻意不走模块系统，
同样不在管辖内。模块**内容**编辑（.py）不归 GUI——内容只读透出供查看。

**生效语义**：graph.py 每次 chat 重建 agent、build_prompt 每次读 profiles/{mode}.yaml，
故保存后下一问即生效，无需重启。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from src.config import IS_DEV
from src.rag.prompts.builder import PromptBuilder
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


def _dev_guard() -> None:
    """router 级保险带：非 dev 环境一律 404（正常 frozen 下路由根本没挂载）。"""
    if not IS_DEV:
        raise HTTPException(status_code=404, detail="Not Found")


class ModuleItem(BaseModel):
    name: str
    enabled: bool


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


def _registered_modules(mode: str) -> list:
    """该 mode 的全部注册模块（shared + mode 专属），按注册顺序。"""
    return get_shared_modules() + get_mode_modules(mode)


def _profile_path(mode: str) -> Path:
    return _PROFILES_DIR / f"{mode}.yaml"


def _validate_mode(mode: str) -> None:
    if mode not in MODES:
        raise HTTPException(status_code=404, detail=f"未知 mode: {mode}")


def _validate_items(mode: str, items: list[ModuleItem]) -> None:
    """校验候选配置：名字须全注册、不得重复。不信任客户端，preview/save 共用。"""
    registered = {m.name for m in _registered_modules(mode)}
    seen: set[str] = set()
    for item in items:
        if item.name not in registered:
            raise HTTPException(
                status_code=400, detail=f"未注册的模块: {item.name}"
            )
        if item.name in seen:
            raise HTTPException(
                status_code=400, detail=f"模块重复出现: {item.name}"
            )
    # 漏模块不拦（保存时按「缺席=禁用」语义补 enabled:false，见 _render_yaml 调用处）


def _apply_candidate(mode: str, items: list[ModuleItem]) -> PromptBuilder:
    """把候选配置套到 fresh builder 上（列表序即真相，order 重编号 (i+1)*10）。
    dry-run 专用，不碰任何文件。"""
    builder = PromptBuilder()
    for m in _registered_modules(mode):
        builder.register(m)
    for i, item in enumerate(items):
        builder.toggle(item.name, item.enabled).set_order(item.name, (i + 1) * 10)
    builder.set_vars(**_PREVIEW_VARS)
    return builder


def _render_yaml(mode: str, items: list[tuple[str, bool]]) -> str:
    """手写渲染 profile yaml（yaml.dump 会丢头注释、重排 key，故手写）。

    items: (模块名, enabled) 全量有序列表；order 按序重编号 (i+1)*10，
    与 GUI 屏幕列表严格镜像、天然无 order 碰撞。
    """
    lines = [
        f"# {mode.capitalize()}模式的模块配置（由 Prompt 调控 GUI 生成）",
        "# enabled: false 可以临时关闭某个模块（调试用）",
        "# order: 控制拼装顺序，数字越小越靠前",
        "",
        "modules:",
    ]
    for i, (name, enabled) in enumerate(items):
        lines.append(f"  - name: {name}")
        lines.append(f"    enabled: {'true' if enabled else 'false'}")
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
    """模块清单 + 当前 yaml 生效状态 + 内容原文（只读查看用）。

    关键：enabled/order 必须在 apply_config(真实 yaml) **之后**读——apply_config 语义是
    「yaml 未列出 = 保持注册默认 enabled=False」，post-apply 读才能让未列入 yaml 的
    模块正确显示禁用 + 默认 order 落位。
    """
    _validate_mode(mode)
    builder = PromptBuilder()
    for m in _registered_modules(mode):
        builder.register(m)
    path = _profile_path(mode)
    if path.exists():
        builder.apply_config(str(path))

    shared_names = {m.name for m in get_shared_modules()}
    modules = []
    for m in sorted(builder._modules.values(), key=lambda x: x.order):
        modules.append(
            {
                "name": m.name,
                "source": "shared" if m.name in shared_names else "mode",
                "enabled": m.enabled,
                "order": m.order,
                "content": m.content,
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
    """校验 → 手写渲染 yaml → 写前 sanity 门（重解析回放须与 preview 一致）→ 原子落盘。"""
    _validate_mode(req.mode)
    _validate_items(req.mode, req.modules)

    # 候选表：GUI 传的序为准；未传到的注册模块按「缺席=禁用」补到末尾
    # （正常 GUI 总发全量；此兜底防直接 curl 漏发导致静默丢模块）
    by_name = {it.name: it.enabled for it in req.modules}
    ordered: list[tuple[str, bool]] = [(it.name, it.enabled) for it in req.modules]
    for m in _registered_modules(req.mode):
        if m.name not in by_name:
            ordered.append((m.name, False))

    text = _render_yaml(req.mode, ordered)

    # 写前 sanity 门：渲染文本重解析 → fresh builder 回放，须不抛且 build 输出与
    # 候选配置的 build 一致。任何不一致都不碰文件（500）。
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=f".{req.mode}.yaml", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        sanity_builder = PromptBuilder()
        for m in _registered_modules(req.mode):
            sanity_builder.register(m)
        # 复用 apply_config 语义（写出去的 yaml 就是给 apply_config 读的）
        sanity_builder.apply_config(tmp)
        sanity_builder.set_vars(**_PREVIEW_VARS)
        candidate_output = _apply_candidate(
            req.mode, [ModuleItem(name=n, enabled=e) for n, e in ordered]
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
