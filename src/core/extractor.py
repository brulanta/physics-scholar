import fitz
import re
import requests
import time
from openai import OpenAI
import os
from dotenv import load_dotenv
import json
from src.config import MAIN_LLM_API_KEY, MAIN_LLM_BASE_URL
from langchain_core.messages import SystemMessage
from src.llm import sub_llm

load_dotenv()


def extract_from_metadata(doc) -> dict:
    # fitz元数据
    Metadata = doc.metadata
    return {"title": Metadata.get("title", ""), "author": Metadata.get("author", "")}


def extract_doi(first_page_text: str) -> str:
    # 正则提取DOI
    pattern = r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+"
    doi = re.findall(pattern, first_page_text)
    return doi


headers = {"User-Agent": "physics-scholar/1.0 (mailto:13159331923@163.com)"}


def query_crossref(doi: str) -> dict:
    # 让GPT帮忙加固了限速和重试
    url = f"https://api.crossref.org/works/{doi}"
    max_retries = 3
    delay = 1  # 限速（秒）

    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, timeout=10)

            if r.status_code == 200:
                data = r.json()
                msg = data.get("message", {})

                return {
                    "success": True,
                    "metadata": {
                        "title": msg.get("title", [""])[0],
                        "author": " ".join(
                            [
                                (a.get("given", "") + " " + a.get("family", "")).strip()
                                for a in msg.get("author", [])
                            ]
                        ),
                        "year": (
                            msg.get("issued", {}).get("date-parts", [[None]])[0][0]
                        ),
                    },
                }

            else:
                return {"success": False, "detail": f"HTTP {r.status_code}"}

        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(delay * (attempt + 1))  # 指数退避
            else:
                return {"success": False, "detail": str(e)}

        time.sleep(delay)  # 限速（每次请求后）


def extract_from_llm(first_page_text: str) -> dict:
    PROMPT = """
角色：
你是一个论文信息提取助手。

任务：
请根据论文首页文本进行推断，并填入论文各项信息。

格式：
{{
    "title":"<论文标题>",
    "author":"<论文作者，多人用英文逗号隔开，如: Zhang Wei, Li Ming>",
    "year":"<论文发表年份>"
}}
    
限制：
必须返回纯 JSON 格式。
如果推断后仍存在不明确的信息项则留空，不要编造。

论文首页文本:
{first_page_text}
"""
    # 让 sub_llm 动态绑定 json 格式输出
    json_llm = sub_llm.bind(response_format={"type": "json_object"})

    # 构建消息并调用
    messages = [SystemMessage(content=PROMPT.format(first_page_text=first_page_text))]
    res = json_llm.invoke(messages)

    try:
        # LangChain 返回的是 AIMessage 对象，内容在 .content 属性里
        answer = json.loads(res.content)
        return answer
    except Exception as e:
        # 加上容错机制，防止页面解析彻底崩溃
        return {"title": "", "author": "", "year": ""}


def is_good_enough(res: dict, strict: bool) -> bool:
    has_title = bool(res.get("title")) and res.get("title") != "untitled"
    if not has_title:
        return False
    if strict:
        return bool(res.get("author")) and bool(res.get("year"))
    return True


def extract_metadata(pdf_path: str, strict: bool = False) -> dict:
    doc = fitz.open(pdf_path)
    page_content = doc[0].get_text()

    # 第一层：PDF元数据
    res = extract_from_metadata(doc)
    if is_good_enough(res, strict):
        return res

    # 第二层：DOI查询
    dois = extract_doi(page_content)
    if dois:
        crossref_res = query_crossref(dois[0])
        if crossref_res.get("success"):
            res = crossref_res["metadata"]
            if is_good_enough(res, strict):
                return res

    # 第三层：LLM兜底
    return extract_from_llm(page_content)
