# src/llm.py  ← 专门导出 llm 实例
from langchain_openai import ChatOpenAI
from src.config import (
    MAIN_LLM_API_KEY,
    MAIN_LLM_BASE_URL,
    MAIN_LLM_MODEL,
    SUB_LLM_API_KEY,
    SUB_LLM_BASE_URL,
    SUB_LLM_MODEL,
    DEEPSEEK_EXTRA_BODY,
)

# 主 API：用于复杂推理和对话
main_llm = ChatOpenAI(
    model=MAIN_LLM_MODEL,
    temperature=0.15,
    api_key=MAIN_LLM_API_KEY,
    base_url=MAIN_LLM_BASE_URL,
    extra_body=DEEPSEEK_EXTRA_BODY,
    max_retries=5,  # ← 核心加固：网络抖动或 429 限流时，自动指数退避重试 5 次
)

# 副 API：用于信息提取、打分等结构化任务（建议温度设为 0）
sub_llm = ChatOpenAI(
    model=SUB_LLM_MODEL,
    temperature=0.0,
    api_key=SUB_LLM_API_KEY,
    base_url=SUB_LLM_BASE_URL,
    max_tokens=1024,
    extra_body=DEEPSEEK_EXTRA_BODY,
    max_retries=5,  # ← 核心加固：循环打分时如果冲太快触发了 RPM 限制，会自动静默重试
)
