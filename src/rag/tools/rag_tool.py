import requests  # 已是依赖（无 torch），用于调硅基流动 /rerank
from src.core.ingestor import get_vectorstore
from src import config  # 调用时取 EMBEDDING_*/RERANK_*，跟随 reload_config
from pydantic import BaseModel, Field
from typing import Literal
from langchain.tools import tool
from src.utils.logger import get_logger

logger = get_logger(__name__)

vs = get_vectorstore()


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
    chunks = []
    for doc in docs:
        title = doc.metadata.get("title", "未知")
        page = doc.metadata.get("page_number", "")
        page_str = f", Page {page}" if page else ""
        chunks.append(f"[{title}{page_str}]\n{doc.page_content}")
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

        # 1. 调用外部的 build_filter，逻辑清晰且可复用
        search_filter = build_filter(user_id=user_id, section=section, doc_id=doc_id)
        # 2. 向量过取：召回 k*RAG_FETCH_MULTIPLIER 个候选喂给重排，重排再截到 k。
        #    探针证实候选池越大重排天花板越高，且重排能把真 chunk 顶进 top-3。
        fetch_k = max(k, k * config.RAG_FETCH_MULTIPLIER)
        candidates = vs.similarity_search(query, k=fetch_k, filter=search_filter)
        # 3. cross-encoder 精排到 k（失败优雅降级为候选原序截 k）
        docs = _rerank(query, candidates, k)
        logger.info(
            "[RAG] User: %s | Query: %s | Doc_ID: %s | 过取: %d→重排: %d",
            user_id,
            query,
            doc_id or "All",
            len(candidates),
            len(docs),
        )
        return format_context(docs)

    return rag_tool
