import json
import logging
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

import re
from typing import Any


# ── 0. JSON格式提取兜底 ──────────────────────────────────────────────────
def safe_json_loads(text: str) -> Any:
    """
    尽可能从 LLM 输出中提取 JSON。

    支持：
    - 纯 JSON
    - ```json ... ```
    - ``` ... ```
    - JSON 前后夹杂解释文字
    """

    if not text:
        raise ValueError("Empty response")

    text = text.strip()

    # =====================
    # Case 1: 代码块
    # =====================
    codeblock_match = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        re.DOTALL | re.IGNORECASE,
    )

    if codeblock_match:
        text = codeblock_match.group(1).strip()

    # =====================
    # Case 2: 直接解析
    # =====================
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # =====================
    # Case 3: 从长文本中提取 JSON 对象
    # =====================
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Unable to extract valid JSON from response:\n{text[:500]}")


def safe_json_from_ai_message(msg) -> dict:
    raw = msg.content

    try:
        return safe_json_loads(raw)

    except Exception as e:
        raise RuntimeError(f"LLM JSON parse failed.\nRaw content:\n{repr(raw)}") from e


# ── 1. 初始化日志与环境变量 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("MWPClassifier")

ROOT_DIR = Path(__file__).resolve().parent
RAW_REFS_PATH = ROOT_DIR / "data" / "raw_refs.json"

# 加载同级目录下的 .env 文件
load_dotenv()

# ── 2. 初始化 LangChain ChatOpenAI ──────────────────────────────────────────
BASE_URL = os.getenv("BASE_URL")
API_KEY = os.getenv("API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME")

if not all([BASE_URL, API_KEY, MODEL_NAME]):
    log.error("❌ 环境变量读取错误！请检查同级目录下 .env 文件中的配置项。")
    exit(1)

# 利用 LangChain 封装，内置 max_retries 自动应对偶发性网络抖动或超时
llm = ChatOpenAI(
    base_url=BASE_URL,
    api_key=API_KEY,
    model=MODEL_NAME,
    max_tokens=4000,  # 给予极度宽裕的吐字空间，严防空回复或截断
    temperature=0.1,  # 极低随机性，确保严格学术分类
    max_retries=3,  # 发生异常自动重试 3 次
    model_kwargs={
        "response_format": {"type": "json_object"}
    },  # 开启物理层强制 JSON Mode
)

# ── 3. 大模型学术分类大一统 Prompt 矩阵 ───────────────────────────────────────
SYSTEM_PROMPT = """你是一位全球顶尖的微波光子学（Microwave Photonics, MWP）领域的资深学术审稿人与专家。
现在你需要对一组论文进行智能化、正交维度的学术标签精细化分类。

请必须严格按照以下三个正交维度的规范进行判定打标（不得臆造或篡改候选标签名）：

📐 维度 A：核心器件 / 物理机制 (对应键名: dim_a)
你必须从以下候选值中精确选择一个：
- "Modulator" (光调制器：薄膜铌酸锂/聚合物/硅基/等)
- "Laser & Frequency Comb" (激光器与光频梳)
- "Photodetector" (光电探测器)
- "Optical Filter & Delay Line" (光滤波器与光时延线/光波导)
- "Optoelectronic Oscillator (OEO)" (光电振荡器)
- "Photonic Integrated Chip" (光子集成芯片：专指片上损耗、工艺、封装、异质集成等芯片底层底层物理技术。注意：若文章侧重于系统级架构或应用如雷达、滤波，请勿错选此类，应选对应的具体器件或系统架构)
- "System Architecture Only" (纯系统架构：不侧重单一底层器件，侧重链路搭建与拓扑系统级架构)
- "Comprehensive/Device-Agnostic" (多器件综合/综述泛指)
- "Pending" (信息严重不足，标题和摘要无法判定)

📡 维度 B：系统应用 / 信号频段 (对应键名: dim_b)
你必须从以下候选值中精确选择一个：
- "Signal Generation" (微波/毫米波/微波频率源产生)
- "Signal Processing & IFM" (微波信号处理：滤波、测频、相位控制、时延处理等)
- "Radar & Sensing" (微波光子雷达、激光雷达、分布式光学传感等)
- "Optical Wireless Communications" (光无线通信：RoF、5G/6G 前传、无线覆盖等)
- "THz Photonics" (太赫兹光子学：超高频 0.1-10 THz 应用)
- "Emerging Computing" (新兴边缘方向：光计算、类脑神经网络、量子微波等)
- "Foundational Theory" (通用基础物理理论)
- "Comprehensive/Application-Agnostic" (多应用综合/综述泛指)
- "Pending" (信息严重不足，无法判定)

📝 维度 C：文献载体类型 (对应键名: dim_c)
你必须从以下候选值中精确选择一个：
- "Review" (综述论文：提供宏观图谱与背景知识)
- "Letter/Express" (快报文献：提供极致核心实验指标与高新颖性突破)
- "Article" (常规全文长文：提供详细的数学推导、实验装置细节、长篇讨论)

【特殊处理要求（针对无摘要文献）】：
如果某篇论文的摘要显示为 "Abstract is missing."，说明该论文元数据残缺。请保持学术中立，完全根据其【标题文字】的字面物理含义（如包含 Modulator, OEO, Radar, Chip 等词）以及你内部的训练知识储备，做出最合理的倾向性分类。如果单凭标题信息量实在太少以至于无法判断，请在这三个维度上均归类为 "Pending"。

输出格式要求：
你必须返回一个合法的标准 JSON 对象，其根键为 "results"，对应一个对象数组。数组中的每个子对象必须包含 "paperId" 以及对应的三个维度标签。使用原样纸张 ID 映射，严禁错位。Do not use markdown.Do not use ```json fences.Do not add explanations.
示例如下：
{
  "results": [
    {
      "paperId": "76f7aa2638557c5a7900547cce65e6623bca0742",
      "dim_a": "Modulator",
      "dim_b": "Signal Processing & IFM",
      "dim_c": "Article"
    }
  ]
}
"""


# ── 4. 工具函数 ────────────────────────────────────────────────────────────
def check_llm_connectivity() -> bool:
    """Pre-flight 检查：测试大模型连通性与 JSON 模式是否运转正常"""
    log.info("🔄 正在执行大模型通道连通性与 JSON Mode 的 Pre-flight 联动检查...")
    try:
        test_prompt = "Return a JSON object with key 'status' equal to the string 'ok'."
        res = llm.invoke([HumanMessage(content=test_prompt)])
        data = safe_json_from_ai_message(res)
        if data.get("status") == "ok":
            log.info(
                f"🟢 [Pre-flight 完美通过] 模型 '{MODEL_NAME}' 成功握手，JSON 输出解析正常！"
            )
            return True
        else:
            log.error(f"❌ Pre-flight 返回异常结构: {res.content}")
            return False
    except json.JSONDecodeError:
        log.error(f"❌ 模型返回内容不是合法 JSON。raw={repr(res.content)}")
        return False

    except Exception as e:
        log.error(f"❌ Pre-flight 检查异常: {e}")
        return False


def is_needing_tag(paper: dict) -> bool:
    """判定某篇论文是否属于急需打标的“高价值漏网之鱼”"""
    # 条件 1：引用量过滤门槛 >= 50
    if paper.get("citationCount", 0) < 50:
        return False
    # 条件 2：核心三维度只要有一个为空或不存在，即说明未打标或打标中途失败了
    a = paper.get("dim_a")
    b = paper.get("dim_b")
    c = paper.get("dim_c")
    return not (a and b and c)


# ── 5. 主核心管线 ────────────────────────────────────────────────────────────
def main():
    log.info("========== 🚀 智能化正交打标签自动化流水线启动 ==========")

    # 0. 强力执行 Pre-flight 通路检查
    if not check_llm_connectivity():
        log.error("💥 大模型前置体检未通过，流程中断，请排查环境后再试。")
        return

    if not RAW_REFS_PATH.exists():
        log.error(f"❌ 找不到候选池文件: {RAW_REFS_PATH}")
        return

    # 读取当前最新进度的文献池
    with open(RAW_REFS_PATH, "r", encoding="utf-8") as f:
        all_papers = json.load(f)

    # 1. 精准提炼出：满足条件（高引且无完整标签）的待打标列表
    target_indices = [i for i, p in enumerate(all_papers) if is_needing_tag(p)]
    total_targets = len(target_indices)

    log.info(
        f"📊 候选池总数: {len(all_papers)} 篇。经过 50 引过滤及去重扫描，当前急需打标的核心文献共: {total_targets} 篇。"
    )

    if total_targets == 0:
        log.info(
            "🎉 完美！所有高价值文献的标签已全部打齐，无任何漏网之鱼，脚本安全退出。"
        )
        return

    # 2. 实施黄金颗粒度分批（10篇论文一组）
    BATCH_SIZE = 10

    # 将下标分成一个个长度为 BATCH_SIZE 的小块
    batches = [
        target_indices[i : i + BATCH_SIZE] for i in range(0, total_targets, BATCH_SIZE)
    ]
    total_batches = len(batches)

    log.info(
        f"📦 已将任务动态装箱，共分为 {total_batches} 个批次执行（每批最多 10 篇），开始全速攻坚..."
    )

    for b_idx, batch_item_indices in enumerate(batches, 1):
        log.info(
            f"⏳ 正在执行批次 [{b_idx}/{total_batches}]... 正在组装长文本上下文..."
        )

        # 提取当前批次的原始论文对象
        current_batch_papers = [all_papers[idx] for idx in batch_item_indices]

        # 3. 构造本批次的动态用户 Prompt 块
        user_content = "请为以下这组微波光子学文献进行专家级正交分类打标，直接返回要求的JSON数据：\n\n"
        for i, paper in enumerate(current_batch_papers, 1):
            pid = paper.get("paperId", "unknown")
            title = paper.get("title", "Untitled")
            abstract = paper.get("abstract", "").strip() or "Abstract is missing."
            user_content += f"--- 论文编号 [{i}] ---\n"
            user_content += f"paperId: {pid}\n"
            user_content += f"Title: {title}\n"
            user_content += f"Abstract: {abstract}\n\n"

        # 4. 投递给 LangChain 封装端点
        try:
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_content),
            ]

            # 发起大规模高抗抖动请求
            response = llm.invoke(messages)

            # 5. 解析并精准映射回全盘大数组
            response_data = safe_json_from_ai_message(response)
            results_list = response_data.get("results", [])

            # 建立纸张ID到新打标签的快速字典索引
            tag_lut = {res["paperId"]: res for res in results_list if "paperId" in res}

            success_count = 0
            for paper in current_batch_papers:
                pid = paper.get("paperId")
                if pid in tag_lut:
                    paper["dim_a"] = tag_lut[pid].get("dim_a", "Pending")
                    paper["dim_b"] = tag_lut[pid].get("dim_b", "Pending")
                    paper["dim_c"] = tag_lut[pid].get("dim_c", "Pending")
                    success_count += 1
                else:
                    # 如果模型漏掉了某篇，赋予默认兜底
                    paper["dim_a"] = "Pending"
                    paper["dim_b"] = "Pending"
                    paper["dim_c"] = "Pending"
                    log.warning(
                        f"⚠️ 大模型返回的列表中遗漏了 paperId: {pid}，自动降级标记为 Pending。"
                    )

            log.info(
                f"   🟢 批次 [{b_idx}/{total_batches}] 成功收纳标签: {success_count}/{len(current_batch_papers)} 篇"
            )

        except Exception as e:
            log.error(
                f"   ❌ 批次 [{b_idx}/{total_batches}] 发生致命处理异常（跳过本组，下次重新执行将自动重试）: {e}"
            )
            # 发生异常直接跳过这一组，不污染当前内存。因为没写入，下次跑还会出来。
            continue

        # 6. 💾 核心机制：每跑完一组，立刻无损覆盖写回硬盘（断点存盘）
        with open(RAW_REFS_PATH, "w", encoding="utf-8") as f:
            json.dump(all_papers, f, ensure_ascii=False, indent=4)
        log.info("   💾 [断点存盘成功] 最新批次战果已无损刷入硬盘。")

        # 7. 🛡️ 物理限速盾牌：每次请求完，强制睡眠 13 秒，严格将频控卡在 5 RPM 以下
        if b_idx < total_batches:
            sleep_sec = 13
            log.info(f"   💤 触发物理频控限速保护，强制静默 {sleep_sec} 秒...")
            time.sleep(sleep_sec)

    print("\n" + "=" * 55)
    print(" 🎉 【智能化正交打标签流水线】本轮安全收工报告")
    print("=" * 55)
    print(f" 💾 最新处理后的多维资产库已安全写回 `data/raw_refs.json`")
    print(
        f" 💡 提示：若因网络或模型原因存在漏网之鱼，再次执行 `python classifier.py` 即可实现全自动补打。"
    )
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
