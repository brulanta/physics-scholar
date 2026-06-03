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


if __name__ == "__main__":
    # 后端在子线程跑
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # 启动完成后开浏览器
    browser_thread = threading.Thread(target=wait_and_open_browser, daemon=True)
    browser_thread.start()

    # 托盘图标在主线程跑（pystray要求）
    tray = make_tray_icon()
    tray.run()
