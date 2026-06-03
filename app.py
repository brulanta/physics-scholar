import sys
import os
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(sys._MEIPASS)
else:
    ROOT = Path(__file__).parent

sys.path.insert(0, str(ROOT))

import uvicorn
import webbrowser
import threading
import time
import urllib.request

PORT = 57321


def open_browser_when_ready():
    """轮询health接口，后端就绪后再开浏览器"""
    url = f"http://localhost:{PORT}/health"
    while True:
        try:
            urllib.request.urlopen(url, timeout=1)
            # 能收到响应说明后端已就绪
            webbrowser.open(f"http://localhost:{PORT}")
            break
        except Exception:
            time.sleep(0.5)


if __name__ == "__main__":
    threading.Thread(target=open_browser_when_ready, daemon=True).start()
    uvicorn.run("src.main:app", host="127.0.0.1", port=PORT)
