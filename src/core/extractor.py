import fitz
import re
import requests
import time
from openai import OpenAI
import os
from dotenv import load_dotenv
import json

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
    # 传给LLM，返回title/author/year
    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com"
    )
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
返回JSON格式。
如果推断后仍存在不明确的信息项则留空，不要编造。

论文首页文本:
{first_page_text}
"""
    res = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {
                "role": "system",
                "content": PROMPT.format(first_page_text=first_page_text),
            },
        ],
        response_format={"type": "json_object"},
        n=1,
        temperature=0.1,
        max_tokens=200,
        stop=["你:"],
    )
    answer = json.loads(res.choices[0].message.content)
    return answer


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
