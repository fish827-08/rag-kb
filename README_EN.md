# rag-kb — Local-First Agent Memory & Knowledge Service

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)]()

**kb** is a local-first, completely free memory & knowledge service for AI agents.
One resident process (`python -m kb serve`) exposes both **REST** and **MCP** protocols,
giving Claude Code / Cursor / TraeWork / custom agents persistent memory, document &
webpage ingestion, and hybrid retrieval (dense vectors + BM25, fused with RRF).

**Fully functional without any LLM** — memory writes, document ingestion, and hybrid
retrieval never call a language model. Optionally configure local Ollama or a cloud API
to unlock `/ask` (RAG question answering).

> 中文文档见 [README.md](README.md)（本文件为英文版，内容同步维护）。

## Why kb

- **Local-first & offline-capable** — models and data stay on your machine; retrieval
  works with the network completely cut off
- **Zero extra infrastructure** — a single process; ChromaDB embedded, no external
  vector DB service, no container required
- **Hybrid retrieval that speaks Chinese** — BGE-M3 dense vectors + BM25 (jieba
  search-mode tokenization), fused via RRF
- **Dual protocol** — REST API for scripts/tools, native MCP server for AI clients,
  mounted from the same process
- **Privacy guardrails** — sensitive namespaces are answered strictly locally;
  `/ask` routes local-first with optional cloud fallback (opt-in)

## Quick Start (Windows PowerShell)

```powershell
# 1. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the service (listens on http://127.0.0.1:8000)
python -m kb serve
```

Health check:

```powershell
curl http://127.0.0.1:8000/api/v1/healthz
```

> First startup lazily loads the embedding model (default `BAAI/bge-m3`, ~2 GB —
> download it beforehand). Without an LLM configured, the service still starts
> normally; `/ask` returns 503 with setup instructions.

## MCP Integration

MCP endpoint (streamable HTTP): `http://127.0.0.1:8000/mcp/`

**Claude Code** — this repo ships a project-level `.mcp.json`, so Claude Code
auto-mounts kb when launched from this directory. Or add it globally:

```powershell
claude mcp add --transport http kb http://127.0.0.1:8000/mcp/
```

**Cursor / TraeWork / any MCP-capable client** — add to your MCP config:

```json
{
  "mcpServers": {
    "kb": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp/"
    }
  }
}
```

Available MCP tools: `write_memory` / `search_memory` / `read_memory` /
`update_memory` / `delete_memory` / `add_document` / `add_webpage` / `ask_kb`.

> With `KB_API_KEY` auth enabled, clients must send `Authorization: Bearer <key>` /
> `X-API-Key` headers — see [USER_GUIDE §5.2](docs/USER_GUIDE.md) (Chinese) for
> per-client configuration.

## REST API Reference

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/memories` | Write a memory `{content, tags?, source?, namespace?}` |
| GET | `/api/v1/memories` | List memories; filters: `type/tag/source/q/limit/offset` |
| GET | `/api/v1/memories/{id}` | Read one memory |
| PATCH | `/api/v1/memories/{id}` | Update content or tags |
| DELETE | `/api/v1/memories/{id}` | Delete one memory |
| POST | `/api/v1/search` | Hybrid search `{query, top_k?, mode?, type?, tag?}` — mode: `hybrid/vector/keyword` |
| POST | `/api/v1/documents` | Ingest a document: multipart `file` or JSON `{"path": "..."}` |
| GET | `/api/v1/documents` | List ingested documents (grouped by source) |
| DELETE | `/api/v1/documents/{source}` | Delete all records of a document |
| POST | `/api/v1/ingest/web` | Ingest a webpage `{url}` — fetch, extract, chunk, store |
| POST | `/api/v1/ask` | RAG answering `{question}`; 503 if no LLM configured |
| GET | `/api/v1/healthz` | Health check & service stats |

Example:

```powershell
# Write a memory
curl -X POST http://127.0.0.1:8000/api/v1/memories `
  -H "Content-Type: application/json" `
  -d '{"content": "User prefers dark theme", "tags": ["preference"]}'

# Hybrid search
curl -X POST http://127.0.0.1:8000/api/v1/search `
  -H "Content-Type: application/json" `
  -d '{"query": "UI preferences", "top_k": 5}'
```

## Configuration

All settings use the `KB_` environment prefix or a local `.env` file. See
[`.env.example`](.env.example) for the full key list (copy to `.env` and fill in;
`.env` is gitignored — real keys never enter version control).

| Key | Default | Description |
|---|---|---|
| `KB_LLM_MODE` | `auto` | `local` (Ollama only) / `auto` (local-first, cloud fallback) / `cloud` |
| `KB_DEVICE` | empty | Embedding device: empty = auto-detect; `cpu` / `cuda` to override |
| `KB_WATCH_DIR` | `data` | Directory watched in serve mode; empty or `.` = disabled |
| `KB_DATA_DIR` | `kb_data` | Data root (ChromaDB, runtime state) |
| `KB_API_HOST` / `KB_API_PORT` | `127.0.0.1` / `8000` | REST & MCP listen address |
| `KB_EMBED_MODEL` | `BAAI/bge-m3` | Embedding model |
| `KB_LLM_MODEL` | `qwen3:4b` | Local Ollama model name |
| `KB_API_KEY` | empty | Empty = no auth (zero friction on loopback); non-empty = Bearer/X-API-Key auth |
| `KB_SENSITIVE_NAMESPACES` | empty | Comma-separated namespaces forced to answer locally |

## Repository Layout

```
kb/            service source (config / models / embedder / storage / bm25 /
               retriever / service / llm / ingest / watcher / api / mcp / cli)
tests/         acceptance tests
orchestra/     multi-agent collaboration system (maintenance mode; see below)
docs/          design specs, node plans, user guide (Chinese)
```

Also in this repo: **agent-orchestra** — an experimental multi-agent collaboration
system built on kb as a shared task board (coordinator splits cards, workers claim
and execute, feedback loop with quotas). Now in maintenance mode, kept as the
author's own scaffolding. Documentation is Chinese-only; see
[orchestra/protocol.md](orchestra/protocol.md).

## License

[Apache-2.0](LICENSE) — commercial use permitted.

## Links

- [README.md](README.md) — Chinese documentation (primary)
- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) — user guide (Chinese)
- [PROJECT.md](PROJECT.md) — AI handover doc / project status (Chinese)
