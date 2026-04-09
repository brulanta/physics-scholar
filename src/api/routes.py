# src/api/routes.py
import uuid
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from src.config import PDF_DIR
from src.core import registry
from src.core.ingestor import ingest_pdf, confirm_and_index
from src.rag.chain import ask
import requests

router = APIRouter()


# ── 会话 ─────────────────────────────────────────────────


@router.post("/conv_id/new")
def new_conversation():
    return {"conv_id": str(uuid.uuid4())}


# ── 论文管理 ──────────────────────────────────────────────


@router.post("/upload")
async def upload_paper(file: UploadFile = File(...), user_id: str = "default"):
    file_bytes = await file.read()
    result = ingest_pdf(
        file_bytes=file_bytes,
        file_name=file.filename,
        source_type="user",
        user_id=user_id,
    )
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
            }
            for v in reg.values()
        ],
    }


@router.post("/ingest_from_arxiv")
async def ingest_from_arxiv(arxiv_ids: list[str], user_id: str = "default"):
    results = []
    for arxiv_id in arxiv_ids:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
        response = requests.get(pdf_url)
        file_bytes = response.content
        file_name = f"{arxiv_id}.pdf"
        result = ingest_pdf(file_bytes, file_name, source_type="user", user_id=user_id)
        results.append({"arxiv_id": arxiv_id, **result})
    return results


# ── 问答 ─────────────────────────────────────────────────


class AskRequest(BaseModel):
    question: str
    conv_id: str
    user_id: str = "default"
    translation: bool = False


@router.post("/ask")
def ask_question(req: AskRequest):
    result = ask(
        question=req.question,
        conv_id=req.conv_id,
        user_id=req.user_id,
        translation=req.translation,
    )
    return {
        "answer": result["answer"],
        "warning": result.get("warning"),
    }
