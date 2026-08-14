# 想法 1 B 半：Prompt 模块调控 GUI（dev-only）✅ 完成（2026-08-14）

> 来源：`plan/top-level-progress-log.md` 【五】想法 1 / harness-ablation-plan A3。A 半（4 硬边界 + 漂移 bug）已于 T0 完成，YAML 已是可写配置；本 plan 落实 B 半——把 YAML 编辑可视化。落成后同步更新总纲（【五】想法 1 标 ✅、子计划索引加行）。

## 状态

- 2026-08-14：plan 落地，开工。
- 2026-08-14：**完成**。后端（`IS_DEV` 铁门 + `/api/dev` 三端点 + 原子写 + 写前 sanity 门）+ 前端（`PromptTunerModal.vue` 双栏 + `SettingsDrawer` dev-gated 入口）+ 10 例单测全绿；全量回归无新增失败（`test_backend` 4 error + `test_s2_tool` 1 fail 为 pre-existing 漂移，stash 对照确认；`test_rag_chain` collection error 亦为已知旧漂移）。
- **live 端到端验证通过**：GET→preview→save 幂等回传 200；live 服务器不重启，save 禁用 CODE_RULES 后 `build_prompt` 立即不含该模块、恢复 enabled 后立即回来——「下一问生效、无需重启」实证。验证后 yaml 已 git checkout 还原原状。
- 实现偏离 plan 两处：①重复模块名校验从路由层 400 改为 pydantic `field_validator` 层 422（更早拦截，测试同步改）；②save 响应的 path 字段对「profiles 目录不在仓库内」（测试 monkeypatch 场景）退回绝对路径。

## Context

prompt 拼装已是声明式引擎（`PromptBuilder`：pkgutil 扫描模块 + yaml 控制 enable/order + 占位符自动注入），但调整模块仍要手改 yaml + 回 IDE 看 .py 内容。做一个**开发态 GUI**：按 mode（normal/discuss）可视化勾选启用 + 上下移调序 + 实时预览拼装结果 + 模块内容只读查看，保存即落 yaml。

关键架构事实（已核实）：
- `graph.py:1225` 每次 chat 都重建 agent、`build_prompt` 每次读 `profiles/{mode}.yaml` → **保存后下一问即生效，无需重启**。
- `apply_config` 的语义：yaml 未列出的模块保持注册默认 `enabled=False` → GUI 必须**展示全部注册模块**，保存时**全部显式写**（enabled: true/false），「缺席=禁用」对可视化编辑器是坑。
- 子 agent prompt（`subagent_prompt.py`）刻意不走模块系统——天然不在本 GUI 管辖内，零冲突。
- 前端无任何 dev/frozen 区分机制，需新增（`/api/health` 加 `dev` 字段）。

## 硬约束

- **dev-only 双层铁门**（对齐 LangSmith dev-only 先例 `src/config.py:9-30`、决策 §三.5）：frozen 下后端**不注册**路由（404 by construction）+ 前端入口不渲染。
- **零改承重逻辑**：不动 `builder.py`/`modules/`/`graph.py`，dry-run 全用现有公开 API（`register/toggle/set_order/set_vars/build/apply_config`）。
- 不新增 npm 依赖；调序用上下箭头按钮（仓库无 DnD 库、手写 CSS 风格）。
- 中文注释/UI 文案；组件仿 `ConfigModal.vue` 形态（Teleport to body、v-if + close emit、scoped CSS、原生 fetch/axios）。

## scope 决策

- **debug.yaml 不在 scope**——它不是 mode（`MODES=("normal","discuss")`），是手动调试 profile。mode 列表由后端 `MODES` 驱动，未来加 mode 自动出现。
- **模块内容编辑不做**（改 .py 不是 GUI 的事）；内容**只读查看**要做（用户已确认）。
- **yaml 注释**：手写渲染（保留头注释 + 「由 GUI 生成」标注），不用 `yaml.dump`（丢注释、重排 key）。
- **order 重编号**：保存时按列表顺序写 `(index+1)*10`——消除 order 碰撞，写出的 yaml 与屏幕列表严格镜像。

## 新增/修改文件

| 文件 | 变更 |
|---|---|
| `src/api/dev_routes.py` **（新）** | dev-only APIRouter（挂 `/api/dev`）：GET 模块清单+现 yaml 状态+内容、POST preview（dry-run build）、POST save（校验+手写渲染+原子落盘） |
| `src/config.py` | 加 `IS_DEV: bool = not getattr(sys, "frozen", False)`（LangSmith 铁门旁，~3 行+注释） |
| `src/main.py` | `if IS_DEV: app.include_router(dev_router, prefix="/api/dev")`（静态挂载之前）——**条件 import 即门** |
| `src/api/routes.py` | `health()` 响应加 `"dev": IS_DEV`（一行） |
| `tests/test_dev_prompt_routes.py` **（新）** | 后端路由测试 |
| `frontend/src/api/prompt.js` **（新）** | 仿 `paper.js` axios 模式，3 个函数 |
| `frontend/src/components/Settings/PromptTunerModal.vue` **（新）** | GUI 主体 |
| `frontend/src/components/Settings/SettingsDrawer.vue` | dev-gated「开发」区 + 入口按钮 + Teleport 挂 modal |

**不改**：`builder.py`、`modules/**`、`graph.py`、3 个 yaml 本身。

## API 设计（均在 `/api/dev` 下，frozen 下 404）

### `GET /api/dev/prompt/{mode}`
mode 对 `MODES` 校验，未知 404。响应：

```json
{
  "mode": "normal",
  "modules": [
    {"name": "ROLE_BASE", "source": "shared", "enabled": true, "order": 10, "content": "## Core Identity\n\n..."}
  ]
}
```

构造：`get_shared_modules() + get_mode_modules(mode)` 注册进 builder → `apply_config(真实 {mode}.yaml)` → **post-apply_config** 读 enabled/order（保证「未列入 yaml 的模块」正确显示禁用 + 默认 order 落位）。`content` 带 `{占位符}` 原文（只读查看用）。

### `POST /api/dev/prompt/preview`
请求：`{"mode": "normal", "modules": [{"name": ..., "enabled": ...}, ...]}`（完整有序列表，列表序即真相，order 不必传）。校验：未注册名 → 400 `未注册的模块: XXX`；重复 → 400。dry-run：

```python
builder = PromptBuilder()
for m in get_shared_modules() + get_mode_modules(mode): builder.register(m)
for i, item in enumerate(req.modules):
    builder.toggle(item.name, item.enabled).set_order(item.name, (i + 1) * 10)
builder.set_vars(history="(预览：此处注入对话历史)", citation_plugin=CITATION_DEFAULT,
                 tool_decision_plugin=TOOL_DECISION_PLUGIN)
prompt = builder.build()
```

响应：`{"prompt", "char_count", "active_count"}`。占位符填可见 marker 串（防空 history 静默隐藏模块内容）。预览是结构预览非逐字节生产 prompt——pane 说明文案注明。

### `POST /api/dev/prompt/save`
同 preview 请求形。服务端：
1. 重校验（同 400 规则，不信任客户端）。
2. 手写渲染 yaml：头注释（原 3 行语义 + `# 由 Prompt 调控 GUI 生成`）+ 全部注册模块逐块 `name/enabled/order`，order 重编号 `(i+1)*10`。
3. **写前 sanity 门**：`yaml.safe_load` 重解析渲染文本 → fresh builder `apply_config` 不抛 + `build()` 输出 == 本次 preview 输出，否则 500 不动文件。
4. 原子写：同目录 `.yaml.tmp`（utf-8）→ `os.replace`——`graph.py` 每请求 `open()`，只见旧或新、不见半个。

响应：`{"success": true, "message": "已保存，下一问生效（无需重启）", "path": ...}`。

## 前端

### `PromptTunerModal.vue`（仿 ConfigModal 骨架）
- 宽模态（~860px 双栏 flex；z-index 低于 ServiceMask）。
- **头**：标题「Prompt 模块调控」+ mode 分段按钮 + 关闭钮。
- **左栏（~320px 滚动）**：模块行按当前序——`Toggle`（复用 `Settings/Toggle.vue`）+ 模块名（等宽）+ 来源徽标（共用/模式）+ 上下箭头（首尾 disabled）。禁用行变暗。**点击模块名 → 右栏切到该模块内容只读视图**（含占位符原文），再点「预览」tab 切回。提示行：「共用模块的开关/顺序按 mode 分别记录在各自 yaml」。
- **右栏**：`<pre>` 实时预览 + 字数/启用数说明。任意 toggle/箭头/mode 切换 400ms 防抖自动重预览；请求序号防过期响应。
- **底**：「重新加载」（re-GET 弃改动）/「保存配置」（成功瞬态提示、400 内联显示 detail）。**无重启流程**——保存即生效是本工具卖点。

### `SettingsDrawer.vue`
`onMounted` fetch `/api/health` → `isDev` ref；`v-if="isDev"` 渲染「开发」区（标题 + 「Prompt 模块调控」行 + 打开按钮 + 一句 desc「可视化调整系统 prompt 模块开关与顺序（下一问生效）」）。Teleport 挂 modal，仿 ConfigModal 接线。

## 测试（`tests/test_dev_prompt_routes.py`）

fixture 用裸 FastAPI app 只挂 dev router（**不 import `src.main`**——lifespan 会 init_db/拉 MCP）。save 测试 `tmp_path` + monkeypatch profiles 目录，不碰真 yaml。

1. GET normal：200、names ⊇ normal.yaml 名集、字段齐全；未知 mode 404。
2. preview 未注册名 → 400 且 detail 含名字；重复名 → 400。
3. preview（GET 原样回传）== 本地 builder + 真实 yaml + 相同 vars 的 build 输出。
4. save round-trip：禁用一个 + 交换两个 → 200 → 再 GET 反映新态；文件 `yaml.safe_load` 可解析、含头注释、order 是 `(i+1)*10`、禁用模块显式 `enabled: false`；无 `.tmp` 残留。
5. save 写全量注册模块（计数 == GET）。
6. 冻结门：`_dev_guard` 依赖（router 级 FastAPI dependency，`not IS_DEV` → 404）在 monkeypatch `IS_DEV=False` 下抛 404——main.py 条件 include 之外的保险带，也是可单测的门。

## 实施顺序（每步带验证）

1. `src/config.py` 加 `IS_DEV` → `python -c` 验 True。
2. `dev_routes.py`（router + `_dev_guard` + 3 端点）→ uvicorn 手 curl：GET 拿到真实清单；preview 假名 400。
3. `main.py` 条件挂载 + `routes.py` health 加 `dev` → curl health 见 `"dev": true`；静态服务不受影响。
4. 测试文件全绿 → 全量 `pytest` 无回归（重点 `test_prompt_byte_equivalence.py`——save 测试已被 monkeypatch 隔离）。
5. `api/prompt.js` → dev proxy 连通。
6. `PromptTunerModal.vue` → 手验：toggle/箭头防抖刷新预览；保存后 `git diff` yaml 干净重编号；**下一问体现变更、不重启**。
7. `SettingsDrawer.vue` 入口 → dev 可见；临时强置 `isDev=false` 验证隐藏。

## 风险

- **yaml 注释损失**：手写渲染已解头注释；未来手工加的模块级注释会在 GUI 保存时被抹——头注释「由 GUI 生成」标注 machine-managed，算显式契约。
- **shared 模块双份状态**：共用模块在两个 mode 列表各有一份独立状态（这正是今日 yaml 的行为），提示行说明，不做跨 mode 同步（那是行为变更）。
- **并发保存**：单人 dev 工具，last-write-wins，不加锁；写前 sanity 重解析兜自伤。
- **TestClient/lifespan**：测试不 import `src.main`。
- **frozen profiles 路径在 `_MEIPASS`**：moot（路由不挂载），`dev_routes.py` 注释说明。

## 收尾（完成后）

- 总纲 `top-level-progress-log.md`：【五】想法 1 标 ✅（B 半落地）、子计划索引加行指向本 plan 落库副本 `plan/prompt-tuner-gui-plan.md`（按 CLAUDE.md「Plans live in the repo」开工时先同步进 `plan/`）。
- 推送当前分支（跨机交接约定）。
