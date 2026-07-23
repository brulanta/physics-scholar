from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import (
    HumanMessage,
    BaseMessage,
    SystemMessage,
    AIMessage,
    ToolMessage,
)
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from typing import TypedDict, Annotated, Sequence
from pydantic import BaseModel, Field
import os
from dotenv import load_dotenv
from src.rag.tools.rag_tool import make_rag_tool
from src.rag.tools.lookup_local_paper_id import make_paper_id_search_tool
from src.rag.tools.arxiv_tool import arxiv_tool
from src.rag.tools.s2_tool import s2_search_tool
from src.rag.tools.jina_tool import jina_tool
from src.rag.tools.openalex_tool import openalex_tool
from src.rag.tool_runtime import USE_MCP
from src.rag import mcp_client
from src.rag.memory import (
    ConversationMemory,
    format_history,
    WARN_THRESHOLD,
    ConversationRepo,
)
from src.rag.prompts import (
    build_prompt,
    CITATION_DEFAULT,
    CITATION_TRANSLATION,
)
from src.rag.prompts.plugins import TOOL_DECISION_PLUGIN
from src.rag.prompts.subagent_prompt import build_subagent_prompt
from src.core.trim_thinking import process_llm_output, THINK_TAG_PATTERN
from src.rag.harness_profile import HarnessProfile, FLASH, RETRIEVER
from src.rag.citation import (
    collect_from_tool_results,
    detect_hallucination,
    enrich_refs,
    parse_refs,
)
from src.core.citation_store import (
    load_enrichment_for_message,
    mark_cited,
    save_candidates,
)
from src.utils.logger import get_logger
import asyncio
import contextlib
import json
import re
import logging
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from openai import (
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
    RateLimitError,
)
from src.llm import main_llm as llm

MAX_THINKING_RETRIES = 3

load_dotenv()
logger = get_logger(__name__)


@retry(
    retry=retry_if_exception_type(
        (
            APITimeoutError,
            APIConnectionError,
            InternalServerError,
            RateLimitError,
        )
    ),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def invoke_with_retry(llm, messages):
    return llm.invoke(messages)


@retry(
    retry=retry_if_exception_type(
        (
            APITimeoutError,
            APIConnectionError,
            InternalServerError,
            RateLimitError,
        )
    ),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def ainvoke_with_retry(llm, messages):
    # 异步版：astream_events(v2) 下节点内必须 await ainvoke，
    # 否则同步 .invoke() 被丢线程池、callback 不冒泡，拿不到 token 级 on_chat_model_stream。
    return await llm.ainvoke(messages)


# ============================================================
# 流式 SSE 辅助（真流式：astream_events v2 → 三态状态机 → SSE 帧）
# ============================================================

_CLOSE_THINK_RE = re.compile(rf"</{THINK_TAG_PATTERN}>", re.IGNORECASE)
# 工具循环标记：DONE=不调用工具直接进入正文；PENDING=闭合 thinking 等待工具执行
_MARKER_RE = re.compile(r"\[TOOL_LOOP:\s*(DONE|PENDING)\]", re.IGNORECASE)

# 子 agent findings（回吐给主 agent 的检索结果拼接）字符上限——防 jina 全文等长 blob
# 撑爆主 agent context。截断兜底，主 agent 至少拿到前段。
MAX_FINDINGS_LEN = 12000


def _rfind_close_think(buf: str) -> int:
    """返回 buf 中最后一个 </thinking|think> 的结束下标（end），无则 -1。"""
    matches = list(_CLOSE_THINK_RE.finditer(buf))
    return matches[-1].end() if matches else -1


def _detect_marker(buf: str):
    """全 buf 扫描 [TOOL_LOOP: DONE/PENDING]，返回最后一个标记（大写）或 None。"""
    found = _MARKER_RE.findall(buf)
    return found[-1].upper() if found else None


def _tool_ok(output) -> bool:
    """从 on_tool_end 的 output（ToolMessage）判定工具是否成功执行。"""
    status = getattr(output, "status", None)
    if status is not None:
        return status != "error"
    return True


def _format_sse(event_type: str, **payload) -> str:
    """组装一帧 SSE：data: <json>\\n\\n，json 必含 type。"""
    return (
        "data: "
        + json.dumps({"type": event_type, **payload}, ensure_ascii=False)
        + "\n\n"
    )


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    conv_id: str
    user_id: str
    translation: bool
    remaining_calls: int
    thinking_retry_count: int
    is_thinking_correction: bool
    pending_correction: str
    findings: str  # 子 agent 专用：finalize 写检索结果拼接，retrieve 壳取；主 agent 不用


def thinking_guard(state: AgentState, profile: HarnessProfile = FLASH) -> dict:
    # 轴 A（guard_mode）：off = 完全不检查，工具调用直接放行
    if profile.guard_mode == "off":
        return state

    messages = list(state["messages"])
    last_msg = messages[-1]
    retry_count = state.get("thinking_retry_count", 0)
    remaining = state.get("remaining_calls", profile.budget_n)

    if not isinstance(last_msg, AIMessage):
        return state
    has_tool_calls = bool(getattr(last_msg, "tool_calls", None))
    if not has_tool_calls:
        return state

    content = last_msg.content or ""
    has_thinking = "<thinking>" in content and "</thinking>" in content

    if has_thinking:
        return {
            **state,
            "thinking_retry_count": 0,
            "is_thinking_correction": False,
        }

    # 轴 A（guard_mode）：soft = 缺 <thinking> 只记警告、不驳回，放行到 tool_node
    if profile.guard_mode == "soft":
        logger.warning("[guard:soft] 工具调用缺 <thinking>，soft 模式放行（不驳回、不纠正）")
        return {
            **state,
            "thinking_retry_count": 0,
            "is_thinking_correction": False,
        }

    # 已经用尽所有纠正机会（包括“最终警告”），不再尝试纠正
    if retry_count >= MAX_THINKING_RETRIES:
        # 直接打回给 after_guard 路由到 final_answer
        return {
            **state,
            "thinking_retry_count": retry_count,
            "is_thinking_correction": False,  # 防止后续路由误判
        }

    # 违规：保留原始 AI message，为每个 tool_call 补虚假 ToolMessage
    fake_tool_messages = [
        ToolMessage(
            content="[工具调用已被取消：未检测到必要的 <thinking> 块，请先完成 [TOOL_LOOP] 再调用工具]",
            tool_call_id=tc["id"],
        )
        for tc in last_msg.tool_calls
    ]

    current_violation = retry_count + 1  # 当前是第几次违规（1-based）

    if current_violation < MAX_THINKING_RETRIES:
        remaining_chances = MAX_THINKING_RETRIES - current_violation
        correction_text = (
            f"⚠️ 工具调用申请被驳回（剩余纠正机会：{remaining_chances} 次）：\n"
            f"原因：未附 thinking 报告。请理解——thinking 是工具调用的申请单，不提交申请单的调用请求一律不受理。\n"
            f"请在下一轮严格按 [TOOL_LOOP: BEGIN] 完成 Q1→Q2→Q3，提交完整申请后重新调用。"
        )
    else:
        correction_text = (
            "⚠️ 工具调用申请最终驳回（最后一次纠正机会）：\n"
            "前几次调用均因未附 thinking 被拒绝。系统要求很明确：先写申请（thinking），再执行调用。\n"
            "这是最后一次机会：下一轮必须先完成 [TOOL_LOOP: BEGIN] 的 Q1-Q3，否则工具调用权限将永久关闭。"
        )

    return {
        **state,
        "messages": messages + fake_tool_messages,
        "pending_correction": correction_text,
        "thinking_retry_count": retry_count + 1,
        "remaining_calls": min(remaining + 1, profile.budget_n),
        "is_thinking_correction": True,
    }


def after_guard(
    state: AgentState,
    terminator: str | None = None,
    budget_route: str = "final_answer",
) -> str:
    last_msg = state["messages"][-1]
    remaining = state.get("remaining_calls", 6)
    retry_count = state.get("thinking_retry_count", 0)

    # 违规次数超限，优先终止
    if retry_count >= MAX_THINKING_RETRIES:
        logger.warning("违规超限，强制进入 final_answer")
        return "final_answer"

    # 正在进行思维链纠正，需要返回 call_llm 让模型重新生成
    if state.get("is_thinking_correction", False):
        return "call_llm"

    # 正常工具调用路由
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        # 子 agent 终止信号：主动调用 terminator 工具（return_findings）→ 路由 finalize，
        # 不走 tool_node 回 call_llm（terminator 是终止意图，先于预算检查）。
        # terminator=None（主 agent）时此分支永不命中，行为字节级不变。
        if terminator and any(
            tc.get("name") == terminator for tc in last_msg.tool_calls
        ):
            return "finalize"
        if remaining < 0:
            # 预算耗尽：主 agent → final_answer（LLM 兜底作答）；
            # 子 agent → finalize（拼已有工具结果作 findings，不浪费 LLM 调用）。
            logger.warning("工具额度耗尽，路由 %s", budget_route)
            return budget_route
        return "tool_node"

    # 无工具调用且非纠正状态，结束
    return END


async def final_answer(state: AgentState, profile: HarnessProfile = FLASH) -> dict:
    messages = list(state["messages"])
    last_msg = messages[-1]

    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        for tc in last_msg.tool_calls:
            messages.append(
                ToolMessage(
                    content="[工具调用已取消，已达最大调用次数上限]",
                    tool_call_id=tc["id"],
                )
            )
    final_prefill = build_final_prefill(profile)
    invoke_messages = messages + [AIMessage(content=final_prefill)]
    response = await ainvoke_with_retry(llm, invoke_messages)

    return {"messages": [response]}


def build_prefill(
    remaining: int,
    is_after_tool: bool,
    is_thinking_correction: bool = False,
    profile: HarnessProfile = FLASH,
) -> str:
    budget_n = profile.budget_n
    if remaining == 1:
        warning = (
            "⚠️ CRITICAL: FINAL_OPPORTUNITY. "
            "仅存最后一次工具调用机会。"
            "评估当前缺口优先级，仅针对最关键缺口调用；若现有信息已基本支撑结论，建议放弃调用直接推导。"
        )
    elif remaining <= 0:
        warning = (
            "🚫 TOOL_USE_DISABLED: 工具调用权限已关闭。"
            "处于信息闭环状态。基于已有证据完成最终推导，禁止输出任何工具调用。"
        )
    else:
        warning = ""

    warning_line = f"\n- {warning}" if warning else ""
    runtime_status = (
        f"[RUNTIME_STATUS]\n"
        f"- Remaining_Tool_Calls: {max(remaining, 0)}/{budget_n}{warning_line}\n"
        f"[/RUNTIME_STATUS]"
    )

    # 轴 C2（prefill_level）：light/minimal 只降 prefill 逼迫强度；
    # RUNTIME_STATUS（轴 D，纯信息）与 [start] 锚点、C1 标记契约均保留。
    if profile.prefill_level == "light":
        note = "工具额度已用尽，仅基于已有证据推导。" if remaining <= 0 else ""
        lead = (
            f"<think>\n"
            f"{note}按流程：先输出 <thinking> 完成评估，再决定是否调用工具。现在输出 [start]。\n"
            f"</think>\n"
            f"[start]"
        )
        return f"{runtime_status}\n\n{lead}"
    if profile.prefill_level == "minimal":
        return f"{runtime_status}\n\n[start]"

    # prefill_level == "full"（现状）：按预算/纠正/是否工具后分支催眠
    if remaining <= 0:
        # 协议 C：调用循环结束，进入最终输出
        lead = (
            "<think>\n"
            "工具调用额度已经用尽，我无法再发起任何工具调用。我必须在 [start] 后立刻输出 <thinking>，然后改变策略，完全基于手头已有证据进行纯逻辑整合，诚实推导并标注结论的基石与边界。现在输出 [start]。\n"
            "</think>\n"
            "[start]"
        )
    elif is_thinking_correction:
        if is_after_tool:
            # 情况：工具返回后违规被打回
            lead = (
                "<think>\n"
                "系统驳回了上一次工具调用，因为没有附带 <thinking> 申请单。我已经拿到了真实工具返回内容，不能浪费这些信息。我必须在 [start] 后立刻输出 <thinking>，并从 [TOOL_LOOP: BEGIN] 进入，基于真实返回内容完成 Q1→Q2→Q3，写完整申请后重新调用工具。现在输出 [start]。\n"
                "<think>\n"
                "[start]"
            )
        else:
            # 情况：首轮违规被打回（没有工具返回，也没有禁止 Phase 0 的必要）
            lead = (
                "<think>\n"
                "系统提醒我，上一轮尝试的工具调用因为缺少 <thinking> 申请单而被驳回。现在我需要严格按照规则来：在 [start] 后立刻输出 <thinking>，然后从 Phase 0 状态同频开始，完整执行所有 Phase，在 [TOOL_LOOP] 正式提交工具调用申请。现在输出 [start]。\n"
                "</think>\n"
                "[start]"
            )
    elif is_after_tool:
        # 协议 B 正常情况
        lead = (
            f"<think>\n"
            f"我已经拿到这一轮工具返回的反馈。系统显示剩余调用次数为 {remaining}，我必须基于这个真实数字推理。我需要在 [start] 后立刻输出 <thinking>，并从 [TOOL_LOOP: BEGIN] 进入，用工具返回的真实内容完成 Q1→Q2→Q3 评估，不得照搬历史。现在输出 [start]。\n"
            "</think>\n"
            "[start]"
        )
    else:
        # 协议 A 首轮
        lead = "<think>\n我要开始处理用户的问题。我必须在输出 [start] 后立刻输出 <thinking> 标签，然后严格遵循 Thinking Protocol，从 Phase 0 状态同频开始逐步梳理，不跳过任何必要步骤。现在输出 [start]。\n</think>\n[start]"

    return f"{runtime_status}\n\n{lead}"


def build_final_prefill(profile: HarnessProfile = FLASH) -> str:
    budget_n = profile.budget_n
    # 轴 E（final_prefill）：light = 中性收尾，去掉终局威胁话术
    if profile.final_prefill == "light":
        runtime_status = (
            "[RUNTIME_STATUS]\n"
            f"- Remaining_Tool_Calls: 0/{budget_n}\n"
            "- 工具调用循环已结束，进入最终作答。\n"
            "[/RUNTIME_STATUS]"
        )
        lead = (
            "<think>\n"
            "工具阶段已结束，我基于已掌握的证据组织最终回答，标注结论的依据与局限。现在输出 [start]。\n"
            "</think>\n"
            "[start]"
        )
        return f"{runtime_status}\n\n{lead}"

    # final_prefill == "full"（现状）
    runtime_status = (
        "[RUNTIME_STATUS]\n"
        f"- Remaining_Tool_Calls: 0/{budget_n}\n"
        "- 🚫 PIPELINE TERMINATED: 工具调用循环已终止（额度耗尽或违规次数超限）。\n"
        "- ⚠️ CRITICAL WARNING: 当前处于最终兜底节点。\n"
        "  若本轮仍输出工具调用，pipeline 将直接终止，用户将收到空回复。\n"
        "  若本轮输出正常文本，用户将收到你的分析结论。\n"
        "  这是唯一的选择窗口。\n"
        "[/RUNTIME_STATUS]"
    )
    lead = (
        "<think>\n"
        "工具调用循环已经永久终止，所有查询路径关闭。我必须放弃任何工具调用企图。在 [start] 后，我会立刻输出 <thinking>，然后进入纯逻辑整合模式，只基于此刻已掌握的证据，组织一次诚实且有边界的最终回答，并清晰标出推断的基石与局限。现在输出 [start]。\n"
        "</think>\n"
        "[start]"
    )
    return f"{runtime_status}\n\n{lead}"


def _build_graph(
    llm,
    tools,
    profile: HarnessProfile,
    *,
    terminator: str | None = None,
    finalize_fn=None,
):
    """共享图组装层（薄、无业务逻辑）：主 agent 与子 agent 共用同一套图骨架。

    只做 bind_tools / ToolNode / 节点+边的组装；业务逻辑（build_prefill /
    build_final_prefill / thinking_guard / after_guard / final_answer）全在模块级
    函数里，本函数仅调用。terminator + finalize_fn 是子 agent 专属终止机制
    （return_findings → finalize 节点）；主 agent 不传 = 完全不感知，行为与重构前字节级一致。

    解绑成本：未来主/子任一边想独立演进，fork 本函数这 ~30 行组装即可，模块级业务
    函数全部仍可复用（沉没成本≈0）。详见 plan/subagent-retrieval-decouple-plan.md。
    """
    llm_with_tools = llm.bind_tools(tools)
    tool_node = ToolNode(tools)

    # 把llm_with_tools和tool_node闭包进节点函数
    async def call_llm(state):
        messages = list(state["messages"])
        last_msg = messages[-1]
        is_after_tool = isinstance(last_msg, ToolMessage) or state.get(
            "is_thinking_correction", False
        )
        # 用完立刻重置
        remaining = state.get("remaining_calls", profile.budget_n)

        # 实时生成，不从 state 读
        prefill = build_prefill(
            remaining,
            is_after_tool,
            is_thinking_correction=state.get("is_thinking_correction", False),
            profile=profile,
        )
        # 用完立刻重置

        pending_correction = state.get("pending_correction", "")
        if pending_correction:
            invoke_messages = messages + [
                HumanMessage(
                    content=pending_correction
                ),  # 只在本次推理可见，不入 state
                AIMessage(content=prefill),
            ]
        else:
            invoke_messages = messages + [AIMessage(content=prefill)]

        response = await ainvoke_with_retry(llm_with_tools, invoke_messages)

        # 检查 reasoning_content 是否有内容
        reasoning = getattr(response, "additional_kwargs", {}).get(
            "reasoning_content", ""
        )
        if reasoning:
            logger.debug("检测到 reasoning_content，长度=%d", len(reasoning))
            # 如果 content 为空但 reasoning_content 有内容，说明 thinking 写错地方了
            if not (response.content or "").strip():
                logger.warning(
                    "content 为空但 reasoning_content 非空，模型可能将 thinking 写入错误字段"
                )

        # 串行裁剪
        if hasattr(response, "tool_calls") and len(response.tool_calls) > 1:
            response.tool_calls = response.tool_calls[:1]
            if "tool_calls" in response.additional_kwargs:
                response.additional_kwargs["tool_calls"] = response.additional_kwargs[
                    "tool_calls"
                ][:1]

        called_tool = bool(response.tool_calls)
        new_remaining = remaining - 1 if called_tool else remaining

        logger.debug(
            "[call_llm][%s] tool_calls=%s | remaining=%d→%d | content_len=%d",
            "after_tool" if is_after_tool else "first",
            [tc["name"] for tc in response.tool_calls] if called_tool else [],
            remaining,
            new_remaining,
            len(response.content or ""),
        )
        logger.debug("本轮返回的文本内容：%s", response.content)

        return {
            "messages": [response],
            "remaining_calls": new_remaining,
            "is_thinking_correction": False,
            "pending_correction": "",  # 清除
        }

    # thinking_guard / final_answer 是模块级函数（便于单测），此处闭包注入 profile。
    # after_guard 路由：soft/off 不 set is_thinking_correction，路由不变；
    # terminator（子 agent 专属）经 _after_guard 闭包注入。
    def _guard(state):
        return thinking_guard(state, profile)

    async def _final(state):
        return await final_answer(state, profile)

    def _after_guard(state):
        # 子 agent（有 finalize_fn）预算耗尽走 finalize 拼工具结果；
        # 主 agent 走 final_answer（LLM 兜底作答）。
        budget_route = "finalize" if finalize_fn is not None else "final_answer"
        return after_guard(state, terminator, budget_route)

    graph = StateGraph(AgentState)
    graph.add_node("call_llm", call_llm)
    graph.add_node("thinking_guard", _guard)
    graph.add_node("tool_node", tool_node)
    graph.add_node("final_answer", _final)
    if finalize_fn is not None:
        graph.add_node("finalize", finalize_fn)

    graph.set_entry_point("call_llm")

    # call_llm 之后统一进 guard
    graph.add_edge("call_llm", "thinking_guard")

    # guard 之后条件路由（主 agent 无 finalize 映射；子 agent 有）
    path_map = {
        "call_llm": "call_llm",
        "tool_node": "tool_node",
        "final_answer": "final_answer",
        END: END,
    }
    if finalize_fn is not None:
        path_map["finalize"] = "finalize"
    graph.add_conditional_edges("thinking_guard", _after_guard, path_map)

    graph.add_edge("tool_node", "call_llm")
    graph.add_edge("final_answer", END)
    if finalize_fn is not None:
        graph.add_edge("finalize", END)

    return graph.compile()


# ============================================================
# 子 agent（检索 agent）—— T2：检索循环挪进独立编译的子图，
# 包成 retrieve 工具壳给主 agent 调用（主 graph 结构零改动）。
# 详见 plan/subagent-retrieval-decouple-plan.md。
# ============================================================


class RetrievalSelection(BaseModel):
    """return_findings 的 selection 项 schema：子 agent 点的检索结果索引 + 理由。"""

    result_index: int = Field(
        description="检索结果序号（0-based，从第一个真实工具结果起算，不数 return_findings）"
    )
    reason: str = Field(
        description="一句话说明这个结果为什么有用（你的判断，不要抄工具返回内容）"
    )


def _make_return_findings_tool():
    """子 agent 终止工具：声明哪些工具结果有用 → 触发 finalize 节点拼 findings。

    工具本身**不执行**——after_guard 拦到 return_findings 的 tool_call 就路由 finalize，
    不走 tool_node；这里只给 bind_tools 提供 schema + 占位实现。子 agent LLM 只需「数数」
    填 result_index，不转写工具结果内容（摘抄归属在主 agent）。
    """

    @tool
    def return_findings(
        selection: list[RetrievalSelection],
        summary: str,
    ) -> str:
        """检索完成、已为问题找到足够支撑时调用本工具终止检索。

        - selection：你认为对回答有用的检索结果列表，每项含 result_index（检索结果序号，
          0-based，按你看到真实结果的顺序数）+ reason（为什么有用）。
        - summary：一句话概括检索到了什么、覆盖了哪些缺口、还缺什么。
        """
        return ""  # 被 finalize 拦截，不真正执行

    return return_findings


async def _subagent_finalize(state: AgentState) -> dict:
    """子 agent 终止节点：按 return_findings 的 selection 抠选中工具结果拼成 findings。

    两条路径都进这里：
      1. 子 agent 调 return_findings（正常收敛）→ 按 selection 抠选中项拼 findings。
      2. 预算耗尽（after_guard budget_route=finalize）→ 无 return_findings args，
         兜底全拼已有工具结果。

    子 agent 全程只点索引不转写——本节点从 state messages 的真实 ToolMessage 抠 content raw，
    拼成 findings 写 state['findings']（不写 messages，保持消息链干净；retrieve 壧取 findings）。
    主 agent 自己读 findings 原文摘抄进 ref（grounding 留主 agent，保反幻觉初衷）。
    """
    messages = list(state["messages"])

    # 找 return_findings 的 args（最后一条带该 tool_call 的 AIMessage）
    rf_args: dict | None = None
    for m in reversed(messages):
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                if tc.get("name") == "return_findings":
                    rf_args = tc.get("args", {}) or {}
                    break
            if rf_args is not None:
                break

    # 真实工具结果（按出现顺序编号）——子 agent guard off 不产伪造 ToolMessage，
    # 故所有 ToolMessage 都是真实检索结果。
    tool_msgs = [
        m
        for m in messages
        if isinstance(m, ToolMessage) and (getattr(m, "name", "") or "")
    ]

    sections: list[str] = []
    if rf_args is not None:
        summary = (rf_args.get("summary") or "").strip()
        if summary:
            sections.append(f"## 检索摘要\n{summary}")
        selection = rf_args.get("selection") or []
        sel_indices = {
            s.get("result_index")
            for s in selection
            if isinstance(s, dict) and isinstance(s.get("result_index"), int)
        }
        picked = [
            f"### 检索结果 #{i}（{tm.name}）\n{tm.content}"
            for i, tm in enumerate(tool_msgs)
            if i in sel_indices
        ]
        if picked:
            sections.append("## 检索结果\n\n" + "\n\n".join(picked))
        elif tool_msgs:
            # selection 索引全部越界——兜底全拼，不丢检索成果
            sections.append(
                "## 检索结果\n\n"
                + "\n\n".join(
                    f"### 检索结果 #{i}（{tm.name}）\n{tm.content}"
                    for i, tm in enumerate(tool_msgs)
                )
            )
    else:
        # 预算耗尽兜底：全拼已有工具结果
        if tool_msgs:
            sections.append(
                "## 检索结果（预算耗尽兜底）\n\n"
                + "\n\n".join(
                    f"### 检索结果 #{i}（{tm.name}）\n{tm.content}"
                    for i, tm in enumerate(tool_msgs)
                )
            )

    findings = "\n\n".join(sections) if sections else "(检索未返回结果)"
    if len(findings) > MAX_FINDINGS_LEN:
        findings = findings[:MAX_FINDINGS_LEN] + "\n\n[...检索结果过长，已截断]"
    return {"findings": findings}


def build_subagent(
    user_id: str,
    profile: HarnessProfile = RETRIEVER,
    llm=None,
):
    """子 agent（检索 agent）图：照搬 build_agent 骨架（共享 _build_graph），差异走参数注入。

    - profile = RETRIEVER（guard off + prefill minimal + 独立预算 8）。
    - 工具 = 检索工具集（rag/s2/openalex/arxiv/jina/lookup）+ return_findings 终止工具。
    - terminator=return_findings + finalize_fn → after_guard 拦 return_findings 路由 finalize，
      预算耗尽也路由 finalize（budget_route）；子 agent 不感知主 agent 的 final_answer 兜底语义。
    对主 agent 它是 retrieve 工具（make_retrieve_tool 包一层），对自己是图。
    """
    if llm is None:
        from src.llm import main_llm as llm

    paper_id_search_tool = make_paper_id_search_tool(user_id)
    rag_tool = make_rag_tool(user_id)

    if USE_MCP:
        # MCP 路径：从 MCP server 取的工具替代对应内嵌工具；未迁移的仍用内嵌实例。
        mcp_tools = mcp_client.get_tools()
        mcp_names = {t.name for t in mcp_tools}
        inline_tools = [
            rag_tool,
            paper_id_search_tool,
            arxiv_tool,
            s2_search_tool,
            openalex_tool,
            jina_tool,
        ]
        inline_tools = [t for t in inline_tools if t.name not in mcp_names]
        search_tools = inline_tools + mcp_tools
    else:
        search_tools = [
            rag_tool,
            paper_id_search_tool,
            arxiv_tool,
            s2_search_tool,
            openalex_tool,
            jina_tool,
        ]

    tools = search_tools + [_make_return_findings_tool()]
    return _build_graph(
        llm,
        tools,
        profile,
        terminator="return_findings",
        finalize_fn=_subagent_finalize,
    )


def _extract_findings(result: dict) -> str:
    """从子 agent ainvoke 结果取 findings。

    优先 finalize 写的 state['findings']；finalize 未跑（罕见异常路径，如预算内
    子 agent 直接 END）兜底拼所有工具结果。
    """
    if not isinstance(result, dict):
        return "(检索未返回结果)"
    findings = (result.get("findings") or "").strip()
    if findings:
        return findings
    msgs = result.get("messages", [])
    parts = [
        f"### 检索结果 #{i}（{name}）\n{content}"
        for i, (name, content) in enumerate(_tool_results_from_messages(msgs))
    ]
    if parts:
        return "## 检索结果（兜底）\n\n" + "\n\n".join(parts)
    return "(检索未返回结果)"


def make_retrieve_tool(user_id: str):
    """主 agent 的 retrieve 工具壳：把子 agent 图包成一个工具。

    主 agent 调 retrieve(query) → 壳内 build_subagent().ainvoke（非流式同步阻塞）→
    取 findings 作 content、子 agent 工具结果作 artifact，返回 (content, artifact)。
    response_format='content_and_artifact' 让 BaseTool 把它包成 ToolMessage——
    content 给主 agent LLM 读 findings，artifact 给 _consume_events 收候选源（T1 seam 兑现）。
    Stage 1–4 不接流式 custom event：retrieve 调用期间前端只见一个工具节点转圈。
    """

    @tool(response_format="content_and_artifact")
    async def retrieve(query: str):
        """检索本地知识库与外部文献（Semantic Scholar / OpenAlex / arXiv / Jina 全文精读），
        返回支撑当前问题的检索结果与可引用文献。当问题需要外部文献证据或本地论文内容时调用。
        """
        try:
            subagent = build_subagent(user_id)
            initial_state = {
                "messages": [
                    SystemMessage(content=build_subagent_prompt()),
                    HumanMessage(content=query),
                ],
                "conv_id": "",
                "user_id": user_id,
                "translation": False,
                "remaining_calls": RETRIEVER.budget_n,
                "findings": "",
            }
            result = await subagent.ainvoke(initial_state)
        except Exception as e:
            logger.warning("[retrieve] 子 agent 检索异常，返回空 findings: %s", e)
            return f"(检索失败：{e})", []
        findings = _extract_findings(result)
        # 候选源 = 子 agent 所有真实工具结果（经 artifact 冒泡给 _consume_events →
        # result['tool_results'] → _persist_and_enrich collect）。findings 本身不进候选收集
        # （它是拼接产物，候选层要原始工具结果做 source_id 提取）。
        candidates = _tool_results_from_messages(result.get("messages", []))
        return findings, candidates

    return retrieve


def build_agent(user_id: str, profile: HarnessProfile = FLASH, llm=None):
    """主 agent 图：T2 后只绑定 retrieve 工具壳——检索循环挪进子 agent，主 agent 注意力
    聚焦思考与输出。主 graph 结构零改动（仍是 _build_graph 组装），仅工具列表从 6 个检索
    工具换成 1 个 retrieve 壳。

    llm=None 走模块级 main_llm；cross-model probe / 子 agent 可传 override。
    主 agent prompt 的去水（删 tool_usage / 压 Phase 3）在 Stage 2，本阶段 prompt 暂不动。
    """
    if llm is None:
        from src.llm import main_llm as llm

    retrieve_shell = make_retrieve_tool(user_id)
    return _build_graph(llm, [retrieve_shell], profile)


def _prepare(
    memory: ConversationMemory,
    user_message: str,
    conv_id: str,
    user_id: str,
    translation: bool,
    mode: str,
    parent_id: int,
    profile: HarnessProfile = FLASH,
):
    """共享构建：system_prompt + history + agent + initial_state。
    供 chat()/regenerate()（非流式兜底）与 chat_stream()/regenerate_stream()（流式）复用。
    profile 默认 FLASH（现状），生产路径不传即零行为变化；开发态实验从此处注入。
    """
    history = format_history(memory.get(leaf_message_id=parent_id, explicit=True))
    system_prompt = build_prompt(
        mode="normal" if mode == "normal" else "discuss",
        history=history,
        citation_plugin=CITATION_TRANSLATION if translation else CITATION_DEFAULT,
        tool_decision_plugin=TOOL_DECISION_PLUGIN,
        debug=False,
    )
    agent = build_agent(user_id, profile)
    initial_state = {
        "messages": [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ],
        "conv_id": conv_id,
        "user_id": user_id,
        "translation": translation,
        "remaining_calls": profile.budget_n,
        "next_prefill": None,
    }
    return agent, initial_state


def chat(
    user_message: str,
    conv_id: str,
    user_id: str = "default",
    translation: bool = False,
    mode: str = "normal",
    parent_id: int = None,
) -> dict:
    # 1.在invoke之前先构建SystemMessage，这时候有所有需要的参数
    conversation_id = f"{user_id}_{conv_id}"
    memory = ConversationMemory(conversation_id)
    try:
        # 2. call agent
        agent, initial_state = _prepare(
            memory, user_message, conv_id, user_id, translation, mode, parent_id
        )

        # 节点已异步化（call_llm/final_answer 为 async），同步 invoke 不再可用；
        # 这是非流式兜底/测试路径，用 asyncio.run 包一层 ainvoke 保持同步签名（过渡态）。
        result = asyncio.run(agent.ainvoke(initial_state))
        # 非流式路径从完整 state messages 抽 ToolMessage 收集成 tool_results，
        # 供 _persist_and_enrich 候选收集（与流式 on_tool_end 累积同构）。
        result["tool_results"] = _tool_results_from_messages(result.get("messages", []))

        # 3. 处理result，写入memory
        agent_msg_pure = process_llm_output(
            result["messages"][-1].content, conversation_id
        )  # 提取纯净answer，并把thinking打印日志

        if not agent_msg_pure:
            agent_msg_pure = (
                "⚠️ 本次回答为空，可能是模型输出异常。可以点击重新生成再试一次。"
            )
            logger.warning("[%s] 写入空回答占位文本", conversation_id)

        # 确保 conversations 表有这条对话的记录
        conv_repo = ConversationRepo()
        try:
            conv_repo.ensure_exists(conversation_id, user_id, user_message)
        finally:
            conv_repo.close()

        user_res = memory.add(HumanMessage(content=user_message), parent_id=parent_id)
        agent_res = memory.add(
            AIMessage(content=agent_msg_pure), parent_id=user_res["message_id"]
        )

        # 候选 enrichment 落 sidecar（非流式测试兜底路径，只存不 enrich——见 plan 边界）
        _persist_and_enrich(
            conversation_id, agent_res["message_id"], agent_msg_pure, result,
            enrich=False,
        )

        # 4. 构造返回
        warning = (
            f"当前对话存储已超上限{WARN_THRESHOLD}，建议开启新对话以保证回答质量。"
            if agent_res.get("warning")
            else None
        )

        return {
            "answer": agent_msg_pure,
            "user_msg_id": user_res["message_id"],
            "agent_msg_id": agent_res["message_id"],
            "warning": warning,
        }
    finally:
        memory.close()


def regenerate(
    user_message: str,
    conv_id: str,
    user_id: str = "default",
    translation: bool = False,
    mode: str = "normal",
    parent_id: int = None,
    old_agent_msg_id: int = None,
) -> dict:
    # 在invoke之前先构建SystemMessage，这时候有所有需要的参数
    conversation_id = f"{user_id}_{conv_id}"
    memory = ConversationMemory(conversation_id)
    try:
        # 流程：
        #   1. memory.regenerate(old_agent_msg_id, parent_id) → 拿到 version
        #   2. graph.invoke() 重新推理
        #   3. memory.add(new_agent_msg, parent_id=parent_id, version=version)
        #   4. 返回新 answer 和 agent_msg_id

        # 1
        regen_res = memory.regenerate(old_agent_msg_id, parent_id)
        if not regen_res.get("success"):
            raise Exception(f"标记旧消息失败: {regen_res.get('detail')}")
        version = regen_res.get("version")

        # 2
        agent, initial_state = _prepare(
            memory, user_message, conv_id, user_id, translation, mode, parent_id
        )

        # 同 chat()：异步节点下用 asyncio.run 包 ainvoke 保持同步兜底签名（过渡态）。
        result = asyncio.run(agent.ainvoke(initial_state))
        result["tool_results"] = _tool_results_from_messages(result.get("messages", []))

        # 3
        agent_msg_pure = process_llm_output(
            result["messages"][-1].content, conversation_id
        )  # 提取纯净answer，并把thinking打印日志

        if not agent_msg_pure:
            agent_msg_pure = (
                "⚠️ 本次回答为空，可能是模型输出异常。可以点击重新生成再试一次。"
            )
            logger.warning("[%s] 写入空回答占位文本", conversation_id)

        agent_res = memory.add(
            AIMessage(content=agent_msg_pure), parent_id=parent_id, version=version
        )

        # 候选 enrichment 落 sidecar（非流式测试兜底路径，只存不 enrich——见 plan 边界）
        _persist_and_enrich(
            conversation_id, agent_res["message_id"], agent_msg_pure, result,
            enrich=False,
        )

        # 4
        warning = (
            f"当前对话存储已超上限{WARN_THRESHOLD}，建议开启新对话以保证回答质量。"
            if agent_res.get("warning")
            else None
        )

        return {
            "answer": agent_msg_pure,
            "user_msg_id": parent_id,
            "agent_msg_id": agent_res["message_id"],
            "warning": warning,
        }
    finally:
        memory.close()


# ============================================================
# 流式入口（真流式）：chat_stream / regenerate_stream，共用 _consume_events
# ============================================================

_EMPTY_ANSWER_TEXT = "⚠️ 本次回答为空，可能是模型输出异常。可以点击重新生成再试一次。"


HEARTBEAT_INTERVAL = 15  # 秒：长工具链（含外接 MCP）静默期保活，防反代/客户端掐断
_HEARTBEAT = ": ping\n\n"  # SSE 注释帧，客户端忽略


async def _consume_events(agent, initial_state, request, result: dict):
    """核心三态状态机：消费 astream_events(v2)，逐帧产出 SSE 字符串。

    状态：is_thinking / answer_open / buf / cur_node / root_run_id。
    正常跑完时把权威 final_content 写入 result['final_content']，供调用方落库；
    断连/中断则不写——调用方据此跳过落库（本轮丢弃）。

    心跳：用单个 pending __anext__ future 与超时竞速。asyncio.wait(timeout) 超时
    不取消该 future（区别于 wait_for），故不会把 astream_events 迭代器在某一步中途
    取消、损坏流；静默 HEARTBEAT_INTERVAL 秒即发一帧 ": ping" 保活并唤醒断连检查。
    """
    is_thinking = False
    answer_open = False
    buf = ""
    cur_node = None
    root_run_id = None

    aiter = agent.astream_events(initial_state, version="v2").__aiter__()
    pending = None
    try:
        while True:
            # 每轮（含心跳唤醒）检查断连，断连即停止消费（不落库）
            if request is not None and await request.is_disconnected():
                logger.info("客户端断连，停止消费事件流")
                return

            if pending is None:
                pending = asyncio.ensure_future(aiter.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_INTERVAL)
            if not done:
                yield _HEARTBEAT  # 静默超时：发心跳保活，不取消 pending
                continue
            try:
                ev = pending.result()
            except StopAsyncIteration:
                break
            pending = None

            etype = ev["event"]
            metadata = ev.get("metadata") or {}

            # 图根 run_id：第一个无 langgraph_node 的 on_chain_start（根 runnable）
            if (
                root_run_id is None
                and etype == "on_chain_start"
                and not metadata.get("langgraph_node")
            ):
                root_run_id = ev["run_id"]

            if etype == "on_chat_model_start":
                cur_node = metadata.get("langgraph_node")
                buf = ""
                answer_open = False
                # 一条回答只发一次 thinking_start；guard 反刍回的 call_llm 不重发
                if not is_thinking:
                    is_thinking = True
                    yield _format_sse("thinking_start")

            elif etype == "on_chat_model_stream":
                chunk = ev["data"].get("chunk")
                piece = getattr(chunk, "content", "") if chunk else ""
                if not piece:
                    continue  # 空 piece（reasoning_content 等）跳过
                if answer_open:
                    yield _format_sse("answer_delta", text=piece)
                    continue
                buf += piece
                close_end = _rfind_close_think(buf)
                if close_end == -1:
                    continue  # 尚未出现 </thinking>，继续累积
                marker = _detect_marker(buf)
                # final_answer 节点必进正文；call_llm 仅在 DONE 标记时进正文
                # （PENDING=工具调用将至 / None=继续思考，均静默等待）
                if cur_node == "final_answer" or marker == "DONE":
                    yield _format_sse("thinking_end")
                    yield _format_sse("answer_start")
                    answer_open = True
                    tail = buf[close_end:]
                    if tail:
                        yield _format_sse("answer_delta", text=tail)

            elif etype == "on_chat_model_end":
                # 兜底：answer 未开且无工具调用，说明流式期间漏判 marker，
                # 用权威 output.content（非 buf，避免 reasoning_content 污染）补发正文
                if not answer_open:
                    output = ev["data"].get("output")
                    tool_calls = getattr(output, "tool_calls", None)
                    if not tool_calls:
                        content = getattr(output, "content", "") or ""
                        close_end = _rfind_close_think(content)
                        tail = content[close_end:] if close_end != -1 else content
                        if tail.strip():
                            yield _format_sse("thinking_end")
                            yield _format_sse("answer_start")
                            answer_open = True
                            yield _format_sse("answer_delta", text=tail)

            elif etype == "on_tool_start":
                # 真实工具执行（guard 伪造的 ToolMessage 不经工具节点，不触发）
                yield _format_sse("thinking_end")
                yield _format_sse(
                    "tool_start", name=ev.get("name", ""), tool_id=ev.get("run_id", "")
                )

            elif etype == "on_tool_end":
                ok = _tool_ok(ev["data"].get("output"))
                is_thinking = (
                    False  # 工具打断后重置，下一轮 call_llm 会重发 thinking_start
                )
                # 累积真实工具结果供落库后候选收集（想法 2(b) bind-by-id）。
                # guard 伪造的 ToolMessage 不经工具节点、不触发 on_tool_end，不会被收集。
                tool_name = ev.get("name", "")
                tool_output = ev["data"].get("output")
                tool_content = getattr(tool_output, "content", "") or ""
                if isinstance(result, dict):
                    # T2：retrieve 壳经 artifact 冒泡子 agent 工具结果作候选源
                    # （retrieve 的 content=findings 不进候选收集——它是拼接产物，
                    # 候选层要原始工具结果做 source_id 提取）。常规工具 artifact=None 走 elif。
                    artifact = getattr(tool_output, "artifact", None)
                    if isinstance(artifact, list):
                        for sub_name, sub_content in artifact:
                            if sub_name and sub_content:
                                result.setdefault("tool_results", []).append(
                                    (sub_name, sub_content)
                                )
                    elif tool_name and tool_content:
                        result.setdefault("tool_results", []).append(
                            (tool_name, tool_content)
                        )
                yield _format_sse(
                    "tool_end",
                    name=ev.get("name", ""),
                    tool_id=ev.get("run_id", ""),
                    ok=ok,
                )

            elif etype == "on_chain_end" and ev["run_id"] == root_run_id:
                # 图根结束：取末条消息作权威 final_content，发 answer_end 收尾
                output = ev["data"].get("output")
                final_content = ""
                if isinstance(output, dict) and output.get("messages"):
                    final_content = output["messages"][-1].content or ""
                result["final_content"] = final_content
                yield _format_sse("answer_end")
    finally:
        # 先取消并等待在途 __anext__（否则 aiter 仍“运行中”，aclose 会报错），
        # 取消会把 CancelledError 注入 astream_events 生成器 → 停止图执行/工具调用。
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        with contextlib.suppress(BaseException):
            await aiter.aclose()


def _tool_results_from_messages(messages: Sequence[BaseMessage]) -> list[tuple[str, str]]:
    """从非流式 ainvoke 的完整 state messages 抽 (tool_name, content) 列表，
    供 _persist_and_enrich 候选收集（与流式 on_tool_end 累积同构）。

    只取真实工具的 ToolMessage（带 name + 非空 content）。guard 伪造的 ToolMessage
    name 为空、content 是驳回语，会被过滤（即便漏过，extract_candidates 也会因
    tool_name 空/解析失败跳过）。
    """
    out = []
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        name = (getattr(m, "name", "") or "").strip()
        content = (m.content or "").strip() if isinstance(m.content, str) else ""
        if name and content:
            out.append((name, content))
    return out


def _detect_and_mark_hallucination(
    conversation_id: str,
    agent_msg_id: int,
    lean_answer: str,
    candidates: list,
) -> None:
    """Step 2 幻觉检测：model 引用了候选集没有的 source_id = binding 幻觉信号。
    命中记 warning 日志（不阻断回答，soft-violation 口径，与 harness_probe 一致）；
    同时把候选集里被实际引用的行标 is_cited=1（候选未引用的留 0，区分候选/被引用）。
    全程 try 包裹——检测失败绝不影响回答落库/展示。
    """
    try:
        refs = parse_refs(lean_answer)
        if not refs:
            return
        cited_ids = [r.source_id for r in refs]
        # 标记候选集里被引用的行
        try:
            mark_cited(agent_msg_id, conversation_id, cited_ids)
        except Exception as e:
            logger.warning("[%s] mark_cited 失败: %s", conversation_id, e)
        # 幻觉检测：被引用集 - 候选集
        hallucinated = detect_hallucination(refs, candidates)
        if hallucinated:
            logger.warning(
                "[%s] 引用幻觉检测：model 引用了候选集没有的 source_id %s "
                "（可能是编造 id 或候选收集漏抓）",
                conversation_id, hallucinated,
            )
    except Exception as e:
        logger.warning("[%s] 幻觉检测异常（不影响回答）: %s", conversation_id, e)


def _persist_and_enrich(
    conversation_id: str,
    agent_msg_id: int,
    lean_answer: str,
    result: dict,
    enrich: bool = True,
) -> str:
    """落库后处理（想法 2(b) bind-by-id）：收集候选 enrichment 存 sidecar，
    再把 lean answer enrich 成 rich 返回给前端展示。

    lean_answer 已是 process_llm_output 后的纯净 answer（messages.content 落的就是 lean）。
    sidecar 关联 agent_msg_id（落库时才有，故候选收集推迟到此刻——语义等价于回路即时收集，
    见 plan「候选收集层语义」）。无工具结果/无 ref 时 no-op，返回原 lean。

    流式路径用 result['tool_results']（_consume_events 在 on_tool_end 累积）；
    非流式路径由调用方先把 ToolMessage 收集成同结构列表塞进 result['tool_results']。
    enrich=False 时只存 sidecar 不做展示 merge（非流式 chat/regenerate 是测试兜底路径，
    返回 lean，测试不关心 rich——见 plan 边界）。
    """
    tool_results = result.get("tool_results") or []
    candidates = collect_from_tool_results(tool_results)
    if candidates:
        try:
            save_candidates(
                agent_msg_id, conversation_id,
                [c.to_row(agent_msg_id, conversation_id) for c in candidates],
            )
        except Exception as e:
            # sidecar 写失败不阻断回答——展示降级为 lean（裸 source_id），回答正文不受影响
            logger.warning("[%s] 候选 enrichment 落 sidecar 失败: %s", conversation_id, e)

    if not enrich:
        # 非流式测试兜底路径仍做幻觉检测 + is_cited 标记（不 enrich 展示，但信号要记）
        _detect_and_mark_hallucination(
            conversation_id, agent_msg_id, lean_answer, candidates
        )
        return lean_answer
    refs = parse_refs(lean_answer)
    if not refs:
        return lean_answer  # 无引用，no-op
    _detect_and_mark_hallucination(
        conversation_id, agent_msg_id, lean_answer, candidates
    )
    # 优先用本消息 sidecar；本消息缺的候选再跨消息补（同会话多次检索同一篇）
    enrich_map = load_enrichment_for_message(agent_msg_id)
    cited_ids = {r.source_id for r in refs}
    missing = cited_ids - enrich_map.keys()
    if missing:
        try:
            from src.core.citation_store import load_enrichment_map
            extra = load_enrichment_map(conversation_id, missing)
            enrich_map.update(extra)
        except Exception as e:
            logger.warning("[%s] 跨消息 enrichment 补全失败: %s", conversation_id, e)
    return enrich_refs(lean_answer, enrich_map)


async def chat_stream(
    user_message: str,
    conv_id: str,
    request,
    user_id: str = "default",
    translation: bool = False,
    mode: str = "normal",
    parent_id: int = None,
):
    """流式问答生成器：边推理边推 SSE，跑完后落库并发 done。"""
    conversation_id = f"{user_id}_{conv_id}"
    memory = ConversationMemory(conversation_id)
    result: dict = {"tool_results": []}  # _consume_events 在 on_tool_end 累积
    try:
        agent, initial_state = _prepare(
            memory, user_message, conv_id, user_id, translation, mode, parent_id
        )

        async for frame in _consume_events(agent, initial_state, request, result):
            yield frame

        # 断连/中断未拿到权威内容 → 不落库（本轮丢弃）
        if "final_content" not in result:
            logger.info("[%s] 未拿到权威 final_content，跳过落库", conversation_id)
            return

        agent_msg_pure = process_llm_output(result["final_content"], conversation_id)
        if not agent_msg_pure:
            agent_msg_pure = _EMPTY_ANSWER_TEXT
            logger.warning("[%s] 写入空回答占位文本", conversation_id)

        # 确保 conversations 表有这条对话的记录
        conv_repo = ConversationRepo()
        try:
            conv_repo.ensure_exists(conversation_id, user_id, user_message)
        finally:
            conv_repo.close()

        user_res = memory.add(HumanMessage(content=user_message), parent_id=parent_id)
        agent_res = memory.add(
            AIMessage(content=agent_msg_pure), parent_id=user_res["message_id"]
        )
        warning = (
            f"当前对话存储已超上限{WARN_THRESHOLD}，建议开启新对话以保证回答质量。"
            if agent_res.get("warning")
            else None
        )
        yield _format_sse(
            "done",
            user_msg_id=user_res["message_id"],
            agent_msg_id=agent_res["message_id"],
            warning=warning,
            answer=_persist_and_enrich(
                conversation_id, agent_res["message_id"], agent_msg_pure, result
            ),  # 权威文本：落 lean 入库 + enrich 出 rich 给前端覆盖
        )
    except asyncio.CancelledError:
        # 客户端断连引发的取消：直接抛出，不落库
        raise
    except Exception as e:
        logger.exception("[%s] 流式问答异常", conversation_id)
        yield _format_sse("error", message=str(e))
    finally:
        memory.close()


async def regenerate_stream(
    user_message: str,
    conv_id: str,
    request,
    user_id: str = "default",
    translation: bool = False,
    mode: str = "normal",
    parent_id: int = None,
    old_agent_msg_id: int = None,
):
    """流式重生成生成器：标旧消息+取 version 推迟到落库时与 add 一起做，
    断连不留悬挂。done.user_msg_id 固定为 parent_id。"""
    conversation_id = f"{user_id}_{conv_id}"
    memory = ConversationMemory(conversation_id)
    result: dict = {"tool_results": []}  # _consume_events 在 on_tool_end 累积
    try:
        agent, initial_state = _prepare(
            memory, user_message, conv_id, user_id, translation, mode, parent_id
        )

        async for frame in _consume_events(agent, initial_state, request, result):
            yield frame

        if "final_content" not in result:
            logger.info("[%s] 未拿到权威 final_content，跳过落库", conversation_id)
            return

        agent_msg_pure = process_llm_output(result["final_content"], conversation_id)
        if not agent_msg_pure:
            agent_msg_pure = _EMPTY_ANSWER_TEXT
            logger.warning("[%s] 写入空回答占位文本", conversation_id)

        # 推迟到落库时：标旧消息 regenerated + 取新 version，再与 add 一起做
        regen_res = memory.regenerate(old_agent_msg_id, parent_id)
        if not regen_res.get("success"):
            raise Exception(f"标记旧消息失败: {regen_res.get('detail')}")
        version = regen_res.get("version")

        agent_res = memory.add(
            AIMessage(content=agent_msg_pure), parent_id=parent_id, version=version
        )
        warning = (
            f"当前对话存储已超上限{WARN_THRESHOLD}，建议开启新对话以保证回答质量。"
            if agent_res.get("warning")
            else None
        )
        yield _format_sse(
            "done",
            user_msg_id=parent_id,
            agent_msg_id=agent_res["message_id"],
            warning=warning,
            answer=_persist_and_enrich(
                conversation_id, agent_res["message_id"], agent_msg_pure, result
            ),  # 权威文本：落 lean 入库 + enrich 出 rich 给前端覆盖
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("[%s] 流式重生成异常", conversation_id)
        yield _format_sse("error", message=str(e))
    finally:
        memory.close()
