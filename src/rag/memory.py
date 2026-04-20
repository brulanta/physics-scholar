# memory.py
from langchain_core.messages import HumanMessage, BaseMessage, AIMessage
from src.core.init_SQLite import get_conn
import sqlite3
from typing import Literal

INSERT_MESSAGES_SQL = """
INSERT INTO messages (
    conversation_id,
    role,
    content,
    parent_id,
    status,
    version
)
VALUES (?, ?, ?, ?, ?, ?)
"""

SELECT_MESSAGES_PARTIAL_SQL = """SELECT id, role, content FROM messages WHERE status='normal' AND conversation_id = ? ORDER BY id ASC"""

SELECT_MESSAGES_FULL_SQL = """
SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC
"""

SELECT_LAST_MESSAGES_ID_SQL = """
SELECT id FROM messages
WHERE conversation_id = ?
ORDER BY id DESC
LIMIT 1
"""

SELECT_MAX_VERSION_SQL = """
SELECT version FROM messages
WHERE parent_id = ?
ORDER BY id DESC
LIMIT 1
"""

SELECT_CHILDREN_SQL = """
SELECT * FROM messages WHERE parent_id = ? ORDER BY id ASC
"""

SELECT_ONE_MESSAGES_SQL = """
SELECT * FROM messages WHERE id = ?
"""

UPDATE_STATUS_SQL = """
UPDATE messages
SET status = ?
WHERE id = ?
"""

UPDATE_LIKED_SQL = """
UPDATE messages
SET liked = ?
WHERE id = ?
"""

DELETE_MESSAGES_SQL = "DELETE FROM messages WHERE conversation_id = ?"


class MessageRepo:
    def __init__(self):
        self.conn = get_conn()
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()

    def insert(
        self,
        conversation_id,
        role,
        content,
        parent_id: int = None,
        status="normal",
        version=1,
    ):
        try:
            if not parent_id:
                res = self.get_last_message_id(conversation_id)
                last_id = res.get("last_id")
                parent_id = last_id if last_id else None
            self.cur.execute(
                INSERT_MESSAGES_SQL,
                (conversation_id, role, content, parent_id, status, version),
            )
            self.conn.commit()
            return {"success": True, "message_id": self.cur.lastrowid}
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def get_last_message_id(self, conversation_id):
        try:
            self.cur.execute(SELECT_LAST_MESSAGES_ID_SQL, (conversation_id,))

            row = self.cur.fetchone()
            last_id = row["id"] if row else None
            return {"success": True, "last_id": last_id}
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def get_messages(self, conversation_id: str, fields: str = "partial"):
        try:
            if fields == "partial":
                SELECT_SQL = SELECT_MESSAGES_PARTIAL_SQL
            else:
                SELECT_SQL = SELECT_MESSAGES_FULL_SQL
            self.cur.execute(
                SELECT_SQL,
                (conversation_id,),
            )
            rows = self.cur.fetchall()
            # 只负责数据库操作
            return rows
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def get_max_version(self, parent_id: int):
        """重新生成时，查同一 parent 下最大 version，用来计算新 version"""
        try:
            self.cur.execute(SELECT_MAX_VERSION_SQL, (parent_id,))
            row = self.cur.fetchone()
            last_version = row["version"] if row else 0
            return {"success": True, "last_version": last_version}
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def update_status(self, message_id: int, status: str = "regenerated"):
        """更新某条消息的 status"""
        try:
            self.cur.execute(UPDATE_STATUS_SQL, (status, message_id))
            self.conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def get_children(self, message_id: int):
        """查某条消息下的直接子节点"""
        try:
            self.cur.execute(SELECT_CHILDREN_SQL, (message_id,))
            rows = self.cur.fetchall()
            return rows
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def get_message_by_id(self, message_id: int):
        """按 id 查单条"""
        try:
            self.cur.execute(SELECT_ONE_MESSAGES_SQL, (message_id,))
            row = self.cur.fetchone()
            return row
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def update_like(self, message_id: int, liked: Literal[1, -1, 0]):
        """点赞/踩"""
        try:
            self.cur.execute(UPDATE_LIKED_SQL, (liked, message_id))
            self.conn.commit()
            return {"success": True}
        except Exception as e:
            return {"success": False, "detail": str(e)}

    def delete(self, conversation_id: str):
        """删除某个对话下所有信息"""
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

    def add(self, message: BaseMessage, parent_id=None, version: int = 1):
        """单条插入，总量超限报预警"""
        role = "user" if isinstance(message, HumanMessage) else "assistant"
        res = self._repo.insert(
            self.conversation_id,
            role,
            message.content,
            parent_id,
            status="normal",
            version=version,
        )
        warning = self.warning()
        return res | warning

    def regenerate(self, old_agent_msg_id: int, parent_id: int) -> dict:
        res = self._repo.update_status(old_agent_msg_id, "regenerated")

        if not res.get("success"):
            return {"success": False, "detail": res.get("detail")}

        res = self._repo.get_max_version(parent_id)
        version = res.get("last_version") + 1
        return {"success": True, "new_parent_id": parent_id, "version": version}

    def get(self) -> list:
        """返回本对话tokens上限内的合法历史记录,list(BaseMessage)"""
        rows = self._repo.get_messages(self.conversation_id)
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

    def get_tree(self) -> list:
        """返回本对话id下的所有记录（含废弃），返回list[dict]"""
        rows = self._repo.get_messages(self.conversation_id, fields="full")
        return [dict(row) for row in rows]

    def warning(self):
        """对话达到tokens上限则触发预警"""
        return (
            {"warning": True}
            if (self._count_chars() > self.max_tokens)
            else {"warning": False}
        )

    def _count_chars(self) -> int:
        """计算本对话目前的token数"""
        rows = self._repo.get_messages(self.conversation_id)
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
