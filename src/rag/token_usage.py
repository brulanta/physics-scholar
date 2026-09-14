# src/rag/token_usage.py  ← per-request token 计量（BYOK 只计不计价）
#
# 用 contextvars.ContextVar 携带 per-request usage dict。jina_tool 是模块级 @tool、
# 无法用闭包传 per-request 累加器；ContextVar 在 astream_events 同一 async task 内
# 的节点/工具都能读到（asyncio 子任务继承上下文）。bucket 标签（main/sub/jina）在各
# 调用点显式传入，由 _build_graph 按 finalize_fn is not None 派生主/子。
#
# 未设置上下文（测试 / 未走 chat_stream 的路径）→ acc no-op，零侵入。
import contextvars

_ctx: "contextvars.ContextVar[dict | None]" = contextvars.ContextVar(
    "token_usage", default=None
)

# buckets：main = 主 agent LLM（call_llm/final_answer 主图）；sub = 子 agent LLM；
# jina = jina 切片打分（sub_llm）。extractor 是 ingestion 期成本，不计入本条回答。
_BUCKETS = ("main", "sub", "jina")


def new_usage() -> dict:
    """新建 per-request usage dict。三桶各含 input/output。"""
    return {b: {"input": 0, "output": 0} for b in _BUCKETS}


def set_usage(usage: dict):
    """把 usage 设进 ContextVar，返回 token 用于 finally reset。"""
    return _ctx.set(usage)


def reset_usage(token) -> None:
    """还原 ContextVar（finally 调，防泄漏到下个请求）。"""
    _ctx.reset(token)


def acc(bucket: str, response) -> None:
    """累加一次 LLM 调用的 usage_metadata 到指定桶。

    无上下文 / response 无 usage_metadata → no-op。
    """
    usage = _ctx.get()
    if usage is None or bucket not in usage:
        return
    um = getattr(response, "usage_metadata", None)
    if not um:
        return
    usage[bucket]["input"] += um.get("input_tokens", 0) or 0
    usage[bucket]["output"] += um.get("output_tokens", 0) or 0


def snapshot(usage: dict) -> dict:
    """返回 usage 的浅拷贝快照（防 done 帧发出后调用方继续 mutate）。

    只保留非零桶，省 SSE 体积；全零则返回空 dict（前端 v-if 守卫）。
    """
    out = {}
    for b in _BUCKETS:
        i, o = usage[b]["input"], usage[b]["output"]
        if i or o:
            out[b] = {"input": i, "output": o}
    return out
