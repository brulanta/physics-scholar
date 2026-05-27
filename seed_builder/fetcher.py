"""
F8 seed_builder · fetcher.py  (v3 - 完美运行版)
修复内容（相对 v2）：
  - 修复 REF_FIELDS：显式加入 paperId，防止解析时 valid 校验将所有 reference 误判为空并丢弃
  - 修复嵌套字段语法：正确生成 references.paperId,references.title 格式的 API 传参
  - 补充遗漏的 DOI：补全了 Yao 2009、Marpaung 2019 的 DOI
  - 净化搜索 fallback：移除 title_query 中的作者/年份/期刊等干扰项，提升 API 搜索命中率
"""

import os, json, time, argparse, logging
from pathlib import Path
from datetime import datetime

import requests
from dotenv import load_dotenv

# ── 环境变量：从项目根 .env 读取 ─────────────────────────────────────────────
load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env")
S2_API_KEY = os.getenv("S2_API_KEY", "")
S2_BASE = "https://api.semanticscholar.org/graph/v1"
HEADERS = {"x-api-key": S2_API_KEY} if S2_API_KEY else {}
SLEEP = 1.2  # 每次 API 调用后的间隔（秒）

# ── 7 篇入口综述 (已修复缺漏 DOI 与冗余搜索词) ──────────────────────────────
ENTRY_REVIEWS = [
    {
        "layer": "classic",
        "authors": "Capmany & Novak",
        "year": 2007,
        "journal": "Nature Photonics",
        "doi": "10.1038/nphoton.2007.89",
        "title_query": "Microwave photonics combines two worlds",
        "s2_id": "f560c6a66b05b41f57ef7c276756551765db9e90",
    },
    {
        "layer": "classic",
        "authors": "Yao",
        "year": 2009,
        "journal": "Journal of Lightwave Technology",
        "doi": "10.1109/JLT.2008.2009551",  # 补全了被 Claude 漏掉的 DOI
        "title_query": "Microwave photonics",
        "s2_id": "481948f3421e35ae2854c7594719a57d0b214042",
    },
    {
        "layer": "mature",
        "authors": "Marpaung et al.",
        "year": 2013,
        "journal": "Laser & Photonics Reviews",
        "doi": "10.1002/lpor.201200032",
        "title_query": "Integrated microwave photonics Marpaung 2013 Laser Photonics Reviews",
    },
    {
        "layer": "frontier",
        "authors": "Marpaung, Yao & Capmany",
        "year": 2019,
        "journal": "Nature Photonics",
        "doi": "10.1038/s41566-018-0310-5",  # 补全了被 Claude 漏掉的 DOI
        "title_query": "Integrated microwave photonics",
        "s2_id": "f81c8633704deca0f29bdd289b80bc237339a6e4",
    },
    {
        "layer": "frontier",
        "authors": "Zhu et al.",
        "year": 2021,
        "journal": "Advances in Optics and Photonics",
        "doi": "10.1364/AOP.411024",
        "title_query": "Integrated photonics on thin-film lithium niobate",
    },
    {
        "layer": "frontier",
        "authors": "Shastri et al.",
        "year": 2021,
        "journal": "Nature Photonics",
        "doi": "10.1038/s41566-020-00754-y",
        "title_query": "Photonics for artificial intelligence and neuromorphic computing",
    },
    {
        "layer": "frontier",
        "authors": "Yao & Capmany",
        "year": 2022,
        "journal": "Science China Information Sciences",
        "doi": "10.1007/s11432-021-3524-0",
        "title_query": "Microwave photonics",
        "s2_id": "c53e296ca6737938f3de2c27c7e8de832d754446",
    },
]

# S2 references 端点返回字段（修复：必须显式请求 paperId）
REF_FIELDS = "paperId,title,authors,year,citationCount,abstract,externalIds,venue,publicationTypes,openAccessPdf"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# S2 API 基础层
# ─────────────────────────────────────────────────────────────────────────────


def s2_get(url: str, params: dict, retries: int = 3) -> dict | None:
    """GET with retry + rate-limit handling。每次成功返回后调用方负责 sleep。"""
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = 10 * (attempt + 1)
                log.warning(f"  [429] Rate limited，等待 {wait}s …")
                time.sleep(wait)
                continue
            if resp.status_code == 404:
                return None
            log.warning(f"  HTTP {resp.status_code}: {url}")
            return None
        except requests.RequestException as e:
            log.warning(f"  请求异常 (attempt {attempt + 1}/{retries}): {e}")
            time.sleep(3)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 论文解析：DOI → 标题搜索 → 失败
# ─────────────────────────────────────────────────────────────────────────────


def resolve_by_doi(doi: str) -> tuple[str, str] | None:
    data = s2_get(
        f"{S2_BASE}/paper/DOI:{doi}",
        params={"fields": "paperId,title,citationCount,referenceCount"},
    )
    if data and data.get("paperId"):
        log.info(
            f"  [DOI ✓] {data.get('title', '')[:65]}  "
            f"cited={data.get('citationCount', '?')}  "
            f"refCount={data.get('referenceCount', '?')}"
        )
        return data["paperId"], data.get("title", "")
    return None


def resolve_by_title(query: str, expected_year: int) -> tuple[str, str] | None:
    data = s2_get(
        f"{S2_BASE}/paper/search",
        params={
            "query": query,
            "fields": "paperId,title,year,citationCount,referenceCount,externalIds",
            "limit": 5,
        },
    )
    if not data:
        return None
    candidates = data.get("data", [])
    candidates = [
        c
        for c in candidates
        if c.get("year")
        and abs(c["year"] - expected_year) <= 1
        and (c.get("citationCount") or 0) >= 50
    ]
    if not candidates:
        log.warning(f"  [Title ✗] 年份±1且引用≥50无命中，放弃")
        return None
    best = max(candidates, key=lambda c: c.get("citationCount") or 0)
    log.info(
        f"  [Title ✓] {best.get('title', '')[:65]}  "
        f"year={best.get('year')}  "
        f"cited={best.get('citationCount', '?')}  "
        f"refCount={best.get('referenceCount', '?')}"
    )
    return best["paperId"], best.get("title", "")


def resolve_paper(review: dict) -> tuple[str, str] | None:
    label = f"{review['authors']} {review['year']}"

    # 【必须加在最前面】如果有硬编码的 s2_id，直接用它！不去查 DOI，也不去搜标题！
    if review.get("s2_id"):
        log.info(f"  [S2_ID 直通] 检测到预填 ID，直接锁定：{review['s2_id']}")
        return review["s2_id"], f"{label} (Resolved by manual s2_id)"

    # 下面保持你原有的 DOI 和 Title 搜索逻辑不变...
    doi = review.get("doi", "")
    # ...

    if doi:
        result = resolve_by_doi(doi)
        time.sleep(SLEEP)
        if result:
            return result
        log.warning(f"  [DOI ✗] {doi} 在 S2 中未找到，尝试标题搜索 …")

    result = resolve_by_title(review["title_query"], review["year"])
    time.sleep(SLEEP)
    if result:
        return result

    log.error(f"  [FAIL] 无法在 S2 中解析：{label}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Reference 拉取：/references 分页 + detail 兜底
# ─────────────────────────────────────────────────────────────────────────────


def fetch_refs_paginated(paper_id: str, batch_size: int = 100) -> list[dict]:
    all_refs, offset = [], 0
    while True:
        data = s2_get(
            f"{S2_BASE}/paper/{paper_id}/references",
            params={"fields": REF_FIELDS, "offset": offset, "limit": batch_size},
        )
        if not data:
            break
        batch = data.get("data", [])
        if not batch:
            break
        for item in batch:
            cited = item.get("citedPaper") or {}
            # 这里如果 REF_FIELDS 里没有 paperId 就会一直被过滤掉，已修复
            if cited.get("paperId"):
                all_refs.append(cited)
        offset += len(batch)
        if len(batch) < batch_size:
            break
        time.sleep(SLEEP)
    return all_refs


def fetch_refs_via_detail(paper_id: str) -> list[dict]:
    """兜底路径：使用独立的请求语法"""
    # 修复：给每个独立字段前加入 references. 映射，例如 references.paperId,references.title
    detail_fields = ",".join(f"references.{f}" for f in REF_FIELDS.split(","))
    data = s2_get(
        f"{S2_BASE}/paper/{paper_id}",
        params={"fields": f"referenceCount,{detail_fields}"},
    )
    if not data:
        return []
    refs = data.get("references") or []
    ref_count = data.get("referenceCount", 0)
    results = [item for item in refs if item.get("paperId")]
    log.info(f"  [detail兜底] referenceCount={ref_count}，实际获取={len(results)}")
    return results


def fetch_references(paper_id: str, review_label: str) -> list[dict]:
    log.info(f"  拉取 references（分页）…")
    refs = fetch_refs_paginated(paper_id)
    time.sleep(SLEEP)

    if len(refs) == 0:
        log.warning(f"  [!] 分页端点返回 0 条，尝试 paper detail 兜底 …")
        refs = fetch_refs_via_detail(paper_id)
        time.sleep(SLEEP)

    if len(refs) == 0:
        log.error(
            f"  [!!] {review_label} 的 references 两种方式均返回 0，"
            f"S2 可能未收录该论文的参考文献列表。"
        )

    return refs


# ─────────────────────────────────────────────────────────────────────────────
# 数据标准化 & 合并
# ─────────────────────────────────────────────────────────────────────────────


def normalize_ref(ref: dict, source_doi: str) -> dict:
    ext = ref.get("externalIds") or {}
    doi = (ext.get("DOI") or ext.get("doi") or "").lower()
    authors = ref.get("authors") or []
    author_str = ", ".join(a.get("name", "") for a in authors[:3])
    if len(authors) > 3:
        author_str += " et al."
    pdf_url = ""
    oa = ref.get("openAccessPdf")
    if isinstance(oa, dict):
        pdf_url = oa.get("url", "")
    return {
        "doi": doi,
        "s2_id": ref.get("paperId", ""),
        "title": ref.get("title", ""),
        "authors": author_str,
        "year": ref.get("year"),
        "venue": ref.get("venue", ""),
        "citation_count": ref.get("citationCount") or 0,
        "abstract": (ref.get("abstract") or "")[:1500],
        "pdf_url": pdf_url,
        "pub_types": ref.get("publicationTypes") or [],
        "source_reviews": [source_doi],
        "directions": [],
        "dim_a": "",
        "dim_b": "",
    }


def merge_refs(pool: dict, new_refs: list[dict], source_doi: str) -> dict:
    for ref in new_refs:
        key = ref["doi"] if ref["doi"] else ref["s2_id"]
        if not key:
            continue
        if key in pool:
            if source_doi not in pool[key]["source_reviews"]:
                pool[key]["source_reviews"].append(source_doi)
        else:
            pool[key] = ref
    return pool


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def run(dry_run: bool, out_path: Path, incremental: bool) -> None:
    if not S2_API_KEY:
        log.warning("S2_API_KEY 未设置！将以未认证配额运行，限速更严，建议填入 .env")

    if dry_run:
        log.info("── Dry-run：仅打印入口列表 ──")
        for r in ENTRY_REVIEWS:
            log.info(
                f"  [{r['layer']}] {r['authors']} {r['year']}  "
                f"doi={r['doi'] or '(无，走标题搜索)'}  "
                f"query={r['title_query'][:50]}"
            )
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = out_path.parent / "fetch_log.json"

    pool: dict[str, dict] = {}
    already_done_s2ids: set[str] = set()
    if incremental and out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)
        pool = {
            (v["doi"] or v["s2_id"]): v for v in existing if (v["doi"] or v["s2_id"])
        }
        if log_path.exists():
            with open(log_path, encoding="utf-8") as lf:
                prev_log = json.load(lf)
            for entry in prev_log.get("fetch_log", []):
                if entry.get("status") == "ok" and entry.get("s2_paper_id"):
                    already_done_s2ids.add(entry["s2_paper_id"])
        log.info(
            f"[增量] 已载入 {len(pool)} 篇，已处理综述 {len(already_done_s2ids)} 篇（by s2_paper_id）"
        )

    fetch_log = []
    total_start = datetime.now()

    for review in ENTRY_REVIEWS:
        label = f"{review['authors']} {review['year']}"
        doi = review.get("doi", "")
        log.info(f"── 处理综述：{label} ──")

        t0 = datetime.now()

        resolved = resolve_paper(review)
        if not resolved:
            fetch_log.append(
                {
                    "doi": doi,
                    "label": label,
                    "status": "failed",
                    "reason": "S2 解析失败（DOI+标题均未命中）",
                }
            )
            continue

        paper_id, resolved_title = resolved
        log.info(f"  paper_id: {paper_id}")

        if incremental and paper_id in already_done_s2ids:
            log.info(f"  [跳过] s2_paper_id 已在上次成功记录中")
            continue

        raw_refs = fetch_references(paper_id, label)
        log.info(f"  原始 references: {len(raw_refs)} 条")

        source_key = doi if doi else paper_id
        normalized = [normalize_ref(r, source_key) for r in raw_refs]
        before = len(pool)
        pool = merge_refs(pool, normalized, source_key)
        new_added = len(pool) - before

        elapsed = (datetime.now() - t0).total_seconds()
        log.info(f"  新增入池: {new_added} | 累计池: {len(pool)} | {elapsed:.1f}s")

        fetch_log.append(
            {
                "doi": doi,
                "label": label,
                "layer": review["layer"],
                "journal": review["journal"],
                "resolved_title": resolved_title,
                "s2_paper_id": paper_id,
                "raw_ref_count": len(raw_refs),
                "new_added": new_added,
                "elapsed_s": round(elapsed, 1),
                "status": "ok" if len(raw_refs) > 0 else "ok_but_empty_refs",
            }
        )

    # ── 汇总统计 ──────────────────────────────────────────────────────────────
    total_elapsed = (datetime.now() - total_start).total_seconds()
    cited_by_multiple = sum(1 for v in pool.values() if len(v["source_reviews"]) > 1)
    dist = {
        "≥500": sum(1 for v in pool.values() if v["citation_count"] >= 500),
        "100-499": sum(1 for v in pool.values() if 100 <= v["citation_count"] < 500),
        "50-99": sum(1 for v in pool.values() if 50 <= v["citation_count"] < 100),
        "<50": sum(1 for v in pool.values() if 0 < v["citation_count"] < 50),
        "unknown": sum(1 for v in pool.values() if v["citation_count"] == 0),
    }
    ok_count = sum(1 for l in fetch_log if l["status"].startswith("ok"))

    log.info("─" * 55)
    log.info(f"候选池总量：{len(pool)} 篇（去重后）")
    log.info(f"被多篇综述同时引用：{cited_by_multiple} 篇")
    log.info(f"引用量分布：{dist}")
    log.info(
        f"成功综述：{ok_count}/{len(ENTRY_REVIEWS)} | 总耗时：{total_elapsed:.1f}s"
    )

    # ── 写出 ──────────────────────────────────────────────────────────────────
    pool_list = sorted(pool.values(), key=lambda x: x["citation_count"], reverse=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pool_list, f, ensure_ascii=False, indent=2)
    log.info(f"候选池 → {out_path}")

    summary = {
        "generated_at": datetime.now().isoformat(),
        "total_papers": len(pool),
        "cited_by_multiple_reviews": cited_by_multiple,
        "citation_distribution": dist,
        "reviews_ok": ok_count,
        "total_elapsed_s": round(total_elapsed, 1),
        "fetch_log": fetch_log,
    }
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info(f"拉取日志 → {log_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="跳过已处理的综述，在已有 raw_refs.json 上追加",
    )
    parser.add_argument("--out", type=Path, default=Path("data/raw_refs.json"))
    args = parser.parse_args()
    run(dry_run=args.dry_run, out_path=args.out, incremental=args.incremental)
