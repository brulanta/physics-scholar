import time
import json
import logging
import requests
from pathlib import Path

# ── 1. 初始化日志与基础路径 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("MWPDownloader")

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
PDF_CACHE_DIR = DATA_DIR / "cached_pdfs"

# 确保缓存目录存在
PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 伪装常用浏览器 Headers，防止被学术服务器拦截
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/pdf,application/json",
}


# ── 2. 三道防线核心嗅探函数 ──────────────────────────────────────────────────
def fetch_pdf_url_from_apis(paper_id: str, doi: str = None) -> str:
    """
    渐进式多源 API 嗅探引擎：S2 OA -> arXiv -> Unpaywall
    """
    # ── 第一防线：Semantic Scholar 官方 OA 接口 ──
    try:
        s2_api_url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}?fields=openAccessPdf,externalIds"
        response = requests.get(s2_api_url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            data = response.json()

            # 1. 尝试直取开放获取 PDF 链接
            oa_info = data.get("openAccessPdf")
            if oa_info and oa_info.get("url"):
                log.info(f" -> [防线 1 命中] 发现 Semantic Scholar OA 直链")
                return oa_info["url"]

            # 2. ── 第二防线：检查是否包含 arXiv 预印本 ID ──
            ext_ids = data.get("externalIds", {})
            arxiv_id = ext_ids.get("ArXiv")
            if arxiv_id:
                arxiv_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
                log.info(f" -> [防线 2 命中] 发现 arXiv 预印本路由: {arxiv_url}")
                return arxiv_url
    except Exception as e:
        log.debug(f"S2 API 检索失败 (ID: {paper_id}): {e}")

    # ── 第三防线：利用 DOI 检索 Unpaywall 绿色/金色开放获取库 ──
    if doi:
        try:
            # Unpaywall 要求提供 email 作为合规标识
            unpaywall_url = (
                f"https://api.unpaywall.org/v2/{doi}?email=mwp_rag_project@academic.org"
            )
            response = requests.get(unpaywall_url, headers=HEADERS, timeout=10)
            if response.status_code == 200:
                data = response.json()
                best_oa_location = data.get("best_oa_location")
                if best_oa_location and best_oa_location.get("url_for_pdf"):
                    pdf_url = best_oa_location["url_for_pdf"]
                    log.info(f" -> [防线 3 命中] Unpaywall 发现合法 OA 副本: {pdf_url}")
                    return pdf_url
        except Exception as e:
            log.debug(f"Unpaywall 检索失败 (DOI: {doi}): {e}")

    return ""


# ── 3. 核心持久化下载器 ──────────────────────────────────────────────────────
def download_pdf_file(url: str, save_path: Path) -> bool:
    """
    流式下载 PDF 文件并保存到本地
    """
    try:
        res = requests.get(url, headers=HEADERS, stream=True, timeout=20)
        # 确保返回的是合法的 PDF 二进制，而不是被拦截后网页返回的 403 HTML 文本
        if (
            res.status_code == 200
            and "application/pdf" in res.headers.get("Content-Type", "").lower()
        ):
            with open(save_path, "wb") as f:
                for chunk in res.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return True
        else:
            log.warning(f" -> 链接下载内容非有效 PDF 文件 (Status: {res.status_code})")
            return False
    except Exception as e:
        log.warning(f" -> 网络流式下载时发生异常: {e}")
        return False


# ── 4. 主控并集分发环 ────────────────────────────────────────────────────────
def main():
    log.info("========== 📡 离线解耦学术论文自动化下载引擎启动 ==========")

    # 1. 加载三个档位的文件
    files_to_load = [
        "selected_refs_64.json",
        "selected_refs_160.json",
        "selected_refs_320.json",
    ]

    union_papers = {}  # 利用 dict 的 key 特性实现以 paperId 为基准的并集去重

    for fname in files_to_load:
        fpath = DATA_DIR / fname
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                papers = json.load(f)
                for p in papers:
                    pid = p.get("paperId")
                    if pid:
                        union_papers[pid] = p
        else:
            log.warning(
                f"⚠️ 找不到预设文件 `{fname}`，系统将自动跳过。若需下载完整并集，请确保已跑完对应档位的 ranker.py"
            )

    total_unique = len(union_papers)
    log.info(
        f"📊 跨档位并集求交计算完毕！共锁定 【{total_unique}】 篇独立核心文献目标。"
    )

    if total_unique == 0:
        log.error("❌ 没有任何有效的目标 JSON 资产，程序退出。")
        return

    # 2. 轮询并集开始断点续传下载
    success_count = 0
    skip_count = 0
    fail_count = 0

    print("\n" + "═" * 60)
    print(" 🚀 开始进入学术网络渐进式多源下载管线")
    print("═" * 60)

    for idx, (pid, paper) in enumerate(union_papers.items(), 1):
        title = paper.get("title", "Unknown Title")
        doi = paper.get("doi")

        # 统一本地缓存文件名格式：{paperId}.pdf
        local_pdf_path = PDF_CACHE_DIR / f"{pid}.pdf"

        print(f"\n[任务 {idx}/{total_unique}] 《{title[:50]}...》")

        # ── 幂等检查：断点续传 ──
        if local_pdf_path.exists() and local_pdf_path.stat().st_size > 10240:
            log.info(f" 🟢 本地缓存命中！自动跳过下载。")
            # ── [新增] 缓存命中时的 source_url 补全兜底 ──────────────────────────
            if not paper.get("source_url"):
                paper["source_url"] = (
                    f"https://doi.org/{doi}"
                    if doi
                    else f"https://www.semanticscholar.org/paper/{pid}"
                )
            # ──────────────────────────────────────────────────────────────────
            skip_count += 1
            success_count += 1
            continue

        # ── 启动多源嗅探 pipeline ──
        pdf_url = fetch_pdf_url_from_apis(pid, doi)

        if pdf_url:
            log.info(f" ⏳ 正在向目标源拉取二进制流...")
            if download_pdf_file(pdf_url, local_pdf_path):
                log.info(f"  🎉 下载成功！已落盘缓存: {local_pdf_path.name}")
                # ── [新增] 动态记录真实的成功下载链接 ────────────────────────────────
                paper["source_url"] = pdf_url
                # ──────────────────────────────────────────────────────────────────
                success_count += 1
            else:
                log.error(f"  ❌ 二进制流拉取失败。")
                fail_count += 1
        else:
            log.error(
                f"  ❌ 三道防线全盘失守，未能在互联网开放网络中定位到该正刊论文的免费 PDF 直链。"
            )
            fail_count += 1

        # ── 友好限流：保护 IP，防止被学术站点识别封锁 ──
        time.sleep(1.2)
    # ── [新增] 核心多文件无损回填持久化机制 ──────────────────────────────────────────
    log.info("💾 正在将最新的 source_url 字段同步流式回填至各个档位的 JSON 文件...")
    for fname in files_to_load:
        fpath = DATA_DIR / fname
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as f:
                current_file_papers = json.load(f)

            # 遍历当前文件内的论文，从内存并集池中同步 source_url
            for p in current_file_papers:
                pid = p.get("paperId")
                if pid in union_papers and "source_url" in union_papers[pid]:
                    p["source_url"] = union_papers[pid]["source_url"]

            # 写回覆盖原 JSON 文件，保持结构优雅漂亮
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(current_file_papers, f, ensure_ascii=False, indent=4)

    log.info(
        "📂 升级完毕！64/160/320 三档 JSON 资产库已全部原地进化，完美支持 source_url 溯源。"
    )
    # ────────────────────────────────────────────────────────────────────────────────
    # 5. 打印最终大捷报告
    print("\n" + "═" * 60)
    print(" 🎉 【解耦下载管线】阶段一任务流执行完毕")
    print("═" * 60)
    print(f" 📂 本地黄金 PDF 缓存区 :  `data/cached_pdfs/`")
    print(f" 🟢 全盘成功获取文献总数:  {success_count} / {total_unique} 篇")
    print(f"   ◽ 本地秒级续传跳过  :  {skip_count} 篇")
    print(f"   ◽ 本次网络新刷下载  :  {success_count - skip_count} 篇")
    print(f" ⚠️ 本次未攻克闭源文献  :  {fail_count} 篇 (将静默等待本地人工补录)")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
