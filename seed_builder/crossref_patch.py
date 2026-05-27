import os
import json
import time
import logging
from pathlib import Path
import requests
from dotenv import load_dotenv

# ── 1. 初始化日志与环境变量 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("CrossrefPatch")

# 自动计算路径，支持在项目任何地方执行
ROOT_DIR = (
    Path(__file__).resolve().parent
    if (Path(__file__).resolve().parent / ".env").exists()
    else Path(__file__).resolve().parent.parent
)
load_dotenv(dotenv_path=ROOT_DIR / ".env")

S2_API_KEY = os.getenv("S2_API_KEY", "")
S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
RAW_REFS_PATH = ROOT_DIR / "data" / "raw_refs.json"

# Crossref 官方礼貌原则：带上合规的用户代理
CROSSREF_HEADERS = {
    "User-Agent": "PhysicsScholarPatch/1.0 (mailto:scholar_bot@example.com)"
}
S2_HEADERS = {"x-api-key": S2_API_KEY} if S2_API_KEY else {}

# ── 2. 需要抢救的 4 篇经典/前沿综述配置 ──────────────────────────────────────
PATCH_TARGETS = [
    # {"label": "Capmany & Novak 2007", "doi": "10.1038/nphoton.2007.89"},
    # {"label": "Yao 2009", "doi": "10.1109/JLT.2008.2009551"},
    # {"label": "Marpaung, Yao & Capmany 2019", "doi": "10.1038/s41566-018-0310-5"},
    {"label": "Yao & Capmany 2022", "doi": "10.1007/s11432-021-3524-0"},
]


def fetch_dois_from_crossref(doi: str, label: str) -> list[str]:
    """从 Crossref API 强力抓取某篇论文当年注册的所有参考文献 DOI"""
    url = f"https://api.crossref.org/works/{doi}"
    log.info(f"🌐 [Crossref] 正在拉取综述 [{label}] 的元数据...")

    try:
        res = requests.get(url, headers=CROSSREF_HEADERS, timeout=15)
        if res.status_code != 200:
            log.error(
                f"❌ Crossref 返回状态码 {res.status_code}，可能该 DOI 在 Crossref 未开放列表。"
            )
            return []

        data = res.json()
        references = data.get("message", {}).get("reference", [])

        # 提取出所有含有 DOI 的参考文献
        extracted_dois = []
        for ref in references:
            ref_doi = ref.get("DOI")
            if ref_doi:
                extracted_dois.append(ref_doi.strip().lower())

        log.info(
            f"✅ [Crossref] 成功提取到 {len(extracted_dois)} 条有效的参考文献 DOI！"
        )
        return extracted_dois
    except Exception as e:
        log.error(f"💥 请求 Crossref 发生异常: {e}")
        return []


def resolve_dois_via_s2_batch(dois: list[str]) -> list[dict]:
    """使用 S2 Batch 批量接口，一次性把上百个 DOI 转换成你需要的 S2 候选池格式"""
    if not dois:
        return []

    log.info(f"🚀 [S2 Batch] 正在将 {len(dois)} 个 DOI 批量换取 S2 论文数据...")

    # S2 Batch 限制单次最多 500 个，我们的综述引用量一般在 100~300，一次全打包完全没问题
    payload = {"ids": [f"DOI:{doi}" for doi in dois]}
    params = {"fields": "paperId,title,venue,year,citationCount"}

    try:
        res = requests.post(
            S2_BATCH_URL, json=payload, params=params, headers=S2_HEADERS, timeout=30
        )
        if res.status_code != 200:
            log.error(f"❌ S2 Batch 接口失败，状态码: {res.status_code}")
            return []

        results = res.json()
        valid_papers = []

        for item in results:
            # 过滤掉 S2 里没收录的 (None) 以及缺少核心 paperId 的幽灵数据
            if item and item.get("paperId"):
                valid_papers.append(
                    {
                        "paperId": item["paperId"],
                        "title": item.get("title", "Unknown Title"),
                        "venue": item.get("venue", ""),
                        "year": item.get("year"),
                        "citationCount": item.get("citationCount", 0),
                    }
                )

        log.info(
            f"✨ [S2 Batch] 转换成功！{len(dois)} 个 DOI 成功匹配到 {len(valid_papers)} 篇 S2 实体论文。"
        )
        return valid_papers
    except Exception as e:
        log.error(f"💥 请求 S2 Batch 发生异常: {e}")
        return []


def merge_to_raw_refs(new_papers: list[dict]):
    """将新抓到的论文无损合并进现有的 data/raw_refs.json 文件（自动去重）"""
    if not new_papers:
        return

    existing_pool = []
    # 如果文件存在，先读出来
    if RAW_REFS_PATH.exists():
        try:
            with open(RAW_REFS_PATH, "r", encoding="utf-8") as f:
                existing_pool = json.load(f)
            log.info(f"📂 已读取现有候选池：共 {len(existing_pool)} 篇论文。")
        except Exception as e:
            log.warning(f"⚠️ 读取现有 raw_refs.json 失败，将创建新文件。原因: {e}")

    # 使用 paperId 建立唯一去重索引
    seen_ids = {p["paperId"] for p in existing_pool if "paperId" in p}

    added_count = 0
    for p in new_papers:
        if p["paperId"] not in seen_ids:
            existing_pool.append(p)
            seen_ids.add(p["paperId"])
            added_count += 0
            added_count += 1

    # 重新写回文件
    RAW_REFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RAW_REFS_PATH, "w", encoding="utf-8") as f:
        json.dump(existing_pool, f, ensure_ascii=False, indent=4)

    log.info(
        f"💾 【合并完成】新增入池: {added_count} 篇 | 累计池总量暴涨至: {len(existing_pool)} 篇！"
    )


# ── 3. 主执行流程 ────────────────────────────────────────────────────────────
def main():
    log.info("========== 开始执行 Crossref 强力数据补丁 ==========")
    if not S2_API_KEY:
        log.warning(
            "⚠️ 未检测到 S2_API_KEY，批量接口可能遭遇严格限流，建议确保 .env 配置正确。"
        )

    all_extracted_papers = []

    for target in PATCH_TARGETS:
        # 1. 从 Crossref 拔 DOI
        dois = fetch_dois_from_crossref(target["doi"], target["label"])
        # 2. 扔给 S2 批量换取详情
        papers = resolve_dois_via_s2_batch(dois)
        all_extracted_papers.extend(papers)

        # 礼貌性休眠，防止请求过快
        time.sleep(1.5)
        print("-" * 60)

    # 3. 统一合并入库并去重
    merge_to_raw_refs(all_extracted_papers)
    log.info("========== 补丁脚本执行完毕，大功告成！ ==========")


if __name__ == "__main__":
    main()
