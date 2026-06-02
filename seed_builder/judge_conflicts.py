import json
import logging
import time
from pathlib import Path
from langchain_core.messages import SystemMessage, HumanMessage
from seed_builder.classifier import llm, safe_json_from_ai_message

# 配置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("SupremeCourt")

# ── [修复版] 注入严格标签白名单的 System Prompt ──
SYSTEM_PROMPT = """你是微波光子学（Microwave Photonics, MWP）领域的顶尖学术专家。
现在有两名标注员对同一篇论文的分类标签产生了分歧。
请根据论文的标题和摘要，对比两组标签，判断哪一组更准确，或者给出修正后的终局标签。

⚠️ 【终局标签必须严格从以下合法的候选池中选择，严禁自行创造、修改任何字眼或符号】:

1. [维度 A: 硬件/器件载体] 必须且只能是以下之一:
   - Optical Filter & Delay Line
   - Laser & Frequency Comb
   - Modulator
   - Photonic Integrated Chip
   - Optoelectronic Oscillator (OEO)
   - System Architecture Only
   - Comprehensive/Device-Agnostic

2. [维度 B: 应用/功能方向] 必须且只能是以下之一:
   - Signal Generation
   - Signal Processing & IFM
   - Radar & Sensing
   - Optical Wireless Communications
   - Emerging Computing
   - Foundational Theory
   - Comprehensive/Application-Agnostic

3. [维度 C: 论文类型] 必须且只能是以下之一:
   - Article
   - Letter/Express
   - Review

必须以严格的 JSON 格式输出，拒绝任何 Markdown 包装，格式如下：
{"judgement_reason": "你的简短判断理由", "final_tags": ["选自维度A", "选自维度B", "选自维度C"]}
"""


def call_llm_api(prompt: str) -> str:
    """
    TODO: 请把你 classify.py 里的模型调用代码直接复制到这里！
    例如:
    response = client.chat.completions.create(
        model="xxx-3.1-pro",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"} # 如果API支持强制JSON
    )
    return response.choices[0].message.content
    """
    # 下面是一个 mock，请务必替换为你真实的 API 请求代码
    raise NotImplementedError("请填入你真实的 API 调用代码！")


def main():
    root_dir = Path(__file__).resolve().parent
    data_dir = root_dir / "data"
    input_file = data_dir / "conflicts_to_judge.json"
    output_file = data_dir / "resolved_conflicts.json"

    if not input_file.exists():
        log.error(f"找不到争议卷宗: {input_file}")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        conflicts = json.load(f)

    resolved_data = []
    log.info(f"⚖️ 最高法庭开庭，共受理案件 {len(conflicts)} 宗。")

    for idx, item in enumerate(conflicts, 1):
        pid = item["paperId"]
        title = item["title"]
        abstract = item["abstract"]
        tag_v1 = item["tag_version_1"]
        tag_v2 = item["tag_version_2"]

        log.info(f"[{idx}/{len(conflicts)}] 正在审理 ID: {pid} ...")

        # 组装用户 Prompt (这部分保持不变)
        user_prompt = f"""
【Title】: {title}
【Abstract】: {abstract}

【Tag Version 1】: {tag_v1}
【Tag Version 2】: {tag_v2}

请给出最终判决的 JSON。
"""

        # ── [修改] 极简调用链路，由 Langchain 和你的清洗函数接管 ──────────────────────────
        try:
            # 1. 构建标准 Message 列表
            messages = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]

            # 2. 触发调用 (发生异常 Langchain 自动根据 max_retries=3 重试)
            res = llm.invoke(messages)

            # 3. 使用你的专属防御性清洗函数提取字典
            decision = safe_json_from_ai_message(res)

            # 4. 解析结果 (依然做个字典键值的兜底)
            final_tags = decision.get("final_tags", tag_v1)
            reason = decision.get("judgement_reason", "无理由")

            log.info(f" 🟢 判决出炉 -> {final_tags} (理由: {reason[:20]}...)")

            # 5. 将最终结果存入记录
            resolved_data.append(
                {
                    "paperId": pid,
                    "title": title,
                    "final_dim_a": final_tags[0] if len(final_tags) > 0 else tag_v1[0],
                    "final_dim_b": final_tags[1] if len(final_tags) > 1 else tag_v1[1],
                    "final_dim_c": final_tags[2] if len(final_tags) > 2 else tag_v1[2],
                    "judgement_reason": reason,
                }
            )

            # ── [修改这里] 匀速限流：1分钟5次 -> 平均每 12 秒 1 次，加 0.5 秒安全冗余 ──
            log.info(" ⏳ 触发速率限制，休眠 12.5 秒以满足 5 RPM...")
            time.sleep(13)
            # ──────────────────────────────────────────────────────────────────

        except Exception as e:
            # 走到这里说明 3 次重试都死翘翘了，或者清洗函数彻底崩溃
            log.error(f" ❌ 案件 {pid} 彻底失败 (Langchain自带重试耗尽或清洗失败): {e}")
            resolved_data.append(
                {
                    "paperId": pid,
                    "title": title,
                    "final_dim_a": tag_v1[0],
                    "final_dim_b": tag_v1[1],
                    "final_dim_c": tag_v1[2],
                    "judgement_reason": "API连续失败，默认采用V1",
                }
            )
        # ────────────────────────────────────────────────────────────────────────────────

    # 保存判决结果
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(resolved_data, f, ensure_ascii=False, indent=4)

    log.info(f"🎉 审理完毕！全部结果已封卷归档至: {output_file.name}")


if __name__ == "__main__":
    main()
