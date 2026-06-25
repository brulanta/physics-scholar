# scripts/calibrate_tokenizer.py
#
# 一次性校准脚本（纯开发工具，非自动测试，不打包进 exe）。
#
# 目的：为 src/core/chunker.py 的本地 token 估算公式
#       est_tokens ≈ A·中文字数 + B·英文词数 + C
# 拟合系数，使其逼近 bge-m3 的真实 token 数。
#
# 原理：从本地论文采样不同长度的文本段，逐段调硅基流动 bge-m3 嵌入 API
#       取真实 usage.prompt_tokens 作 ground-truth，最小二乘拟合 A/B/C。
#       脚本只打印拟合结果与一段可粘贴的 config 出厂默认值（不写 yaml、不写 .env）。
#
# 用法：
#   python scripts/calibrate_tokenizer.py            # 拟合并打印结果 + 粘贴片段
#   python scripts/calibrate_tokenizer.py --max 300  # 限制采样段数（默认 200）
#
# 流程：打包分发前跑一次本脚本，确认 R² 达标后，把打印出的 A/B/C 硬编码进
#       src/config.py 的 CHUNK_CALIB_* 出厂默认，并将 CHUNK_CALIBRATED fallback 改为 True。

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import (  # noqa: E402
    EMBEDDING_API_KEY,
    EMBEDDING_BASE_URL,
    EMBEDDING_MODEL,
    PDF_DIR,
)
from src.core import parser  # noqa: E402
from src.core.chunker import _CJK, _WORD  # noqa: E402

# 采样段的目标字符长度谱（覆盖回归空间，从短到长）
_TARGET_LENS = [60, 120, 200, 320, 480, 700, 1000, 1400]
# 句子切分（与 chunker 分隔符同源，粗到细）
_SENT_SPLIT = __import__("re").compile(r"(?<=[。.！!？?；;\n])")


def _iter_pdfs():
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"❌ {PDF_DIR} 下没有 PDF，无法采样。请先放入若干论文。")
        sys.exit(1)
    return pdfs


def _segments_from_text(body: str):
    """把正文按句子累积成不同目标长度的文本段，覆盖长度谱。"""
    sents = [s for s in _SENT_SPLIT.split(body) if s.strip()]
    segs, buf, idx = [], "", 0
    target = _TARGET_LENS[idx % len(_TARGET_LENS)]
    for s in sents:
        buf += s
        if len(buf) >= target:
            segs.append(buf.strip())
            buf = ""
            idx += 1
            target = _TARGET_LENS[idx % len(_TARGET_LENS)]
    if buf.strip() and len(buf.strip()) >= 30:
        segs.append(buf.strip())
    return segs


def _collect_segments(max_samples: int):
    samples = []
    for pdf in _iter_pdfs():
        try:
            blocks = parser.parse_pdf(str(pdf))
        except Exception as e:
            print(f"  ⚠ 解析失败，跳过 {pdf.name}: {e}")
            continue
        body = blocks.get("body", "")
        if not body.strip():
            continue
        segs = _segments_from_text(body)
        print(f"  • {pdf.name}: 采样 {len(segs)} 段")
        samples.extend(segs)
    # 截断到上限，均匀抽样以兼顾各篇/各长度
    if len(samples) > max_samples:
        step = len(samples) / max_samples
        samples = [samples[int(i * step)] for i in range(max_samples)]
    return samples


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=200, help="最大采样段数（默认 200）")
    args = ap.parse_args()

    if not EMBEDDING_API_KEY:
        print("❌ EMBEDDING_API_KEY 为空，无法调用嵌入 API。请在 .env 或 config 中配置。")
        sys.exit(1)

    print("\n========== 采样文本段 ==========")
    segments = _collect_segments(args.max)
    if len(segments) < 10:
        print(f"❌ 有效样本过少（{len(segments)}），无法稳定拟合。")
        sys.exit(1)
    print(f"共 {len(segments)} 段，开始调用 bge-m3 取真实 token 数……\n")

    client = OpenAI(api_key=EMBEDDING_API_KEY, base_url=EMBEDDING_BASE_URL)

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, max=20))
    def real_tokens(text: str) -> int:
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
        return int(resp.usage.prompt_tokens)

    rows, y = [], []
    for i, seg in enumerate(segments, 1):
        cjk = len(_CJK.findall(seg))
        words = len(_WORD.findall(seg))
        try:
            tok = real_tokens(seg)
        except Exception as e:
            print(f"  ⚠ [{i}/{len(segments)}] API 失败，跳过该段: {e}")
            continue
        rows.append([cjk, words, 1.0])
        y.append(tok)
        if i % 20 == 0:
            print(f"  进度 {i}/{len(segments)}")
        time.sleep(0.05)  # 轻节流，RPM 2000 充裕

    if len(rows) < 10:
        print(f"❌ 成功样本过少（{len(rows)}），无法拟合。")
        sys.exit(1)

    X = np.array(rows, dtype=float)
    Y = np.array(y, dtype=float)
    coef, *_ = np.linalg.lstsq(X, Y, rcond=None)
    a, b, c = coef
    pred = X @ coef
    ss_res = float(((Y - pred) ** 2).sum())
    ss_tot = float(((Y - Y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    mae = float(np.abs(Y - pred).mean())

    print("\n========== 拟合结果 ==========")
    print(f"  样本数      : {len(rows)}")
    print(f"  A (中文/字) : {a:.4f}")
    print(f"  B (英文/词) : {b:.4f}")
    print(f"  C (常数)    : {c:.4f}")
    print(f"  R²          : {r2:.4f}  (期望 > 0.95)")
    print(f"  平均绝对误差: {mae:.2f} token")

    if r2 < 0.95:
        print("\n⚠ R² 偏低，建议增大 --max 或补充更多代表性论文后重跑，再硬编码。")

    print("\n========== 粘贴到 src/config.py 出厂默认 ==========")
    print(f"CHUNK_CALIB_A = _get_typed(\"CHUNK_CALIB_A\", fallback={a:.4f}, cast=float)")
    print(f"CHUNK_CALIB_B = _get_typed(\"CHUNK_CALIB_B\", fallback={b:.4f}, cast=float)")
    print(f"CHUNK_CALIB_C = _get_typed(\"CHUNK_CALIB_C\", fallback={c:.4f}, cast=float)")
    print('CHUNK_CALIBRATED = _get_typed("CHUNK_CALIBRATED", fallback=True, cast=bool)')
    print("\n（脚本不写 yaml/.env；请人工核对 R² 后再把上面四行硬编码进 config 并提交。）")


if __name__ == "__main__":
    main()
