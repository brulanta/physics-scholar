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
        (str(ROOT / 'dist'), 'dist'),
        (str(ROOT / 'config'), 'config'),
        (str(ROOT / 'src'), 'src'),  # 整个src强制打包
        (str(ROOT / 'src' / 'rag' / 'prompts' / 'profiles'), 'src/rag/prompts/profiles'),
    ],
    hiddenimports=[
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
    console=True,   # 先保留控制台窗口，方便调试，确认没问题后改False
    icon=None,      # 有icon文件的话填路径
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