# src/api/routes.py
import uuid
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Response, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from src.config import PDF_DIR, get_config_dict, save_config_dict
from src.core import registry
from src.core.ingestor import ingest_pdf, confirm_and_index, delete_paper
from src.rag.graph import chat_stream, regenerate_stream
import requests
from typing import Literal
from src.rag.memory import ConversationMemory, MessageRepo, ConversationRepo
from src.rag.citation import enrich_refs, parse_refs
from src.core.citation_store import load_enrichment_for_message
from src.utils.logger import get_logger
import httpx
import sys, os, subprocess, asyncio

router = APIRouter()
logger = get_logger(__name__)


# src/api/routes.py里加
@router.get("/health")
def health():
    # restarting：本进程正主动重启（托盘/配置页触发），供前端切到「正在重启」转圈遮罩，
    # 与「意外断联/退出」（前端连续失败判 down、显示 X）区分开。
    from src import service_state

    return {
        "status": "ok",
        "version": "0.1.0",
        "restarting": service_state.is_restarting(),
    }


# ── 全局配置 ─────────────────────────────────────────────────


class LLMConfig(BaseModel):
    api_key: str = ""
    base_url: str = ""
    model: str = ""


class ToolsConfig(BaseModel):
    jina_api_key: str = ""
    s2_api_key: str = ""
    openalex_email: str = ""
    openalex_api_key: str = ""  # 须与 config.py(yaml tools.openalex_api_key) + 前端对齐；
    # 旧名 openalex_key 会让 Pydantic 静默丢弃前端发的 openalex_api_key → 存不进 yaml → 重启栏空


class EmbeddingConfig(BaseModel):
    api_key: str = ""


class UserConfig(BaseModel):
    main_llm: LLMConfig = LLMConfig()
    sub_llm: LLMConfig = LLMConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    tools: ToolsConfig = ToolsConfig()


@router.get("/config")
async def get_config():
    return get_config_dict()


@router.post("/config")
async def update_config(data: UserConfig):
    # 只落盘。配置生效走「强制整程重启」：前端保存成功后即触发 /config/restart，新 exe
    # 会带新 key 重拉 MCP 子进程。故此处不再 await mcp_client.restart() 热重启会话——
    # 那次热重启会被随后的整程重启覆盖，纯属浪费（且曾是 P2-a CancelledError 场景）。
    save_config_dict(data.model_dump())
    return {"success": True, "message": "配置已保存"}


class FetchModelsRequest(BaseModel):
    base_url: str
    api_key: str


@router.post("/config/fetch-models")  # 斜杠补上
async def fetch_models(data: FetchModelsRequest):
    url = data.base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {data.api_key}"}

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url=url, headers=headers)
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"上游错误：{e.response.status_code}",
        )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail=f"无法连接到 {data.base_url}")

    model_ids = [m["id"] for m in r.json().get("data", [])]
    return {"models": model_ids}


@router.post("/config/restart")
async def restart_app():
    # 置「正在重启」标志：此后 /api/health 会带 restarting=true，前端（含托盘重启时
    # 不知情的旧页面）轮询读到即显示转圈遮罩，而非误判为退出（X）。
    from src import service_state

    service_state.mark_restarting()

    async def _do_restart():
        await asyncio.sleep(0.5)  # 等前端收到200响应
        exe = sys.executable

        # CREATE_BREAKAWAY_FROM_JOB：与 app.py 托盘「重启」同理——本进程在 kill-on-close
        # 的 Job 里，新 exe 若不脱离，随后的 os._exit(0) 关 Job 句柄会把它连带杀掉
        # （前后端全关、无自动拉起）。Job 已设 BREAKAWAY_OK，故子进程可显式脱离；
        # 非 Windows 上该 flag 取 0、天然 no-op。
        # PS_SUPPRESS_BROWSER=1：新进程不再开浏览器窗口——旧前端窗口（浏览器已脱离 Job、
        # 不受 os._exit 波及）仍在，由其 ServiceMask 收到 200 自刷新到新后端，避免重复窗口。
        subprocess.Popen(
            [exe],
            cwd=os.path.dirname(exe),
            creationflags=getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),
            env=dict(os.environ, PS_SUPPRESS_BROWSER="1"),
        )

        os._exit(0)

    asyncio.create_task(_do_restart())
    return {"message": "正在重启，请稍候..."}


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
        messages = memory.get_tree()
        # 展示期 merge（想法 2(b) bind-by-id）：对 assistant 消息，按 message_id 查
        # sidecar enrichment，把 lean ref（[source_id] | 摘抄）merge 成 rich（完整引用
        # + 可点 url）。user 消息不动；历史消息无 sidecar 时 parse_refs 返回空、no-op。
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content") or ""
            if not parse_refs(content):
                continue  # 无 lean ref，跳过（省一次 sidecar 查询）
            try:
                enrich_map = load_enrichment_for_message(msg["id"])
                if enrich_map:
                    msg["content"] = enrich_refs(content, enrich_map)
            except Exception as e:
                logger.warning("[tree] msg %s enrich 失败，返回 lean: %s", msg.get("id"), e)
        return {"messages": messages}
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

# SSE 响应头：禁缓存 + 关闭反代缓冲（Nginx/PyInstaller 直连均需），保持长连接
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


class AskRequest(BaseModel):
    question: str
    conv_id: str
    user_id: str = "default"
    translation: bool = False
    # mode 真相源 = src/rag/prompts/modules/__init__.py 的 _MODE_MODULES.keys()
    # 新增 mode 时：① _MODE_MODULES 加键 ② 建 modules/<new>/ 子包 ③ 此处 Literal 加值
    # ④ spec hiddenimports 加 src.rag.prompts.modules.<new>。routes 层保留 Literal 是 FastAPI schema 校验需要。
    mode: Literal["normal", "discuss"] = "normal"
    parent_id: int | None = None


@router.post("/ask")
async def ask_question(req: AskRequest, request: Request):
    # 真流式：返回 SSE 流，事件契约见 graph._consume_events。
    # 非流式兜底仍在 chain.ask()（供测试/脚本），此路由不再走它。
    logger.debug(
        "[API] /ask(stream) translation = %r | mode = %r", req.translation, req.mode
    )
    gen = chat_stream(
        user_message=req.question,
        conv_id=req.conv_id,
        request=request,
        user_id=req.user_id,
        translation=req.translation,
        mode=req.mode,
        parent_id=req.parent_id,
    )
    return StreamingResponse(gen, media_type="text/event-stream", headers=SSE_HEADERS)


class RegenerateRequest(BaseModel):
    question: str
    conv_id: str
    user_id: str = "default"
    translation: bool = False
    # mode 真相源 = src/rag/prompts/modules/__init__.py 的 _MODE_MODULES.keys()（见 AskRequest.mode 注释）
    mode: Literal["normal", "discuss"] = "normal"
    parent_id: int
    old_agent_msg_id: int


@router.post("/regenerate")
async def ask_question_regenerate(req: RegenerateRequest, request: Request):
    # 真流式重生成；非流式兜底仍在 graph.regenerate()（供测试/脚本）。
    logger.debug(
        "[API] /regenerate(stream) translation = %r | mode = %r",
        req.translation,
        req.mode,
    )
    gen = regenerate_stream(
        user_message=req.question,
        conv_id=req.conv_id,
        request=request,
        user_id=req.user_id,
        translation=req.translation,
        mode=req.mode,
        parent_id=req.parent_id,
        old_agent_msg_id=req.old_agent_msg_id,
    )
    return StreamingResponse(gen, media_type="text/event-stream", headers=SSE_HEADERS)


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
