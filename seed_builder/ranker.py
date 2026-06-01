import argparse
import json
import logging
from pathlib import Path

# ── 1. 初始化日志与路径 ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("MWPRanker")

ROOT_DIR = Path(__file__).resolve().parent
RAW_REFS_PATH = ROOT_DIR / "data" / "raw_refs.json"
DATA_DIR = ROOT_DIR / "data"


# ── 2. 核心多维加权排序算法 ──────────────────────────────────────────────────
def calculate_rank_score(paper: dict) -> float:
    """
    基于三维正交标签的学术价值复合评分算法。
    不仅看原始引用量，还对高价值快报、打破纪录的跨界前沿成果赋予 Bonus 积分。
    """
    base_citation = float(paper.get("citationCount", 0))
    bonus = 0.0

    dim_a = paper.get("dim_a", "")
    dim_b = paper.get("dim_b", "")
    dim_c = paper.get("dim_c", "")

    # 1. 文献载体加权：快报代表突破性 SoTA 指标，给予 10% 优先红利
    if dim_c == "Letter/Express":
        bonus += 0.10

    # 2. 跨界交叉红利加权：核心物理器件与核心系统应用的强强联手
    # 示例 A：高性能光频梳应用于太赫兹超高频段
    if dim_a == "Laser & Frequency Comb" and dim_b == "THz Photonics":
        bonus += 0.30
    # 示例 B：薄膜铌酸锂/集成调制器应用于新兴光计算/类脑神经网络
    if dim_a == "Modulator" and dim_b == "Emerging Computing":
        bonus += 0.25
    # 示例 C：高性能滤波器/时延线应用于雷达光子学架构
    if dim_a == "Optical Filter & Delay Line" and dim_b == "Radar & Sensing":
        bonus += 0.20

    return base_citation * (1.0 + bonus)


# ── 3. 主核心路由器 ────────────────────────────────────────────────────────────
def main():
    log.info("========== ⚖️ 智能正交配额裁剪算法引擎启动 ==========")

    # 1. 命令行参数解析体系
    parser = argparse.ArgumentParser(
        description="MWP Reference Multi-Dimensional Ranker & Clipper"
    )
    parser.add_argument(
        "-t",
        "--total",
        type=int,
        default=0,
        help="指定最终筛选出来的目标论文总数。若不指定，将触发交互式档位选择。",
    )
    args = parser.parse_args()

    if not RAW_REFS_PATH.exists():
        log.error(f"❌ 找不到上游资产库，请确认路径: {RAW_REFS_PATH}")
        return

    with open(RAW_REFS_PATH, "r", encoding="utf-8") as f:
        all_papers = json.load(f)

    # 2. 严格执行双重前置清洗（晒引用、去残缺漏网之鱼、剔除标签错乱）

    # ── [新增] 定义严格的合法标签白名单 ──────────────────────────────────────────
    VALID_DIM_A = {
        "Modulator",
        "Laser & Frequency Comb",
        "Photodetector",
        "Optical Filter & Delay Line",
        "Optoelectronic Oscillator (OEO)",
        "Photonic Integrated Chip",
        "System Architecture Only",
        "Comprehensive/Device-Agnostic",
        "Pending",
    }

    VALID_DIM_B = {
        "Signal Generation",
        "Signal Processing & IFM",
        "Radar & Sensing",
        "Optical Wireless Communications",
        "THz Photonics",
        "Emerging Computing",
        "Foundational Theory",
        "Comprehensive/Application-Agnostic",
        "Pending",
    }

    malformed_count = 0
    # ── [新增] 引入全盘唯一 paperId 追踪集合，彻底粉碎交叉引用导致的影分身占座 ──
    seen_paper_ids = set()
    # ──────────────────────────────────────────────────────────────────────────

    cleaned_pool = []
    for p in all_papers:
        # ── [新增/微调] 优先提取并校验 paperId ──────────────────────────────────
        pid = p.get("paperId")
        if not pid:
            continue  # 没有唯一ID的边缘数据直接弃用
        # ──────────────────────────────────────────────────────────────────────────

        citation = p.get("citationCount", 0)
        dim_a = p.get("dim_a")
        dim_b = p.get("dim_b")
        dim_c = p.get("dim_c")

        if citation < 50:
            continue
        if not (dim_a and dim_b and dim_c):
            continue

        if (dim_a not in VALID_DIM_A) or (dim_b not in VALID_DIM_B):
            malformed_count += 1
            continue

        # ── [新增严格去重核心拦截线] ──────────────────────────────────────────────
        if pid in seen_paper_ids:
            continue  # 如果该论文之前已经从别的引用源里收录过了，直接斩断，不准重复占座
        seen_paper_ids.add(pid)
        # ──────────────────────────────────────────────────────────────────────────

        if dim_b == "Pending":
            continue

        cleaned_pool.append(p)

    # ── [修改日志输出] 动态反馈错乱论文数量 ──────────────────────────────────────
    log.info(
        f"🧹 资产初筛完毕。原始资产: {len(all_papers)} 篇 -> 合规高引核心池: {len(cleaned_pool)} 篇。"
    )
    if malformed_count > 0:
        log.warning(
            f"⚠️ 警报：本次扫描共强力拦截了 【{malformed_count}】 篇标签非法/串位的错乱论文！"
        )
    else:
        log.info("🟢 极佳！本次未检测到任何标签串位的错乱论文。")
    # ──────────────────────────────────────────────────────────────────────────

    # 3. 动态确定目标总数 T
    target_total = args.total
    if target_total <= 0:
        print(
            "\n💡 提示：检测到未输入具体数量参数。请从以下学术知识库标准预设档位中选择一档："
        )
        print("  [1] Lite 模式     --> 最终保留 64 篇核心文献 (适合轻量快速体验)")
        print("  [2] Standard 模式 --> 最终保留 160 篇核心文献 (推荐，学科生态最稳健)")
        print(
            "  [3] Full 模式     --> 最终保留 320 篇核心文献 (适合重度科研与大资产RAG)"
        )
        choice = input("请输入对应档位编号 [1-3] 并回车 (默认 2): ").strip()
        if choice == "1":
            target_total = 64
        elif choice == "3":
            target_total = 320
        else:
            target_total = 160

    # 边界盾牌：如果要求的总数比合规池还多，自动锁死上限
    if target_total > len(cleaned_pool):
        log.warning(
            f"⚠️ 请求的总数 {target_total} 超过了当前合规池最大存量 {len(cleaned_pool)}，已自动降级为最大存量。"
        )
        target_total = len(cleaned_pool)

    log.info(
        f"🎯 选定最终战术终点：必须精准筛选并交付【{target_total}】篇核心论文资产。"
    )

    # 4. 核心路由分流：按照维度 B（系统应用）归类
    b_buckets = {}
    for p in cleaned_pool:
        b_tag = p["dim_b"]
        if b_tag not in b_buckets:
            b_buckets[b_tag] = []
        b_buckets[b_tag].append(p)

    valid_directions = list(b_buckets.keys())
    num_directions = len(valid_directions)
    log.info(f"📡 动态扫描发现当前资产池共涵盖 {num_directions} 个有效系统应用方向。")

    # 5. 计算并优化各方向配额（自适应平衡调度）
    # 给每个方向计算初始平均期望配额
    base_quota_per_dir = target_total // num_directions
    dir_quotas = {d: base_quota_per_dir for d in valid_directions}

    # 将因整除丢失的微小余数补偿给第一个方向
    remainder = target_total - sum(dir_quotas.values())
    if remainder > 0 and valid_directions:
        dir_quotas[valid_directions[0]] += remainder

    # 【核心动态补偿平衡环】：如果某个方向的论文储备根本不够它的配额，把空闲配额分给其他方向
    while True:
        quota_changed = False
        surplus_pool = 0
        under_quota_dirs = []

        for d in valid_directions:
            available_papers = len(b_buckets[d])
            assigned_quota = dir_quotas[d]

            if assigned_quota > available_papers and assigned_quota > 0:
                # 产生顺差：配额给多了，把多出来的收回
                surplus_pool += assigned_quota - available_papers
                dir_quotas[d] = available_papers
                quota_changed = True
            elif assigned_quota < available_papers:
                # 还有扩容空间的瓶颈方向
                under_quota_dirs.append(d)

        if surplus_pool > 0 and under_quota_dirs:
            # 将收集上来的富余配额，平均分配给还有存量储备的方向
            add_per_dir = surplus_pool // len(under_quota_dirs)
            rem = surplus_pool % len(under_quota_dirs)

            for i, d in enumerate(under_quota_dirs):
                dir_quotas[d] += add_per_dir
                if i < rem:
                    dir_quotas[d] += 1
            quota_changed = True

        if not quota_changed:
            break

    # 6. 精准筛选晋级赛（执行金字塔与集成类上限控制）
    final_selected_papers = []

    for d in valid_directions:
        quota = dir_quotas[d]
        if quota <= 0:
            continue

        papers_in_dir = b_buckets[d]

        # 核心：计算每篇论文的复合得分，并从高到低排序
        for p in papers_in_dir:
            p["_rank_score"] = calculate_rank_score(p)
        papers_in_dir.sort(key=lambda x: x["_rank_score"], reverse=True)

        # 开始逐篇过筛，落实“片上集成上限控制 (30%)”
        imwp_max = int(quota * 0.30)
        imwp_count = 0
        selected_in_dir = []

        for p in papers_in_dir:
            if len(selected_in_dir) >= quota:
                break

            is_imwp = p.get("dim_a") == "Photonic Integrated Chip"

            if is_imwp:
                if imwp_count < imwp_max:
                    imwp_count += 1
                    selected_in_dir.append(p)
                else:
                    # 触发熔断限制：当前方向的集成工艺类论文已达30%上限，这篇虽高引但必须被系统拦截，留给纯系统架构
                    continue
            else:
                selected_in_dir.append(p)

        # 兜底平衡：如果因为拦截了集成类，导致该方向没塞满配额，用刚才被拦截的或者剩下的递补
        if len(selected_in_dir) < quota:
            for p in papers_in_dir:
                if len(selected_in_dir) >= quota:
                    break
                if p not in selected_in_dir:
                    selected_in_dir.append(p)

        # 清理掉临时排序分数键
        for p in selected_in_dir:
            if "_rank_score" in p:
                del p["_rank_score"]

        final_selected_papers.extend(selected_in_dir)

    # 7. 💾 持久化无损输出
    output_filename = f"selected_refs_{target_total}.json"
    OUTPUT_PATH = DATA_DIR / output_filename

    # 确保 data 目录存在
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(final_selected_papers, f, ensure_ascii=False, indent=4)

    # 8. 📊 打印最终成果复盘报告
    print("\n" + "═" * 60)
    print(f" 🎉 【Ranker 资产动态路由裁剪器】工作圆满完成")
    print("═" * 60)
    print(f" 📈 目标保留论文数 :  {target_total} 篇")
    print(f" 📉 实际交付论文数 :  {len(final_selected_papers)} 篇")
    print(f" 💾 黄金知识库已写入 :  `data/{output_filename}`")
    print(" 📊 各研究应用方向资产分布生态表:")
    print("─" * 60)

    # 统计方向分布
    dist = {}
    for p in final_selected_papers:
        dist[p["dim_b"]] = dist.get(p["dim_b"], 0) + 1
    for d, c in dist.items():
        print(f"  🔹 {d.ljust(32)} : {c} 篇 (配额: {dir_quotas.get(d, 0)} 篇)")

    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
