# src/rag/tools/lookup_local_paper_id.py
from langchain.tools import tool
from pydantic import BaseModel, Field
from src.core.registry import search_by_keyword
import json
from src.utils.logger import get_logger

logger = get_logger(__name__)


class SearchPaperRequest(BaseModel):
    keywords: list[str] = Field(
        ...,
        description="用于匹配论文的关键词或短语列表，应尽量来自论文标题、作者或年份等可检索字段。避免使用宽泛主题词，应选择更具体、可能出现在标题中的片段。",
    )


def make_paper_id_search_tool(user_id: str):
    @tool(args_schema=SearchPaperRequest)
    def lookup_local_paper_id(keywords: list[str]) -> str:
        """
        在本地论文注册表中按关键词检索论文，返回匹配的标题和 doc_id。

        输入应为可能出现在论文标题、作者或年份中的关键词或短语，
        而不是宽泛主题词。

        返回结果按匹配度排序。。
        """
        results = search_by_keyword(keywords, user_id)
        logger.info("[paper_id_search] params: %s", keywords)
        if not results:
            return json.dumps(
                {
                    "success": False,
                    "message": "未找到匹配论文，请尝试其他关键词",
                    "results": [],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"success": True, "results": results}, ensure_ascii=False, indent=2
        )

    return lookup_local_paper_id
