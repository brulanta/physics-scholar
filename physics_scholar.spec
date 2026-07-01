# physics_scholar.spec
# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path
import sysconfig

python_dlls = Path(sysconfig.get_path('data')) / 'DLLs'
if not python_dlls.exists():
    python_dlls = Path(sys.executable).parent / 'DLLs'

extra_dlls = []
for pattern in [
    'libssl*.dll',
    'libcrypto*.dll',
    'libffi*.dll',
    'sqlite3.dll',
    '_ctypes*.pyd',
    '_ssl*.pyd',
    '_sqlite3*.pyd',
    '_hashlib*.pyd',
    '_decimal*.pyd',
    '_overlapped*.pyd',
    '_multiprocessing*.pyd',
    '_queue*.pyd',
    '_uuid*.pyd',
]:
    found = list(python_dlls.glob(pattern))
    extra_dlls.extend([(str(p), '.') for p in found])

ROOT = Path(SPECPATH)

a = Analysis(
    ['app.py'],
    pathex=[str(ROOT)],
    binaries=extra_dlls,
    datas=[
        # dist/ = 前端构建产物（vite outDir）。分发前务必先跑 `python scripts/build_release.py`：
        # 它会先 npm run build 刷新前端、再清掉上一轮的 dist/PhysicsScholar/ 旧 bundle，
        # 最后才 pyinstaller。直接跑 pyinstaller 会打进旧前端，且递归进 dist/PhysicsScholar/
        # 刷一大片「Ignoring non-existent resource」WARNING。
        (str(ROOT / 'dist'), 'dist'),
        (str(ROOT / 'config'), 'config'),
        (str(ROOT / 'src'), 'src'),
        (str(ROOT / 'src' / 'rag' / 'prompts' / 'profiles'), 'src/rag/prompts/profiles'),
        (str(ROOT / 'frontend' / 'public' / 'favicon.ico'), 'assets'),  # 打包到_internal/assets/下
    ],
    hiddenimports=[
        # 多路召回 BM25（纯 Python + numpy，无编译扩展）
        'rank_bm25',
        # 系统托盘
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
        'PIL.IcoImagePlugin',
        # FastAPI / Starlette
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'starlette.routing',
        'starlette.staticfiles',
        # ChromaDB
        'chromadb',
        'chromadb.api',
        'chromadb.api.client',
        'chromadb.db.impl',
        'chromadb.db.impl.sqlite',
        'chromadb.segment',
        'chromadb.segment.impl',
        'chromadb.segment.impl.vector',
        'chromadb.segment.impl.vector.local_hnsw',
        'chromadb.segment.impl.metadata',
        'chromadb.segment.impl.metadata.sqlite',
        'chromadb.telemetry',
        'chromadb.telemetry.product',
        'chromadb.telemetry.product.posthog',
        'chromadb.api.rust',
        'chromadb.api.shared_system_client',
        'chromadb.api.rust',
        'chromadb.api.shared_system_client',
        'chromadb.migrations',
        'chromadb.migrations.embeddings_queue',
        'chromadb.migrations.metadb',
        'chromadb.db.migrations',
        'chromadb.execution',
        'chromadb.execution.executor',
        'chromadb.execution.executor.local',
        'chromadb.quota',
        # LangChain
        'langchain',
        'langchain_core',
        'langchain_openai',
        'langchain_chroma',
        'langchain_text_splitters',
        'langgraph',
        # MCP（stdio server 子进程 + client）——frozen 子进程靠这些 import 起 server
        'mcp',
        'mcp.types',
        'mcp.server',
        'mcp.server.fastmcp',
        'mcp.server.fastmcp.server',
        'mcp.server.stdio',
        'mcp.server.lowlevel',
        'mcp.server.lowlevel.server',
        'mcp.client',
        'mcp.client.stdio',
        'mcp.client.session',
        'mcp.shared',
        'mcp.shared.memory',
        'mcp.shared.session',
        # Windows stdio 句柄工具（mcp 平台分支，PyInstaller 易漏）
        'mcp.os',
        'mcp.os.win32',
        'mcp.os.win32.utilities',
        # langchain-mcp-adapters（agent 侧 client 适配）
        'langchain_mcp_adapters',
        'langchain_mcp_adapters.client',
        'langchain_mcp_adapters.tools',
        'langchain_mcp_adapters.sessions',
        # anyio asyncio 后端（mcp/anyio 运行期动态选择，PyInstaller 静态分析常漏）
        'anyio',
        '_anyio_backend',
        'anyio._backends',
        'anyio._backends._asyncio',
        # 其他
        'pymupdf',
        'pydantic',
        'yaml',
        'dotenv',
        'feedparser',
        'tiktoken',
        'tiktoken_ext',
        'tiktoken_ext.openai_public',
        # src
        'src',
        'src.main',
        'src.config',
        'src.llm',
        'src.api',
        'src.api.routes',
        'src.core',
        'src.core.chunker',
        'src.core.extractor',
        'src.core.hash_file',
        'src.core.ingestor',
        'src.core.init_SQLite',
        'src.core.parser',
        'src.core.registry',
        'src.core.trim_thinking',
        'src.core.chroma_gen',
        'src.rag',
        'src.rag.chain',
        'src.rag.graph',
        'src.rag.memory',
        'src.rag.prompt',
        'src.rag.retriever',
        'src.rag.prompts',
        'src.rag.prompts.builder',
        'src.rag.prompts.plugins',
        'src.rag.prompts.modules',
        'src.rag.prompts.modules.discuss',
        'src.rag.prompts.modules.normal',
        'src.rag.prompts.modules.shared',
        'src.rag.tools',
        'src.rag.tools.arxiv_tool',
        'src.rag.tools.jina_tool',
        'src.rag.tools.lookup_local_paper_id',
        'src.rag.tools.openalex_tool',
        'src.rag.tools.rag_tool',
        'src.rag.tools.s2_tool',
        # MCP 接线（client 单例 + 开关 + 3 个 stdio server 子进程入口）
        'src.rag.mcp_client',
        'src.rag.tool_runtime',
        'src.mcp_servers',
        'src.mcp_servers.local_server',
        'src.mcp_servers.web_server',
        'src.mcp_servers.jina_server',
        'src.utils',
        'src.utils.logger',
    ],
    excludes=[
        # torch全家桶，单独分发模型后不需要
        'torch',
        'torchvision',
        'torchaudio',
        'transformers',
        'sentence_transformers',
        'huggingface_hub',
        'tokenizers',
        'safetensors',
        # 开发工具
        'pytest',
        'black',
        'ruff',
        'mypy_extensions',
        # jupyter相关
        'IPython',
        'ipykernel',
        'notebook',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PhysicsScholar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(ROOT / 'frontend' / 'public' / 'favicon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='PhysicsScholar',
)