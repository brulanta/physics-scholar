"""Windows 分发一键构建：前端 build → 清旧打包产物 → PyInstaller 打包。

## 为什么需要这个脚本
分发流程有两个隐坑，忘一步就出错，故固化成一条命令：
  1. **忘跑 `npm run build`**：exe 里的前端是 `dist/` 上一次构建的旧产物，外观与源码不符。
  2. **旧打包产物残留**：`dist/` 顶层同时含前端产物与上一轮 `dist/PhysicsScholar/`
     （PyInstaller COLLECT 输出）。spec 的 `datas` 把整个 `ROOT/dist` 打入，会递归进
     `dist/PhysicsScholar/` 旧构建刷一大片「Ignoring non-existent resource」WARNING
     （无害但 bundle 虚胖）。

本脚本顺序执行：
  Step 1  cd frontend && npm run build     （vite outDir=../dist，产物落仓库根 dist/）
  Step 2  删 dist/PhysicsScholar/           （只删该子目录，保留刚构建的前端产物）
  Step 3  pyinstaller physics_scholar.spec --noconfirm

任一步失败即停并打印中文错误。用 subprocess.run(check=True)、不拼 shell，跨平台稳。

用法（仓库根或任意目录均可，路径基于脚本自身）：
    python scripts/build_release.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

# 脚本在 scripts/ 下，仓库根是其父目录的父目录
ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DIST = ROOT / "dist"
STALE_BUNDLE = DIST / "PhysicsScholar"  # 上一轮 PyInstaller COLLECT 输出


def _run(cmd: list[str], cwd: Path, step: str) -> None:
    """跑一条命令，失败即抛清晰中文错误并终止。"""
    print(f"\n=== {step} ===\n$ {' '.join(cmd)}  (cwd={cwd})", flush=True)
    try:
        subprocess.run(cmd, cwd=str(cwd), check=True)
    except FileNotFoundError:
        sys.exit(f"[构建失败] {step}：找不到可执行文件 {cmd[0]}，请确认已安装并在 PATH 中。")
    except subprocess.CalledProcessError as e:
        sys.exit(f"[构建失败] {step}：命令返回非零退出码 {e.returncode}。")


def main() -> None:
    # Step 1：前端构建（Windows 下 npm 是 npm.cmd，用 shutil.which 兜住）
    npm = shutil.which("npm") or "npm"
    _run([npm, "run", "build"], cwd=FRONTEND, step="Step 1/3 前端构建 (npm run build)")

    # Step 2：清上一轮打包产物，消除 spec 递归旧 bundle 的 WARNING 与体积虚胖
    print("\n=== Step 2/3 清理旧打包产物 ===", flush=True)
    if STALE_BUNDLE.exists():
        shutil.rmtree(STALE_BUNDLE)
        print(f"已删除旧产物：{STALE_BUNDLE}", flush=True)
    else:
        print(f"无旧产物需清理：{STALE_BUNDLE} 不存在", flush=True)

    # Step 3：PyInstaller 打包（--noconfirm 覆盖旧输出、不交互）
    _run(
        [sys.executable, "-m", "PyInstaller", "physics_scholar.spec", "--noconfirm"],
        cwd=ROOT,
        step="Step 3/3 PyInstaller 打包",
    )

    print(f"\n✅ 构建完成：{STALE_BUNDLE / 'PhysicsScholar.exe'}", flush=True)


if __name__ == "__main__":
    main()
