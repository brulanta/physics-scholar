"""
模型连通性测试
==============
正式跑评测之前，验证所有模型都能正常响应。
每个模型发一条短消息，检查：网络可达、key有效、返回非空内容、延迟合理。

用法：
  python check_models.py
"""

import asyncio
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv

load_dotenv()
from evaluator import (
    DS_API_KEY,
    GEMINI_API_KEY,
    DS_BASE_URL,
    GEMINI_BASE_URL,
    JUDGE_MODELS,
    SUMMARY_MODEL,
    call_model,
)

TEST_PROMPT = "请用一句话解释什么是光子微波滤波器。"
TEST_SYSTEM = "You are a helpful assistant."
TIMEOUT_SECONDS = 30


async def check_one(model_cfg: dict) -> dict:
    name = model_cfg["name"]
    result = {
        "name": name,
        "model_id": model_cfg["model_id"],
        "provider": model_cfg["provider"],
        "status": None,
        "latency_s": None,
        "preview": None,
        "error": None,
    }

    t0 = time.monotonic()
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            output = await call_model(model_cfg, TEST_PROMPT, TEST_SYSTEM)

        latency = time.monotonic() - t0

        if not output or not output.strip():
            result["status"] = "ERROR"
            result["error"] = "返回内容为空"
        else:
            result["status"] = "OK"
            result["latency_s"] = round(latency, 2)
            result["preview"] = output.strip()[:80]

    except asyncio.TimeoutError:
        result["status"] = "TIMEOUT"
        result["error"] = f"超过 {TIMEOUT_SECONDS}s 无响应"
    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)

    return result


def print_result(r: dict):
    icon = {"OK": "✅", "ERROR": "❌", "TIMEOUT": "⏱️"}.get(r["status"], "?")
    print(f"\n{icon} [{r['name']}]  ({r['provider']} / {r['model_id']})")
    if r["status"] == "OK":
        print(f"   延迟：{r['latency_s']}s")
        print(f"   预览：{r['preview']}")
    else:
        print(f"   错误：{r['error']}")


async def main():
    print("=== PhysicsScholar 模型连通性测试 ===\n")

    # key配置检查
    warnings = []
    if not DS_API_KEY:
        warnings.append("⚠️  DEEPSEEK_API_KEY 未设置")
    if not GEMINI_API_KEY:
        warnings.append("⚠️  GEMINI_API_KEY 未设置")
    for w in warnings:
        print(w)
    if warnings:
        print()

    print(f"DeepSeek base_url : {DS_BASE_URL}")
    print(f"Gemini  base_url  : {GEMINI_BASE_URL}")
    print()

    all_models = JUDGE_MODELS
    print(f"待测模型：{len(all_models)} 个，并行发送测试消息...\n")

    results = await asyncio.gather(*[check_one(m) for m in all_models])

    for r in results:
        print_result(r)

    ok_count = sum(1 for r in results if r["status"] == "OK")
    err_count = len(results) - ok_count

    print("\n" + "=" * 40)
    print(f"结果：{ok_count}/{len(results)} 个模型正常")

    if err_count == 0:
        print("✅ 所有模型可用，可以开始正式评测。")
        sys.exit(0)
    else:
        print(f"❌ {err_count} 个模型异常，请检查后再运行 evaluator.py。")
        for r in results:
            if r["status"] != "OK":
                print(f"   - {r['name']}: {r['error']}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
