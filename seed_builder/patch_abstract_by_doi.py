import json
import logging
import random
import shutil
import time
from pathlib import Path
import requests

# ── 1. 初始化日志与路径 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("OpenAlexAbsScraper")

ROOT_DIR = Path(__file__).resolve().parent
RAW_REFS_PATH = ROOT_DIR / "data" / "raw_refs.json"
BACKUP_PATH = ROOT_DIR / "data" / "raw_refs.json.abs_bak"

# ── 2. 核心反爬配置（请填写你的真实邮箱，OpenAlex 会将你归入高带宽的 Polite Pool 礼貌通道） ──
USER_EMAIL = "13159331923@163.com"  # <-- 强烈建议改成你的真实邮箱
HEADERS = {
    "User-Agent": f"ScholarAgentBot/2.0 (mailto:{USER_EMAIL}; PoweredByRequests)"
}


# ── 3. 工具函数：倒排索引转纯文本（OpenAlex 核心专利解码技术） ────────────────────
def reconstruct_abstract(inverted_index: dict) -> str:
    """
    OpenAlex 规避版权风险不提供明文摘要，只提供倒排索引字典。
    本函数严格按照单词在文中的物理位置，将其100%还原为流畅的英文纯文本摘要。
    """
    if not inverted_index:
        return ""
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    # 严格按照单词在文中的物理顺序进行排序拼接
    word_positions.sort(key=lambda x: x[0])
    return " ".join([word for pos, word in word_positions])


# ── 4. 主核心逻辑 ────────────────────────────────────────────────────────────
def main():
    log.info("========== 🔮 开始通过 OpenAlex (DOI映射) 强力补全论文摘要 ==========")

    if not RAW_REFS_PATH.exists():
        log.error(f"❌ 找不到 raw_refs.json，请确认路径: {RAW_REFS_PATH}")
        return

    # 自动备份，防患于未然
    shutil.copy(RAW_REFS_PATH, BACKUP_PATH)
    log.info(f"💾 安全第一，已成功将当前最新候选池备份至: {BACKUP_PATH}")

    with open(RAW_REFS_PATH, "r", encoding="utf-8") as f:
        papers = json.load(f)

    # 过滤出：【有 DOI 身份证】但是【没有摘要】的文献进行精准打击
    target_indices = [
        i for i, p in enumerate(papers) if p.get("doi") and not p.get("abstract")
    ]
    total_targets = len(target_indices)

    log.info(
        f"📊 候选池共有 {len(papers)} 篇文献，其中【{total_targets}】篇有DOI但缺失摘要，启动补全引擎..."
    )

    if total_targets == 0:
        log.info("🎉 太棒了！所有能补全摘要的文献都已经全部搞定，无需执行此脚本。")
        return

    success_count = 0
    fail_count = 0
    backoff_time = 15  # 触发429频控时的初始熔断睡眠时间（秒）

    for progress, idx in enumerate(target_indices, 1):
        p = papers[idx]
        doi = p["doi"]
        title = p["title"]

        log.info(f"⏳ [{progress}/{total_targets}] 正在检索摘要: 《{title[:35]}...》")

        # OpenAlex 官方标准的 DOI 请求端点
        url = f"https://api.openalex.org/works/doi:{doi}"

        retry = True
        while retry:
            try:
                res = requests.get(url, headers=HEADERS, timeout=12)

                # 🛡️ 安全盾牌 1：触发 429 频控限制时的熔断指数退避
                if res.status_code == 429:
                    log.warning(
                        f"⚠️ 触发 OpenAlex 429 限制！熔断器激活，深度休眠 {backoff_time} 秒..."
                    )
                    time.sleep(backoff_time)
                    backoff_time = min(
                        backoff_time * 2, 120
                    )  # 失败则睡眠时间翻倍，最高120秒
                    continue  # 不计入失败，醒来后继续重试当前这篇

                if res.status_code == 200:
                    backoff_time = 15  # 成功后重置熔断时间
                    retry = False

                    data = res.json()
                    inverted_index = data.get("abstract_inverted_index")

                    if inverted_index:
                        # 调用还原引擎
                        plain_abstract = reconstruct_abstract(inverted_index)
                        if plain_abstract:
                            p["abstract"] = plain_abstract
                            success_count += 1
                            log.info(
                                f"   🟢 [补全成功] 摘要还原完毕 ({len(plain_abstract)} 字符)"
                            )
                        else:
                            log.warning(
                                "   ❌ 该文献在 OpenAlex 中数据残缺，倒排索引为空。"
                            )
                            fail_count += 1
                    else:
                        log.warning("   ❌ 该文献在 OpenAlex 中未录入摘要。")
                        fail_count += 1

                elif res.status_code == 404:
                    log.warning("   ❌ OpenAlex 数据库中未收录此 DOI，跳过。")
                    fail_count += 1
                    retry = False
                else:
                    log.error(f"   ❌ 异常状态码 {res.status_code}，跳过此文献。")
                    fail_count += 1
                    retry = False

            except Exception as e:
                log.error(f"💥 网络请求异常: {e}，将在 5 秒后自动尝试重连...")
                time.sleep(5)

        # 🛡️ 安全盾牌 2：硬性随机延迟，打乱请求特征
        sleep_interval = random.uniform(1.0, 2.0)
        time.sleep(sleep_interval)

        # 🛡️ 安全盾牌 3：断点续传机制，每成功修复 10 篇自动存盘
        if success_count > 0 and success_count % 10 == 0:
            with open(RAW_REFS_PATH, "w", encoding="utf-8") as f:
                json.dump(papers, f, ensure_ascii=False, indent=4)
            log.info("💾 [断点存盘] 阶段性战果已安全写入硬盘。")

    # ── 5. 最终收尾写回 ──────────────────────────────────────────────────────────
    with open(RAW_REFS_PATH, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=4)

    print("\n" + "=" * 55)
    print(" 🎉 【OpenAlex 摘要强力补全行动】圆满收工")
    print("=" * 55)
    print(f" 📈 累计成功还原摘要 :  {success_count} 篇")
    print(f" 📉 数据库残缺/未收录 :  {fail_count} 篇")
    print(f" 💾 干净的学术池已写回 `data/raw_refs.json`")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
