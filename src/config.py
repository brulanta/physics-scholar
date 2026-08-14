# src/config.py
from pathlib import Path
from dotenv import load_dotenv
import os, sys, yaml
from pydantic import BaseModel

load_dotenv()

# ── LangSmith 观测：dev-only 铁门 ──────────────────────────
# LangSmith 自动 trace（langsmith 包，已是 langchain 依赖；零额外埋点，主图/子 agent/
# 未来子图全嵌套自动 trace）只由 env 激活：LANGSMITH_TRACING=true + LANGSMITH_API_KEY
# + LANGSMITH_PROJECT（写 dev .env）。用途：T2 后系统折叠度升高，trace 是「真实 LLM IO」
# 的持续观测入口（测试管回归，trace 管「合不合场景」——互补，trace 不进 CI）。
# 铁门：.env 不入包（spec datas 只带 config/ yaml），packaged exe 默认无此 env = 不 trace。
# 加固：frozen 运行下若检测到 tracing 意外开着，强制清掉——用户数据绝不上云。
if getattr(sys, "frozen", False) and os.getenv("LANGSMITH_TRACING", "").lower() in (
    "true",
    "1",
    "yes",
):
    for _k in (
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        "LANGSMITH_ENDPOINT",
    ):
        os.environ.pop(_k, None)
    print(
        "[config] frozen 运行下检测到 LangSmith tracing 开启——已强制关闭（用户数据不上云）。",
        flush=True,
    )

# ── 路径 ──────────────────────────────────────────────────
# frozen（PyInstaller onedir）下 __file__ 指向 _MEIPASS 临时解压目录，data/chroma/SQLite
# 与用户 yaml 若写进那里会在重启/退出时丢失。故 ROOT 必须指向 **exe 真实所在目录**（可写、
# 持久），而非 _MEIPASS。注意：这与 app.py 的 ROOT（=_MEIPASS，供 sys.path 导入打包内 src）
# 故意取向相反——config.ROOT 只管「可写持久数据 + 用户 yaml」，只读打包资产（dist/profiles）
# 由各自的 __file__ 落在 _MEIPASS、不归 config.ROOT 管。dev 下走原逻辑（仓库根）。
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
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
CHUNK_CALIB_A = _get_typed("CHUNK_CALIB_A", fallback=0.9491, cast=float)
CHUNK_CALIB_B = _get_typed("CHUNK_CALIB_B", fallback=1.6108, cast=float)
CHUNK_CALIB_C = _get_typed("CHUNK_CALIB_C", fallback=3.1937, cast=float)
CHUNK_CALIBRATED = _get_typed("CHUNK_CALIBRATED", fallback=True, cast=bool)
## 这套系数是基于4篇中文+4篇英文、共200个样本拟合得到的,R²=0.9698（此点可以补入plan）

# ── 召回 / 重排 ── 纯开发态配置，不进 yaml、不暴露前端、不进 reload_config ──────
# 与 chunker.* 同一定位：只读 .env（开发期临时调参）> 下面硬编码出厂默认。
# 过取倍数：rag_tool 实际向量召回 k*RAG_FETCH_MULTIPLIER 个候选喂给重排，再截到 k。
# 探针实测（scripts/probe_rerank.py）：候选池越大、重排天花板越高（fetch 10→50 把
# 命中天花板从 0.30 抬到 0.70），故默认给足 10（k=5 → 过取 ~50）。
RAG_FETCH_MULTIPLIER = _get_typed("RAG_FETCH_MULTIPLIER", fallback=10, cast=int)

# 多路召回 kill switch：True 时走「向量 + BM25，RRF 融合」过取候选；False 回退纯向量过取。
# BM25 靠精确术语（MPF/滤波带宽/Q值等）命中稠密检索捞不到的题，把它们送进候选池抬高
# 重排天花板（探针证实残留 30% 是稠密过取 50 都埋在 rank>50 的 gold）。
RAG_HYBRID_ENABLED = _get_typed("RAG_HYBRID_ENABLED", fallback=True, cast=bool)

# 重排：硅基流动 bge-reranker-v2-m3，cross-encoder 直接对 (query, chunk) 打分。
# 凭证/URL 复用 embedding（同账号同 key 可调 /rerank，已据官方文档确认），故不设
# RERANK_BASE_URL/RERANK_API_KEY——_rerank 调用时直接引用 EMBEDDING_BASE_URL/KEY，
# 使前端改 embedding key（经 reload_config 刷新）后重排自动跟随。
RERANK_ENABLED = _get_typed("RERANK_ENABLED", fallback=True, cast=bool)
RERANK_MODEL = _get("RERANK_MODEL") or "BAAI/bge-reranker-v2-m3"
RERANK_TIMEOUT = _get_typed("RERANK_TIMEOUT", fallback=20, cast=int)


# ── 热重载 ────────────────────────────────────────────────
def reload_config() -> None:
    """重新读取 yaml，刷新模块级常量。供 POST /api/config 调用后使用。"""
    global _cfg
    global MAIN_LLM_API_KEY, MAIN_LLM_BASE_URL, MAIN_LLM_MODEL
    global SUB_LLM_API_KEY, SUB_LLM_BASE_URL, SUB_LLM_MODEL
    global JINA_API_KEY, S2_API_KEY, OPENALEX_EMAIL, OPENALEX_API_KEY
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
    OPENALEX_API_KEY = _get("OPENALEX_API_KEY", "tools", "openalex_api_key")  # 新增

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
