# scripts/verify_rag_chain.py
# 手动验证脚本不是自动测试
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag.chain import ask, get_or_create_session

CONV_ID = "test_conv_001"

# 把memory阈值临时调低方便测试
from src.rag import memory as mem_module

mem_module.WARN_THRESHOLD = 500
from src.rag.memory import ConversationMemory

ConversationMemory.__init__.__defaults__ = (300,)  # max_tokens调到300

QUESTIONS = [
    # "What is reservoir computing?",
    # "How do optical amplifiers contribute to reservoir computing?",
    "请用中文总结一下储备池计算的主要优势",
    # 越界测试
    "胶原蛋白对皮肤有什么作用？",
]

if __name__ == "__main__":
    print("========== RAG Chain 测试 ==========\n")
    for q in QUESTIONS:
        print(f"🙋 问题: {q}")
        result = ask(q, CONV_ID)
        print(f"🤖 回答:\n{result['answer']}")
        if result["warning"]:
            print(f"⚠️  {result['warning']}")
        print("\n" + "=" * 50 + "\n")
        input("按回车继续...")
