"""进程内共享的服务状态标志。

托盘（app.py）与后端 uvicorn 跑在**同一进程**、共享内存。托盘触发的重启从原生菜单
发起，前端页面无从知晓——只能看到后端掉线，会误判成「已退出」（X 遮罩）。

为区分「我们主动触发的重启（应显示转圈遮罩）」与「彻底退出/意外断联（应显示 X）」，
重启前把 `RESTARTING` 置位，`/api/health` 带上该标志给前端；前端轮询读到即切到
「正在重启」转圈遮罩。退出路径不置位 → 前端走既有 down 检测显示 X。

跨两条重启路径共用：托盘 on_restart（app.py）、配置页 /config/restart（routes.py）。
"""

from __future__ import annotations

# True 表示本进程正在主动重启（新 exe 即将/已被拉起）。仅在内存中，随进程消亡。
RESTARTING = False


def mark_restarting() -> None:
    """标记「正在主动重启」，供 /api/health 上报前端切换到转圈遮罩。"""
    global RESTARTING
    RESTARTING = True


def is_restarting() -> bool:
    return RESTARTING
