import json
import logging
from datetime import datetime
from pathlib import Path

# 初始化日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("RawCleaner")


def main():
    root_dir = Path(__file__).resolve().parent
    data_dir = root_dir / "data"
    raw_path = data_dir / "raw_refs.json"

    # ── [修改一] 生成带时间戳的专属备份文件名，防止与普通 bak 混淆 ──
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = data_dir / f"raw_refs_backup_preclean_{timestamp}.json"

    if not raw_path.exists():
        log.error(f"❌ 找不到原始数据文件: {raw_path}，请确认路径是否正确。")
        return

    # 1. 读取原始论文数据
    with open(raw_path, "r", encoding="utf-8") as f:
        raw_papers = json.load(f)

    original_count = len(raw_papers)
    log.info(f"📂 成功加载原始资产，当前共计: {original_count} 条记录。")

    # 2. 执行双重 ID 排他性去重，并引入维度 (dim) 冲突校验
    clean_papers = []
    seen_papers = {}  # 改用字典：存放 id -> paper_dict 的映射，方便对比
    conflicts_to_judge = []  # <--- [新增] 专门存放争议论文的列表
    duplicate_count = 0
    no_id_count = 0

    for p in raw_papers:
        pid = p.get("paperId")
        doi = p.get("doi")

        # 检查是否命中重复 ID
        conflict_id = None
        if pid and pid in seen_papers:
            conflict_id = pid
        elif doi and doi in seen_papers:
            conflict_id = doi

        if conflict_id:
            duplicate_count += 1
            existing_p = seen_papers[conflict_id]

            # ── [修改二] 发现重复时，比对核心维度标签 ──
            dims_match = (
                p.get("dim_a") == existing_p.get("dim_a")
                and p.get("dim_b") == existing_p.get("dim_b")
                and p.get("dim_c") == existing_p.get("dim_c")
            )

            if not dims_match:
                log.warning(
                    f"⚠️ 发现维度冲突 (ID: {conflict_id})! \n"
                    f"   >> 旧记录: [{existing_p.get('dim_a')}, {existing_p.get('dim_b')}, {existing_p.get('dim_c')}] \n"
                    f"   >> 新记录: [{p.get('dim_a')}, {p.get('dim_b')}, {p.get('dim_c')}] \n"
                    f"   >> 策略: 默认保留旧记录，新记录已丢弃。"
                )
                # ── [新增] 将争议数据及其两套标签提取出来，供 3.1 Pro 裁决 ──
                conflicts_to_judge.append(
                    {
                        "paperId": conflict_id,
                        "title": existing_p.get("title", ""),
                        "abstract": existing_p.get("abstract", ""),
                        "tag_version_1": [
                            existing_p.get("dim_a"),
                            existing_p.get("dim_b"),
                            existing_p.get("dim_c"),
                        ],
                        "tag_version_2": [
                            p.get("dim_a"),
                            p.get("dim_b"),
                            p.get("dim_c"),
                        ],
                    }
                )
                # ────────────────────────────────────────────────────────
            continue

        # ── 以下为非重复的全新记录 ──
        if pid or doi:
            # 双重注册，防止有些论文只有 doi 没有 pid 导致的交叉重叠
            if pid:
                seen_papers[pid] = p
            if doi:
                seen_papers[doi] = p
            clean_papers.append(p)
        else:
            no_id_count += 1
            clean_papers.append(p)

    # 3. 稳妥起见，先创建本地备份
    log.info(f"💾 正在创建安全备份: {backup_path.name} ...")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(raw_papers, f, ensure_ascii=False, indent=4)

    # 4. 覆盖写入脱水后的纯净 raw 文件
    log.info(f"✍️ 正在将纯净数据写回: {raw_path.name} ...")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(clean_papers, f, ensure_ascii=False, indent=4)
    # ── [新增] 导出争议案件卷宗 ──
    if conflicts_to_judge:
        conflict_path = data_dir / "conflicts_to_judge.json"
        with open(conflict_path, "w", encoding="utf-8") as f:
            json.dump(conflicts_to_judge, f, ensure_ascii=False, indent=4)
        log.info(
            f"⚖️ 已将 {len(conflicts_to_judge)} 篇争议论文单独提取至: {conflict_path.name}"
        )
    # 5. 打印终审去重报告
    print("\n" + "═" * 60)
    print(" 🎉 原始资产库 [raw_refs.json] 源头脱水完毕！")
    print("═" * 60)
    print(f" 📊 历史累计总记录数 : {original_count} 篇")
    print(f" ✂️ 斩杀影分身(重复)  : {duplicate_count} 篇")
    print(f" ⚠️ 缺失关键ID记录    : {no_id_count} 篇 (已保留供下游清洗)")
    print(f" 🟢 最终源头纯净总数 : {len(clean_papers)} 篇")
    print(f" 📁 历史快照已封存至 : {backup_path.name}")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
