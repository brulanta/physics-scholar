import sqlite3
from src.config import DB_PATH


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # messages
    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        conversation_id TEXT,
        role TEXT,
        content TEXT,

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        -- ⭐ 新增
        parent_id INTEGER,        -- 分支用（上一条）

        status TEXT DEFAULT 'normal',  -- normal / regenerated / deleted

        liked INTEGER DEFAULT 0,  -- 1 like, -1 dislike, 0 none

        version INTEGER DEFAULT 1 -- 重发版本
)
    """)

    cur.execute("""CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id)
""")

    # papers
    cur.execute("""
    CREATE TABLE IF NOT EXISTS papers (
        doc_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        title TEXT,
        author TEXT,
        year TEXT,
        file_name TEXT NOT NULL,
        upload_time TEXT,
        source_type TEXT,
        status TEXT CHECK(status IN ('pending', 'processing', 'indexed', 'failed')),
        page_count INTEGER,
        chunk_count INTEGER,
        PRIMARY KEY (doc_id, user_id)
    )
    """)

    cur.execute(
        """CREATE INDEX IF NOT EXISTS idx_papers_user_doc ON papers(user_id, doc_id)"""
    )

    # conversations
    cur.execute("""
    CREATE TABLE IF NOT EXISTS conversations (
        conversation_id TEXT PRIMARY KEY,
        user_id TEXT DEFAULT 'default',
        title TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id)
    """)

    conn.commit()
    conn.close()
