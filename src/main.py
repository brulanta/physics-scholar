# main.py
from fastapi import FastAPI
from src.api.routes import router  # 新增

app = FastAPI(title="physicsScholar", version="0.1.0")

app.include_router(router)  # 新增


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}
