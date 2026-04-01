# memory.py
from datetime import datetime


class ConversationMemory:
    def __init__(self, max_tokens: int = 20000):
        self.max_tokens = max_tokens  # 字数上限放这里
        self.history = []

    def add(self, role: str, content: str):
        self.history.append(
            {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
        )
        self._trim()

    def get(self) -> list:
        return self.history

    def _trim(self):
        while self._count_chars() > self.max_tokens and self.history:
            self.history.pop(0)

    def _count_chars(self) -> int:
        return sum(len(m["content"]) for m in self.history)

    def clear(self):
        self.history = []


# 调用层维护（现在用dict，后期换SQLite）
sessions = {}  # {conversation_id: ConversationMemory}

WARN_THRESHOLD = 3000


def get_or_create_session(conversation_id: str) -> ConversationMemory:
    if conversation_id not in sessions:
        sessions[conversation_id] = ConversationMemory()
    return sessions[conversation_id]


def clear_user_sessions(user_id: str):
    keys_to_delete = [k for k in sessions if k.startswith(f"{user_id}_")]
    for k in keys_to_delete:
        del sessions[k]
