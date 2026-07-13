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
        source_url TEXT,          -- 新增
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

    # ref_enrichment：引用元信息 sidecar（想法 2(b) bind-by-id）。
    # lean ref（messages.content 存 model 原文 [source_id | 摘抄]）+ enrichment（本表，harness 填的
    # 结构化元信息 ground truth）。展示期后端 merge 出 rich 给前端，不入库 messages。
    # 候选集语义：工具返回的所有可引用项都落这里（is_cited=0）；model 实际引用的 Step 2 标 1。
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ref_enrichment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id INTEGER NOT NULL,
        conversation_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        ref_type TEXT,
        title TEXT,
        authors TEXT,
        venue TEXT,
        year TEXT,
        doi TEXT,
        url TEXT,
        doc_id TEXT,
        page TEXT,
        raw_meta TEXT,
        is_cited INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute(
        """CREATE INDEX IF NOT EXISTS idx_enrich_msg ON ref_enrichment(message_id)"""
    )

    cur.execute(
        """CREATE INDEX IF NOT EXISTS idx_enrich_source ON ref_enrichment(conversation_id, source_id)"""
    )

    conn.commit()
    conn.close()
