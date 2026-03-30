# memory.py
from datetime import datetime


class ConversationMemory:
    def __init__(self, max_tokens: int = 4000):
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
        # 超出上限时从最早的消息开始删
        while self._count_chars() > self.max_tokens and len(self.history) > 2:
            self.history.pop(0)

    def _count_chars(self) -> int:
        return sum(len(m["content"]) for m in self.history)

    def clear(self):
        self.history = []
