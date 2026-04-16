# memory.py
from langchain_core.messages import HumanMessage, BaseMessage, AIMessage
from src.core.init_SQLite import get_conn
import sqlite3

INSERT_MESSAGES_SQL = """
INSERT INTO messages (
        conversation_id,
        role,
        content
)
VALUES (?, ?, ?)
"""
SELECT_MESSAGES_SQL = """SELECT id,
        conversation_id,
        role,
        content,
        created_at FROM messages"""

DELETE_MESSAGES_SQL = "DELETE FROM messages WHERE conversation_id = ?"


class MessageRepo:
    def __init__(self):
        self.conn = get_conn()
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()

    def insert(self, conversation_id: str, message: BaseMessage):
        try:
            self.cur.execute(
                INSERT_MESSAGES_SQL,
                (
                    conversation_id,
                    "user" if isinstance(message, HumanMessage) else "assistant",
                    message.content,
                ),
            )
            self.conn.commit()

            return {"success": True}
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def get_history(self, conversation_id: str):
        try:
            self.cur.execute(
                SELECT_MESSAGES_SQL + " WHERE conversation_id=? ORDER BY id ASC",
                (conversation_id,),
            )
            rows = self.cur.fetchall()
            # 只负责数据库操作
            return rows
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def delete(self, conversation_id: str):
        try:
            self.cur.execute(DELETE_MESSAGES_SQL, (conversation_id,))
            self.conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def close(self):
        self.conn.close()


class ConversationMemory:
    def __init__(
        self,
        conversation_id: str,
        max_tokens: int = 20000,
    ):
        self.max_tokens = max_tokens  # 字数上限放这里
        self.conversation_id = conversation_id
        self._repo = MessageRepo()

    def add(self, message: BaseMessage):
        """单条插入，总量超限报预警"""
        res = self._repo.insert(self.conversation_id, message)
        warning = self.warning()
        return res | warning

    def get(self) -> list:
        """返回本对话tokens上限内的历史记录,list(BaseMessage)"""
        rows = self._repo.get_history(self.conversation_id)
        # 窗口取最后 max_tokens 囊括的条
        total = 0
        selected = []

        for msg in reversed(rows):
            total += len(msg["content"])
            if total > self.max_tokens:
                break
            selected.append(msg)

        selected_rows = reversed(selected)
        # 从list[tuple]转换为list[BaseMessage]
        history = []
        for row in selected_rows:
            role = row["role"]
            content = row["content"]
            if role == "user":
                history.append(HumanMessage(content=content))
            else:
                history.append(AIMessage(content=content))

        return history

    def warning(self):
        """对话达到tokens上限则触发预警"""
        return (
            {"warning": True}
            if (self._count_chars() > self.max_tokens)
            else {"warning": False}
        )

    def _count_chars(self) -> int:
        """计算本对话目前的token数"""
        rows = self._repo.get_history()
        return sum(len(m.content) for m in rows)

    def clear(self):
        """删除本对话对应的所有记录"""
        return self._repo.delete(self.conversation_id)

    def close(self):
        """由数据库操作层向外透传，调用实例后手动关闭数据库连接"""
        self._repo.close()


# 调用层维护（现在用dict，后期换SQLite）
sessions = {}  # {conversation_id: ConversationMemory}

WARN_THRESHOLD = 18000


# 迁移SQLite后废弃
# def get_or_create_session(conversation_id: str) -> ConversationMemory:
#     if conversation_id not in sessions:
#         sessions[conversation_id] = ConversationMemory(conversation_id)
#     return sessions[conversation_id]


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
