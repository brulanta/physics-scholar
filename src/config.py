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


# ── 热重载 ────────────────────────────────────────────────
def reload_config() -> None:
    """重新读取 yaml，刷新模块级常量。供 POST /api/config 调用后使用。"""
    global _cfg
    global MAIN_LLM_API_KEY, MAIN_LLM_BASE_URL, MAIN_LLM_MODEL
    global SUB_LLM_API_KEY, SUB_LLM_BASE_URL, SUB_LLM_MODEL
    global JINA_API_KEY, S2_API_KEY, OPENALEX_EMAIL
    global EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL

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
