# src/llm.py  ← 专门导出 llm 实例
from langchain_openai import ChatOpenAI
from src.config import (
    MAIN_LLM_API_KEY,
    MAIN_LLM_BASE_URL,
    MAIN_LLM_MODEL,
    DEEPSEEK_EXTRA_BODY,
)

main_llm = ChatOpenAI(
    model=MAIN_LLM_MODEL,
    temperature=0.15,
    api_key=MAIN_LLM_API_KEY,
    base_url=MAIN_LLM_BASE_URL,
    extra_body=DEEPSEEK_EXTRA_BODY,
)
