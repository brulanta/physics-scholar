from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import os
from src.rag.retriever import restriever
from src.rag.prompt import prompt, CITATION_DEFAULT, CITATION_TRANSLATION
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from src.rag.memory import get_or_create_session, WARN_THRESHOLD
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


def strip_thinking(answer: str) -> str:
    # 去掉<thinking>...</thinking>部分
    import re

    return re.sub(r"<thinking>.*?</thinking>", "", answer, flags=re.DOTALL).strip()


def ask(
    question: str,
    conv_id: str,
    translation: bool = False,
    user_id: str = "default",
) -> dict:
    conversation_id = f"{user_id}_{conv_id}"
    memory = get_or_create_session(conversation_id)
    history = memory.get()

    answer = rag_chain.invoke(
        {
            "question": question,
            "history": format_history(history),
            "citation_plugin": CITATION_TRANSLATION
            if translation
            else CITATION_DEFAULT,
        }
    )

    memory.add("user", question)
    memory.add("assistant", strip_thinking(answer))  # 只存正式回答

    # 检查是否接近上限
    char_count = memory._count_chars()
    warning = None
    if char_count > WARN_THRESHOLD:
        warning = f"当前对话已累积{char_count}字，建议开启新对话以保证回答质量。"

    return {"answer": answer, "warning": warning}
