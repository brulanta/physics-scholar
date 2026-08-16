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
        assert set(m) == {
            "name", "source", "enabled", "order", "content",
            "deletable", "overridden", "default_content",
        }
        assert m["source"] in ("shared", "mode", "yaml")
        assert isinstance(m["enabled"], bool)
        assert isinstance(m["order"], int)
        assert isinstance(m["content"], str)
        assert isinstance(m["deletable"], bool)
        assert isinstance(m["overridden"], bool)

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

    # 本地参照：register + apply_config(真实 yaml) + 同款 vars。
    # ⚠ 必须拷贝注册——pkgutil 缓存是进程级共享引用，apply_config 原地改写会污染
    # 后续测试（test_v2_global_state_not_polluted 断言的就是这个不变量）
    import copy

    ref = PromptBuilder()
    for m in get_shared_modules() + get_mode_modules("normal"):
        ref.register(copy.copy(m))
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


# ── v2：内容覆盖 + yaml 定义模块（新增/删除） ────────────────


def _get_items(client, mode="normal"):
    mods = client.get(f"/api/dev/prompt/{mode}").json()["modules"]
    return mods, [{"name": m["name"], "enabled": m["enabled"]} for m in mods]


def test_v2_content_override_round_trip(client, tmp_profiles) -> None:
    """覆盖 .py 模块内容 → GET 报 overridden + default_content 保持 .py 原版；
    传回 default_content（恢复默认）→ 收敛，落盘无 content 键。"""
    _, items = _get_items(client)
    for it in items:
        if it["name"] == "ROLE_BASE":
            it["content"] = "## 覆盖版身份 {history}"
    r = client.post("/api/dev/prompt/save", json={"mode": "normal", "modules": items})
    assert r.status_code == 200

    mods = client.get("/api/dev/prompt/normal").json()["modules"]
    rb = next(m for m in mods if m["name"] == "ROLE_BASE")
    assert rb["overridden"] is True
    assert rb["content"] == "## 覆盖版身份 {history}"
    assert "## Core Identity" in rb["default_content"]  # .py 原版未被污染

    # 恢复默认 → 落盘无模块条目 content 键（逐行查，头注释里的 # content: 不算）
    items3 = [
        {"name": m["name"], "enabled": m["enabled"], "content": m["default_content"]}
        for m in mods
    ]
    assert client.post("/api/dev/prompt/save", json={"mode": "normal", "modules": items3}).status_code == 200
    lines = (tmp_profiles / "normal.yaml").read_text(encoding="utf-8").split("\n")
    cur = None
    for ln in lines:
        if ln.startswith("  - name:"):
            cur = ln
        assert not ln.startswith("    content:"), f"恢复默认后仍带 content 键: {cur}"
    mods3 = client.get("/api/dev/prompt/normal").json()["modules"]
    assert next(m for m in mods3 if m["name"] == "ROLE_BASE")["overridden"] is False


def test_v2_yaml_defined_module_lifecycle(client, tmp_profiles) -> None:
    """新增（未注册名+content）→ 生效 source=yaml/deletable=True；
    preview 含其内容；缺席保存 = 真删。"""
    _, items = _get_items(client)
    items.append({"name": "MY_YAML_MOD", "enabled": True, "content": "新模块内容\n第二行"})
    r = client.post("/api/dev/prompt/save", json={"mode": "normal", "modules": items})
    assert r.status_code == 200

    mods = client.get("/api/dev/prompt/normal").json()["modules"]
    my = next(m for m in mods if m["name"] == "MY_YAML_MOD")
    assert my["source"] == "yaml"
    assert my["deletable"] is True
    assert my["default_content"] is None

    r = client.post(
        "/api/dev/prompt/preview",
        json={
            "mode": "normal",
            "modules": [
                {"name": m["name"], "enabled": m["enabled"], "content": m["content"]}
                for m in mods
            ],
        },
    )
    assert r.status_code == 200
    assert "新模块内容" in r.json()["prompt"]

    # 缺席保存（不发 MY_YAML_MOD）→ 真删
    items2 = [
        {"name": m["name"], "enabled": m["enabled"]} for m in mods if m["name"] != "MY_YAML_MOD"
    ]
    assert client.post("/api/dev/prompt/save", json={"mode": "normal", "modules": items2}).status_code == 200
    mods2 = client.get("/api/dev/prompt/normal").json()["modules"]
    assert not any(m["name"] == "MY_YAML_MOD" for m in mods2)


def test_v2_new_module_requires_content(client) -> None:
    """未注册名不带 content → 400（区分于 .py 模块的未注册 400：文案带新模块指引）。"""
    r = client.post(
        "/api/dev/prompt/preview",
        json={"mode": "normal", "modules": [{"name": "NEW_ONE", "enabled": True}]},
    )
    assert r.status_code == 400
    assert "content" in r.json()["detail"]


def test_v2_bad_module_name_400(client) -> None:
    """新模块名不匹配 UPPER_SNAKE → 400。"""
    r = client.post(
        "/api/dev/prompt/preview",
        json={"mode": "normal", "modules": [{"name": "bad-name", "enabled": True, "content": "x"}]},
    )
    assert r.status_code == 400


def test_v2_unknown_placeholder_400(client) -> None:
    """content 含未知占位符（拼错等）→ 400 且 detail 列出占位符名——防 build 期 KeyError。"""
    r = client.post(
        "/api/dev/prompt/preview",
        json={
            "mode": "normal",
            "modules": [
                {"name": "ROLE_BASE", "enabled": True, "content": "用 {histroy} 拼错"},
                {"name": "ROLE_NORMAL_EXT", "enabled": True, "content": "用 {history} 正确"},
            ],
        },
    )
    assert r.status_code == 400
    assert "histroy" in r.json()["detail"]


def test_v2_py_module_absent_save_kept_disabled(client, tmp_profiles) -> None:
    """v2 语义：.py 模块缺席补禁用；yaml 模块缺席 = 真删（两者并存时的兜底）。"""
    _, items = _get_items(client)
    items.append({"name": "TMP_YAML_MOD", "enabled": True, "content": "临时模块"})
    assert client.post("/api/dev/prompt/save", json={"mode": "normal", "modules": items}).status_code == 200

    # 只发一个 .py 模块：其余 .py 补禁用，TMP_YAML_MOD 真删
    r = client.post(
        "/api/dev/prompt/save",
        json={"mode": "normal", "modules": [{"name": "ROLE_BASE", "enabled": True}]},
    )
    assert r.status_code == 200
    mods = client.get("/api/dev/prompt/normal").json()["modules"]
    assert not any(m["name"] == "TMP_YAML_MOD" for m in mods)
    rb = next(m for m in mods if m["name"] == "ROLE_BASE")
    assert rb["enabled"] is True
    others = [m for m in mods if m["name"] != "ROLE_BASE" and m["source"] != "yaml"]
    assert all(m["enabled"] is False for m in others)


def test_v2_global_state_not_polluted(client, tmp_profiles) -> None:
    """dry-run（preview/_apply_candidate）不得污染 pkgutil 进程级模块缓存——
    preview 改了 enabled/order/content 后，生产 build_prompt 状态不受影响。"""
    from src.rag.prompts.modules import get_shared_modules

    _, items = _get_items(client)
    for it in items:
        if it["name"] == "ROLE_BASE":
            it["content"] = "POLLUTION-TEST"
    r = client.post("/api/dev/prompt/preview", json={"mode": "normal", "modules": items})
    assert r.status_code == 200

    g = next(m for m in get_shared_modules() if m.name == "ROLE_BASE")
    assert "POLLUTION-TEST" not in g.content
    assert g.enabled is False  # pkgutil 注册默认 enabled=False（apply_config 才开）
