# src/llm.py  ← 连接组合 + 通用工厂 + 实例
#
# 设计：连接组合（可继承）与运行参数（按用途定制）正交分离。
# - LLMEndpoint：一个 api_key/base_url/model 组合。fallback 在 config 层完成
#   （SUB_LLM_* or MAIN_LLM_*），本结构拿到的是已 fallback 后的值，不感知。
# - build_llm：通用工厂，传入组合 + 自定义参数 → ChatOpenAI。只固定 max_retries
#   （全网加固）+ extra_body 默认（禁思考/禁并行工具，当前全网一致；未来接非
#   DeepSeek 兼容供应商时调用方传 extra_body={} 覆盖）。temperature/streaming/
#   max_tokens/stream_usage 等一律由调用方显式传——工厂不藏默认，避免用途间互相污染。
# - 单例 main_llm/sub_llm 在 import 时建（捕获当时 config），与 README 声明的
#   「改 config 需重启」一致；子 agent 每次 retrieve 调 build_llm 重建，但不刷新
#   SUB_ENDPOINT 元组本身——保持与 main_llm 同语义。
from typing import NamedTuple
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


class LLMEndpoint(NamedTuple):
    """一个 LLM 连接组合：api_key + base_url + model。"""
    api_key: str
    base_url: str
    model: str


MAIN_ENDPOINT = LLMEndpoint(MAIN_LLM_API_KEY, MAIN_LLM_BASE_URL, MAIN_LLM_MODEL)
SUB_ENDPOINT = LLMEndpoint(SUB_LLM_API_KEY, SUB_LLM_BASE_URL, SUB_LLM_MODEL)


def build_llm(
    ep: LLMEndpoint,
    *,
    extra_body=DEEPSEEK_EXTRA_BODY,
    max_retries: int = 5,
    **kwargs,
) -> ChatOpenAI:
    """通用 LLM 工厂：传入连接组合 + 自定义参数 → ChatOpenAI。

    temperature/streaming/max_tokens/stream_usage 等由调用方按用途显式传入。
    """
    return ChatOpenAI(
        model=ep.model,
        api_key=ep.api_key,
        base_url=ep.base_url,
        extra_body=extra_body,
        max_retries=max_retries,  # ← 网络抖动/429 限流时自动指数退避重试
        **kwargs,
    )


# 主 LLM：复杂推理与对话。主 graph 走 astream_events（流式图）→ streaming=True 对齐，
# 否则非流式图拿不到 on_chat_model_stream 逐 token 事件。stream_usage=True 让流式
# 末 chunk 带 usage（自定义 base_url 不会像默认 OpenAI 自动开，须显式设）→ ainvoke
# 聚合后 usage_metadata 有值（token 计量需）。
main_llm = build_llm(MAIN_ENDPOINT, temperature=0.15, streaming=True, stream_usage=True)

# 副 LLM 单例（兼容旧引用）：jina 切片打分 / extractor 元信息提取。结构化短任务，
# temp 0 / max_tokens 1024 / 非流式，行为与重构前字节级一致。
sub_llm = build_llm(SUB_ENDPOINT, temperature=0.0, max_tokens=1024)
