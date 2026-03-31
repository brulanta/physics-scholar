from src.core.ingestor import get_vectorstore

vs = get_vectorstore()
restriever = vs.as_retriever(search_kwargs={"k": 5, "filter": {"section": "body"}})
