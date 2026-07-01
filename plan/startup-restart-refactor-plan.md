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

## D — 强制重启 + 消除双重启浪费（待议，先记载不执行）

### 问题链
1. `update_config`（[routes.py:63](../src/api/routes.py#L63)）保存后 `await mcp_client.restart()` —— MCP 会话重启一次（子进程带新 key 重拉）。
2. 前端弹「部分设置需重启」弹窗（[ConfigModal.vue:242-248](../frontend/src/components/Settings/ConfigModal.vue#L242-L248)），可选「稍后重启」/「立即重启」。
3. 点「立即重启」→ `POST /api/config/restart` → 整个 exe（含 MCP 子进程）全部重启。

→ 步骤 1 的 MCP restart 与步骤 3 的全量重启**重复**；步骤 1 白做，且是 `CancelledError` bug 场景。

### 改动方向（前后端，实装时定稿）
- **后端**：`update_config` **去掉** `await mcp_client.restart()`（既然强制整程重启，子进程会随新进程带新 key 起来，无需热重启会话）。保留 `save_config_dict`（落盘）+ `reload_config`（若同进程还需读新值）。
- **前端**：把「稍后重启/立即重启」二选弹窗改为**强制重启**流程——保存成功后直接进入 `serviceState.state = 'restarting'`（[ConfigModal.vue:382-389](../frontend/src/components/Settings/ConfigModal.vue#L382) `doRestart`）并触发 `/api/config/restart`，不再给「稍后重启」选项。ServiceMask（[ServiceMask.vue](../frontend/src/components/ServiceMask.vue)）已有「正在重启→收到 200 刷新页面」逻辑，复用。
- **交互确认点（问用户）**：强制重启是否需要一个「即将重启」的提示/倒计时，还是保存后直接重启无提示？会打断用户连续改多项配置的场景吗（若用户想连改几项再统一重启，强制每次保存即重启会烦）。→ **实装前用 AskUserQuestion 敲定交互**。
- **依赖 A**：D 的整程重启复用 `/api/config/restart`，必须 A 的 breakaway 修复先坐实（否则强制重启后一样拉不起）。

### 风险
- 强制重启改变既有交互习惯；若用户有「连改多项」诉求，需保留「改完手动触发重启」而非「每次保存即重启」。这是**产品交互决策**，实装前必须与用户确认。

### ★ 背景机制：Job Object 令浏览器窗口「跟着后端一起消失」（2026-07-01 实机观察 + 分析，交互定稿必读）
用户 2026-07-01 实机观察到：重启/退出时旧前端浏览器窗口会**跟着后端一起消失**——而 MCP 迁移**之前**后端退出前端窗口不关（当时还专门做了 [ServiceMask.vue](../frontend/src/components/ServiceMask.vue) 遮罩提醒用户手动关）。根因是本次为防孤儿 MCP 子进程新增的 **Windows Job Object（kill-on-close）**：

- **机制**：[app.py](../app.py#L90) 把主进程放进 `KILL_ON_JOB_CLOSE` 的 Job；Windows 下子进程默认继承 Job 成员资格，主进程退出时内核连整棵后代进程树一并回收。浏览器由 [`wait_and_open_browser` → `webbrowser.open()`](../app.py#L40) 拉起，**作为主进程后代落进 Job** → 后端一退，浏览器窗口被连带杀。迁移前无 Job，后端 `os._exit` 只杀自己、浏览器独立进程不受影响，故窗口留存。
- **⚠️ 不稳定 / 有风险，别当稳定契约**：这个「跟着关」取决于**点开时浏览器是否已在运行**：
  - 点开前**没开**浏览器 → 本程序拉起的实例即窗口宿主、在 Job 里 → 连带关（用户这次看到的）。
  - 点开前浏览器**已在运行** → `webbrowser.open` 多半只往**已有浏览器进程**新开标签，那进程**不是**后代、**不在 Job 里** → 后端退出不杀它，PhysicsScholar 标签残留（老问题回来）。最坏情况：若宿主进程恰好进了 Job 且用户开着其它重要标签，理论上会被一起清掉（数据丢失风险）；实际因主流浏览器单实例转发较少触发，但非零。
- **对 D 交互定稿的直接影响**：ServiceMask 的「重启中→收到 200 刷新页面」逻辑在「旧窗口已被 Job 杀掉」场景里**根本跑不到**（窗口没了，没有页面可刷新），新窗口是新进程自己 `webbrowser.open` 开的。故 D 必须**二选一、不能混用**：
  - 方案甲「杀旧窗口 + 开新窗口」：依赖 Job 连带回收 + 新进程自开窗口。简单但受上述「浏览器是否已运行」的不确定性影响，且可能误杀用户其它标签。
  - 方案乙「保留旧窗口 + 遮罩刷新」：回到 ServiceMask 的既有设计，需让浏览器**脱离 Job**（如启动浏览器时不作为 Job 内后代，或不依赖 Job 杀窗口），重启后旧窗口收到 200 自行刷新。
  - **实装前用 AskUserQuestion 敲定走甲还是乙**；两者对 app.py（浏览器是否入 Job）、ServiceMask、`doRestart` 的改动方向不同。

## 端到端验证（分任务）

- **A**：见上（实机配置页「立即重启」）。
- **B**：临时脚本并行 enter 3 session 正常握手 + teardown 无 CancelledError；`get_tools()` 仍 6 工具；启动耗时前后对比；`pytest --ignore=tests/test_rag_chain.py` 基线不破（116 passed）。
- **D**：实机保存配置 → 观察后台**不再**有 `update_config` 触发的 MCP restart 日志（去了那次热重启）→ 强制重启整程 → 新配置生效；确认无双跳。

## 收尾
- 临时验证脚本用完即删（信任边界）；中文注释；收尾 push 当前分支 `temp-work-mcp-tool-migration`。
- 本 plan 处理完后，MCP 迁移 plan 保持为已闭环的迁移记录，不再往里堆新工作。
