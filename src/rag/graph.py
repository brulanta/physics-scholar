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


def should_call_tool(state: AgentState) -> str:
    last_msg = state["messages"][-1]
    remaining = state.get("remaining_calls", 6)

    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        if remaining < 0:
            logger.warning("[should_call_tool] 工具额度耗尽，强制进入 final_answer")
            return "final_answer"
        return "execute_tools"

    return END


def build_final_prefill() -> str:
    runtime_status = (
        "[RUNTIME_STATUS]\n"
        "- Remaining_Tool_Calls: 0/6\n"
        "- 🚫 SYSTEM OVERRIDE: 检测到违规工具调用请求，已强制拦截。\n"
        "- ⚠️ CRITICAL WARNING: 当前处于最终兜底节点。\n"
        "  若本轮仍输出工具调用，pipeline 将直接终止，用户将收到空回复。\n"
        "  若本轮输出正常文本，用户将收到你的分析结论。\n"
        "  这是唯一的选择窗口。\n"
        "[/RUNTIME_STATUS]"
    )
    lead = (
        "上一轮的工具调用请求已被系统强制取消，所有查询路径已关闭。"
        "现在，我必须进入纯逻辑整合模式：只基于此刻已掌握的证据，"
        "进行一次诚实且有边界的推理。无需为无法核查的信息致歉，"
        "直接基于现有拼图给出最优答案，并清晰标出推断的基石与局限。"
        "我来整理手头所有资料，组织最终结论。"
    )
    return f"{runtime_status}\n\n{lead}\n<thinking>\n"


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


def build_prefill(remaining: int, is_after_tool: bool) -> str:
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
        lead = "明白了，工具调用已达上限，现在完全基于已有信息进行推理。"
    elif is_after_tool:
        lead = "拿到这一轮的反馈了，先过筛，再决定下一步。"
    else:
        lead = "好的，收到，让我梳理一下思路。"

    return f"{runtime_status}\n\n{lead}\n<thinking>\n"


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
        is_after_tool = isinstance(last_msg, ToolMessage)
        remaining = state.get("remaining_calls", 6)

        # 实时生成，不从 state 读
        prefill = build_prefill(remaining, is_after_tool)

        invoke_messages = messages + [AIMessage(content=prefill)]
        response = invoke_with_retry(llm_with_tools, invoke_messages)

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
        }

    graph = StateGraph(AgentState)
    graph.add_node("call_llm", call_llm)
    graph.add_node("tool_node", tool_node)
    # graph.add_node("save_to_memory", save_to_memory)
    graph.add_node("final_answer", final_answer)
    graph.set_entry_point("call_llm")
    graph.add_conditional_edges(
        "call_llm",
        should_call_tool,
        {
            "execute_tools": "tool_node",
            "final_answer": "final_answer",
            END: END,
        },
    )
    graph.add_edge("final_answer", END)
    graph.add_edge("tool_node", "call_llm")
    # graph.add_edge("save_to_memory", END)

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
        history = format_history(memory.get(leaf_message_id=parent_id))

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
        history = format_history(memory.get(leaf_message_id=parent_id))
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
