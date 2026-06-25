# src/config.py
from pathlib import Path
from dotenv import load_dotenv
import os, yaml
from pydantic import BaseModel

load_dotenv()

# ── 路径 ──────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"
PDF_DIR = DATA_DIR / "pdfs"
CHROMA_DIR = DATA_DIR / "chroma_db"
DB_PATH = DATA_DIR / "SQLite" / "app.db"

PDF_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── 加载 user_config.yaml ─────────────────────────────────
_cfg: dict = {}
_yaml_path = ROOT / "config" / "user_config.yaml"


def _load_yaml() -> dict:
    if _yaml_path.exists():
        with open(_yaml_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


_cfg = _load_yaml()


def _get(env_key: str, *yaml_keys: str, fallback: str = "") -> str:
    """优先读 .env（开发），再读 yaml（用户部署），最后返回 fallback。"""
    if val := os.getenv(env_key):
        return val
    node = _cfg
    for k in yaml_keys:
        if not isinstance(node, dict):
            return fallback
        node = node.get(k, fallback)
    return node if isinstance(node, str) else fallback


def _get_typed(env_key: str, *yaml_keys: str, fallback, cast):
    """同 _get，但支持非字符串类型（int/float/bool）。

    .env 读到的恒为字符串，yaml 读到的可能是原生 int/float/bool；统一用 cast 转换。
    不传 yaml_keys 时只读 .env（用于纯开发态、不暴露给前端 yaml 的配置）。
    取不到或转换失败时返回 fallback。
    """
    raw = os.getenv(env_key)
    if raw is None and yaml_keys:
        node = _cfg
        for k in yaml_keys:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(k)
        raw = node
    if raw is None or raw == "":
        return fallback
    try:
        if cast is bool:
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
        return cast(raw)
    except (ValueError, TypeError):
        return fallback


# ── 常量（模块级，供其他模块 import）─────────────────────
MAIN_LLM_API_KEY = _get("MAIN_API_KEY", "main_llm", "api_key")
MAIN_LLM_BASE_URL = _get("MAIN_BASE_URL", "main_llm", "base_url")
MAIN_LLM_MODEL = _get("MAIN_MODEL", "main_llm", "model")

SUB_LLM_API_KEY = _get("SUB_API_KEY", "sub_llm", "api_key") or MAIN_LLM_API_KEY
SUB_LLM_BASE_URL = _get("SUB_BASE_URL", "sub_llm", "base_url") or MAIN_LLM_BASE_URL
SUB_LLM_MODEL = _get("SUB_MODEL", "sub_llm", "model") or MAIN_LLM_MODEL

JINA_API_KEY = _get("JINA_API_KEY", "tools", "jina_api_key")
S2_API_KEY = _get("S2_API_KEY", "tools", "s2_api_key")
OPENALEX_EMAIL = _get("OPENALEX_EMAIL", "tools", "openalex_email")
OPENALEX_API_KEY = _get("OPENALEX_API_KEY", "tools", "openalex_api_key")  # 新增

DEEPSEEK_EXTRA_BODY: dict = {
    "thinking": {"type": "disabled"},
    "parallel_tool_calls": False,
}

EMBEDDING_API_KEY = _get("EMBEDDING_API_KEY", "embedding", "api_key")
EMBEDDING_BASE_URL = (
    _get("EMBEDDING_BASE_URL", "embedding", "base_url")
    or "https://api.siliconflow.cn/v1"
)
EMBEDDING_MODEL = _get("EMBEDDING_MODEL", "embedding", "model") or "BAAI/bge-m3"

# ── 切片（chunker）── 纯开发态配置，不进 yaml、不暴露前端 ──────────
# 切片长度按 bge-m3 token 语义容量度量；length_function 用本地校准公式估算 token：
#   est_tokens ≈ CHUNK_CALIB_A·中文字数 + CHUNK_CALIB_B·英文词数 + CHUNK_CALIB_C
# 中英文分路线仅作用于 size/overlap（让两种语言语义容量相近），分隔符统一。
# 只读 .env（开发期临时调参，如召回测试 bp）> 下面的硬编码出厂默认值。
CHUNK_SIZE_ZH = _get_typed("CHUNK_SIZE_ZH", fallback=384, cast=int)
CHUNK_OVERLAP_ZH = _get_typed("CHUNK_OVERLAP_ZH", fallback=76, cast=int)
CHUNK_SIZE_EN = _get_typed("CHUNK_SIZE_EN", fallback=384, cast=int)
CHUNK_OVERLAP_EN = _get_typed("CHUNK_OVERLAP_EN", fallback=76, cast=int)

# 校准系数：当前为经验占位（中文≈1.05 token/字、英文≈1.3 token/词），非真实校准。
# 打包分发前须跑 scripts/calibrate_tokenizer.py 用真实论文样本拟合，把结果硬编码到此处
# 出厂默认值并将 CHUNK_CALIBRATED fallback 改为 True（不写 yaml，不做前端 UI）。
CHUNK_CALIB_A = _get_typed("CHUNK_CALIB_A", fallback=1.05, cast=float)
CHUNK_CALIB_B = _get_typed("CHUNK_CALIB_B", fallback=1.30, cast=float)
CHUNK_CALIB_C = _get_typed("CHUNK_CALIB_C", fallback=0.0, cast=float)
CHUNK_CALIBRATED = _get_typed("CHUNK_CALIBRATED", fallback=False, cast=bool)


# ── 热重载 ────────────────────────────────────────────────
def reload_config() -> None:
    """重新读取 yaml，刷新模块级常量。供 POST /api/config 调用后使用。"""
    global _cfg
    global MAIN_LLM_API_KEY, MAIN_LLM_BASE_URL, MAIN_LLM_MODEL
    global SUB_LLM_API_KEY, SUB_LLM_BASE_URL, SUB_LLM_MODEL
    global JINA_API_KEY, S2_API_KEY, OPENALEX_EMAIL
    global EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL
    # 注：chunker.* 为纯开发态配置（只读 .env > 硬编码默认），不进 yaml，
    # 故无需在 reload_config（yaml 热重载）中重读。

    _cfg = _load_yaml()

    MAIN_LLM_API_KEY = _get("MAIN_API_KEY", "main_llm", "api_key")
    MAIN_LLM_BASE_URL = _get("MAIN_BASE_URL", "main_llm", "base_url")
    MAIN_LLM_MODEL = _get("MAIN_MODEL", "main_llm", "model")

    SUB_LLM_API_KEY = _get("SUB_API_KEY", "sub_llm", "api_key") or MAIN_LLM_API_KEY
    SUB_LLM_BASE_URL = _get("SUB_BASE_URL", "sub_llm", "base_url") or MAIN_LLM_BASE_URL
    SUB_LLM_MODEL = _get("SUB_MODEL", "sub_llm", "model") or MAIN_LLM_MODEL

    JINA_API_KEY = _get("JINA_API_KEY", "tools", "jina_api_key")
    S2_API_KEY = _get("S2_API_KEY", "tools", "s2_api_key")
    OPENALEX_EMAIL = _get("OPENALEX_EMAIL", "tools", "openalex_email")

    EMBEDDING_API_KEY = _get("EMBEDDING_API_KEY", "embedding", "api_key")
    EMBEDDING_BASE_URL = (
        _get("EMBEDDING_BASE_URL", "embedding", "base_url")
        or "https://api.siliconflow.cn/v1"
    )
    EMBEDDING_MODEL = _get("EMBEDDING_MODEL", "embedding", "model") or "BAAI/bge-m3"


# ── 供接口使用的读写函数 ───────────────────────────────────
def get_config_dict() -> dict:
    """返回当前 yaml 内容（不含 .env 覆盖），供前端展示。"""
    return _load_yaml()


def save_config_dict(data: dict) -> None:
    """将 data 写入 yaml，并热重载常量。"""
    _yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(_yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(
            data, f, allow_unicode=True, default_flow_style=False, sort_keys=False
        )
    reload_config()
