from src.core import registry, hash_file, extractor, parser, chunker
from pathlib import Path
from datetime import datetime
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from src.config import CHROMA_DIR
from langchain_core.documents import Document

_embeddings = None
_vectorstore = None


def get_vectorstore():
    global _vectorstore, _embeddings

    if _vectorstore is None:
        # 1️⃣ 初始化 embeddings（只做一次）
        if _embeddings is None:
            _embeddings = HuggingFaceEmbeddings(
                model_name="paraphrase-multilingual-MiniLM-L12-v2"
            )

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


def ingest_pdf(
    pdf_path: str, source_type: str = "user", user_id: str = "", strict: bool = False
) -> dict:
    """
    第一阶段：哈希去重 + 提取元数据
    返回待确认的元数据给上层，不写注册表
    """
    # 1. 哈希去重
    doc_id = hash_file.get_pdf_hash(pdf_path)
    if registry.is_duplicate(doc_id, user_id):
        return {"success": False, "detail": "文件已存在"}

    # 2. 提取元数据
    metadata = extractor.extract_metadata(pdf_path, strict)

    # 3. 组装PaperMeta，但先不写注册表
    paper_meta = registry.PaperMeta(
        doc_id=doc_id,
        file_name=Path(pdf_path).name,
        upload_time=datetime.now().isoformat(),
        source_type=source_type,
        user_id=user_id,
        status="pending",
        **metadata,  # title, author, year
    )

    # 4. 返回给上层等待用户确认，不注册
    return {"success": True, "paper_meta": paper_meta}


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
    registry.register_paper(paper_meta, user_id)

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
        # 任一环节失败，清除注册表记录
        registry.remove_paper(paper_meta.doc_id, user_id)
        return {"success": False, "detail": str(e)}
