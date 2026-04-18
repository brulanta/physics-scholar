# tests/test_rag_chain.py
import pytest
from src.rag.chain import ask
from src.rag.memory import ConversationMemory, sessions, get_or_create_session

CONV_ID = "test_rag_001"


@pytest.fixture(autouse=True)
def cleanup():
    sessions.pop(CONV_ID, None)
    yield
    sessions.pop(CONV_ID, None)


def test_returns_answer():
    result = ask("What is reservoir computing?", CONV_ID)
    assert "answer" in result
    assert len(result["answer"]) > 0


def test_history_accumulates():
    from src.rag.memory import sessions  # 直接import sessions

    ask("What is reservoir computing?", CONV_ID)
    print(sessions)  # 看看第一次ask后sessions里有没有CONV_ID
    ask("Can you elaborate?", CONV_ID)
    print(sessions)
    memory = ConversationMemory(CONV_ID)
    try:
        assert len(memory.get()) == 4
    finally:
        memory.close()


# def test_memory_trim():
#     # 临时调低上限
#     memory = ConversationMemory(max_tokens=100)
#     for i in range(20):
#         memory.add("user", f"这是第{i}条测试消息，用来撑满内存上限")
#         memory.add("assistant", f"这是第{i}条回复消息")
#     # trim后总字数应该在上限以内
#     assert memory._count_chars() <= 100


def test_warning_triggered():
    from src.rag import memory as mem_module

    original = mem_module.WARN_THRESHOLD
    mem_module.WARN_THRESHOLD = 10  # 极低阈值必触发

    result = ask("What is reservoir computing?", CONV_ID)
    assert result["warning"] is not None

    mem_module.WARN_THRESHOLD = original  # 还原
