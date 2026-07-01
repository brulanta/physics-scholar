# 启动/重启流程改造：重启路径补漏 + MCP 并行拉起 + 强制重启去双跳

## Context（为什么做）

MCP 工具迁移（`plan/mcp-tool-migration-plan.md`）已闭环，但收尾实机暴露出「启动与重启流程」这一主题下的三件相关工作，集中在此 plan 处理：

- **A（P0，本轮已做）**：遗留 1 的 Job Object breakaway 修复，上一轮只补了 [app.py](../app.py) 托盘「重启」路径，**漏了第二条重启路径**——配置页「立即重启」按钮走 `POST /api/config/restart`（[routes.py:102](../src/api/routes.py#L102) `_do_restart`），同样 `Popen → os._exit(0)` 但没加 breakaway flag。用户 2026-07-01 实机点「立即重启」→ 前后端全关、无自动拉起，正是这条漏网路径失败。**这反过来印证了 Job Object kill-on-close 假设成立**（两条路径机制相同，修了的托盘路径应好使、没修的按钮路径果然失败）。
- **B（新需求，小）**：MCP server 现为**串行**拉起（[mcp_client.py:177-191](../src/rag/mcp_client.py#L177-L191) owner 任务里 `for name in connections:` 逐个 `enter_async_context` + `load_mcp_tools`）。改**并行**可缩短启动/重启等待。
- **D（新需求，大，先记载不执行）**：把「保存配置后是否重启」的可选弹窗改为**强制重启**，并消除**双重启浪费**——现在 `update_config`（[routes.py:63-73](../src/api/routes.py#L63-L73)）末尾 `await mcp_client.restart()` 重启一次 MCP 会话，用户随即点「立即重启」又把整个 exe（含 MCP）全重启一遍，**中间那次 MCP restart 纯属浪费**，还是上一轮 `CancelledError` bug 的来源场景。

## A — 重启路径补漏（P0）✅ 本轮已落地（2026-07-01）

[routes.py](../src/api/routes.py) `restart_app._do_restart` 的 `subprocess.Popen` 加 `creationflags=getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)`，与 [app.py](../app.py) `on_restart` 同理（Job 已在 `_setup_kill_on_close_job` 设 `BREAKAWAY_OK`）。非 Windows 取 0、no-op。

**验证（需实机）**：打包后配置页填主 LLM 配置 → 保存 → 弹「立即重启」→ 点击 → **新 exe 应自动拉起**（浏览器新窗口自动开）。若成功，遗留 1 的 Job 假设即被双路径共同证实。

## B — MCP server 并行拉起（待议/待做）

### 现状
[mcp_client.py](../src/rag/mcp_client.py) `_MCPClientSingleton._owner` 里：
```python
async with contextlib.AsyncExitStack() as stack:
    for name in connections:                       # ← 串行
        session = await stack.enter_async_context(client.session(name))
        server_tools = await load_mcp_tools(session, server_name=name)
        server_tools = [_patch_tool_flatten(t) for t in server_tools]
        tools.extend(server_tools)
```
3 个 server（web/local/jina）依次冷启：local 要初始化 chromadb、jina 要 import llm/sub_llm，串行累加等待。

### 改动方向（实装时定稿）
- 用 `asyncio.gather` 并行 enter 三个 session + load tools，再汇总 `self._tools`。
- **关键约束（anyio cancel scope）**：上一轮踩过的坑——session 的 enter/exit 必须落在**同一个任务**（owner 任务）。`asyncio.gather` 的子协程在**同一任务内**并发调度（非新任务），`AsyncExitStack.enter_async_context` 仍在 owner 任务栈上退出，**理论上不破坏 cancel scope 规则**；但 stdio_client 内部各自建 anyio task group，需实测 gather 并行 enter 是否引入跨 scope 干扰。**先小步验证：临时脚本并行 enter 3 session → 正常 list_tools + 正常 teardown 无 CancelledError，再落地。**
- 保持 `_patch_tool_flatten`、工具名、错误降级不变；工具**顺序**若因并行乱序，检查是否影响 `build_agent` 的 `mcp_names` 剔除逻辑（应无关，按名匹配）。
- 收益有限时（stdio 握手本身快，瓶颈在 chromadb/llm import 的 CPU 冷启，未必能并行压缩）可能不值得——**实测启动耗时前后对比，收益不显著则放弃，记录结论**。

### 风险
- 并行 teardown（shutdown 时 AsyncExitStack 逆序 aclose）比串行更易触发 stdio benign 噪声；现有 owner 的 `except BaseException` 兜底应能吞掉，需确认。

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

## 端到端验证（分任务）

- **A**：见上（实机配置页「立即重启」）。
- **B**：临时脚本并行 enter 3 session 正常握手 + teardown 无 CancelledError；`get_tools()` 仍 6 工具；启动耗时前后对比；`pytest --ignore=tests/test_rag_chain.py` 基线不破（116 passed）。
- **D**：实机保存配置 → 观察后台**不再**有 `update_config` 触发的 MCP restart 日志（去了那次热重启）→ 强制重启整程 → 新配置生效；确认无双跳。

## 收尾
- 临时验证脚本用完即删（信任边界）；中文注释；收尾 push 当前分支 `temp-work-mcp-tool-migration`。
- 本 plan 处理完后，MCP 迁移 plan 保持为已闭环的迁移记录，不再往里堆新工作。
