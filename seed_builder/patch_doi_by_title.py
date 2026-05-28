import json
import logging
import random
import re
import shutil
import time
from difflib import SequenceMatcher
from pathlib import Path
import requests

# ── 1. 初始化日志与路径 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("CrossrefScraper")

ROOT_DIR = Path(__file__).resolve().parent.parent
RAW_REFS_PATH = ROOT_DIR / "data" / "raw_refs.json"
BACKUP_PATH = ROOT_DIR / "data" / "raw_refs.json.doi_bak"

# ── 2. 核心反爬配置（请务必填写你的真实邮箱，可以大幅降低 429 概率） ───────────────
USER_EMAIL = "13159331923@163.com"  # <-- 强烈建议改成你的真实邮箱
HEADERS = {
    "User-Agent": f"ScholarAgentBot/2.0 (mailto:{USER_EMAIL}; PoweredByRequests)"
}
CROSSREF_URL = "https://api.crossref.org/works"


# ── 3. 工具函数：文本清洗与相似度匹配 ──────────────────────────────────────────
def clean_title(title: str) -> str:
    """去除特殊符号、空格并转小写，用于极其精准的模糊比对"""
    if not title:
        return ""
    # 只保留字母、数字和单个空格
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", title.lower())
    return " ".join(cleaned.split())


def get_similarity(str1: str, str2: str) -> float:
    """计算两个字符串的相似度比例 (0.0 ~ 1.0)"""
    return SequenceMatcher(None, clean_title(str1), clean_title(str2)).ratio()


# ── 4. 主核心逻辑 ────────────────────────────────────────────────────────────
def main():
    log.info("========== 🛰️ 开始通过 Crossref 标题逆向检索补全 DOI ==========")

    if not RAW_REFS_PATH.exists():
        log.error(f"❌ 找不到 raw_refs.json，请确认路径: {RAW_REFS_PATH}")
        return

    # 自动备份
    shutil.copy(RAW_REFS_PATH, BACKUP_PATH)
    log.info(f"💾 安全第一，已成功将当前候选池备份至: {BACKUP_PATH}")

    with open(RAW_REFS_PATH, "r", encoding="utf-8") as f:
        papers = json.load(f)

    # 过滤出急需抢救（没有DOI）的文献
    target_indices = [i for i, p in enumerate(papers) if not p.get("doi")]
    total_targets = len(target_indices)

    log.info(
        f"📊 候选池共有 {len(papers)} 篇文献，其中【{total_targets}】篇缺失 DOI，开始精准拦截..."
    )

    if total_targets == 0:
        log.info("🎉 所有的文献都有 DOI 了，无需执行此脚本！")
        return

    success_count = 0
    fail_count = 0
    backoff_time = 15  # 触发429时的初始熔断睡眠时间（秒）

    # 逐篇小心翼翼地请求，防止轰炸服务器
    for progress, idx in enumerate(target_indices, 1):
        p = papers[idx]
        title = p["title"]

        log.info(f"⏳ [{progress}/{total_targets}] 正在逆向匹配: 《{title[:40]}...》")

        # 构造请求参数，只返回前 3 条结果（减少带宽，提高响应速度）
        params = {"query.title": title, "rows": 3}

        retry = True
        while retry:
            try:
                res = requests.get(
                    CROSSREF_URL, params=params, headers=HEADERS, timeout=15
                )

                # 触发 429 频控时的熔断逻辑
                if res.status_code == 429:
                    log.warning(
                        f"⚠️ 触发 Crossref 429 频控限制！触发熔断，深度睡眠 {backoff_time} 秒..."
                    )
                    time.sleep(backoff_time)
                    backoff_time = min(backoff_time * 2, 120)  # 指数退避，最高睡 2 分钟
                    continue  # 不算失败，继续重试当前这篇

                if res.status_code == 200:
                    # 重置熔断时间
                    backoff_time = 15
                    retry = False  # 成功，跳出当前重试循环

                    data = res.json()
                    items = data.get("message", {}).get("items", [])

                    if not items:
                        log.warning(f" ❌ Crossref 未检索到任何相关结果。")
                        fail_count += 1
                        break

                    # 遍历前几条结果，寻找标题相似度最高的亲人
                    matched_doi = None
                    best_score = 0.0

                    for item in items:
                        cr_titles = item.get("title", [])
                        if not cr_titles:
                            continue
                        cr_title = cr_titles[0]

                        score = get_similarity(title, cr_title)
                        if score > best_score:
                            best_score = score
                            if score >= 0.85:  # 只有高于 85% 相似度才采信
                                matched_doi = item.get("DOI")

                    if matched_doi:
                        p["doi"] = matched_doi.strip().lower()
                        success_count += 1
                        log.info(
                            f"   🟢 [匹配成功] 相似度: {best_score:.2f} | DOI: {p['doi']}"
                        )
                    else:
                        log.warning(
                            f"   ❌ 找到最接近结果相似度仅 {best_score:.2f}，低于安全阈值 0.85，拒绝采信。"
                        )
                        fail_count += 1
                else:
                    log.error(f" ❌ 异常状态码 {res.status_code}，跳过此文献。")
                    fail_count += 1
                    retry = False

            except Exception as e:
                log.error(f"💥 网络请求异常: {e}，将在 5 秒后重试...")
                time.sleep(5)

        # ── 极其重要的限速盾牌 ──────────────────────────────────────────────────
        # 每次成功请求后，强制随机休眠 1.5 到 2.5 秒，让网关抓不到固定频率特征
        sleep_interval = random.uniform(1.5, 2.5)
        time.sleep(sleep_interval)

        # 每成功修复 10 篇，进行一次断点保存，防止程序中途被强杀导致进度丢失
        if success_count > 0 and success_count % 10 == 0:
            with open(RAW_REFS_PATH, "w", encoding="utf-8") as f:
                json.dump(papers, f, ensure_ascii=False, indent=4)
            log.info("💾 [断点存盘] 已自动保存阶段性战果至硬盘。")

    # ── 5. 收尾工作 ──────────────────────────────────────────────────────────
    with open(RAW_REFS_PATH, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=4)

    print("\n" + "=" * 55)
    print(" 🎉 【Crossref 逆向抢救行动】安全收工报告")
    print("=" * 55)
    print(f" 📈 累计成功寻回 DOI :  {success_count} 篇")
    print(f" 📉 匹配失败/残缺文献 :  {fail_count} 篇")
    print(f" 💾 数据已无损回写至 `data/raw_refs.json`")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
