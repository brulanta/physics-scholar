# main.py
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from src.api.routes import router
from src.core.init_SQLite import init_db
from src.rag.tool_runtime import USE_MCP
from src.rag import mcp_client
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from src.utils.logger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # MCP 路径开启时，启动期一次性拉起常驻 stdio 会话（子进程长驻、速率锁持久）。
    # 关闭开关则完全不碰 MCP，工具仍走老内嵌路径（新旧互斥，避免速率锁分裂）。
    if USE_MCP:
        try:
            await mcp_client.startup()
        except Exception:
            logger.exception("[mcp] startup 失败，本次运行 MCP 工具不可用")
    yield
    if USE_MCP:
        await mcp_client.shutdown()


app = FastAPI(title="physicsScholar", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


# 所有API路由注册完之后，最后挂载静态文件
dist_path = Path(__file__).resolve().parent.parent / "dist"
if dist_path.exists():
    app.mount("/", StaticFiles(directory=str(dist_path), html=True), name="static")
