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
log = logging.getLogger("TagMerger")


def main():
    root_dir = Path(__file__).resolve().parent
    data_dir = root_dir / "data"
    raw_path = data_dir / "raw_refs.json"
    resolved_path = data_dir / "resolved_conflicts.json"

    # 生成合并前的安全备份文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = data_dir / f"raw_refs_backup_premerge_{timestamp}.json"

    # 1. 检查必要文件是否存在
    if not raw_path.exists():
        log.error(f"❌ 找不到原始数据文件: {raw_path}")
        return
    if not resolved_path.exists():
        log.error(
            f"❌ 找不到法庭判决文件: {resolved_path}，请确认 judge_conflicts.py 是否已运行完毕。"
        )
        return

    # 2. 加载数据
    with open(raw_path, "r", encoding="utf-8") as f:
        raw_papers = json.load(f)
    with open(resolved_path, "r", encoding="utf-8") as f:
        resolved_cases = json.load(f)

    log.info(
        f"📂 成功加载原始数据 ({len(raw_papers)} 篇) 与判决数据 ({len(resolved_cases)} 篇)。"
    )

    # 3. 将判决结果转换为字典索引，提升匹配查找速度
    resolved_map = {
        case["paperId"]: case for case in resolved_cases if case.get("paperId")
    }

    # 4. 执行遍历回填
    update_count = 0
    for p in raw_papers:
        pid = p.get("paperId")
        if pid and pid in resolved_map:
            case = resolved_map[pid]

            # 保存旧标签用于日志比对（可选）
            old_tags = [p.get("dim_a"), p.get("dim_b"), p.get("dim_c")]

            # 精准覆盖为 3.1 Pro 的终审标签
            p["dim_a"] = case["final_dim_a"]
            p["dim_b"] = case["final_dim_b"]
            p["dim_c"] = case["final_dim_c"]

            new_tags = [p["dim_a"], p["dim_b"], p["dim_c"]]
            log.info(f"🔄 已修正 ID: {pid} \n   Old: {old_tags} \n   New: {new_tags}")
            update_count += 1

    if update_count == 0:
        log.warning("⚠️ 没有发现任何匹配的 paperId，未对原始文件做任何修改。")
        return

    # 5. 稳妥起见，先创建合并前的本地快照备份
    log.info(f"💾 正在封存合并前备份: {backup_path.name} ...")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(raw_papers, f, ensure_ascii=False, indent=4)

    # 6. 将最终更新后的全量干净数据覆盖写回 raw_refs.json
    log.info(f"✍️ 正在将终审完工的数据写回: {raw_path.name} ...")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(raw_papers, f, ensure_ascii=False, indent=4)

    # 7. 打印收工报告
    print("\n" + "═" * 60)
    print(" 🎉 终审标签回填大功告成！")
    print("═" * 60)
    print(f" 📊 判决书总案件数 : {len(resolved_cases)} 宗")
    print(f" 🟢 成功修正记录数 : {update_count} 篇")
    print(f" 📁 历史快照已封存至 : {backup_path.name}")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
