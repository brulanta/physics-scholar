import requests  # 已是依赖（无 torch），用于调硅基流动 /rerank
from rank_bm25 import BM25Okapi
from langchain_core.documents import Document
from src.core.ingestor import get_vectorstore
from src.core import chroma_gen
from src.core.chunker import _CJK, _WORD  # 复用 chunker 的 CJK/英文词正则，分词口径一致
from src import config  # 调用时取 EMBEDDING_*/RERANK_*，跟随 reload_config
from pydantic import BaseModel, Field
from typing import Literal
from langchain.tools import tool
from src.utils.logger import get_logger

logger = get_logger(__name__)

vs = get_vectorstore()

# 全库 BM25 一次抓取的 chunk 上限（本地单用户库通常数百~数千，足够覆盖；防极端爆内存）。
_BM25_CORPUS_LIMIT = 5000


def _bm25_tokenize(text: str) -> list[str]:
    """轻量分词：英文按词、中文按单字 + 字二元组（兼顾召回与精度），零额外打包。

    与 chunker 复用同一套 CJK/英文词正则。中文不引 jieba：单字保召回，
    相邻二元组补精度（如「滤波」「带宽」作为整体命中）。
    """
    low = text.lower()
    words = _WORD.findall(low)
    cjk = _CJK.findall(low)  # 单字列表
    bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return words + cjk + bigrams


def _chunk_key(doc) -> tuple:
    """chunk 唯一键 = (doc_id, section, chunk_index)，对应 chroma id f'{doc_id}_{chunk_index}'。"""
    m = doc.metadata
    return (m.get("doc_id"), m.get("section"), m.get("chunk_index"))


def _rrf_merge(ranked_lists: list, c: int = 60) -> list:
    """Reciprocal Rank Fusion：按各路名次融合去重，无需校准不同分数量纲。

    分数 = Σ 1/(c + rank)；同一 chunk 在多路命中得分累加。按融合分降序返回。
    """
    score: dict = {}
    keep: dict = {}
    for docs in ranked_lists:
        for rank, doc in enumerate(docs):
            key = _chunk_key(doc)
            score[key] = score.get(key, 0.0) + 1.0 / (c + rank)
            keep[key] = doc
    return [keep[key] for key in sorted(score, key=score.get, reverse=True)]


def hybrid_search(query: str, *, search_filter: dict, fetch_k: int, store=None) -> list:
    """多路召回：向量 + BM25，RRF 融合去重，返回 ≤fetch_k 个候选（喂给重排）。

    向量路与 BM25 路都在同一 search_filter（多租户隔离）内取数，保证不串户。
    BM25 在「该过滤命中的全部 chunk」上现建索引（本地库规模可接受）。
    store 默认用生产单例 vs；eval 脚本可传入独立 collection 复用同一份逻辑。
    """
    store = store if store is not None else vs
    vec_docs = store.similarity_search(query, k=fetch_k, filter=search_filter)

    corpus = store._collection.get(
        where=search_filter,
        include=["documents", "metadatas"],
        limit=_BM25_CORPUS_LIMIT,
    )
    bm25_docs = []
    if corpus["documents"]:
        bm25 = BM25Okapi([_bm25_tokenize(d) for d in corpus["documents"]])
        scores = bm25.get_scores(_bm25_tokenize(query))
        top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:fetch_k]
        bm25_docs = [
            Document(page_content=corpus["documents"][i], metadata=corpus["metadatas"][i])
            for i in top
        ]

    return _rrf_merge([vec_docs, bm25_docs])[:fetch_k]


def _rerank(query: str, docs: list, top_n: int) -> list:
    """用硅基流动 bge-reranker-v2-m3 对候选 docs 精排，返回 top_n。

    凭证/URL 复用 embedding（调用时取 config.EMBEDDING_*，故前端改 key 经 reload_config
    刷新后自动跟随）。任何异常都优雅降级为「按原候选顺序截 top_n」，绝不抛进 agent。
    """
    if not config.RERANK_ENABLED or not docs:
        return docs[:top_n]
    try:
        r = requests.post(
            f"{config.EMBEDDING_BASE_URL}/rerank",  # 复用 embedding base_url
            json={
                "model": config.RERANK_MODEL,
                "query": query,
                "documents": [d.page_content for d in docs],
                "top_n": min(top_n, len(docs)),
                "return_documents": False,
            },
            headers={"Authorization": f"Bearer {config.EMBEDDING_API_KEY}"},  # 复用 embedding key
            timeout=config.RERANK_TIMEOUT,
        )
        r.raise_for_status()
        order = [it["index"] for it in r.json()["results"]]  # results 已按相关度降序
        return [docs[i] for i in order][:top_n]
    except Exception as e:
        logger.warning("[RAG] rerank 失败，回退候选原序: %s", e)
        return docs[:top_n]


class RagToolRequest(BaseModel):
    query: str = Field(
        ...,
        description="用于向量检索的查询语句，应该是完整的语义描述，而不是单个关键词。例如：'reservoir computing的训练方法' 而不是 'training'",
    )
    k: int = Field(
        default=5, description="召回文段数量，默认5，内容复杂的问题可以适当增大"
    )
    section: Literal["body", "reference"] = Field(
        default="body",
        description="限定召回范围：body=论文正文，reference=参考文献列表",
    )
    doc_id: str = Field(
        default="",
        description="限定召回某篇特定论文，留空则全库检索。需先调用lookup_local_paper_id获取doc_id",
    )


def format_context(docs) -> str:
    """格式化检索文段串。串头带 `rag:<doc_id>` source_id——model 引用时只抄这个 id
    （bind-by-id），harness 据 id 查工具结果填完整元信息（enrichment）。
    doc_id 在 doc.metadata 里本就有，写进串是 enabling edit（见 citation plan）。
    """
    chunks = []
    for doc in docs:
        doc_id = doc.metadata.get("doc_id", "")
        title = doc.metadata.get("title", "未知")
        page = doc.metadata.get("page_number", "")
        # 串头 source_id 段：rag:<doc_id>；其后 title + page 供人/模型阅读，不参与绑定
        sid = f"rag:{doc_id}" if doc_id else ""
        sid_str = f"[{sid} | " if sid else "["
        page_str = f", Page {page}" if page else ""
        chunks.append(f"{sid_str}{title}{page_str}]\n{doc.page_content}")
    return "\n\n---\n\n".join(chunks)


def build_filter(user_id: str, section: str, doc_id: str = ""):
    """
    构造向量数据库的过滤条件。
    保证所有的查询都必须锁定在当前 user_id 下。
    """
    # 基础过滤：必须是当前用户的，且匹配对应的正文/参考文献区域
    filters = [{"user_id": user_id}, {"section": section}]

    # 增强过滤：如果有特定的 doc_id，则加入
    if doc_id:
        filters.append({"doc_id": doc_id})

    # 如果只有一个条件（虽然这里至少有两个），直接返回字典；否则返回 $and 组合
    if len(filters) == 1:
        return filters[0]
    return {"$and": filters}


def make_rag_tool(user_id: str):
    """
    闭包工厂：为特定用户生成具有数据隔离能力的 RAG 工具。
    """

    @tool(args_schema=RagToolRequest)
    def rag_tool(
        query: str,
        k: int = 5,
        section: Literal["body", "reference"] = "body",
        doc_id: str = "",
    ) -> str:
        """
        从本地向量知识库中检索与问题语义相关的文段，作为回答依据。

        ## 两种模式

        ### 跨库检索
        不传 doc_id，对整个本地库做语义检索。

        ### 定向检索
        传入 doc_id，检索范围限定为该论文。doc_id 由 lookup_local_paper_id 获取。
        section 参数可选 body（正文）或 reference（参考文献）。
        """
        # 0. 跨进程写后读守卫：若主进程已入库/删除（代际令牌推进），重建本子进程的
        #    chroma 连接，否则常驻 HNSW 看不到新文档。内嵌路径下为 no-op。
        chroma_gen.ensure_fresh()

        # 1. 调用外部的 build_filter，逻辑清晰且可复用
        search_filter = build_filter(user_id=user_id, section=section, doc_id=doc_id)
        # 2. 过取候选喂给重排：k*RAG_FETCH_MULTIPLIER 个。探针证实候选池越大重排天花板
        #    越高。HYBRID 开启时走「向量 + BM25，RRF 融合」（BM25 把精确术语命中的题
        #    送进候选池）；关闭时回退纯向量（kill switch）。
        fetch_k = max(k, k * config.RAG_FETCH_MULTIPLIER)
        if config.RAG_HYBRID_ENABLED:
            candidates = hybrid_search(query, search_filter=search_filter, fetch_k=fetch_k)
        else:
            candidates = vs.similarity_search(query, k=fetch_k, filter=search_filter)
        # 3. cross-encoder 精排到 k（失败优雅降级为候选原序截 k）
        docs = _rerank(query, candidates, k)
        logger.info(
            "[RAG] User: %s | Query: %s | Doc_ID: %s | hybrid: %s | 过取: %d→重排: %d",
            user_id,
            query,
            doc_id or "All",
            config.RAG_HYBRID_ENABLED,
            len(candidates),
            len(docs),
        )
        return format_context(docs)

    return rag_tool
