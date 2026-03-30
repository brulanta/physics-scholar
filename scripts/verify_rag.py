# scripts/verify_rag.py
# 手动验证脚本不是自动测试

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import PDF_DIR
from src.core.ingestor import ingest_pdf, confirm_and_index, get_vectorstore
from src.core import registry

TEST_USER = "seed"

PDFS = [
    "2021_SPIE_Real-time identification of frequency-hopping millimeter-wave signals using photonic time stretch and reservoir computing.pdf",
    "Parallel-Reservoir-Computing-Using-Optical-Amplifiers.pdf",
    "Tang 等 - 2023 - Asynchronous photonic time-delay reservoir computi.pdf",
    "Zhong Dongzhou 等 - 2022 - 基于光学储备池计算的高速混沌保密通信的研究.pdf",
    "不同类型胶原蛋白在皮肤衰老中的作用及其研究进展.pdf",
]

QUERIES = [
    "reservoir computing optical amplifier photonic",
    "frequency hopping millimeter wave signal recognition",
    "time delay reservoir computing asynchronous",
    "混沌保密通信 光学储备池",
    "胶原蛋白 皮肤衰老",
]


def ingest_all():
    print("\n========== 开始入库 ==========\n")
    for filename in PDFS:
        pdf_path = str(PDF_DIR / filename)
        print(f"▶ 处理: {filename}")

        result = ingest_pdf(pdf_path, source_type="seed", user_id=TEST_USER)

        if not result["success"]:
            print(f"  ⚠ 跳过: {result['detail']}\n")
            continue

        meta = result["paper_meta"]
        print(f"  提取title: {meta.title}")
        print(f"  作者: {meta.author}")
        print(f"  年份: {meta.year}")

        # 让用户确认title
        user_input = input(f"  确认title？直接回车接受，或输入新title: ").strip()
        confirmed_title = user_input if user_input else meta.title

        print(f"  入库中...")
        confirmed = confirm_and_index(
            paper_meta=meta,
            pdf_path=pdf_path,
            confirmed_title=confirmed_title,
            user_id=TEST_USER,
        )

        if confirmed["success"]:
            print(f"  ✅ 入库成功\n")
        else:
            print(f"  ❌ 入库失败: {confirmed.get('detail')}\n")


def verify_recall():
    print("\n========== 开始召回验证 ==========\n")
    vs = get_vectorstore()

    for query in QUERIES:
        print(f"🔍 查询: {query}")
        docs = vs.similarity_search(
            query,
            k=3,
            filter={"section": "body"},  # 只召回body
        )

        for i, doc in enumerate(docs):
            print(f"  [{i + 1}] 来源: {doc.metadata.get('title', '未知')}")
            print(f"       段落: {doc.page_content[:100]}...")

        print()
        input("  按回车继续下一个查询...")
        print()


if __name__ == "__main__":
    print("选择操作:")
    print("  1. 全部入库")
    print("  2. 只测召回")
    print("  3. 入库 + 测召回")
    choice = input("请输入 1/2/3: ").strip()

    if choice == "1":
        ingest_all()
    elif choice == "2":
        verify_recall()
    elif choice == "3":
        ingest_all()
        verify_recall()
    else:
        print("无效输入")
