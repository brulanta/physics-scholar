from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage, BaseMessage, SystemMessage, AIMessage
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
from src.rag.prompt import (
    SYSTEM_PROMPT_NORMAL,
    CITATION_DEFAULT,
    CITATION_TRANSLATION,
    SYSTEM_PROMPT_DISCUSS,
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


llm = ChatOpenAI(
    model="deepseek-chat",
    temperature=0.5,
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)


def should_call_tool(state: AgentState) -> dict:
    if state["messages"][-1].tool_calls:
        return "execute_tools"
    return END


def build_agent(user_id: str):
    search_tool = make_search_tool(user_id)
    rag_tool = make_rag_tool(user_id)
    tools = [rag_tool, search_tool, arxiv_tool]
    llm_with_tools = llm.bind_tools(tools)
    tool_node = ToolNode(tools)

    # 把llm_with_tools和tool_node闭包进节点函数
    def call_llm(state):
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    graph = StateGraph(AgentState)
    graph.add_node("call_llm", call_llm)
    graph.add_node("tool_node", tool_node)
    # graph.add_node("save_to_memory", save_to_memory)
    graph.set_entry_point("call_llm")
    graph.add_conditional_edges(
        "call_llm",
        should_call_tool,
        {"execute_tools": "tool_node", END: END},
    )
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
        citation_plugin = CITATION_TRANSLATION if translation else CITATION_DEFAULT

        if mode == "discuss":
            system_msg = SystemMessage(
                content=SYSTEM_PROMPT_DISCUSS.format(
                    history=history, citation_plugin=citation_plugin
                )
            )
        else:
            system_msg = SystemMessage(
                content=SYSTEM_PROMPT_NORMAL.format(
                    history=history, citation_plugin=citation_plugin
                )
            )

        # 2. call agent
        agent = build_agent(user_id)

        result = agent.invoke(
            {
                "messages": [system_msg, HumanMessage(content=user_message)],
                "conv_id": conv_id,
                "user_id": user_id,
                "translation": translation,
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
        history = format_history(memory.get())
        citation_plugin = CITATION_TRANSLATION if translation else CITATION_DEFAULT

        if mode == "discuss":
            system_msg = SystemMessage(
                content=SYSTEM_PROMPT_DISCUSS.format(
                    history=history, citation_plugin=citation_plugin
                )
            )
        else:
            system_msg = SystemMessage(
                content=SYSTEM_PROMPT_NORMAL.format(
                    history=history, citation_plugin=citation_plugin
                )
            )

        agent = build_agent(user_id)

        result = agent.invoke(
            {
                "messages": [system_msg, HumanMessage(content=user_message)],
                "conv_id": conv_id,
                "user_id": user_id,
                "translation": translation,
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
