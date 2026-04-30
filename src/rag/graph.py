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

load_dotenv()
logger = get_logger(__name__)


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    conv_id: str
    user_id: str
    translation: bool
    tool_call_count: int  # 新增


llm = ChatOpenAI(
    model="deepseek-v4-flash",
    temperature=0.5,
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
    extra_body={
        "thinking": {"type": "disabled"},
        "parallel_tool_calls": False,
    },
)


def should_call_tool(state: AgentState) -> str:
    last_msg = state["messages"][-1]

    if state.get("tool_call_count", 0) >= 6:
        logger.warning("[should_call_tool] 达到最大工具调用轮次，强制进入final_answer")
        return "final_answer"  # 新增节点

    if last_msg.tool_calls:
        return "execute_tools"
    return END


def final_answer(state: AgentState) -> dict:
    messages = list(state["messages"])
    last_msg = messages[-1]

    # 如果最后一条消息有未响应的tool_calls，补假的ToolMessage
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        for tc in last_msg.tool_calls:
            messages.append(
                ToolMessage(
                    content="[工具调用已取消，已达最大调用次数上限，请根据已有信息作答]",
                    tool_call_id=tc["id"],
                )
            )

    # 追加强制作答提示
    messages.append(
        HumanMessage(
            content=(
                "[系统提示] 工具调用已达上限。"
                "请根据已获取的信息直接回答用户问题，不要再调用工具。"
                "如果信息不足，如实说明已知部分并指出局限。"
            )
        )
    )

    response = llm.invoke(messages)
    return {"messages": [response]}


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

        if is_after_tool:
            messages[-1] = ToolMessage(
                content=last_msg.content
                + (
                    "\n\n[请在 <thinking> 块中整合以上工具结果，"
                    "判断信息是否足够，再决定下一步。]"
                ),
                tool_call_id=last_msg.tool_call_id,
            )

        response = llm_with_tools.invoke(messages)

        # 串行裁剪
        if hasattr(response, "tool_calls") and len(response.tool_calls) > 1:
            logger.debug(
                "[call_llm] 并行tool_calls裁剪: %d → 1，丢弃: %s",
                len(response.tool_calls),
                [tc["name"] for tc in response.tool_calls[1:]],
            )
            response.tool_calls = response.tool_calls[:1]
            if "tool_calls" in response.additional_kwargs:
                response.additional_kwargs["tool_calls"] = response.additional_kwargs[
                    "tool_calls"
                ][:1]

        # 更新tool_call_count
        new_count = state.get("tool_call_count", 0)
        if response.tool_calls:
            new_count += 1

        content = response.content or ""
        has_thinking = "<thinking>" in content
        logger.debug(
            "[call_llm][%s] thinking=%s | tool_calls=%s | content_len=%d | round=%d",
            "tool_after" if is_after_tool else "first_call",
            has_thinking,
            [tc["name"] for tc in response.tool_calls] if response.tool_calls else [],
            len(content),
            new_count,
        )
        logger.debug(content)

        return {"messages": [response], "tool_call_count": new_count}

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
                "tool_call_count": 0,
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
                "tool_call_count": 0,
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
