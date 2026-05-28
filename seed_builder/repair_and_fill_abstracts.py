import os
import json
import time
import logging
import shutil
from pathlib import Path
import requests
from dotenv import load_dotenv

# ── 1. 初始化日志与路径 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("DataUnifier")

ROOT_DIR = (
    Path(__file__).resolve().parent
    if (Path(__file__).resolve().parent / ".env").exists()
    else Path(__file__).resolve().parent.parent
)
load_dotenv(dotenv_path=ROOT_DIR / ".env")

S2_API_KEY = os.getenv("S2_API_KEY", "")
RAW_REFS_PATH = ROOT_DIR / "data" / "raw_refs.json"
BACKUP_PATH = ROOT_DIR / "data" / "raw_refs.json.bak"

S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_HEADERS = {"x-api-key": S2_API_KEY} if S2_API_KEY else {}
OPENALEX_HEADERS = {
    "User-Agent": "ScholarAgentPatch/1.0 (mailto:scholar_bot@example.com)"
}


# ── 2. 工具函数：倒排索引转纯文本（OpenAlex 专用） ────────────────────────────
def reconstruct_abstract(inverted_index: dict) -> str:
    """将 OpenAlex 独有的版权保护倒排索引还原为正常的纯文本摘要"""
    if not inverted_index:
        return ""
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    # 严格按照单词在文中的位置排序
    word_positions.sort(key=lambda x: x[0])
    return " ".join([word for pos, word in word_positions])


# ── 3. 工具函数：字段大一统兼容核心 ──────────────────────────────────────────
def normalize_item(item: dict) -> dict:
    """无损揉合 Claude 的 Schema 和 S2 Batch 的 Schema，确保双向兼容"""
    # 1. 统一 S2 唯一标识符
    if "paperId" in item and "s2_id" not in item:
        item["s2_id"] = item["paperId"]
    elif "s2_id" in item and "paperId" not in item:
        item["paperId"] = item["s2_id"]

    # 2. 统一 引用计数
    if "citationCount" in item and "citation_count" not in item:
        item["citation_count"] = item["citationCount"]
    elif "citation_count" in item and "citationCount" not in item:
        item["citationCount"] = item["citation_count"]

    # 3. 统一并建立 externalIds.DOI 映射（兼容 Claude 的核心去重和检索逻辑）
    if "doi" in item and item["doi"]:
        item["doi"] = item["doi"].strip().lower()
        if "externalIds" not in item or not isinstance(item["externalIds"], dict):
            item["externalIds"] = {}
        item["externalIds"]["DOI"] = item["doi"]
    elif (
        "externalIds" in item
        and isinstance(item["externalIds"], dict)
        and "DOI" in item["externalIds"]
    ):
        if item["externalIds"]["DOI"]:
            item["doi"] = item["externalIds"]["DOI"].strip().lower()

    # 4. 确保 baseline 默认字段存在，防止 LLM 脚本在循环中报 KeyError
    if "doi" not in item:
        item["doi"] = ""
    if "abstract" not in item:
        item["abstract"] = ""
    if "authors" not in item:
        item["authors"] = ""
    if "venue" not in item:
        item["venue"] = ""
    if "year" not in item:
        item["year"] = None

    return item


# ── 4. 主核心逻辑 ────────────────────────────────────────────────────────────
def main():
    log.info("🚀 [开始执行] 学术候选池数据结构大一统与摘要全自动补全...")

    if not RAW_REFS_PATH.exists():
        log.error(f"❌ 未找到 raw_refs.json 文件，请确认路径: {RAW_REFS_PATH}")
        return

    # 自动备份，安全第一
    shutil.copy(RAW_REFS_PATH, BACKUP_PATH)
    log.info(f"💾 已经自动创建原始数据备份至: {BACKUP_PATH}")

    with open(RAW_REFS_PATH, "r", encoding="utf-8") as f:
        papers = json.load(f)

    log.info(f"📂 成功读取候选池，共包含 {len(papers)} 篇文献。")

    # 步骤一：格式全量大一统清洗
    cleaned_papers = [normalize_item(p) for p in papers]

    # 找出哪些论文目前没有摘要，或者缺少关键的 DOI
    missing_abstract_indices = [
        i for i, p in enumerate(cleaned_papers) if not p.get("abstract")
    ]
    missing_doi_indices = [i for i, p in enumerate(cleaned_papers) if not p.get("doi")]
    needs_patch_indices = list(set(missing_abstract_indices + missing_doi_indices))

    log.info(
        f"📊 [体检报告] 格式清洗完毕！其中缺失摘要: {len(missing_abstract_indices)} 篇 | 缺失 DOI: {len(missing_doi_indices)} 篇。"
    )

    if not needs_patch_indices:
        log.info("🎉 所有的论文信息都是完美的，无需补全！正在保存大一统格式...")
        with open(RAW_REFS_PATH, "w", encoding="utf-8") as f:
            json.dump(cleaned_papers, f, ensure_ascii=False, indent=4)
        return

    # 步骤二：第一层防御 —— Semantic Scholar Batch 批量抢救
    log.info(
        f"🛠️ [Layer 1] 正在通过 S2 批量接口尝试挽救 {len(needs_patch_indices)} 篇异常数据..."
    )

    # 构造 S2 批量请求的 ID 列表
    s2_query_ids = []
    idx_mapping = {}  # 用于将返回的结果对齐回原数组

    for idx in needs_patch_indices:
        p = cleaned_papers[idx]
        if p.get("paperId"):
            s2_query_ids.append(p["paperId"])
            idx_mapping[p["paperId"]] = idx
        elif p.get("doi"):
            query_id = f"DOI:{p['doi']}"
            s2_query_ids.append(query_id)
            idx_mapping[query_id] = idx

    # S2 批量分块请求（每块最多 500 个）
    chunk_size = 500
    s2_patched_count = 0

    for i in range(0, len(s2_query_ids), chunk_size):
        chunk = s2_query_ids[i : i + chunk_size]
        payload = {"ids": chunk}
        params = {
            "fields": "paperId,externalIds,abstract,citationCount,title,venue,year,authors"
        }

        try:
            res = requests.post(
                S2_BATCH_URL,
                json=payload,
                params=params,
                headers=S2_HEADERS,
                timeout=30,
            )
            if res.status_code == 200:
                results = res.json()
                for q_id, res_item in zip(chunk, results):
                    if not res_item:
                        continue
                    orig_idx = idx_mapping[q_id]

                    # 补齐关键元数据
                    if (
                        res_item.get("abstract")
                        and not cleaned_papers[orig_idx]["abstract"]
                    ):
                        cleaned_papers[orig_idx]["abstract"] = res_item["abstract"]
                        s2_patched_count += 1
                    if res_item.get("externalIds") and "DOI" in res_item["externalIds"]:
                        cleaned_papers[orig_idx]["doi"] = (
                            res_item["externalIds"]["DOI"].strip().lower()
                        )

                    # 顺手刷新一下最新引用量和缺失的作者
                    if res_item.get("citationCount") is not None:
                        cleaned_papers[orig_idx]["citationCount"] = res_item[
                            "citationCount"
                        ]
                        cleaned_papers[orig_idx]["citation_count"] = res_item[
                            "citationCount"
                        ]
                    if not cleaned_papers[orig_idx]["authors"] and res_item.get(
                        "authors"
                    ):
                        authors_list = [
                            a.get("name") for a in res_item["authors"] if a.get("name")
                        ]
                        cleaned_papers[orig_idx]["authors"] = ", ".join(authors_list)
            else:
                log.warning(f"⚠️ S2 批量接口返回状态码: {res.status_code}，跳过该分块。")
        except Exception as e:
            log.error(f"💥 S2 批量请求引发异常: {e}")

        time.sleep(1.0)  # 礼貌休眠

    log.info(
        f"✨ [Layer 1 结束] 通过 S2 批量解救成功补齐了 {s2_patched_count} 篇论文的摘要！"
    )

    # 再次运行规范化，确保刚拿到的 DOI 成功映射到各个衍生键中
    cleaned_papers = [normalize_item(p) for p in cleaned_papers]

    # 步骤三：第二层终极防御 —— OpenAlex 对顽固无摘要文献实施精确降维打击
    still_missing_abstract = [
        p for p in cleaned_papers if not p.get("abstract") and p.get("doi")
    ]

    if still_missing_abstract:
        log.info(
            f"🔮 [Layer 2] 侦测到还有 {len(still_missing_abstract)} 篇顽固文献在 S2 中毫无摘要。启动 OpenAlex 终极引擎补全..."
        )
        openalex_patched_count = 0

        for p in still_missing_abstract:
            doi = p["doi"]
            url = f"https://api.openalex.org/works/doi:{doi}"
            try:
                res = requests.get(url, headers=OPENALEX_HEADERS, timeout=10)
                if res.status_code == 200:
                    oa_data = res.json()
                    inverted_index = oa_data.get("abstract_inverted_index")
                    if inverted_index:
                        plain_abstract = reconstruct_abstract(inverted_index)
                        if plain_abstract:
                            p["abstract"] = plain_abstract
                            openalex_patched_count += 1
                            log.info(
                                f"💎 [OpenAlex 成功] 强力还原摘要: 《{p['title'][:30]}...》"
                            )
                elif res.status_code == 429:
                    log.warning("⚠️ OpenAlex 触发频率限制，休眠 3 秒...")
                    time.sleep(3)
                # 礼貌性控制速度，对免费公共池表示尊重
                time.sleep(0.2)
            except Exception as e:
                log.warning(f"⚠️ 无法通过 OpenAlex 获取 DOI {doi} 的摘要: {e}")

        log.info(
            f"✨ [Layer 2 结束] OpenAlex 成功救回了 {openalex_patched_count} 篇顽固老文献的摘要！"
        )

    # 步骤四：最终格式复检并写回硬盘
    final_papers = [normalize_item(p) for p in cleaned_papers]
    final_missing = len([p for p in final_papers if not p.get("abstract")])

    with open(RAW_REFS_PATH, "w", encoding="utf-8") as f:
        json.dump(final_papers, f, ensure_ascii=False, indent=4)

    log.info(f"💾 【大功告成】数据已安全写回。当前候选池总数: {len(final_papers)} 篇。")
    log.info(
        f"📊 最终仍旧缺失摘要的顽固残疾文献（如无摘要的简短会议通告）仅剩: {final_missing} 篇。"
    )


if __name__ == "__main__":
    main()
