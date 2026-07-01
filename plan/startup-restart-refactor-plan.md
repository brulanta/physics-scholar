# 启动/重启流程改造：重启路径补漏 + MCP 并行拉起 + 强制重启去双跳

## Context（为什么做）

MCP 工具迁移（`plan/mcp-tool-migration-plan.md`）已闭环，但收尾实机暴露出「启动与重启流程」这一主题下的三件相关工作，集中在此 plan 处理：

- **A（P0，本轮已做）**：遗留 1 的 Job Object breakaway 修复，上一轮只补了 [app.py](../app.py) 托盘「重启」路径，**漏了第二条重启路径**——配置页「立即重启」按钮走 `POST /api/config/restart`（[routes.py:102](../src/api/routes.py#L102) `_do_restart`），同样 `Popen → os._exit(0)` 但没加 breakaway flag。用户 2026-07-01 实机点「立即重启」→ 前后端全关、无自动拉起，正是这条漏网路径失败。**这反过来印证了 Job Object kill-on-close 假设成立**（两条路径机制相同，修了的托盘路径应好使、没修的按钮路径果然失败）。
- **B（新需求，小）**：MCP server 现为**串行**拉起（[mcp_client.py:177-191](../src/rag/mcp_client.py#L177-L191) owner 任务里 `for name in connections:` 逐个 `enter_async_context` + `load_mcp_tools`）。改**并行**可缩短启动/重启等待。
- **D（新需求，大，先记载不执行）**：把「保存配置后是否重启」的可选弹窗改为**强制重启**，并消除**双重启浪费**——现在 `update_config`（[routes.py:63-73](../src/api/routes.py#L63-L73)）末尾 `await mcp_client.restart()` 重启一次 MCP 会话，用户随即点「立即重启」又把整个 exe（含 MCP）全重启一遍，**中间那次 MCP restart 纯属浪费**，还是上一轮 `CancelledError` bug 的来源场景。

## A — 重启路径补漏（P0）✅ 本轮已落地（2026-07-01）

[routes.py](../src/api/routes.py) `restart_app._do_restart` 的 `subprocess.Popen` 加 `creationflags=getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)`，与 [app.py](../app.py) `on_restart` 同理（Job 已在 `_setup_kill_on_close_job` 设 `BREAKAWAY_OK`）。非 Windows 取 0、no-op。

**验证（需实机）**：打包后配置页填主 LLM 配置 → 保存 → 弹「立即重启」→ 点击 → **新 exe 应自动拉起**（浏览器新窗口自动开）。若成功，遗留 1 的 Job 假设即被双路径共同证实。

## B — MCP server 并行拉起 —— ✅ 已落地（2026-07-01）

### 背景（原串行）
[mcp_client.py](../src/rag/mcp_client.py) `_owner` 原以 `for name in connections:` 串行 `enter_async_context` + `load_mcp_tools`，3 个 server（web/local/jina）依次冷启。**实测串行 startup 总耗时 77s**，构成：web 27.7s / local 34.8s（chromadb import+初始化）/ jina 14.8s（llm import）——三段主体都是**独立子进程各自的 Python import 冷启**，天然可并行。

### 已落地实现（避开 anyio cancel-scope 坑）
把 `_owner` 从「单任务串行 enter/exit 全部 session」重构为：
- **每 server 一个常驻子任务** `_run_one_session`：在**自己任务内** `async with client.session(name)` enter → load → hold（`await stop_event.wait()`）→ 退出 async with 时在**同一子任务内** aclose。故每个 session 的 cancel scope 都同任务进出，**P2-a 修复（跨任务退栈误杀 lifespan）得以保持**。
- **协调者任务** `_owner`：`asyncio.create_task` 起 N 个子任务并发拉起（并行冷启）→ `gather(per_ready)` 等全部就绪 → 按 `_ACTIVE_SERVERS` 声明序汇总 `self._tools`（并行不打乱工具顺序）→ `gather(tasks)` 等收尾。协调者**从不 enter/exit 任何 session**。
- 加载失败：发 `stop_event` 让已就绪子任务 teardown，gather 收尾后向 startup 上报首个异常（startup 降级逻辑不变）。
- `_patch_tool_flatten`、工具名、错误降级、startup/shutdown/restart 对外接口全不变。

### 验证（全部通过，临时脚本用完即删）
- **并行 startup 24.09s**（串行 77s → **省 69%**，优于预估 ~35s）；`get_tools()` 6 工具齐全、顺序 = `_ACTIVE_SERVERS` 声明序。
- **P2-a 保持**：从独立任务（模拟 `POST /api/config` 请求任务）调 `restart()` → 6 工具齐全、模拟 lifespan 任务未被取消、无 `CancelledError`。
- **shutdown 干净**、无异常抛出（并行 teardown 的 benign 噪声由各子任务 `except` 吞掉）。
- `pytest --ignore=tests/test_rag_chain.py` 基线不破。

## D — 强制重启 + 消除双重启浪费 + 浏览器不再连带被杀 —— ✅ 已落地（2026-07-01，方案乙）

### 问题链（三件交织）
1. **双重启浪费**：`update_config` 保存后 `await mcp_client.restart()` 热重启一次 MCP 会话，用户随即点「立即重启」又整程重启一遍——中间那次纯浪费（且是 `CancelledError` bug 场景）。
2. **重启交互多一跳**：保存成功弹「稍后重启/立即重启」二选弹窗，但配置本就必须重启才生效，「稍后」无价值。
3. **旧浏览器窗口连带被杀（数据丢失风险）**：见下 ★。

### 已落地实现（方案乙：浏览器永不进 Job + 旧窗口自刷新 + 强制重启）
- **浏览器脱离 Job**（[app.py](../app.py) `_open_browser`）：Windows 经 `explorer.exe <url>` 转交——常驻 shell 去拉起默认浏览器，浏览器成为 **shell 的子进程**而非本进程后代 → 冷/热启动都不落进 kill-on-close Job，非 Windows 退回 `webbrowser.open`。`wait_and_open_browser` 与托盘 `on_open` 都改调它。后端 `os._exit` / 重启关 Job **不再波及浏览器窗口及用户其他标签页**。
  - ⚠️ 曾用 `os.startfile`（ShellExecuteW）——**实机翻车**：浏览器冷启动（尚未运行）时它把浏览器作为本进程直接子进程创建、仍落进 Job → exe 自己拉起的窗口被连杀，只有「浏览器已在运行」才幸免。`explorer.exe` 转交才真正冷/热都脱离。
- **新进程不再开窗口**（env `PS_SUPPRESS_BROWSER=1`）：两条重启路径的 Popen（托盘 `on_restart`、[routes.py](../src/api/routes.py) `_do_restart`）都注入该标志；`wait_and_open_browser` 开头检测到即 `return`。旧窗口靠 ServiceMask 自刷新接管，不产生重复窗口。对 MCP 子进程无害（顶部 `PS_MCP_SERVER` 分流后即 `sys.exit`，走不到开浏览器逻辑）。
- **端口竞态兜底**（[app.py](../app.py) `run_server`）：两条重启路径都「先 Popen 新进程、再 os._exit 旧进程」，新进程可能抢在旧进程释放 57321 前 bind → WinError 10048 崩。加端口探测重试（最多约 10s）兜住。
- **去双重启**（[routes.py](../src/api/routes.py) `update_config`）：删掉 `await mcp_client.restart()` 整块（连同该文件已无用的 `mcp_client`/`USE_MCP` import）；只 `save_config_dict` 落盘，配置生效交给整程重启。
- **强制重启交互**（[ConfigModal.vue](../frontend/src/components/Settings/ConfigModal.vue)）：删「保存成功」二选弹窗，`doSave` 成功即调 `doRestart()`（`/api/config/restart` + `serviceState.state='restarting'`）；清理 `savedDialog`/`.mini-*`/`.btn-restart` 死代码。
- **遮罩语义分离 + 时序修复**（[ServiceMask.vue](../frontend/src/components/ServiceMask.vue) + 新增 [src/service_state.py](../src/service_state.py)）：
  - **重启用转圈、退出/断联用 X**：托盘重启从原生托盘发起、前端不知情，只见后端掉线会误判为退出（X）。新增进程内共享标志 `service_state.RESTARTING`（托盘与 uvicorn 同进程），重启前置位、`/api/health` 带 `restarting` 字段；前端 `ok` 态轮询读到即切「正在重启」转圈。托盘 `on_restart` 置标志后 `sleep(2.5s)` 兜住前端一轮（2s）轮询再退出。退出/强杀不置标志 → 走 down 检测显示 X。
  - **修「立刻刷回旧后端→空白→遮罩延迟」**：配置重启置 `restarting` 时旧后端还没死，原「200 即 reload」会刷回活着的旧后端。`restarting` 分支加 `sawDown` 守卫，先确认旧后端下线过一次，之后的 200 才 reload。
  - **X 态文案**：由「程序已退出/请关闭此页面」改为「连接已中断/正在尝试重新连接，请稍候…」（方案乙不再要求用户手动关；退出与断联通用）。

### ★ 背景机制：Job Object 令浏览器窗口「跟着后端一起消失」（2026-07-01 实机确认，方案乙据此定案）
主进程放进 `KILL_ON_JOB_CLOSE` 的 Job（防孤儿 MCP 子进程）；`webbrowser.open()` 拉起的浏览器作为主进程后代落进 Job → 后端 `os._exit` 关 Job 时被连带回收，**连同用户在同一浏览器开的其他标签页一起杀掉**。2026-07-01 实机确认「重启会关掉同浏览器的其他标签页」。

- **为何不选方案甲（杀旧窗口+开新窗口）**：实测会误杀其他标签页（数据丢失）；且「只关自己那个标签页」在浏览器安全策略下不可靠——`window.close()` 只能关脚本自身 `window.open` 出来的窗口，对用户导航打开的普通标签页会被拒绝，跨浏览器不一致。
- **方案乙定案**：让浏览器**永不进 Job**（`os.startfile` 交由 OS 天然进程归属，不依赖 breakaway 能否穿透浏览器单例转交），后端退出/重启完全不波及浏览器；旧窗口靠 ServiceMask 自刷新，对用户零损失。

## 端到端验证（分任务）

- **A**：见上（实机配置页「立即重启」）。
- **B**：临时脚本并行 enter 3 session 正常握手 + teardown 无 CancelledError；`get_tools()` 仍 6 工具；启动耗时前后对比；`pytest --ignore=tests/test_rag_chain.py` 基线不破（116 passed）。
- **D（实机核验通过，2026-07-01，打包后 `python scripts/build_release.py`）**：
  1. **浏览器不连带被杀（核心）✅**：exe 冷启动自拉起窗口 + 同浏览器另开标签页 → 保存配置重启 / 托盘重启 / 托盘退出 → 窗口及其他标签页均完好（`explorer.exe` 转交修复后成立；`os.startfile` 版曾在冷启动翻车）。
  2. **强制重启交互 ✅**：保存 → 不弹二选框、直接「正在重启」转圈 → 后端拉起后自动刷新、新配置生效（`sawDown` 守卫修复后不再刷回旧后端）。
  3. **遮罩语义 ✅**：配置重启 / 托盘重启 → 转圈；托盘退出 / 任务管理器强杀 → X（「连接已中断…」）。托盘重启的 2.5s 续命体感无感（≤2~3s）。
  4. **去双重启 ✅**：保存配置不再触发 `update_config` 的 MCP 热重启。
  5. `pytest --ignore=tests/test_rag_chain.py` 基线不破（116 passed）。

## 收尾
- 临时验证脚本用完即删（信任边界）；中文注释。
- A/B 已在 `main`；D 在临时分支 `temp-work-startup-restart` 开发并实机通过，**待合并回 `main` + 删临时分支**。
- 本 plan 处理完后，MCP 迁移 plan 保持为已闭环的迁移记录，不再往里堆新工作。
