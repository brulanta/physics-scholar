from src.core import registry, hash_file, extractor, parser, chunker
from pathlib import Path
from datetime import datetime
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from src.config import CHROMA_DIR, PDF_DIR
from langchain_core.documents import Document
from src.core.registry import load_registry, remove_paper

_embeddings = None
_vectorstore = None


def get_vectorstore():
    global _vectorstore, _embeddings

    if _vectorstore is None:
        # 1️⃣ 初始化 embeddings（只做一次）
        if _embeddings is None:
            _embeddings = HuggingFaceEmbeddings(
                model_name="paraphrase-multilingual-MiniLM-L12-v2",
                cache_folder="./models",
            )  # model_name="BAAI/bge-m3",

        # 2️⃣ 初始化 vectorstore
        _vectorstore = Chroma(
            embedding_function=_embeddings,
            persist_directory=str(CHROMA_DIR),
            collection_name="rag_langchain",
            collection_metadata={"hnsw:space": "cosine"},
        )

    return _vectorstore


def write_to_chroma(chunks, ref_chunks, paper_meta, user_id):

    base_metadata = {
        "doc_id": paper_meta.doc_id,
        "source_type": paper_meta.source_type,
        "user_id": user_id,
        "title": paper_meta.title,
        "author": paper_meta.author,
        "year": paper_meta.year,
    }

    docs_body = [
        Document(
            page_content=chunk,
            metadata={**base_metadata, "section": "body", "chunk_index": i},
        )
        for i, chunk in enumerate(chunks)
    ]
    offset = len(chunks)
    docs_reference = [
        Document(
            page_content=ref_chunk,
            metadata={
                **base_metadata,
                "section": "reference",
                "chunk_index": offset + i,
            },
        )
        for i, ref_chunk in enumerate(ref_chunks)
    ]
    all_docs = docs_body + docs_reference
    all_ids = [f"{paper_meta.doc_id}_{i}" for i in range(len(docs_body))] + [
        f"{paper_meta.doc_id}_ref_{i}" for i in range(len(docs_reference))
    ]

    vs = get_vectorstore()

    vs.add_documents(documents=all_docs, ids=all_ids)

    return len(all_ids)  # 返回chunk总数，用于回填


def delete_from_chroma(doc_id: str, user_id: str = "default") -> dict:
    vs = get_vectorstore()
    result = vs._collection.get(
        where={"$and": [{"doc_id": doc_id}, {"user_id": user_id}]},
        limit=1,
        include=[],  # 只要 id
    )
    exists = bool(result["ids"])
    if not exists:
        return {"success": True, "existed": False}
    try:
        vs.delete(where={"$and": [{"doc_id": doc_id}, {"user_id": user_id}]})
        return {"success": True, "existed": True}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def delete_from_disk(doc_id: str, user_id: str = "default") -> dict:
    reg = load_registry(user_id)
    file_metadata = reg.get(doc_id, "")
    if file_metadata:
        file_name = file_metadata.get("file_name", "")
        pdf_path = PDF_DIR / file_name
        if pdf_path.exists():
            try:
                pdf_path.unlink()  # 清本地pdf
                return {"success": True, "existed": True}
            except Exception as e:
                return {"success": False, "detail": str(e)}
    return {"success": True, "existed": False}


def delete_paper(doc_id: str, user_id: str = "default"):
    try:
        res1 = delete_from_disk(doc_id, user_id)
        res2 = delete_from_chroma(doc_id, user_id)
        res3 = remove_paper(doc_id, user_id)

        if not (res1["success"] and res2["success"] and res3["success"]):
            return {
                "success": False,
                "status": "failed",
                "detail": f"{res1}\n{res2}\n{res3}",
            }

        # 判断是否空删
        existed_any = res1.get("existed") or res2.get("existed") or res3.get("existed")

        if existed_any:
            return {"success": True, "status": "deleted", "detail": "资源已清理"}
        else:
            return {
                "success": True,
                "status": "not_found",
                "detail": "资源不存在（幂等删除）",
            }

    except Exception as e:
        return {"success": False, "status": "exception", "detail": str(e)}


def ingest_pdf(
    file_bytes: bytes,
    file_name: str,
    source_type: str = "user",
    user_id: str = "default",
    strict: bool = False,
    source_url: str = "",  # 新增
) -> dict:
    """
    第一阶段：保存文件 + 哈希去重 + 提取元数据 + 写注册表(pending)
    返回待确认的元数据给上层
    """
    # 0. 哈希去重
    doc_id = hash_file.get_pdf_hash(file_bytes)
    if registry.is_duplicate(doc_id, user_id):
        return {"success": False, "detail": "文件已存在"}

    # 1. 确认不重复，再写文件
    save_path = PDF_DIR / file_name
    save_path.write_bytes(file_bytes)

    try:
        # 2. 提取元数据
        metadata = extractor.extract_metadata(str(save_path), strict)

        # 3. 组装PaperMeta
        paper_meta = registry.PaperMeta(
            doc_id=doc_id,
            file_name=file_name,
            upload_time=datetime.now().isoformat(),
            source_type=source_type,
            source_url=source_url,  # 新增
            user_id=user_id,
            status="pending",
            **metadata,
        )

        # 4. 写注册表
        registry.register_paper(paper_meta)

        return {"success": True, "paper_meta": paper_meta}

    except Exception as e:
        save_path.unlink(missing_ok=True)
        registry.remove_paper(doc_id, user_id)  # 写没写都调，反正是安全的
        return {"success": False, "detail": str(e)}


def confirm_and_index(
    paper_meta: registry.PaperMeta,
    pdf_path: str,
    confirmed_title: str,
    user_id: str = "",
) -> dict:
    """
    第二阶段：用户确认后，解析入库
    """
    # 1. 覆盖title，写入注册表，status=processing
    paper_meta.title = confirmed_title
    paper_meta.status = "processing"
    registry.register_paper(paper_meta)

    try:
        # 2. 解析
        blocks = parser.parse_pdf(pdf_path)
        page_count = blocks["page_count"]

        # 3. 切片
        chunks = chunker.chunker(blocks["body"])
        ref_chunks = chunker.chunker(blocks["reference"])

        # 4. 入库ChromaDB
        chunk_count = write_to_chroma(chunks, ref_chunks, paper_meta, user_id)

        # 5. 全部成功，回填并更新状态
        registry.update_after_index(paper_meta.doc_id, chunk_count, page_count, user_id)
        return {"success": True}

    except Exception as e:
        registry.remove_paper(paper_meta.doc_id, user_id)  # 清注册表
        pdf_path = PDF_DIR / paper_meta.file_name
        if pdf_path.exists():
            pdf_path.unlink()  # 清本地pdf
        return {"success": False, "detail": str(e)}
