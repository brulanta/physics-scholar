from src.rag.tools.arxiv_tool import arxiv_tool

if __name__ == "__main__":
    result = arxiv_tool.invoke(
        {"keywords": ["photonics"], "recent_days": 7, "max_results": 3}
    )

    print(type(result))
    print(len(result) if isinstance(result, list) else result)

    if isinstance(result, list):
        for p in result:
            print(p)
            print("-" * 50)
