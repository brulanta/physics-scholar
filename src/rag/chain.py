from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os
from src.rag.retriever import restriever
from src.rag.prompt import prompt
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from src.rag.memory import ConversationMemory
from operator import itemgetter

load_dotenv()

llm = ChatOpenAI(
    model="deepseek-chat",
    temperature=0.5,
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)


def format_context(docs) -> str:
    chunks = []
    for doc in docs:
        title = doc.metadata.get("title", "未知")
        page = doc.metadata.get("page_number", "")
        page_str = f", Page {page}" if page else ""
        chunks.append(f"[{title}{page_str}]\n{doc.page_content}")
    return "\n\n---\n\n".join(chunks)


def format_history(history: list) -> str:
    if not history:
        return "无对话历史"
    lines = []
    for msg in history:
        role = "用户" if msg["role"] == "user" else "助手"
        lines.append(f"{role}: {msg['content']}")
    return "\n".join(lines)


rag_chain = (
    RunnablePassthrough.assign(
        context=itemgetter("question") | restriever | format_context
    )
    | prompt
    | llm
    | StrOutputParser()
)


# 调用层维护（现在用dict，后期换SQLite）
sessions = {}  # {conversation_id: ConversationMemory}

WARN_THRESHOLD = 3000


def get_or_create_session(conversation_id: str) -> ConversationMemory:
    if conversation_id not in sessions:
        sessions[conversation_id] = ConversationMemory()
    return sessions[conversation_id]


def ask(question: str, conversation_id: str) -> dict:
    memory = get_or_create_session(conversation_id)
    history = memory.get()

    answer = rag_chain.invoke(
        {
            "question": question,
            "history": format_history(history),
        }
    )

    memory.add("user", question)
    memory.add("assistant", answer)

    # 检查是否接近上限
    char_count = memory._count_chars()
    warning = None
    if char_count > WARN_THRESHOLD:
        warning = f"当前对话已累积{char_count}字，建议开启新对话以保证回答质量。"

    return {"answer": answer, "warning": warning}
