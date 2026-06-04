# PhysicsScholar

> A local academic agent for microwave photonics researchers — RAG Q&A · Literature Search · Academic Discussion · Offline-ready

[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green)](https://fastapi.tiangolo.com/)
[![Vue3](https://img.shields.io/badge/Vue-3-brightgreen)](https://vuejs.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Agent-orange)](https://github.com/langchain-ai/langgraph)

---

## Overview

PhysicsScholar is a locally-running academic research assistant agent, designed for microwave photonics researchers. It can be adapted to other research domains by swapping the domain prompt module.

**Core idea:** researchers upload their own papers to build a private RAG knowledge base. A LangGraph-powered agent integrates arXiv / Semantic Scholar / OpenAlex retrieval tools and Jina full-text reading to support literature Q&A, citation tracing, academic discussion, and code assistance — all running locally as a Windows executable, no Python environment required.

**What makes it different:**

- Purpose-built for research workflows, not a general chatbot
- Private paper data stays on-device; no cloud knowledge base
- Ships as a self-contained exe — double-click to start, open in browser

---

## Features

### Core

| Feature                        | Description                                                                                                                                            |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **PDF Upload & Parsing**       | Drag-and-drop or click to upload; auto-chunking, embedding, and indexing; concurrent multi-file support                                                |
| **RAG Q&A + Citation Tracing** | Retrieves relevant chunks, injects into prompt, returns answers annotated with source title and location                                               |
| **Bilingual Citation**         | Displays English original alongside Chinese translation for every cited passage (toggleable in frontend)                                               |
| **Multi-turn Memory**          | Context-aware within session; supports continuous follow-up questions                                                                                  |
| **Structured Academic Prompt** | Strict CoT + role anchoring + mandatory citation mechanism — grounds answers in verifiable sources rather than hallucination                           |
| **Code Generation**            | Prompt-level capability: generates MATLAB / Python code when appropriate, with enforced output correctness (generation only, no server-side execution) |

### Agent Tools

The agent decides which tools to call each turn based on CoT reasoning (hard limit: 6 tool calls per turn):

| Tool                      | Role                        | Description                                                                                                                                    |
| ------------------------- | --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| **Local RAG Retrieval**   | Primary                     | Vector-searches user-indexed papers; supports both full-library and single-paper targeted retrieval                                            |
| **Local Paper ID Lookup** | Pre-step                    | Resolves fuzzy paper descriptions (partial title, author, year) to exact doc_id for targeted RAG                                               |
| **Semantic Scholar**      | Primary search              | Default for external retrieval; provides citation count, venue, tldr, and rich metadata                                                        |
| **OpenAlex**              | Abstract fill-in / fallback | Batch-completes missing S2 abstracts; upgrades to primary search when S2 is unavailable                                                        |
| **arXiv**                 | Final fallback / preprints  | Last-resort retrieval; dedicated channel for latest preprints (S2/OpenAlex have multi-week indexing lag)                                       |
| **Jina Full-text Reader** | Deep reading                | Reads full PDF when abstracts are insufficient; two modes: head truncation (no secondary LLM) and full-text chunk scoring with targeted recall |

### Engineering

| Feature                         | Description                                                                                                       |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| **In-app Config Page**          | Fill in Key and Base URL in the settings panel, click "Fetch Models", select and save; takes effect after restart |
| **Paper Library Management**    | Keyword search, sorting, ingestion status visualization, and deletion (syncs disk + vector store)                 |
| **System Tray**                 | Minimizes to tray; right-click to restart or quit                                                                 |
| **Multi-session Management**    | Create, switch, rename, and delete sessions; data persisted in SQLite                                             |
| **Message Tree & Branching**    | Regeneration and message editing both preserve branches; navigate versions with `‹ 1/2 ›` controls                |
| **Theme & Font**                | Light / dark mode toggle; multiple font options                                                                   |
| **Backend Unavailable Overlay** | Global overlay displayed when the backend goes offline, with user instructions                                    |

---

## Architecture

```
┌─────────────────────────────────────────────┐
│                  Vue 3 Frontend              │
│  Chat UI · Paper Manager · Config · Sessions │
│  KaTeX · Markdown · Syntax Highlighting      │
│  Light/Dark Theme · Font Switch · Bilingual Toggle │
└──────────────────┬──────────────────────────┘
                   │ HTTP (same port, relative-path API)
┌──────────────────▼──────────────────────────┐
│              FastAPI Backend                 │
│  Routers · Session Mgmt · File · Config      │
│  Serves Vue3 build output (dist/)            │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│           LangGraph Agent                    │
│                                              │
│  ┌─────────┐   ┌──────────────────────────┐ │
│  │ Main LLM │   │  Tool Set (CoT-driven)   │ │
│  │(OpenAI- │   │  RAG · S2 · OpenAlex    │ │
│  │compatible│   │  arXiv · Jina            │ │
│  └─────────┘   └──────────────────────────┘ │
│                                              │
│  ┌──────────────┐   ┌──────────────────────┐ │
│  │   ChromaDB   │   │       SQLite          │ │
│  │ (vector search)  │ Sessions · Messages   │ │
│  │              │   │ Paper Registry        │ │
│  └──────────────┘   └──────────────────────┘ │
└─────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│           External APIs                      │
│  Main LLM (DeepSeek / Gemini / any OpenAI-compat) │
│  Secondary LLM (Jina chunk scoring; low-cost, large-context recommended) │
│  SiliconFlow Embedding (BAAI/bge-m3, free)   │
│  Jina Reader · arXiv · S2 · OpenAlex        │
└─────────────────────────────────────────────┘
```

### Agent Workflow

```
User message
  → LangGraph: enter LLM node — CoT analyzes intent + identifies information gaps
  → If tool needed: jump to Tool node, result returns to LLM node
  → LLM node CoT again: enough information? Call another tool?
  → Loop (LLM ↔ Tool), max 6 tool calls then forced to answer
  → Every tool call must include CoT; calls without reasoning are rejected
  → LLM generates answer: all external sources annotated with inline citation markers
  → Frontend renders markers + footer Reference section, typewriter display effect

Note: tool results are only visible within the current graph cycle.
      The next user message starts a new cycle with a fresh 6-call budget;
      tool results not written into the answer are not carried forward.
```

---

## Project Structure

```
physics-scholar/
├── app.py                        # Package entry: launches FastAPI + system tray
├── physics_scholar.spec          # PyInstaller packaging config
├── requirements.txt
├── .env.example                  # Env var template (development; takes priority over yaml)
│
├── src/                          # Backend core
│   ├── main.py                   # FastAPI application entry
│   ├── config.py                 # Config loader (.env priority > user_config.yaml)
│   ├── llm.py                    # LLM instance management
│   ├── api/
│   │   └── routes.py             # All API routes
│   ├── core/                     # Document processing
│   │   ├── parser.py             # PDF parsing (pymupdf)
│   │   ├── chunker.py            # Text chunking
│   │   ├── ingestor.py           # Ingestion (embedding + ChromaDB write)
│   │   ├── extractor.py          # Metadata extraction
│   │   ├── registry.py           # Paper registry (SQLite)
│   │   ├── hash_file.py          # File deduplication
│   │   ├── init_SQLite.py        # Database initialization
│   │   └── trim_thinking.py      # Strip LLM chain-of-thought from output
│   ├── rag/                      # RAG and Agent core
│   │   ├── graph.py              # LangGraph agent graph definition
│   │   ├── chain.py              # Conversation chain
│   │   ├── retriever.py          # Vector retrieval
│   │   ├── memory.py             # Multi-turn conversation memory
│   │   ├── prompt.py             # Prompt entry point
│   │   ├── prompts/              # Modular prompt system
│   │   │   ├── builder.py        # Prompt assembler
│   │   │   ├── plugins.py        # Plugin registration
│   │   │   ├── profiles/         # Mode configs (normal / discuss / debug)
│   │   │   └── modules/          # Prompt modules
│   │   │       ├── shared/       # Shared modules (role, constraints, citation format, etc.)
│   │   │       ├── normal/       # Standard Q&A mode
│   │   │       └── discuss/      # Academic discussion mode
│   │   └── tools/                # Agent tools
│   │       ├── rag_tool.py                # Local RAG retrieval
│   │       ├── arxiv_tool.py              # arXiv search
│   │       ├── s2_tool.py                 # Semantic Scholar search
│   │       ├── openalex_tool.py           # OpenAlex search
│   │       ├── jina_tool.py               # Jina full-text reader
│   │       └── lookup_local_paper_id.py   # Local paper ID lookup
│   └── utils/
│       └── logger.py             # Logging config
│
├── frontend/                     # Vue 3 + Vite frontend
│   ├── src/
│   │   ├── api/                  # Backend request wrappers
│   │   ├── components/
│   │   │   ├── Chat/             # Chat interface components
│   │   │   ├── Paper/            # Paper management components
│   │   │   ├── Settings/         # Settings page components
│   │   │   └── Sidebar/          # Sidebar components
│   │   ├── store/                # Global state management
│   │   ├── views/                # Page views
│   │   └── utils/                # Utility functions
│   └── public/
│       └── favicon.svg
│
├── config/
│   └── user_config.yaml.example  # User config template (production / packaged deployment)
│
├── data/                         # Runtime data (gitignored)
│   ├── pdfs/                     # User-uploaded PDF files
│   ├── chroma_db/                # Vector store
│   └── SQLite/                   # Paper registry database
│
├── seed_builder/                 # Seed library build scripts (dev only)
├── eval_framework/               # Evaluation framework (dev only)
└── tests/                        # Automated tests
```

---

## Getting Started (Development)

### Requirements

- Python 3.10+
- Node.js 18+
- API keys: any OpenAI-compatible LLM provider; SiliconFlow ([register here](https://siliconflow.cn), bge-m3 is free)

### Backend

```bash
git clone https://gitee.com/a-leaf-boat-is-light/physics-scholar.git
cd physics-scholar

pip install -r requirements.txt

# Option A: .env file (recommended for development, higher priority)
cp .env.example .env          # edit .env and fill in your keys

# Option B: yaml config (for production / packaged deployment)
cp config/user_config.yaml.example config/user_config.yaml

# Start (default port 8000)
uvicorn src.main:app --reload
```

### Frontend (dev mode)

```bash
cd frontend
npm install
npm run dev
# vite.config.js proxy is pre-configured to forward API requests to :8000
```

### Frontend (production build)

```bash
cd frontend
npm run build
# After build, restart the backend; dist/ is served by FastAPI
# Access http://localhost:8000 — no separate frontend process needed
```

---

## Configuration

All configuration is done through the in-app **Settings** panel. Changes take effect after restarting the program.

| Field           | Description                                                                   | Required |
| --------------- | ----------------------------------------------------------------------------- | -------- |
| LLM API Key     | Key for any OpenAI-compatible provider                                        | ✅       |
| LLM Base URL    | Provider endpoint, e.g. `https://api.deepseek.com`                            | ✅       |
| LLM Model       | Click "Fetch Models", then select from dropdown                               | ✅       |
| Secondary LLM   | Used for Jina full-text chunk scoring; choose a low-cost, large-context model | ❌       |
| SiliconFlow Key | For embedding; free tier covers bge-m3                                        | ✅       |
| Jina Key        | Raises Jina RPM limit; works without it at 20 RPM                             | ❌       |
| S2 Key          | Raises Semantic Scholar rate limit; without it, uses shared pool              | ❌       |
| OpenAlex Email  | Providing an email improves OpenAlex request speed                            | ❌       |

> ⚠️ **Changing the embedding model invalidates the entire vector store** — all papers must be re-uploaded. The current version locks the model to BAAI/bge-m3 to prevent accidental data loss.

---

## Packaging

Built with PyInstaller onedir mode. Users unzip and run — no installation needed.

```bash
pyinstaller physics_scholar.spec
```

Distribution layout:

```
PhysicsScholar/
├── PhysicsScholar.exe    # Double-click to launch
└── _internal/
    ├── ...               # Python runtime and dependencies
    └── data/             # ChromaDB + SQLite + PDFs
```

On launch, a system tray icon appears and the default browser opens `http://localhost:57321`.

---

## Design Decisions

**From LangChain to LangGraph**

The project started with a LangChain Chain for conversation logic and JSON for storage. As the number of tools grew and serial tool-calling with per-step reasoning became necessary, the linear Chain structure hit its limits. The codebase migrated to LangGraph, with storage moving to SQLite in parallel. The graph structure makes control logic explicit at the definition level: the LLM ↔ Tool cycle, the 6-call hard limit with forced jump to the answer node, and mandatory CoT validation before each tool call are all expressed as graph-level constraints rather than scattered callbacks.

**Tiered External Search Design**

The three retrieval tools are not interchangeable fallbacks — they have distinct roles in a defined degradation chain. S2 is the primary tool (richest metadata: citation count, venue, tldr). OpenAlex handles abstract completion (higher abstract coverage than S2; batches missing abstracts in one call) and becomes the primary search when S2 is rate-limited. arXiv is the final fallback and the dedicated channel for latest preprints, since both S2 and OpenAlex have multi-week indexing delays for new uploads. The degradation chain is explicitly defined in the agent's tool-usage prompt.

**The Role of Local Paper ID Lookup**

`lookup_local_paper_id` resolves a user's fuzzy paper reference (partial title, author, year) into a precise doc_id, which is then passed to the RAG tool for single-paper targeted retrieval. This enables a "deep-dive on one paper" usage pattern: the agent first pins the target, then retrieves within its scope, avoiding vector search noise from the broader library.

**How Mode Switching Works**

Normal and Discuss modes are driven by a frontend toggle. The backend selects the corresponding prompt module based on the mode parameter passed in the request. The bilingual citation toggle works the same way. This design keeps all mode differences in the prompt layer — the graph structure does not need to branch for it.

**Why the Embedding Model Is Not User-selectable**

The vector store is tightly coupled to the embedding model; switching models requires full re-indexing. To prevent accidental data loss, the current version locks the model to BAAI/bge-m3. A migration mechanism may be added in a future release.

**Why Switch from Local sentence-transformers to SiliconFlow API**

The local torch dependency made the packaged binary several GB in size with slow cold-start — a poor experience for non-technical users. BAAI/bge-m3 via SiliconFlow API delivers comparable quality, dramatically reduces package size, and requires only free registration.

---

## Roadmap

- [x] PDF parsing and indexing (M2)
- [x] RAG Q&A + bilingual citation (M3)
- [x] LangGraph agent + tiered multi-tool calling (M6)
- [x] Academic discussion mode (M8)
- [x] Vue 3 frontend + multi-session management (M9)
- [x] Error handling & robustness: atomic ingestion rollback, exponential backoff, 429 circuit breaking, LLM auto-retry (M10)
- [x] PyInstaller exe packaging (M11)
- [ ] Seed paper library (curated microwave photonics corpus — in progress)

---

## Extensibility

The prompt system is modular. Modules under `src/rag/prompts/modules/` (role definition, constraints, citation format, domain knowledge framework, etc.) are maintained independently and decoupled from tool logic. To adapt PhysicsScholar for another research domain, replace the domain knowledge module — no changes to the tool chain required. (Note: a small amount of prefill logic lives in `graph.py`; if you restructure the chain-of-thought, check there too.)

Forks and adaptations are welcome.

---

## Notes

This project was developed independently as a personal engineering project.  
The repository is publicly available for learning, research, and portfolio demonstration purposes.
