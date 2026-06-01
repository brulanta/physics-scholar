import json
import logging
import shutil
from pathlib import Path

# ── 1. 初始化日志与路径 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("RefSanitizer")

ROOT_DIR = Path(__file__).resolve().parent
RAW_REFS_PATH = ROOT_DIR / "data" / "raw_refs.json"
BACKUP_PATH = ROOT_DIR / "data" / "raw_refs.json.clean_bak"


def normalize_and_clean_schema(item: dict) -> dict:
    """
    清洗异构字典：
    1. 移除重复和多余字段 (citation_count, s2_id, externalIds)
    2. 统一保留标准键: paperId, title, doi, abstract, citationCount, venue, year, authors
    3. 规范化 DOI 为小写格式
    """
    cleaned = {}

    # 1. 唯一标识符对齐 (优先保留 paperId)
    cleaned["paperId"] = item.get("paperId") or item.get("s2_id") or ""

    # 2. 标题规范化
    cleaned["title"] = (item.get("title") or "").strip()

    # 3. 引用数对齐 (优先保留 citationCount)
    c_count = (
        item.get("citationCount")
        if item.get("citationCount") is not None
        else item.get("citation_count")
    )
    cleaned["citationCount"] = int(c_count) if c_count is not None else 0

    # 4. DOI 深度清理（提取并强制转小写）
    doi_val = ""
    if "doi" in item and item["doi"]:
        doi_val = item["doi"]
    elif "externalIds" in item and isinstance(item["externalIds"], dict):
        doi_val = item["externalIds"].get("DOI") or ""

    cleaned["doi"] = str(doi_val).strip().lower() if doi_val else ""

    # 5. 摘要、期刊、年份、作者基础字段清洗 (确保绝无 KeyError)
    cleaned["abstract"] = (item.get("abstract") or "").strip()
    cleaned["venue"] = (item.get("venue") or "").strip()
    cleaned["year"] = item.get("year")
    cleaned["authors"] = (item.get("authors") or "").strip()

    return cleaned


def evaluate_completeness(item: dict) -> int:
    """
    评估单篇论文在核心 4 字段中拥有的数量：
    核心 4 字段：title, doi, abstract, citationCount
    注意：citationCount 即使为 0 只要数字存在也算作“有价值”。
    """
    score = 0
    if item.get("title"):
        score += 1
    if item.get("doi"):
        score += 1
    if item.get("abstract"):
        score += 1
    if item.get("citationCount") is not None:
        score += 1
    return score


def main():
    log.info("========== 🛠️ 开始执行候选池数据清洗与缺陷普查 ==========")

    if not RAW_REFS_PATH.exists():
        log.error(f"❌ 找不到需要清洗的文件，请确认路径: {RAW_REFS_PATH}")
        return

    # 自动备份，防患于未然
    shutil.copy(RAW_REFS_PATH, BACKUP_PATH)
    log.info(f"💾 备份成功！原始文件的副本已安全备份到: {BACKUP_PATH}")

    # 读取原始脏数据
    with open(RAW_REFS_PATH, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    total_raw = len(raw_data)
    log.info(f"📂 成功读取到原始文献共: {total_raw} 篇。开始强力清洗...")

    # 实施字段收拢和统一化
    standardized_pool = [normalize_and_clean_schema(p) for p in raw_data]

    # 统计核心指标字典
    stats = {
        "perfect_4": [],  # 4个都有
        "missing_1": [],  # 缺1个
        "missing_2": [],  # 缺2个
        "missing_3": [],  # 缺3个
        "missing_all": [],  # 4个全缺
        # 专项指标单项缺失统计
        "no_title": 0,
        "no_doi": 0,
        "no_abstract": 0,
    }

    for p in standardized_pool:
        score = evaluate_completeness(p)

        # 按完整度级别归类
        if score == 4:
            stats["perfect_4"].append(p)
        elif score == 3:
            stats["missing_1"].append(p)
        elif score == 2:
            stats["missing_2"].append(p)
        elif score == 1:
            stats["missing_3"].append(p)
        else:
            stats["missing_all"].append(p)

        # 单项缺失明细累计
        if not p.get("title"):
            stats["no_title"] += 1
        if not p.get("doi"):
            stats["no_doi"] += 1
        if not p.get("abstract"):
            stats["no_abstract"] += 1

    # ── 2. 打印极度逼真的可视化体检报告 ──────────────────────────────────────
    print("\n" + "=" * 55)
    print(" 📊 【物理与微波光子学学术种子池】深度数据体检报告")
    print("=" * 55)
    print(f" 📦 候选池文献总总量 :  {total_raw} 篇")
    print("-" * 55)
    print(
        f" 🟢 四要素俱全(完美)   :  {len(stats['perfect_4']):>4} 篇  ({len(stats['perfect_4']) / total_raw * 100:6.2f}%)"
    )
    print(
        f" 🟡 缺失一个关键字段   :  {len(stats['missing_1']):>4} 篇  ({len(stats['missing_1']) / total_raw * 100:6.2f}%)"
    )
    print(
        f" 🟠 缺失两个关键字段   :  {len(stats['missing_2']):>4} 篇  ({len(stats['missing_2']) / total_raw * 100:6.2f}%)"
    )
    print(
        f" 🔴 严重残疾(缺3个以上):  {len(stats['missing_3']) + len(stats['missing_all']):>4} 篇  ({(len(stats['missing_3']) + len(stats['missing_all'])) / total_raw * 100:6.2f}%)"
    )
    print("-" * 55)
    print(" 🔍 【单项缺陷精确制导】:")
    print(f" ❌ 缺失「标题 (Title)」   :  {stats['no_title']:>4} 篇")
    print(
        f" ❌ 缺失「去重主键 (DOI)」 :  {stats['no_doi']:>4} 篇  <-- [下一步优先抢救对象]"
    )
    print(f" ❌ 缺失「内容摘要 (Abs)」 :  {stats['no_abstract']:>4} 篇")
    print("=" * 55 + "\n")

    # 写回清洗完毕、无冗余字段的整洁文件
    with open(RAW_REFS_PATH, "w", encoding="utf-8") as f:
        json.dump(standardized_pool, f, ensure_ascii=False, indent=4)

    log.info("💾 清洗完毕的无冗余标准数据已成功覆盖写回原目录 `data/raw_refs.json`！")
    log.info("========== 🏁 补丁与整理流第一阶段安全收工 ==========")


if __name__ == "__main__":
    main()
