"""
test_dev_prompt_routes.py — dev-only Prompt 模块调控 GUI 的后端路由测试

覆盖（见 plan/prompt-tuner-gui-plan.md）：
- GET 模块清单形状 + 未知 mode 404
- preview 校验（未注册名/重复名 400）+ preview == builder 同状态拼装输出
- save round-trip（tmp 目录隔离，不碰真 yaml）：渲染可解析、order 重编号、
  禁用模块显式 enabled:false、无 .tmp 残留、再 GET 反映新态、写全量注册模块
- _dev_guard 冻结门（IS_DEV=False → 404）

测试挂裸 FastAPI app 只含 dev router（不 import src.main——其 lifespan 会 init_db/
拉 MCP）。save 系全部 monkeypatch _PROFILES_DIR 到 tmp_path。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.dev_routes as dr


@pytest.fixture
def client():
    from src.api.dev_routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api/dev")
    return TestClient(app)


@pytest.fixture
def tmp_profiles(tmp_path, monkeypatch):
    """把 profiles 目录指到 tmp_path 并预置一份 normal.yaml 副本（保真实初始状态）。"""
    import shutil
    from pathlib import Path

    src = Path(dr.__file__).resolve().parents[1] / "rag" / "prompts" / "profiles"
    for name in ("normal.yaml", "discuss.yaml"):
        shutil.copy(src / name, tmp_path / name)
    monkeypatch.setattr(dr, "_PROFILES_DIR", tmp_path)
    return tmp_path


# ── GET ────────────────────────────────────────────────────


def test_get_modules_shape(client) -> None:
    """GET normal：200、names ⊇ normal.yaml 名集、字段齐全；未知 mode 404。"""
    import yaml
    from pathlib import Path

    r = client.get("/api/dev/prompt/normal")
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "normal"

    real_yaml = (
        Path(dr.__file__).resolve().parents[1] / "rag" / "prompts" / "profiles" / "normal.yaml"
    )
    yaml_names = {m["name"] for m in yaml.safe_load(real_yaml.read_text(encoding="utf-8"))["modules"]}
    got_names = {m["name"] for m in data["modules"]}
    assert yaml_names <= got_names  # 注册全量 ⊇ yaml 已列

    for m in data["modules"]:
        assert set(m) == {"name", "source", "enabled", "order", "content"}
        assert m["source"] in ("shared", "mode")
        assert isinstance(m["enabled"], bool)
        assert isinstance(m["order"], int)
        assert isinstance(m["content"], str)

    assert client.get("/api/dev/prompt/bogus").status_code == 404


def test_get_reflects_yaml_state(client) -> None:
    """未列入 yaml 的注册模块须显示 enabled=False（post-apply_config 语义）。"""
    r = client.get("/api/dev/prompt/normal").json()
    by_name = {m["name"]: m for m in r["modules"]}
    # THINKING_SHARED 是注册但未列入 normal.yaml 的模块（T0 验证过的现状）
    if "THINKING_SHARED" in by_name:
        assert by_name["THINKING_SHARED"]["enabled"] is False


# ── preview 校验 ────────────────────────────────────────────


def test_preview_unknown_module_400(client) -> None:
    """未注册名 → 400 且 detail 含名字。"""
    r = client.post(
        "/api/dev/prompt/preview",
        json={"mode": "normal", "modules": [{"name": "NO_SUCH", "enabled": True}]},
    )
    assert r.status_code == 400
    assert "NO_SUCH" in r.json()["detail"]


def test_preview_duplicate_module_rejected(client) -> None:
    """重复名 → 422（pydantic field_validator 层即拒，msg 含名字）。"""
    item = {"name": "ROLE_BASE", "enabled": True}
    r = client.post(
        "/api/dev/prompt/preview",
        json={"mode": "normal", "modules": [item, dict(item)]},
    )
    assert r.status_code == 422
    assert "ROLE_BASE" in str(r.json())


def test_preview_equals_builder_output(client) -> None:
    """GET 原样回传的 preview == 本地 builder + 真实 yaml + 相同 vars 的 build 输出
    （证路由的候选拼装与 apply_config 生产语义一致）。"""
    from src.rag.prompts.builder import PromptBuilder
    from src.rag.prompts.modules import get_mode_modules, get_shared_modules
    from src.rag.prompts.plugins import CITATION_DEFAULT, TOOL_DECISION_PLUGIN

    mods = client.get("/api/dev/prompt/normal").json()["modules"]
    items = [{"name": m["name"], "enabled": m["enabled"]} for m in mods]
    r = client.post("/api/dev/prompt/preview", json={"mode": "normal", "modules": items})
    assert r.status_code == 200

    # 本地参照：register + apply_config(真实 yaml) + 同款 vars
    ref = PromptBuilder()
    for m in get_shared_modules() + get_mode_modules("normal"):
        ref.register(m)
    import pathlib

    ref.apply_config(
        str(pathlib.Path(dr.__file__).resolve().parents[1] / "rag" / "prompts" / "profiles" / "normal.yaml")
    )
    ref.set_vars(
        history="(预览：此处注入对话历史)",
        citation_plugin=CITATION_DEFAULT,
        tool_decision_plugin=TOOL_DECISION_PLUGIN,
    )
    assert r.json()["prompt"] == ref.build()
    assert r.json()["active_count"] == sum(1 for it in items if it["enabled"])


# ── save round-trip ─────────────────────────────────────────


def test_save_round_trip(client, tmp_profiles) -> None:
    """禁用一个 + 交换前两个 → 200 → 文件可解析、order=(i+1)*10、禁用显式 false、
    无 .tmp 残留、再 GET 反映新态。"""
    import yaml

    mods = client.get("/api/dev/prompt/normal").json()["modules"]
    items = [{"name": m["name"], "enabled": m["enabled"]} for m in mods]
    items[0], items[1] = items[1], items[0]
    for it in items:
        if it["name"] == "CODE_RULES":
            it["enabled"] = False

    r = client.post("/api/dev/prompt/save", json={"mode": "normal", "modules": items})
    assert r.status_code == 200, r.json()
    assert r.json()["success"] is True

    text = (tmp_profiles / "normal.yaml").read_text(encoding="utf-8")
    assert "由 Prompt 调控 GUI 生成" in text  # 头注释标注 machine-managed
    parsed = yaml.safe_load(text)["modules"]
    assert [m["order"] for m in parsed] == [(i + 1) * 10 for i in range(len(parsed))]
    code_rules = next(m for m in parsed if m["name"] == "CODE_RULES")
    assert code_rules["enabled"] is False
    assert not list(tmp_profiles.glob("*.tmp"))  # 原子写无残留

    # 再 GET 反映新态：首位交换生效、CODE_RULES 禁用
    after = client.get("/api/dev/prompt/normal").json()["modules"]
    assert after[0]["name"] == items[0]["name"]
    assert next(m for m in after if m["name"] == "CODE_RULES")["enabled"] is False


def test_save_writes_all_registered_modules(client, tmp_profiles) -> None:
    """文件列出全部注册模块（计数 == GET），防「缺席=禁用」静默丢模块。"""
    import yaml

    mods = client.get("/api/dev/prompt/normal").json()["modules"]
    items = [{"name": m["name"], "enabled": m["enabled"]} for m in mods]
    r = client.post("/api/dev/prompt/save", json={"mode": "normal", "modules": items})
    assert r.status_code == 200

    parsed = yaml.safe_load((tmp_profiles / "normal.yaml").read_text(encoding="utf-8"))["modules"]
    assert len(parsed) == len(mods)
    assert {m["name"] for m in parsed} == {m["name"] for m in mods}


def test_save_appends_missing_modules_as_disabled(client, tmp_profiles) -> None:
    """客户端漏发模块（直接 curl 场景）：按「缺席=禁用」补到末尾，不静默丢。"""
    import yaml

    r = client.post(
        "/api/dev/prompt/save",
        json={"mode": "normal", "modules": [{"name": "ROLE_BASE", "enabled": True}]},
    )
    assert r.status_code == 200
    parsed = yaml.safe_load((tmp_profiles / "normal.yaml").read_text(encoding="utf-8"))["modules"]
    assert len(parsed) > 1
    assert parsed[0] == {"name": "ROLE_BASE", "enabled": True, "order": 10}


def test_save_unknown_module_400_no_write(client, tmp_profiles) -> None:
    """save 校验失败不落盘。"""
    before = (tmp_profiles / "normal.yaml").read_text(encoding="utf-8")
    r = client.post(
        "/api/dev/prompt/save",
        json={"mode": "normal", "modules": [{"name": "NO_SUCH", "enabled": True}]},
    )
    assert r.status_code == 400
    assert (tmp_profiles / "normal.yaml").read_text(encoding="utf-8") == before


# ── 冻结门 ──────────────────────────────────────────────────


def test_dev_guard_frozen_404(monkeypatch) -> None:
    """_dev_guard 在 IS_DEV=False 下抛 404（main.py 条件挂载之外的保险带）。"""
    import fastapi
    from src.config import IS_DEV

    assert IS_DEV is True  # dev 测试环境前提
    monkeypatch.setattr(dr, "IS_DEV", False)
    with pytest.raises(fastapi.HTTPException) as exc:
        dr._dev_guard()
    assert exc.value.status_code == 404
    monkeypatch.setattr(dr, "IS_DEV", True)
    dr._dev_guard()  # 恢复后不抛
