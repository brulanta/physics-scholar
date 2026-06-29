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

import uvicorn
import webbrowser
import threading
import time
import urllib.request
import subprocess
from PIL import Image
import pystray

PORT = 57321


def wait_and_open_browser():
    url = f"http://localhost:{PORT}/api/health"  # 加上/api前缀
    while True:
        try:
            urllib.request.urlopen(url, timeout=1)
            webbrowser.open(f"http://localhost:{PORT}")
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
        webbrowser.open(f"http://localhost:{PORT}")

    def on_restart(icon, item):
        icon.stop()
        subprocess.Popen([sys.executable], cwd=os.path.dirname(sys.executable))
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
        info["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info
        )
        win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
        _JOB_HANDLE = job  # 持有句柄，进程退出时句柄关闭 → 触发 kill-on-close
    except Exception:
        pass  # 无 pywin32 或非 Windows：降级，子进程随 stdin 关闭自行退出


if __name__ == "__main__":
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
