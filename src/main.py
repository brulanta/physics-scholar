# main.py
from fastapi import FastAPI
from src.api.routes import router  # 新增

app = FastAPI(title="physicsScholar", version="0.1.0")

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发阶段直接全开
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)  # 新增


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}
