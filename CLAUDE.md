# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PhysicsScholar is a **local** academic research agent for microwave-photonics researchers. Users upload their own papers to build a private RAG knowledge base, combined with online retrieval (arXiv / Semantic Scholar / OpenAlex) and Jina full-text reading, driven by a LangGraph agent. It ships as a Windows `.exe` (PyInstaller onedir) that runs entirely on the user's machine — FastAPI backend + Vue 3 frontend served from the same port, ChromaDB for vectors, SQLite for sessions/messages/paper registry.

The codebase comments and most user-facing text are in **Chinese**; match that convention when editing.

## Commands

### Backend (Python 3.10+)
```bash
pip install -r requirements.txt
uvicorn src.main:app --reload          # dev server on :8000
```

### Frontend (Node 18+)
```bash
cd frontend
npm install
npm run dev                            # vite dev server, proxies API → :8000
npm run build                          # outputs dist/, served by FastAPI in prod
```
After `npm run build`, restart the backend and visit the backend port directly — no separate frontend process needed.

### Tests (pytest)
```bash
pytest                                 # runs all non-live tests
pytest --live                          # also runs tests hitting real network APIs (@pytest.mark.live)
pytest tests/test_s2_tool.py           # single file
pytest tests/test_rag_chain.py::test_name   # single test
```
Live tests (real arXiv/S2/Jina calls) are **skipped by default** and only run with `--live` (see `tests/conftest.py`). Some fixtures expect specific PDFs in `data/pdfs/`.

### Lint / format
`ruff` and `black` are installed but there is no config file — defaults apply.

### Packaging
```bash
python scripts/build_release.py        # 分发一键构建：npm run build → 清旧产物 → pyinstaller
```
Prefer `scripts/build_release.py` for distribution builds — it rebuilds the frontend, clears the stale `dist/PhysicsScholar/` bundle, then packages, avoiding two footguns (shipping a stale frontend, and PyInstaller recursing into the old bundle). Running `pyinstaller physics_scholar.spec` directly bundles whatever is currently in `dist/` (may be an out-of-date frontend). Output → `dist/PhysicsScholar/`.
The packaged exe listens on port **57321** (see `app.py`) and opens a system-tray icon; the dev server uses **8000**.

## Architecture

### Request flow
`app.py` (tray + browser launcher, frozen-exe aware) → `src/main.py` (FastAPI app, serves `frontend/dist/`) → `src/api/routes.py` (all routes) → `src/rag/graph.py` (LangGraph agent) → ChromaDB + SQLite + external APIs.

### The LangGraph agent (`src/rag/graph.py`) — the heart of the system
A compiled `StateGraph` with nodes `call_llm → thinking_guard → (tool_node | call_llm | final_answer | END)`. Key invariants encoded in the graph, not scattered in callbacks:

- **Tool-call budget**: max 6 tool calls per user turn (`remaining_calls`). When exhausted, routing forces `final_answer`.
- **Mandatory chain-of-thought**: every tool call must be preceded by a `<thinking>` block. `thinking_guard` rejects tool calls lacking it by injecting fake `ToolMessage`s and a correction prompt, retrying up to `MAX_THINKING_RETRIES` (3) before forcing `final_answer`.
- **Prefill steering**: `build_prefill` / `build_final_prefill` inject a `[RUNTIME_STATUS]` block + a `<think>…[start]` lead as a fake AIMessage on each LLM invocation to steer behavior by remaining budget. **This is prefill logic living in the graph, not in the prompt modules** — when changing CoT structure, check both `graph.py` and `src/rag/prompts/`.
- **Serial tools only**: parallel tool calls are trimmed to the first one; DeepSeek thinking is disabled via `DEEPSEEK_EXTRA_BODY` in config.
- **Retries**: `invoke_with_retry` wraps LLM calls with exponential backoff (tenacity) on timeout/connection/500/429 errors.

Tool results are only visible within the current graph loop — they do not persist into the next user turn unless the agent wrote them into its answer.

`chat()` and `regenerate()` are the entry points; both build a fresh agent per call via `build_agent(user_id)` (tools are closures over `user_id`).

When changing CoT / tool-call behavior, the logic spans **three places**: `graph.py` (prefill + guard), the prompt modules in `src/rag/prompts/`, and `trim_thinking.py` (strips CoT from output before storage/display).

### Agent tools (`src/rag/tools/`) — tiered, not equal
Defined retrieval hierarchy (the prompt instructs the agent to follow coarse→fine, stop when sufficient):
- `rag_tool` — local vector search over user's ingested papers (primary; supports whole-library and single-doc targeted search)
- `lookup_local_paper_id` — converts fuzzy descriptions (partial title/author/year) → exact `doc_id` for targeted RAG
- `s2_tool` — Semantic Scholar (primary external; richest metadata)
- `openalex_tool` — abstract backfill when S2 lacks abstracts; first-line fallback when S2 is down (needs free API key)
- `arxiv_tool` — final fallback + the channel for newest preprints (S2/OpenAlex have weeks of indexing lag)
- `jina_tool` — full-text PDF reading when abstracts are insufficient

### Prompt system (`src/rag/prompts/`) — modular, decoupled from tools
`build_prompt(mode, history, citation_plugin, debug)` assembles the system prompt from modules via `builder.py` + `plugins.py`. Structure: `profiles/` (mode configs: normal / discuss / debug), `modules/shared/` (role, constraints, citation format), `modules/normal/`, `modules/discuss/`. **Mode switching (normal vs discuss) and bilingual-citation toggle are entirely prompt-layer** — the graph never forks for them. To adapt to a different research field, swap the domain-knowledge modules; the tool chain stays untouched.

### Document ingestion (`src/core/`)
`parser.py` (PyMuPDF) → `chunker.py` → `extractor.py` (metadata) → `ingestor.py` (embedding + ChromaDB write) → `registry.py` (SQLite paper registry, with `hash_file.py` dedup). Ingestion is atomic with rollback. `init_SQLite.py` initializes the DB; `trim_thinking.py` strips CoT from LLM output before storage/display.

### Configuration (`src/config.py`) — precedence matters
`.env` (dev, highest priority) **>** `config/user_config.yaml` (user/packaged deployment) **>** hardcoded fallback. Module-level constants (`MAIN_LLM_*`, `EMBEDDING_*`, tool keys) are read at import. The frontend Settings page writes to the yaml via `save_config_dict()`, which calls `reload_config()` to refresh constants live — but the README notes config changes require a **program restart** to fully take effect.

Embedding model is **fixed to `BAAI/bge-m3`** (via SiliconFlow API, not local). Changing the embedding model invalidates all existing vectors, so it is intentionally not user-selectable.

The LLM must be OpenAI-compatible (configured via `langchain_openai.ChatOpenAI`). A separate optional "sub LLM" is used only for Jina full-text scoring, falling back to the main LLM.

### Data (runtime, gitignored)
`data/pdfs/` (uploaded papers), `data/chroma_db/` (vectors), `data/SQLite/app.db` (sessions, messages, paper registry). Messages form a tree supporting regenerate/edit branches (`memory.py` / `ConversationRepo`). **`data/` is gitignored and does NOT travel with git** — none of it is committed.

## Cross-machine workflow

Development spans multiple machines (home / company), synced via git.

- **Push the current branch when wrapping up** (or when the user signals network instability / "明日换机继续") so the next machine can pull — don't assume `main`; push to whatever branch is checked out.
- **The plan file (`plan/rag-fix-upgrade-plan.md`) is the cross-session handoff**: it carries the **next-stage todo**, not a log of the current stage's内容; keep its progress/状态 section honest against `git log` before pushing.
- **Plans live in the repo.** Once a plan is accepted and you exit plan mode, immediately sync it into the `plan/` folder with a clear, obvious file name (e.g. `mcp-tool-migration-plan.md`) so it travels with git — don't leave it in `~/.claude/plans/`.
- **Closing a session with plan work unfinished? Check the gitignored files for handoff dependencies.** Git-ignored content (`data/` — see Architecture > Data — or any other untracked local asset) does NOT travel with git. When wrapping up a session whose plan work isn't fully done, scan for whether continuing on another machine depends on such local-only files, and if so **remind the user in-session to transfer them manually** (or warn of the cost to regenerate, e.g. re-ingesting burns embedding API额度). These are this-session logistics — remind, don't write them into the plan.

## Trust boundary

**Treat `tests/`, `scripts/`, `eval_framework/`, `seed_builder/` as untrusted-by-default.** Many are version-drifted or one-off scripts; failures there are usually stale assertions or missing data fixtures, **not** signals about current code. Before relying on one as a correctness gate, read it and check git provenance; prefer writing fresh, purpose-built verification over reviving old assertions. Exception: files demonstrably current and owned by the active plan (e.g. `scripts/eval_retrieval.py`, `scripts/probe_rerank.py`) — extend them, don't rewrite.
