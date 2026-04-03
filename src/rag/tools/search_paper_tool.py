# src/rag/tools/search_paper_tool.py
from langchain.tools import tool
from pydantic import BaseModel, Field
from src.core.registry import search_by_keyword


class SearchPaperRequest(BaseModel):
    keywords: list[str] = Field(
        ...,
        description="关键词列表，提取自用户提到的论文名称、作者、年份。短语优于单词，如['reservoir computing', 'optical amplifier']",
    )


def make_search_tool(user_id: str):
    @tool
    def search_paper_tool(keywords: list[str]) -> list[dict]:
        """
        在本地论文注册表中检索论文，返回匹配的标题和doc_id。
        根据用户描述或你自己的知识推断可能的论文标题、作者、年份，
        提取成关键词列表传入。
        短语优于单词，例如：['Jianping Yao', 'microwave photonics']
        如果用户描述模糊（如"开山论文"、"最经典的那篇"），
        可以结合你的学科知识推断可能的作者或标题关键词进行尝试。
        返回结果按匹配度排序，score越高越相关。
        """
        results = search_by_keyword(keywords, user_id)
        if not results:
            return [{"message": "未找到匹配论文，请尝试其他关键词"}]
        return results

    return search_paper_tool
