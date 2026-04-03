# memory.py
from langchain_core.messages import HumanMessage, BaseMessage


class ConversationMemory:
    def __init__(self, max_tokens: int = 20000):
        self.max_tokens = max_tokens  # 字数上限放这里
        self.history = []

    def add(self, message: BaseMessage):
        self.history.append(message)
        self._trim()

    def get(self) -> list:
        return self.history

    def _trim(self):
        while self._count_chars() > self.max_tokens and self.history:
            self.history.pop(0)

    def _count_chars(self) -> int:
        return sum(len(m.content) for m in self.history)

    def clear(self):
        self.history = []


# 调用层维护（现在用dict，后期换SQLite）
sessions = {}  # {conversation_id: ConversationMemory}

WARN_THRESHOLD = 18000


def get_or_create_session(conversation_id: str) -> ConversationMemory:
    if conversation_id not in sessions:
        sessions[conversation_id] = ConversationMemory()
    return sessions[conversation_id]


def clear_user_sessions(user_id: str):
    keys_to_delete = [k for k in sessions if k.startswith(f"{user_id}_")]
    for k in keys_to_delete:
        del sessions[k]


def format_history(history: list) -> str:
    if not history:
        return "无对话历史"
    lines = []
    for msg in history:
        role = "用户" if isinstance(msg, HumanMessage) else "助手"
        lines.append(f"{role}: {msg.content}")
    return "\n".join(lines)


def strip_thinking(answer: str) -> str:
    # 去掉<thinking>...</thinking>部分
    import re

    return re.sub(r"<thinking>.*?</thinking>", "", answer, flags=re.DOTALL).strip()
