# src/api/routes.py
import uuid
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Response
from pydantic import BaseModel, Field
from src.config import PDF_DIR
from src.core import registry
from src.core.ingestor import ingest_pdf, confirm_and_index, delete_paper
from src.rag.chain import ask
from src.rag.graph import regenerate
import requests
from typing import Literal
from src.rag.memory import ConversationMemory, MessageRepo, ConversationRepo
from src.utils.logger import get_logger
import httpx

router = APIRouter()
logger = get_logger(__name__)

# ── 防 CORS，轻量 ─────────────────────────────────────────────────


@router.get("/proxy-head")
async def proxy_head(url: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    async with httpx.AsyncClient(timeout=8) as client:
        try:
            r = await client.head(
                url, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
            )
            return {
                "status": r.status_code,
                "content_type": r.headers.get("content-type", ""),
            }
        except Exception as e:
            return {"status": 0, "content_type": "", "error": str(e)}


# ── 会话 ─────────────────────────────────────────────────


@router.post("/conv_id/new")
def new_conversation():
    return {"conv_id": str(uuid.uuid4())}


@router.get("/conversation/{conversation_id}/tree")
def get_conversation(conversation_id: str):
    memory = ConversationMemory(conversation_id)
    try:
        return {"messages": memory.get_tree()}
    except Exception as e:
        return {"success": False, "detail": str(e)}
    finally:
        memory.close()


@router.delete("/conversation/{conversation_id}")
def delete_conversation(conversation_id: str):
    memory = ConversationMemory(conversation_id)
    repo = ConversationRepo()
    try:
        repo.delete(conversation_id)
        return memory.clear()
    finally:
        memory.close()
        repo.close()


@router.get("/conversations")
def list_conversations(user_id: str = "default"):
    repo = ConversationRepo()
    try:
        return {"conversations": repo.list_by_user(user_id)}
    finally:
        repo.close()


@router.patch("/conversation/{conversation_id}/title")
def update_title(conversation_id: str, title: str):
    repo = ConversationRepo()
    try:
        return repo.update_title(conversation_id, title)
    finally:
        repo.close()


# ── 论文管理 ──────────────────────────────────────────────


@router.post("/upload")
async def upload_paper(
    file: UploadFile = File(...),
    user_id: str = Form("default"),
    strict: str = Form("false"),  # 改成str接收
):
    strict_bool = strict.lower() == "true"
    file_bytes = await file.read()
    result = ingest_pdf(
        file_bytes=file_bytes,
        file_name=file.filename,
        source_type="user",
        user_id=user_id,
        strict=strict_bool,
    )
    logger.debug("[API] /upload get strict= %r -> %r", strict, strict_bool)
    if not result["success"]:
        raise HTTPException(status_code=409, detail=result["detail"])

    meta = result["paper_meta"]
    return {
        "doc_id": meta.doc_id,
        "title": meta.title,
        "author": meta.author,
        "year": meta.year,
        "file_name": meta.file_name,
        "status": meta.status,
    }


class ConfirmRequest(BaseModel):
    doc_id: str
    confirmed_title: str
    user_id: str = "default"


@router.post("/confirm")
def confirm_paper(req: ConfirmRequest):
    # 从注册表拿到paper_meta
    reg = registry.load_registry(req.user_id)
    if req.doc_id not in reg:
        raise HTTPException(status_code=404, detail="论文不存在，请重新上传")

    raw = reg[req.doc_id]
    paper_meta = registry.PaperMeta(**raw)
    pdf_path = str(PDF_DIR / paper_meta.file_name)

    result = confirm_and_index(
        paper_meta=paper_meta,
        pdf_path=pdf_path,
        confirmed_title=req.confirmed_title,
        user_id=req.user_id,
    )

    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["detail"])

    return {"success": True, "message": f"《{req.confirmed_title}》入库成功"}


@router.get("/papers")
def list_papers(user_id: str = "default"):
    reg = registry.load_registry(user_id)
    return {
        "count": len(reg),
        "papers": [
            {
                "doc_id": v["doc_id"],
                "title": v["title"],
                "author": v.get("author", ""),
                "year": v.get("year", ""),
                "status": v["status"],
                "chunk_count": v.get("chunk_count", -1),
                "file_name": v.get("file_name", ""),
                "source_url": v.get("source_url", ""),  # 新增
            }
            for v in reg.values()
        ],
    }


class IngestFromUrlRequest(BaseModel):
    pdf_urls: list[str]
    user_id: str = "default"


@router.post("/ingest_from_url")
async def ingest_from_url(req: IngestFromUrlRequest):
    results = []
    for pdf_url in req.pdf_urls:
        # 取 URL 末段作为文件名，兜底用 hash
        file_name = pdf_url.rstrip("/").split("/")[-1]
        if not file_name.endswith(".pdf"):
            file_name = file_name + ".pdf"

        pdf_path = PDF_DIR / file_name
        if not pdf_path.exists():
            response = requests.get(pdf_url)
            pdf_path.write_bytes(response.content)

        file_bytes = pdf_path.read_bytes()
        result = ingest_pdf(
            file_bytes,
            file_name,
            source_type="user",
            user_id=req.user_id,
            strict=True,
            source_url=pdf_url,  # 存原始 URL
        )
        if not result["success"]:
            results.append(
                {"pdf_url": pdf_url, "success": False, "detail": result["detail"]}
            )
            continue

        meta = result["paper_meta"]
        confirm_result = confirm_and_index(
            paper_meta=meta,
            pdf_path=str(pdf_path),
            confirmed_title=meta.title,
            user_id=req.user_id,
        )
        results.append(
            {
                "pdf_url": pdf_url,
                "success": confirm_result["success"],
                "title": meta.title,
            }
        )
    return results


@router.delete("/papers/{doc_id}")
def delete_paper_route(doc_id: str, user_id: str = "default"):
    result = delete_paper(doc_id, user_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["detail"])
    return {"success": True}


# ── 问答 ─────────────────────────────────────────────────


class AskRequest(BaseModel):
    question: str
    conv_id: str
    user_id: str = "default"
    translation: bool = False
    mode: Literal["normal", "discuss"] = "normal"
    parent_id: int | None = None


@router.post("/ask")
def ask_question(req: AskRequest):
    logger.debug(
        "[API] /ask get translation = %r | mode = %r", req.translation, req.mode
    )
    result = ask(
        question=req.question,
        conv_id=req.conv_id,
        user_id=req.user_id,
        translation=req.translation,
        mode=req.mode,
        parent_id=req.parent_id,
    )
    return result


class RegenerateRequest(BaseModel):
    question: str
    conv_id: str
    user_id: str = "default"
    translation: bool = False
    mode: Literal["normal", "discuss"] = "normal"
    parent_id: int
    old_agent_msg_id: int


@router.post("/regenerate")
def ask_question_regenerate(req: RegenerateRequest):
    logger.debug(
        "[API] /regenerate get translation = %r | mode = %r", req.translation, req.mode
    )
    result = regenerate(
        user_message=req.question,
        conv_id=req.conv_id,
        user_id=req.user_id,
        translation=req.translation,
        mode=req.mode,
        parent_id=req.parent_id,
        old_agent_msg_id=req.old_agent_msg_id,
    )
    return result


# ── 赞踩 ─────────────────────────────────────────────────
@router.patch("/message/{id}/like")
def message_like(id: int, liked: int):
    if liked not in (1, -1, 0):
        raise HTTPException(status_code=422, detail="liked 只能是 1, -1, 0")
    repo = MessageRepo()
    try:
        return repo.update_like(message_id=id, liked=liked)
    finally:
        repo.close()
