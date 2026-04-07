from src.core.ingestor import get_vectorstore
from pydantic import BaseModel, Field
from typing import Literal
from langchain.tools import tool

vs = get_vectorstore()


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
        description="限定召回某篇特定论文，留空则全库检索。需先调用search_paper_tool获取doc_id",
    )


def format_context(docs) -> str:
    chunks = []
    for doc in docs:
        title = doc.metadata.get("title", "未知")
        page = doc.metadata.get("page_number", "")
        page_str = f", Page {page}" if page else ""
        chunks.append(f"[{title}{page_str}]\n{doc.page_content}")
    return "\n\n---\n\n".join(chunks)


def build_filter(section: str, doc_id: str | None):
    filters = [{"section": section}]  # 用传入的section
    if doc_id:
        filters.append({"doc_id": doc_id})
    if len(filters) == 1:
        return filters[0]
    return {"$and": filters}


@tool
def rag_tool(request: RagToolRequest) -> str:
    """
    从本地向量知识库中检索与问题语义相关的文段，作为回答依据。

    适用于以下情况：
    - 需要引用论文或教材内容支撑回答
    - 用户询问具体研究工作、方法或结论
    - 用户希望讨论某一篇论文（可配合 search_paper_tool 使用 doc_id）

    注意：
    - 返回内容为检索到的相关文段，应基于其进行回答，而非凭空补充未提及的信息
    """

    restriever = vs.as_retriever(
        search_kwargs={
            "k": request.k,
            "filter": build_filter(request.section, request.doc_id),
        }
    )
    docs = restriever.invoke(request.query)
    print(f"检索了RAG库，关键词为{request.query},限定id为{request.doc_id}")
    return format_context(docs)
