# src/api/routes.py
import uuid
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from pydantic import BaseModel, Field
from src.config import PDF_DIR
from src.core import registry
from src.core.ingestor import ingest_pdf, confirm_and_index, delete_paper
from src.rag.chain import ask
from src.rag.graph import regenerate
import requests
from typing import Literal
from src.core.trim_thinking import extract_thinking, strip_thinking
from src.rag.memory import ConversationMemory, MessageRepo

router = APIRouter()


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
    try:
        return memory.clear()
    finally:
        memory.close()


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
    print(f"DEBUG strict={strict!r} -> {strict_bool!r}")
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
            }
            for v in reg.values()
        ],
    }


# 只下载，不写注册表，不入库
# 废弃
# @router.post("/download_from_arxiv")
# async def download_from_arxiv(arxiv_ids: list[str], user_id: str = "default"):
#     results = []
#     for arxiv_id in arxiv_ids:
#         pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
#         response = requests.get(pdf_url)
#         file_bytes = response.content
#         file_name = f"{arxiv_id}.pdf"
#         # 落盘但不写注册表
#         pdf_path = PDF_DIR / file_name
#         pdf_path.write_bytes(file_bytes)
#         results.append({"arxiv_id": arxiv_id, "file_name": file_name, "success": True})
#     return results


# 入库：复用ingest_pdf（文件已在盘上）
@router.post("/ingest_from_arxiv")
async def ingest_from_arxiv(arxiv_ids: list[str], user_id: str = "default"):
    results = []
    for arxiv_id in arxiv_ids:
        file_name = f"{arxiv_id}.pdf"
        pdf_path = PDF_DIR / file_name
        if not pdf_path.exists():
            # 还没下载过，先下载
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
            response = requests.get(pdf_url)
            pdf_path.write_bytes(response.content)

        file_bytes = pdf_path.read_bytes()
        # strict=True，arxiv论文元数据完整
        result = ingest_pdf(
            file_bytes, file_name, source_type="user", user_id=user_id, strict=True
        )
        if not result["success"]:
            results.append(
                {"arxiv_id": arxiv_id, "success": False, "detail": result["detail"]}
            )
            continue

        # 自动confirm，arxiv标题可信
        meta = result["paper_meta"]
        confirm_result = confirm_and_index(
            paper_meta=meta,
            pdf_path=str(pdf_path),
            confirmed_title=meta.title,
            user_id=user_id,
        )
        results.append(
            {
                "arxiv_id": arxiv_id,
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
    print(f"DEBUG translation={req.translation!r} mode={req.mode!r}")
    result = ask(
        question=req.question,
        conv_id=req.conv_id,
        user_id=req.user_id,
        translation=req.translation,
        mode=req.mode,
        parent_id=req.parent_id,
    )
    thinking = extract_thinking(result.get("answer", ""))
    if thinking:
        print(thinking)
    result["answer"] = strip_thinking(result["answer"])
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
    print(f"DEBUG translation={req.translation!r} mode={req.mode!r}")
    result = regenerate(
        user_message=req.question,
        conv_id=req.conv_id,
        user_id=req.user_id,
        translation=req.translation,
        mode=req.mode,
        parent_id=req.parent_id,
        old_agent_msg_id=req.old_agent_msg_id,
    )
    thinking = extract_thinking(result.get("answer", ""))
    if thinking:
        print(thinking)
    result["answer"] = strip_thinking(result["answer"])
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
