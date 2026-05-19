# src/config.py
from pathlib import Path
from dotenv import load_dotenv
import os, yaml

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

if _yaml_path.exists():
    with open(_yaml_path, encoding="utf-8") as f:
        _cfg = yaml.safe_load(f) or {}


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


# ── 主 LLM ───────────────────────────────────────────────
MAIN_LLM_API_KEY = _get("MAIN_API_KEY", "main_llm", "api_key")
MAIN_LLM_BASE_URL = _get("MAIN_BASE_URL", "main_llm", "base_url")
MAIN_LLM_MODEL = _get("MAIN_MODEL", "main_llm", "model")

# ── 副 LLM（slice打分用，空则回退到主LLM）─────────────────
SLICE_LLM_API_KEY = _get("SLICE_API_KEY", "slice_llm", "api_key") or MAIN_LLM_API_KEY
SLICE_LLM_BASE_URL = (
    _get("SLICE_BASE_URL", "slice_llm", "base_url") or MAIN_LLM_BASE_URL
)
SLICE_LLM_MODEL = _get("SLICE_MODEL", "slice_llm", "model") or MAIN_LLM_MODEL

# ── 第三方工具 Key ────────────────────────────────────────
JINA_API_KEY = _get("JINA_API_KEY", "tools", "jina_api_key")
S2_API_KEY = _get("S2_API_KEY", "tools", "s2_api_key")

# ── LLM extra参数（DeepSeek特有，可选）───────────────────
DEEPSEEK_EXTRA_BODY: dict = {
    "thinking": {"type": "disabled"},
    "parallel_tool_calls": False,
}
