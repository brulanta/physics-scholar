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
from typing import TypedDict, Annotated, Sequence
import os
from dotenv import load_dotenv
from src.rag.tools.rag_tool import make_rag_tool
from src.rag.tools.search_paper_tool import make_search_tool
from src.rag.tools.arxiv_tool import arxiv_tool
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
from src.core.trim_thinking import process_llm_output
from src.utils.logger import get_logger
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


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    conv_id: str
    user_id: str
    translation: bool
    remaining_calls: int
    thinking_retry_count: int
    is_thinking_correction: bool
    pending_correction: str  # 新增


llm = ChatOpenAI(
    model="deepseek-v4-flash",
    temperature=0.15,
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    extra_body={
        "thinking": {"type": "disabled"},
        "parallel_tool_calls": False,
    },
)


def thinking_guard(state: AgentState) -> dict:
    messages = list(state["messages"])
    last_msg = messages[-1]
    retry_count = state.get("thinking_retry_count", 0)
    remaining = state.get("remaining_calls", 6)

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
        "remaining_calls": min(remaining + 1, 6),
        "is_thinking_correction": True,
    }


def after_guard(state: AgentState) -> str:
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
        if remaining < 0:
            logger.warning("工具额度耗尽，强制进入 final_answer")
            return "final_answer"
        return "tool_node"

    # 无工具调用且非纠正状态，结束
    return END


def final_answer(state: AgentState) -> dict:
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
    final_prefill = build_final_prefill()
    invoke_messages = messages + [AIMessage(content=final_prefill)]
    response = llm.invoke(invoke_messages)

    return {"messages": [response]}


def build_prefill(
    remaining: int, is_after_tool: bool, is_thinking_correction: bool = False
) -> str:
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
        f"- Remaining_Tool_Calls: {max(remaining, 0)}/6{warning_line}\n"
        f"[/RUNTIME_STATUS]"
    )

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


def build_final_prefill() -> str:
    runtime_status = (
        "[RUNTIME_STATUS]\n"
        "- Remaining_Tool_Calls: 0/6\n"
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


def build_agent(user_id: str):
    search_tool = make_search_tool(user_id)
    rag_tool = make_rag_tool(user_id)
    tools = [rag_tool, search_tool, arxiv_tool]
    llm_with_tools = llm.bind_tools(tools)
    tool_node = ToolNode(tools)

    # 把llm_with_tools和tool_node闭包进节点函数
    def call_llm(state):
        messages = list(state["messages"])
        last_msg = messages[-1]
        is_after_tool = isinstance(last_msg, ToolMessage) or state.get(
            "is_thinking_correction", False
        )
        # 用完立刻重置
        remaining = state.get("remaining_calls", 6)

        # 实时生成，不从 state 读
        prefill = build_prefill(
            remaining,
            is_after_tool,
            is_thinking_correction=state.get("is_thinking_correction", False),
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

        response = invoke_with_retry(llm_with_tools, invoke_messages)

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

    graph = StateGraph(AgentState)
    graph.add_node("call_llm", call_llm)
    graph.add_node("thinking_guard", thinking_guard)
    graph.add_node("tool_node", tool_node)
    graph.add_node("final_answer", final_answer)

    graph.set_entry_point("call_llm")

    # call_llm 之后统一进 guard
    graph.add_edge("call_llm", "thinking_guard")

    # guard 之后条件路由
    graph.add_conditional_edges(
        "thinking_guard",
        after_guard,
        {
            "call_llm": "call_llm",
            "tool_node": "tool_node",
            "final_answer": "final_answer",
            END: END,
        },
    )

    graph.add_edge("tool_node", "call_llm")
    graph.add_edge("final_answer", END)

    return graph.compile()


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
        history = format_history(memory.get(leaf_message_id=parent_id, explicit=True))

        system_prompt = build_prompt(
            mode="normal" if mode == "normal" else "discuss",
            history=history,
            citation_plugin=CITATION_TRANSLATION if translation else CITATION_DEFAULT,
            debug=False,
        )
        print(system_prompt)

        # 2. call agent
        agent = build_agent(user_id)

        result = agent.invoke(
            {
                "messages": [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_message),
                ],
                "conv_id": conv_id,
                "user_id": user_id,
                "translation": translation,
                "remaining_calls": 6,
                "next_prefill": None,
            }
        )

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
        history = format_history(memory.get(leaf_message_id=parent_id, explicit=True))
        system_prompt = build_prompt(
            mode="normal" if mode == "normal" else "discuss",
            history=history,
            citation_plugin=CITATION_TRANSLATION if translation else CITATION_DEFAULT,
        )

        agent = build_agent(user_id)

        result = agent.invoke(
            {
                "messages": [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_message),
                ],
                "conv_id": conv_id,
                "user_id": user_id,
                "translation": translation,
                "remaining_calls": 6,
                "next_prefill": None,
            }
        )

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
