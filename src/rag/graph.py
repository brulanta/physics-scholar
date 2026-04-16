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
    strip_thinking,
    WARN_THRESHOLD,
)
from src.rag.prompt import (
    SYSTEM_PROMPT_NORMAL,
    CITATION_DEFAULT,
    CITATION_TRANSLATION,
    SYSTEM_PROMPT_DISCUSS,
)

load_dotenv()


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


def save_to_memory(state: AgentState):
    conversation_id = f"{state['user_id']}_{state['conv_id']}"
    memory = ConversationMemory(conversation_id)
    try:
        messages = state["messages"]

        # 跳过第一条（system+history拼好的prompt）
        # 只存HumanMessage和最后的AIMessage
        for msg in messages:
            if isinstance(msg, HumanMessage):
                memory.add(msg)

        # 最后一条AIMessage是本轮最终回答
        last_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)), None
        )
        if last_ai:
            clean_content = strip_thinking(last_ai.content)
            memory.add(AIMessage(content=clean_content))
    finally:
        memory.close()


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
    graph.add_node("save_to_memory", save_to_memory)
    graph.set_entry_point("call_llm")
    graph.add_conditional_edges(
        "call_llm",
        should_call_tool,
        {"execute_tools": "tool_node", END: "save_to_memory"},
    )
    graph.add_edge("tool_node", "call_llm")
    graph.add_edge("save_to_memory", END)

    return graph.compile()


def chat(
    user_message: str,
    conv_id: str,
    user_id: str = "default",
    translation: bool = False,
    mode: str = "normal",
) -> dict:
    # 在invoke之前先构建SystemMessage，这时候有所有需要的参数
    conversation_id = f"{user_id}_{conv_id}"
    memory = ConversationMemory(conversation_id)
    try:
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

        warning_flag = memory.warning().get("warning")
        warning = None
        if warning_flag:
            warning = (
                f"当前对话存储已超上限{WARN_THRESHOLD}，建议开启新对话以保证回答质量。"
            )

        return {"answer": result["messages"][-1].content, "warning": warning}
    finally:
        memory.close()
