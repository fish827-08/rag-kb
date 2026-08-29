# rag-kb — Local-First Agent Memory & Knowledge Service

<!-- mcp-name: io.github.fish827-08/kb-memory -->

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
- **Multi-agent identity isolation** — every access carries `agent_id` (use your task name, e.g.
  `TASK-0076`): personal memories (`memory`) are visible only to their owning agent (read/update/delete of
  another agent's memory is rejected); document/web chunks are shared knowledge visible to all
- **Access audit** — every write/read/update/delete/search/ask logs a JSON line to
  `logs/agent-audit/<client>__<project>__<task>.log` (one file per agent); humans query it via
  `GET /api/v1/audit?agent=<task name>` or the CLI `kb audit <task name>`
- **Privacy guardrails** — sensitive namespaces are answered strictly locally;
  `/ask` routes local-first with optional cloud fallback (opt-in)

## Quick Start (Windows / Linux / macOS)

**Windows PowerShell:**

```powershell
# 1. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the service (listens on http://127.0.0.1:8000)
python -m kb serve
```

**Linux / macOS:**

```bash
# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Start the service
python -m kb serve
```

> Important: the `kb` command only lives inside the virtual environment —
> **activate it in every new terminal** (Windows `.\venv\Scripts\Activate.ps1`,
> Linux/macOS `source venv/bin/activate`), otherwise you'll get `kb: command not found`.
> Alternatively, run `python -m kb <subcommand>` without activating.
>
> No-venv alternative (Python 3.10+): `pip install --user -r requirements.txt`, then
> `python -m kb serve`. A virtual environment is still recommended to isolate deps.

Health check:

```powershell
curl http://127.0.0.1:8000/api/v1/healthz
```

> First startup lazily loads the embedding model (default `BAAI/bge-m3`, ~2 GB —
> download it beforehand). Without an LLM configured, the service still starts
> normally; `/ask` returns 503 with setup instructions.
>
> **LLM is OFF by default (`KB_LLM_MODE=off`)**: the service never probes/loads/calls
> any LLM — zero VRAM, zero cost, fully offline. Memory storage/retrieval work completely
> without one. Configure a local or cloud LLM below only if you want `/ask` Q&A.

### Configure an LLM (optional; only needed for `/ask` Q&A)

`KB_LLM_MODE` defaults to `off` (never loads/calls an LLM). Four modes:

| Mode | Behavior |
|---|---|
| `off` (default) | Never loads/calls an LLM; memory store & retrieval fully work |
| `local` | Local Ollama only (fully offline, data never leaves the machine) |
| `auto` | **Local-first, cloud fallback**: local Ollama if available, else cloud if key set |
| `cloud` | Everything via the cloud (local only compresses & isolates privacy) |

**Local LLM (Ollama):**

```powershell
# 1. Install and start Ollama (on Windows start it from the Start menu / tray,
#    not from an AI-sandbox terminal)
# 2. Pull a model that fits your machine (pick by VRAM/RAM, e.g. qwen3:4b ~3.2GB,
#    qwen3:1.7b ~1.8GB; in China use ModelScope mirrors + `ollama cp` rename)
ollama pull <your-model>
# 3. Configure in .env and restart the service:
#    KB_LLM_MODE=local            # local only
#    KB_LLM_MODEL=<your-model>    # exact name per `ollama list`
#    KB_OLLAMA_BASE_URL=http://localhost:11434
```

**Cloud LLM (any OpenAI-compatible provider — not tied to DeepSeek):**
DeepSeek / OpenAI / Qwen / SiliconFlow / Moonshot etc. all work, with three
generic keys:

```ini
# .env
KB_LLM_MODE=auto                 # local-first, cloud fallback; or cloud for all-cloud
KB_LLM_API_KEY=sk-xxx            # your provider API key
KB_LLM_BASE_URL=https://api.deepseek.com   # your provider's OpenAI-compatible endpoint
KB_LLM_CLOUD_MODEL=deepseek-v4-flash       # cloud model name
```

Verify: `GET /api/v1/healthz` — the `llm` field shows `local`/`cloud` when an LLM is
ready, `disabled` when not enabled.

### FAQ: embedding model download fails

- **Direct `huggingface.co` access times out from mainland China.** Set the HF mirror
  and restart:
  ```bash
  export HF_ENDPOINT=https://hf-mirror.com   # or append to ~/.bashrc to persist
  python -m kb serve
  ```
  The model downloads from the mirror and is cached to `~/.cache/huggingface/hub/`,
  after which it loads fully offline.
- **Model already cached locally but no internet:** kb is offline-first (local cache
  first, network only as a fallback) — with a complete cache it runs fully offline.

## Onboarding Agents to kb (client-agnostic)

Hand the kb access spec to an AI client (TraeWork / Claude Code / Cursor / a custom agent)
so it knows how to read/write memory, which identity rules to follow, and how to query the
audit log. **Two ways — pick one:**

1. **Skill (recommended; auto-triggers) — an OPTIONAL, standalone step**: [`skills/kb-memory/SKILL.md`](skills/kb-memory/SKILL.md)
   in this repo is a client-agnostic Anthropic open-format skill. Install it to your client's
   user-level skills directory and any project session of that client will **auto-detect and
   trigger** it on memory read/write, RAG Q&A, and audit queries.
   Installing = copying the `skills/kb-memory` directory over (a script exists, but manual
   copy works too — no dependencies). Updating (re-copy to overwrite), uninstalling, and
   post-install usage: see [`scripts/README.md`](scripts/README.md).

   > **Skipping it never affects the kb service**: the skill is only a "prompt wrapper" for
   > AI clients and is unrelated to installing/starting the service. Skip it and the service
   > runs normally — you can always connect with option 2's plain-text prompt. Skill install
   > is **one-time, on-demand, and standalone**: it never auto-runs with `kb serve`, and it
   > writes nothing outside your client's user-level directories.
2. **Plain-text prompt (fallback; works on every client)**: copy
   [`docs/AGENT_PROMPT.md`](docs/AGENT_PROMPT.md) in full and paste it to the agent —
   no skill mechanism required.

> **Are `.trae-cn/skills` / `.claude/skills` / `.cursor/skills` a universal standard every client
> follows? — No.** They are each vendor's **own user-level convention**. The `SKILL.md` file itself
> is a uniform Anthropic open format, but *which directory a client reads and whether it auto-loads*
> is decided per client, with varying support:

| Client | User-level skills dir | Auto-load |
|---|---|---|
| TraeWork | `~/.trae-cn/skills/` | Auto-discovered |
| Claude Code | `~/.claude/skills/` | Supported on newer versions |
| Cursor | `~/.cursor/skills/` | Rolling out gradually |
| Other / custom agents | No common convention | Manual load or unsupported |

> There is no single directory that **every** client honors. If your client does not support
> skills, **option 2 always works** (paste `AGENT_PROMPT.md` — plain text, any client).
> Install steps, per-client directory differences and load mechanisms, and how the docs
> reference each other: see [`scripts/README.md`](scripts/README.md) (not duplicated here).

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
| POST | `/api/v1/memories` | Write a memory `{content, tags?, source?, namespace?, agent_id?}` |
| GET | `/api/v1/memories` | List memories; filters: `type/tag/source/q/limit/offset` |
| GET | `/api/v1/memories/{id}` | Read one memory (`?agent_id=`; another agent's memory → 404) |
| PATCH | `/api/v1/memories/{id}` | Update content or tags (`?agent_id=`; non-owner → 404) |
| DELETE | `/api/v1/memories/{id}` | Delete one memory (`?agent_id=`; non-owner → 404) |
| POST | `/api/v1/search` | Hybrid search `{query, top_k?, mode?, type?, tag?, agent_id?}` — mode: `hybrid/vector/keyword` |
| POST | `/api/v1/documents` | Ingest a document: multipart `file` or JSON `{"path": "...", "agent_id"?}` |
| GET | `/api/v1/documents` | List ingested documents (grouped by source) |
| DELETE | `/api/v1/documents/{source}` | Delete all records of a document |
| POST | `/api/v1/ingest/web` | Ingest a webpage `{url}` — fetch, extract, chunk, store |
| POST | `/api/v1/ask` | RAG answering `{question, agent_id?}`; 503 if no LLM configured |
| GET | `/api/v1/audit` | Agent access audit `?agent=<identity>&action?&days?&limit?` |
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
| `KB_LLM_MODE` | `off` | `off` (default, never loads/calls an LLM) / `local` (Ollama only) / `auto` (local-first, cloud fallback) / `cloud` |
| `KB_DEVICE` | empty | Embedding device: empty = auto-detect; `cpu` / `cuda` to override |
| `KB_WATCH_DIR` | `data` | Directory watched in serve mode; empty or `.` = disabled |
| `KB_DATA_DIR` | `kb_data` | Data root (ChromaDB, runtime state) |
| `KB_API_HOST` / `KB_API_PORT` | `127.0.0.1` / `8000` | REST & MCP listen address |
| `KB_EMBED_MODEL` | `BAAI/bge-m3` | Embedding model |
| `KB_LLM_MODEL` | empty | Local Ollama model name (empty by default; when `KB_LLM_MODE=local/auto`, pick one fitting your machine — exact name per `ollama list`) |
| `KB_LLM_API_KEY` / `KB_LLM_BASE_URL` / `KB_LLM_CLOUD_MODEL` | empty | Cloud LLM (optional): **any OpenAI-compatible provider** (DeepSeek / OpenAI / Qwen / SiliconFlow / Moonshot...); set in a local `.env` only |
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
