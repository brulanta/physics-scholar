import sys
import os
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(sys._MEIPASS)
else:
    ROOT = Path(__file__).parent

sys.path.insert(0, str(ROOT))

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# ── MCP server 子进程分流 ──────────────────────────────────
# agent 把工具拆进本地 stdio MCP server，子进程即本 exe/解释器自身，靠环境变量
# PS_MCP_SERVER（local/web/jina）分流。命中即阻塞跑 server，绝不进入下面的 tray-app。
# 必须在 import uvicorn 等重组件之前，让子进程保持轻量。
if os.environ.get("PS_MCP_SERVER"):
    from src.mcp_servers import run_server

    run_server(os.environ["PS_MCP_SERVER"])  # mcp.run(stdio)，阻塞直到 stdin 关闭
    sys.exit(0)

import multiprocessing
import uvicorn
import webbrowser
import threading
import time
import urllib.request
import subprocess
from PIL import Image
import pystray

PORT = 57321


def _open_browser(url: str):
    """开默认浏览器且让其不落入本进程 kill-on-close Job（方案乙核心）。

    Windows：os.startfile 走 ShellExecuteW，浏览器由 explorer 代拉起、非本进程后代 →
    不在 Job 里，后端 os._exit / 重启关 Job 时不波及浏览器窗口及用户的其他标签页。
    非 Windows（dev 的 mac/linux，且未启用 Job）：无 os.startfile，退回 webbrowser.open。
    """
    startfile = getattr(os, "startfile", None)
    if startfile is not None:
        try:
            startfile(url)
            return
        except OSError:
            pass  # 无默认浏览器关联等极端情况，退回 webbrowser
    webbrowser.open(url)


def wait_and_open_browser():
    # 重启拉起的新进程：旧浏览器窗口仍在、由其 ServiceMask 自刷新接管，新进程不再开窗口
    # （避免重复窗口）。故重启路径的 Popen 注入 PS_SUPPRESS_BROWSER=1，这里检测到就跳过。
    if os.environ.get("PS_SUPPRESS_BROWSER"):
        return
    url = f"http://localhost:{PORT}/api/health"  # 加上/api前缀
    while True:
        try:
            urllib.request.urlopen(url, timeout=1)
            _open_browser(f"http://localhost:{PORT}")
            break
        except Exception:
            time.sleep(0.5)


def make_tray_icon():
    # 加载图标
    if getattr(sys, "frozen", False):
        icon_path = Path(sys._MEIPASS) / "assets" / "favicon.ico"
    else:
        icon_path = ROOT / "frontend" / "public" / "favicon.ico"

    image = Image.open(str(icon_path))

    def on_open(icon, item):
        _open_browser(f"http://localhost:{PORT}")

    def on_restart(icon, item):
        icon.stop()
        # CREATE_BREAKAWAY_FROM_JOB：让新 exe 脱离本进程即将关闭的 kill-on-close Job，
        # 否则随后的 os._exit(0) 关 Job 句柄会连带把新进程杀掉。非 Windows 上该 flag 为 0、
        # 天然 no-op（Job 也未启用）。
        # PS_SUPPRESS_BROWSER=1：新进程不再开浏览器窗口——旧窗口（浏览器已脱离 Job、不受
        # os._exit 波及）仍在，由其 ServiceMask 收到 200 自刷新到新后端，避免重复窗口。
        subprocess.Popen(
            [sys.executable],
            cwd=os.path.dirname(sys.executable),
            creationflags=getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),
            env=dict(os.environ, PS_SUPPRESS_BROWSER="1"),
        )
        os._exit(0)

    def on_exit(icon, item):
        icon.stop()
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("打开界面", on_open, default=True),  # 双击托盘图标也触发
        pystray.MenuItem("重启", on_restart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_exit),
    )

    return pystray.Icon("PhysicsScholar", image, "PhysicsScholar", menu)


def run_server():
    # 重启场景两条路径都是「先 Popen 新进程、再 os._exit 旧进程」，新进程可能在旧进程
    # 释放 57321 之前抢先 bind → WinError 10048 → 新进程崩、前后端全灭。这里先探测端口
    # 可绑再交给 uvicorn，兜住旧进程退出释放端口的窗口（最多约 10s）。
    import socket

    for _ in range(20):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", PORT))
            break
        except OSError:
            time.sleep(0.5)
    uvicorn.run("src.main:app", host="127.0.0.1", port=PORT)


# 持有 Job 句柄的进程级引用，防止被 GC 关闭（关闭即触发 kill-on-close）。
_JOB_HANDLE = None


def _setup_kill_on_close_job():
    """把本进程放进一个 kill-on-close 的 Windows Job Object。

    MCP server 子进程由本进程派生、默认继承 Job 成员资格；一旦本进程退出
    （含 tray 的 os._exit(0)，它绕过 FastAPI lifespan 的 mc.shutdown），Windows
    会连同整棵子进程树一并杀掉，杜绝孤儿 MCP server。这是比「手动 terminate
    子进程 pid」更稳的做法——MCP stdio client 把子进程 pid 私有化，公开 API
    取不到，Job Object 从内核层兜住整棵树。

    任何失败都降级为「不启用 Job」（开发态/无 pywin32 环境照常运行）。
    """
    global _JOB_HANDLE
    try:
        import win32job
        import win32api

        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        # kill-on-close：主进程退出即连带回收整棵子进程树（杜绝孤儿 MCP server）。
        # breakaway-ok：允许子进程显式 CREATE_BREAKAWAY_FROM_JOB 脱离本 Job——托盘「重启」
        # 时新 exe 必须脱离这个「即将被 os._exit 关闭」的 Job，否则会被 kill-on-close 连带杀掉
        # （新进程拉不起 + 旧窗口异常消失的根因）。新进程经 __main__ 会重建自己的 kill-on-close
        # Job，孤儿防护在新进程侧自动重新成立。
        info["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | win32job.JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info
        )
        win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
        _JOB_HANDLE = job  # 持有句柄，进程退出时句柄关闭 → 触发 kill-on-close
    except Exception:
        pass  # 无 pywin32 或非 Windows：降级，子进程随 stdin 关闭自行退出


if __name__ == "__main__":
    # PyInstaller 官方要求：frozen 入口首行调 freeze_support()，否则任何经 multiprocessing
    # 派生的子进程在 frozen Windows 下会重新跑整个 bootloader（→ 又起一套 tray-app，套娃）。
    # 本项目 MCP 子进程走 subprocess.Popen([sys.executable], env=PS_MCP_SERVER) 而非
    # multiprocessing，当前不直接依赖它；此处为防御性脚手架——拦住未来自身或第三方库
    # （如某些 embedding/并行后端）触发的 mp spawn。非 frozen / 非 Windows 下为 no-op，
    # dev 与 pytest 行为零变化。必须在 _setup_kill_on_close_job / 起线程 / tray.run() 之前。
    multiprocessing.freeze_support()

    # ── MCP 总闸：frozen（打包分发）默认开启 ──────────────────────────────
    # 阶段 3 收尾切换。dev 跑 `uvicorn src.main:app`（不经本 __main__），保持 tool_runtime
    # 的 false 默认、走轻量内嵌路径、pytest 基线不变；MCP 仅需开发时 PS_USE_MCP=true 显式 opt-in。
    # 而 frozen exe 即本 app.py，这里把开关 setdefault 成 true（用户仍可用环境变量强制覆盖回退），
    # 让分发出去的产品默认走 MCP（6 工具拆进 3 个 stdio 子进程）。必须在起 server 线程
    # （惰性 import src.main → tool_runtime.USE_MCP 读 env）之前设置。
    if getattr(sys, "frozen", False):
        os.environ.setdefault("PS_USE_MCP", "true")

    # 先把自己放进 kill-on-close Job，确保后续派生的 MCP 子进程不会变孤儿
    _setup_kill_on_close_job()

    # 后端在子线程跑
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 启动完成后开浏览器
    browser_thread = threading.Thread(target=wait_and_open_browser, daemon=True)
    browser_thread.start()

    # 托盘图标在主线程跑（pystray要求）
    tray = make_tray_icon()
    tray.run()
