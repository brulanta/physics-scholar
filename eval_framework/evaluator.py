"""
PhysicsScholar 内容测试评分框架
================================
流程：
1. 从 test_cases.json 读取测试用例
2. 并行调用多个评委LLM打分（openai SDK，asyncio.to_thread包装）
3. 存档原始评分结果到 results/raw/
4. 调用汇总LLM生成测试报告到 results/report/

依赖：pip install openai python-dotenv
"""

import os
import json
import asyncio
from datetime import datetime
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

# 加载 .env 文件（自动向上查找，找到为止）
load_dotenv()


# ── 目录配置 ──────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
RAW_DIR = RESULTS_DIR / "raw"
REPORT_DIR = RESULTS_DIR / "report"
RAW_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

TEST_CASES_FILE = BASE_DIR / "test_cases.json"


# ── API配置（从环境变量读取）─────────────────────────────
DS_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# DeepSeek base_url（官方；第三方代理改这里）
DS_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# Gemini base_url（第三方代理改这里，需兼容OpenAI格式）
# 注意：填到 /v1 这一级，不含 /chat/completions
GEMINI_BASE_URL = os.environ.get(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"
)


# ── 客户端工厂──────────────────────────────────────────────
def make_ds_client() -> OpenAI:
    return OpenAI(api_key=DS_API_KEY, base_url=DS_BASE_URL)


def make_gemini_client() -> OpenAI:
    return OpenAI(api_key=GEMINI_API_KEY, base_url=GEMINI_BASE_URL)


# ── 模型配置──────────────────────────────────────────────
# provider决定用哪个client；model_id是传给API的model字符串
JUDGE_MODELS = os.environ.get(
    "JUDGE_MODELS",
    [
        {"name": "deepseek-v3", "provider": "deepseek", "model_id": "deepseek-chat"},
        {
            "name": "deepseek-v4-flash",
            "provider": "deepseek",
            "model_id": "deepseek-v4-flash",
        },
        {
            "name": "gemini-2.5-flash",
            "provider": "gemini",
            "model_id": "gemini-2.5-flash-preview-04-17",
        },
        {
            "name": "gemini-2.5-pro",
            "provider": "gemini",
            "model_id": "gemini-2.5-pro-preview-05-06",
        },
    ],
)
JUDGE_MODELS = json.loads(JUDGE_MODELS)

SUMMARY_MODEL = os.environ.get(
    "SUMMARY_MODEL",
    {
        "name": "gemini-3.1-pro-preview",
        "provider": "gemini",
        "model_id": "gemini-3.1-pro-preview",
    },
)

SUMMARY_MODEL = json.loads(SUMMARY_MODEL)

# ── Prompt第一层：评分宪法（固定）────────────────────────
SCORING_CONSTITUTION = """
## PhysicsScholar 评分宪法

### 被评对象说明

PhysicsScholar是一个面向微波光子学研究者的学术Agent。它的设定不是通用助手，而是一个有立场的研究者：
直接给出有依据的判断，从物理机制出发解释问题，对模糊表述会要求澄清，不做礼貌性的信息搬运。

你拿到的输入包含三部分：
- 用户的原始问题
- PhysicsScholar的最终回答
- 工具调用简报（如有，可能为空）

你的任务是对最终回答打分。工具调用简报仅用于异常检查，不作为打分依据。

---

### 评分维度

以下四个维度独立打分，每个维度三档。

**D1 内容准确性**
评判回答在微波光子学领域内是否站得住脚。
- 高：核心概念、物理机制、数值量级均无明显错误；如有不确定处主动说明
- 中：主体正确但有细节偏差，或对不确定的内容未作区分
- 低：存在实质性错误，或以模糊表述掩盖了知识缺口

**D2 回答深度**
评判回答是否触及问题的物理本质，而非停留在表层描述。
- 高：从物理机制出发解释现象，说清楚"为什么"而不只是"是什么"；数学出现时服务于理解，不是装饰
- 中：有一定解释但停留在定性描述，缺乏机制层面的分析
- 低：只有结论或术语堆砌，没有解释为什么

**D3 角色一致性**
评判回答是否符合PhysicsScholar的设定。
- 高：立场直接，有自己的判断；不用礼貌套话暖场；对模糊问题要求澄清而非宽松解读；不确定时明说
- 中：部分符合，但有通用助手的习惯性表达（如"当然"、"很好的问题"、过度总结）
- 低：表现为标准AI助手，设定基本失效

**D4 任务完成度**
评判用户的实际需求是否被满足。
- 高：用户的核心需求被直接回应；如果需求本身有问题，PhysicsScholar指出并给出更有效的方向
- 中：部分满足，但有遗漏或跑偏
- 低：回答与用户需求基本脱节

---

### 异常标记

不计入分数，独立检查。

**A1 信息填补异常**
检查工具调用简报（如有）：是否存在工具调用失败、但最终回答中使用了无来源信息的情况？
- 标记：是 / 否
- 如标记"是"，简要说明哪里存疑
""".strip()


# ── Prompt第二层：意图权重模板────────────────────────────
INTENT_TEMPLATES = {
    "I1": {
        "label": "理解型",
        "core": ["D1", "D2"],
        "lenient": ["D3", "D4"],
        "note": "重点看内容是否准确、是否触及物理机制本质。",
    },
    "I2": {
        "label": "检索型",
        "core": ["D4"],
        "lenient": ["D2", "D3"],
        "note": "找到没有、找得准不准是唯一核心，深度和风格退居其次。",
    },
    "I3": {
        "label": "评价型",
        "core": ["D3", "D2"],
        "lenient": ["D1", "D4"],
        "note": "这类问题最能暴露角色漂移，PhysicsScholar必须给出有依据的立场，不能和稀泥。",
    },
    "I4": {
        "label": "实现型",
        "core": ["D4", "D1"],
        "lenient": ["D2", "D3"],
        "note": "能跑、对不对是核心；风格和深度退居其次。",
    },
    "I5": {
        "label": "探索型",
        "core": ["D3", "D4"],
        "lenient": ["D1", "D2"],
        "note": "PhysicsScholar是否在推进而非评判，用户的探索需求是否被真正回应。",
    },
}


def build_intent_section(intents: list) -> str:
    if not intents:
        return ""

    labels = []
    core_dims = set()
    for intent in intents:
        t = INTENT_TEMPLATES.get(intent, {})
        labels.append(f"{intent} {t.get('label', '')}")
        core_dims.update(t.get("core", []))

    all_dims = {"D1", "D2", "D3", "D4"}
    lenient_dims = all_dims - core_dims

    if len(core_dims) == 4:
        weight_note = (
            "本题意图叠加后核心维度覆盖全部四个维度，请均衡评判，不做从宽处理。"
        )
    else:
        weight_note = (
            f"核心维度：{', '.join(sorted(core_dims))}\n"
            f"从宽维度：{', '.join(sorted(lenient_dims))}\n"
            "评分时以核心维度为主要判断依据，从宽维度如无明显问题可不作扣分依据。"
        )

    notes = "  ".join(
        INTENT_TEMPLATES[i].get("note", "") for i in intents if i in INTENT_TEMPLATES
    )

    return f"""### 本题意图标签：{" + ".join(labels)}

{weight_note}

说明：{notes}""".strip()


# ── 输出格式要求（固定）──────────────────────────────────
OUTPUT_FORMAT = """
### 输出格式

严格按以下格式输出，不要添加任何前言或后记：

D1 内容准确性：高/中/低
理由：（2-3句，指出具体依据）

D2 回答深度：高/中/低
理由：（2-3句）

D3 角色一致性：高/中/低
理由：（2-3句）

D4 任务完成度：高/中/低
理由：（2-3句）

A1 信息填补异常：是/否
说明：（如标记"是"则填写，否则留空）
""".strip()


# ── 汇总Prompt────────────────────────────────────────────
SUMMARY_SYSTEM = """
你是一个资深的AI系统评测专家。你将收到针对同一个学术Agent（PhysicsScholar）
同一道测试题的多个评委的打分结果。

你的任务是：
1. 综合各评委的评分和理由，给出每个维度的综合判断
2. 指出评委之间的主要分歧点（如有）
3. 标记是否存在"高争议"case（评委在同一维度出现高/低两极分化）
4. 对本题的整体表现给出简短的综合评语（3-5句）

输出格式：

## 综合评分
D1 内容准确性：高/中/低（置信度：高/中/低）
D2 回答深度：高/中/低（置信度：高/中/低）
D3 角色一致性：高/中/低（置信度：高/中/低）
D4 任务完成度：高/中/低（置信度：高/中/低）
A1 异常标记：是/否

## 评委分歧
（列出评委之间存在明显分歧的维度及分歧内容，无分歧则写"无明显分歧"）

## 争议标记
高争议：是/否
（如是，说明哪个维度存在两极分化）

## 综合评语
（3-5句，指出本题回答的主要优点和不足）
""".strip()


# ── Prompt组装──────────────────────────────────────────
def build_judge_prompt(case: dict) -> str:
    question = case.get("question", "")
    answer = case.get("answer", "")
    tool_log = case.get("tool_log", "")
    intents = case.get("intents", [])
    ref_points = case.get("reference_points", "")

    intent_section = build_intent_section(intents)
    tool_section = (
        f"### 工具调用简报\n{tool_log}"
        if tool_log.strip()
        else "### 工具调用简报\n（本题无工具调用）"
    )
    ref_section = f"### 参考要点\n{ref_points}" if ref_points.strip() else ""

    parts = [SCORING_CONSTITUTION, "", "---", "", intent_section, "", "---"]
    if ref_section:
        parts += ["", ref_section, ""]
    parts += [
        "---",
        "",
        "### 用户问题",
        question,
        "",
        "### PhysicsScholar回答",
        answer,
        "",
        tool_section,
        "",
        "---",
        "",
        OUTPUT_FORMAT,
    ]
    return "\n".join(parts)


def build_summary_prompt(case: dict, judge_results: list) -> str:
    question = case.get("question", "")
    parts = [f"## 测试题目\n{question}", "", "## 各评委打分结果"]
    for r in judge_results:
        parts += [f"\n### 评委：{r['judge_name']}", r.get("raw_output", "（无输出）")]
    return "\n".join(parts)


# ── API调用（openai SDK，同步；用to_thread并行）──────────
def _sync_call(
    model_cfg: dict, prompt: str, system: str, max_tokens: int = 1024
) -> str:
    """同步调用，在线程池中执行以支持asyncio并行。"""
    provider = model_cfg["provider"]
    model_id = model_cfg["model_id"]

    if provider == "deepseek":
        client = make_ds_client()
    elif provider == "gemini":
        client = make_gemini_client()
    else:
        raise ValueError(f"Unknown provider: {provider}")

    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
        stream=False,
    )
    return response.choices[0].message.content


async def call_model(
    model_cfg: dict,
    prompt: str,
    system: str = "You are a helpful evaluator.",
    max_tokens: int = 1024,
) -> str:
    """异步包装：在线程池中运行同步SDK调用。"""
    return await asyncio.to_thread(_sync_call, model_cfg, prompt, system, max_tokens)


# ── 单题评分──────────────────────────────────────────────
async def evaluate_case(case: dict) -> dict:
    """对单道题并行调用所有评委，然后调用汇总模型。"""
    qid = case["id"]
    judge_prompt = build_judge_prompt(case)
    system_judge = "你是一个微波光子学领域的专业评审。请严格按照给定格式输出评分结果，不要添加任何额外内容。"

    # 并行调用所有评委
    async def run_judge(m):
        try:
            return m["name"], await call_model(m, judge_prompt, system_judge)
        except Exception as e:
            return m["name"], f"ERROR: {e}"

    pairs = await asyncio.gather(*[run_judge(m) for m in JUDGE_MODELS])
    judge_results = [
        {"judge_name": name, "raw_output": output} for name, output in pairs
    ]

    # 汇总模型（max_tokens放宽，报告更长）
    summary_prompt = build_summary_prompt(case, judge_results)
    try:
        summary_output = await call_model(
            SUMMARY_MODEL, summary_prompt, SUMMARY_SYSTEM, max_tokens=2048
        )
    except Exception as e:
        summary_output = f"ERROR: {e}"

    return {
        "id": qid,
        "question": case.get("question", ""),
        "intents": case.get("intents", []),
        "timestamp": datetime.now().isoformat(),
        "judge_results": judge_results,
        "summary": summary_output,
    }


# ── 主流程──────────────────────────────────────────────
async def run_evaluation(case_ids=None):
    """
    运行评测。
    case_ids: 指定题目ID列表（如 ["Q01","Q02"]），None 表示全部。
    """
    with open(TEST_CASES_FILE, encoding="utf-8") as f:
        all_cases = json.load(f)

    cases = [c for c in all_cases if c["id"] in case_ids] if case_ids else all_cases

    print(f"开始评测，共 {len(cases)} 道题，{len(JUDGE_MODELS)} 个评委 + 1 个汇总模型")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 所有题目并行
    results = await asyncio.gather(*[evaluate_case(c) for c in cases])

    # 存档原始结果
    raw_path = RAW_DIR / f"raw_{timestamp}.json"
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(list(results), f, ensure_ascii=False, indent=2)
    print(f"原始结果已保存：{raw_path}")

    # 生成汇总报告
    report = {
        "timestamp": timestamp,
        "total_cases": len(results),
        "judge_models": [m["name"] for m in JUDGE_MODELS],
        "summary_model": SUMMARY_MODEL["name"],
        "cases": [
            {
                "id": r["id"],
                "question": r["question"],
                "intents": r["intents"],
                "summary": r["summary"],
                "anomaly_flagged": "A1 异常标记：是" in r["summary"],
            }
            for r in results
        ],
    }
    report_path = REPORT_DIR / f"report_{timestamp}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"评测报告已保存：{report_path}")

    return list(results), report


if __name__ == "__main__":
    import sys

    ids = sys.argv[1:] if len(sys.argv) > 1 else None
    asyncio.run(run_evaluation(ids))
