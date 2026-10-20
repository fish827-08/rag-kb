# rag-kb — Local-First Agent Memory & Knowledge Service

<!-- mcp-name: io.github.fish827-08/kb-memory -->

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)]()
[![Glama MCP score](https://glama.ai/mcp/servers/fish827-08/rag-kb/badges/score.svg)](https://glama.ai/mcp/servers/fish827-08/rag-kb)

**Local-first, offline-capable agent memory & knowledge service.** REST + MCP dual
protocol, hybrid retrieval (vector + BM25 with normalized-score fusion), document
and webpage ingestion, and RAG Q&A — storage and retrieval remain fully usable
without any LLM.

<!-- GitHub Topics: mcp, memory-service, rag, ai-agent, knowledge-base, local-first, llm, claude-code, hybrid-search, embedding -->

**kb** is a local-first, completely free memory & knowledge service for AI agents.
One resident process (`python -m kb serve`) exposes both **REST** and **MCP**,
giving Claude Code / Cursor / TraeWork / custom agents persistent memory, document
& webpage ingestion, and hybrid retrieval.

**Fully functional without any LLM** — memory writes, document ingestion, and hybrid
retrieval never call a language model. Optionally configure local Ollama or a cloud
API to unlock `/ask` (RAG question answering).

> Chinese documentation: [README.md](README.md) (primary).

**kb** is the shippable product: a local, offline-capable memory & knowledge
service (v2.1.2).

## Why kb

- **Local-first & offline-capable** — models and data stay on your machine;
  retrieval works with the network completely cut off.
- **Zero extra infrastructure** — one process; ChromaDB is embedded, no external
  vector DB service and no container required.
- **Hybrid retrieval that speaks Chinese** — dense vectors + BM25 (jieba
  search-mode tokenization), each min-max normalized and evenly weighted across
  the active retrieval paths.
- **Dual protocol** — REST for scripts/tools, native streamable-HTTP MCP for AI
  clients, served by the same process.
- **Shared by default** — memories and knowledge written by any client or session
  are visible to all sessions. `client` is audit metadata only.
- **Global + per-project memory (v2.1)** — without `project`, records go to the
  global store; with `project`, they go to an isolated sub-store
  `kb_data/projects/<project>/` (delete the directory to delete the project's
  memory). Project searches merge the sub-store with the global store, weighted by
  `KB_PROJECT_BOOST`.
- **PyPI install & slim dependencies (v2.1)** — `pip install "kb-memory[ingest]"`;
  ingestion/LLM deps are optional extras, and Linux users can preinstall CPU torch
  for a much smaller download.
- **Skill-driven install & discovery (v2.1)** — the skill ships `bootstrap.py` and
  installs **only after asking the user** into `~/.kb/venv`; `kb where` reports the
  install location, `kb autostart enable` starts it at login.
- **Idle unload (v2.1)** — `KB_IDLE_UNLOAD_SECONDS` unloads the embedding model and
  releases GPU memory when idle, so background footprint drops to near zero.
- **Memory language & custom rules (v2.1)** — `KB_MEMORY_LANG=auto` follows the OS
  language; `~/.kb/rules.md` customizes when the agent should store memories.
- **Access audit** — every write/read/update/delete/search/ask emits a JSON line
  under `logs/agent-audit/`; query it through `GET /api/v1/audit?client=<client>`
  or `kb audit --client <client>`.
- **Privacy guardrails** — sensitive namespaces are answered strictly locally;
  `/ask` routes local-first with optional cloud fallback.
- **Lightweight default model** — `BAAI/bge-small-zh-v1.5` (~92 MB, 512-dim),
  with `BAAI/bge-m3` (~2.2 GB, 1024-dim) as an opt-in upgrade.

## Quick Start (Windows / Linux / macOS)

**Install from PyPI (recommended):**

```bash
pip install "kb-memory[ingest]"   # core + document/web ingestion; [llm] for Q&A, [full] for both
python -m kb serve
```

**CPU-only Linux machines: install the CPU torch wheel first**
(the default PyPI torch wheel bundles CUDA and is far larger):

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install "kb-memory[ingest]"
python -m kb serve
```

**Source checkout (development):**

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1          # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
pip install -e ".[full]"             # dev/tests need ingestion, watcher and LLM extras
python -m kb serve
```

> **What the first install downloads** (so it doesn't look stuck):
> - Core Python dependencies: about **400–600 MB** (largest single wheel on
>   Windows: `torch` CPU, ~124 MB). On Linux install the CPU torch wheel first to
>   avoid the multi-GB default CUDA wheel.
> - Optional `[ingest]` adds a few tens of MB (PDF/DOCX/Office parsing, web
>   extraction, directory watching).
> - The embedding model is loaded **on the first write/search** (lazy), not at
>   startup: default `bge-small-zh-v1.5`, about **92 MB** (or ~2.2 GB if you set
>   `KB_EMBED_MODEL=BAAI/bge-m3`).
> - China / slow networks: use a pip mirror such as
>   `pip install "kb-memory[ingest]" -i https://mirrors.aliyun.com/pypi/simple/`
>   (Aliyun tested; Tsinghua may return 403), and set
>   `HF_ENDPOINT=https://hf-mirror.com` for model downloads.
>
> Important: with a source venv the `kb` command is installed inside the virtual
> environment. **Activate it in every new terminal** (Windows
> `.\venv\Scripts\Activate.ps1`, Linux/macOS `source venv/bin/activate`),
> otherwise you'll see `kb: command not found`. Use `python -m kb <subcommand>`
> to bypass activation. Not sure where kb lives or how to start it? Run
> `kb where`, or `python bootstrap.py --status` / `--start-only` from the skill
> package.

Health check:

```powershell
curl.exe http://127.0.0.1:8000/api/v1/healthz
```

> The model is lazy-loaded: an idle service uses almost no memory. The first write
> or search loads and caches the model. `/healthz` reports
> `embedder.state` as `unloaded | loading | ok | error`; if the model cannot be
> loaded, write/search return **503 `EMBED_MODEL_UNAVAILABLE`** with the model name
> and a fix hint (never a bare 500).
>
> **CPU is enough — no GPU required.** A fresh install gets the CPU build of torch
> (`device=cpu`). To use a GPU, install a CUDA build of torch and set
> Install a CUDA build of torch and set `KB_DEVICE=cuda` to enable GPU acceleration.
>
> **LLM is OFF by default (`KB_LLM_MODE=off`)**: the service never probes, loads,
> or calls any LLM — zero VRAM, zero cost, fully offline. Memory storage and
> retrieval work completely without one.

### Choosing an embedding model

The lightweight default keeps the first-run cost low. You can switch at any time:

| Model | Size | Dim | Resident memory after load | Best for |
|---|---|---|---|---|
| **`BAAI/bge-small-zh-v1.5` (default)** | ~92 MB | 512 | ~100 MB | Everyday preferences, decisions, notes; fast on CPU |
| `BAAI/bge-m3` | ~2.2 GB | 1024 | ~2 GB | Long documents, cross-lingual and nuanced semantics |

Two models with different dimensions cannot share one ChromaDB collection. kb
records the model used to build the database in `kb_data/runtime.json` and
**keeps using it automatically**, so changing the default never corrupts existing
data. To deliberately migrate, back up the data and re-ingest after clearing
`kb_data/chroma`.

### Configure an LLM (optional; only needed for `/ask` Q&A)

`KB_LLM_MODE` defaults to `off` (never loads/calls an LLM). Four modes:

| Mode | Behavior |
|---|---|
| `off` (default) | Never loads/calls an LLM; memory store & retrieval fully work |
| `local` | Local Ollama only (fully offline, data never leaves the machine) |
| `auto` | **Local-first, cloud fallback**: local Ollama if available, else cloud if a key is set |
| `cloud` | Everything via the cloud (local only compresses & isolates privacy) |

**Local LLM (Ollama):**

```powershell
# 1. Install and start Ollama (on Windows start it from the Start menu / tray,
#    not from an AI-sandbox terminal)
# 2. Pull a model that fits your machine (e.g. qwen3:4b ~3.2GB, qwen3:1.7b ~1.8GB)
ollama pull <your-model>
# 3. Configure in .env and restart:
#    KB_LLM_MODE=local
#    KB_LLM_MODEL=<your-model>    # exact name from `ollama list`
#    KB_OLLAMA_BASE_URL=http://localhost:11434
```

**Cloud LLM (any OpenAI-compatible provider — not tied to DeepSeek):**

```ini
# .env
KB_LLM_MODE=auto                 # local-first, cloud fallback; or cloud for all-cloud
KB_LLM_API_KEY=sk-xxx            # your provider API key
KB_LLM_BASE_URL=https://api.deepseek.com   # OpenAI-compatible endpoint
KB_LLM_CLOUD_MODEL=deepseek-v4-flash       # cloud model name
```

Verify: `GET /api/v1/healthz` — `llm` shows `local`/`cloud` when ready, `disabled`
when not enabled.

### FAQ: embedding model download fails

- **Direct `huggingface.co` access times out from mainland China.** Set the HF
  mirror and restart:

  ```bash
  export HF_ENDPOINT=https://hf-mirror.com   # or append to ~/.bashrc
  python -m kb serve
  ```

  The model downloads from the mirror and is cached to
  `~/.cache/huggingface/hub/`, after which it loads fully offline.
- **Model already cached but no internet:** kb is offline-first (local cache first,
  network only as a fallback) — a complete cache runs fully offline.

## Onboarding Agents to kb (client-agnostic)

1. **Skill (recommended; auto-triggers) — an optional, standalone step**:
   [`skills/kb-memory/SKILL.md`](skills/kb-memory/SKILL.md) is a client-agnostic
   Anthropic open-format skill. Install it into your client's user-level skills
   directory; any project session of that client will auto-detect and trigger it
   on memory read/write, RAG Q&A, and audit queries. Installing = copying the
   `skills/kb-memory` directory (a script exists; manual copy works too). See
   [`scripts/README.md`](scripts/README.md) for install/update/uninstall steps.
2. **Plain-text prompt (fallback; works on every client)**: copy
   [`docs/AGENT_PROMPT_EN.md`](docs/AGENT_PROMPT_EN.md) and paste it to the agent.

> Skipping the skill never affects the kb service — it is only a prompt wrapper.
> There is no universal skills directory; each client has its own convention
> (`~/.trae-cn/skills/`, `~/.claude/skills/`, `~/.cursor/skills/`). If your client
> does not support skills, option 2 always works.

### Standalone skill package (Agensi / marketplaces)

`skills/kb-memory/` is kb's **standalone delivery unit**, depending only on the kb
service itself. It contains a standard `SKILL.md` at the package root, an
MCP config template (`mcp.json`), a config template (`env.example`), a README and
the license — covering install → start → configure → verify → troubleshoot.

```powershell
python scripts/build_skill_package.py        # output: dist/kb-memory-skill-<version>.zip
```

The zip keeps `SKILL.md` at its **root** and excludes tests, runtime data and any
secrets. The build script validates front matter, self-containment
and accidental secret leakage.

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

> With `KB_API_KEY` auth enabled, clients must send `Authorization: Bearer <key>`
> or `X-API-Key` headers — see [USER_GUIDE §5.2](docs/USER_GUIDE.md) (Chinese).

## REST API Reference

All memories and knowledge are **shared** by default (global store); `client` is
audit metadata. Passing `project` routes the record into an isolated sub-store
`kb_data/projects/<project>/` (v2.1), and project searches merge that sub-store
with the global store. Record IDs are generated server-side.

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/memories` | Write a memory `{content, tags?, source?, namespace?}` |
| GET | `/api/v1/memories` | List memories; filters: `type/tag/source/q/limit/offset` |
| GET | `/api/v1/memories/{id}` | Read one memory |
| PATCH | `/api/v1/memories/{id}` | Update content or tags |
| DELETE | `/api/v1/memories/{id}` | Delete one memory |
| POST | `/api/v1/search` | Hybrid search `{query, top_k?, mode?, type?, tag?}` — mode: `hybrid/vector/keyword`; blank query → 422 |
| POST | `/api/v1/documents` | Ingest a document: multipart `file` or JSON `{"path": "..."}`; re-ingesting the same source replaces old chunks |
| GET | `/api/v1/documents` | List ingested documents (grouped by source) |
| DELETE | `/api/v1/documents/{source}` | Delete all records of a document (simple sources such as file names) |
| DELETE | `/api/v1/documents?source=<urlencoded>` | Delete by query parameter (required for URLs containing slashes) |
| POST | `/api/v1/ingest/web` | Ingest a webpage `{url}` — fetch, extract, chunk, store |
| POST | `/api/v1/ask` | RAG answering `{question}`; 503 if no LLM configured |
| GET | `/api/v1/audit` | Access audit `?client=<client>&project?&days?&limit?` |
| GET | `/api/v1/healthz` | Health check & service stats (includes `embedder.state`) |

Error responses use `{"error": "<CODE>", "message": "..."}`. Notable codes:
`DUPLICATE` (semantic dedup enabled), `EMBED_MODEL_UNAVAILABLE`,
`LLM_DISABLED`, `NOT_FOUND`, `INVALID_ARGUMENT`.

Example (Windows PowerShell — copy as-is; Chinese content must be sent as UTF-8):

```powershell
function Invoke-KbJson($Uri, $Body) {
  Invoke-RestMethod -Uri $Uri -Method Post -ContentType 'application/json; charset=utf-8' `
    -Body ([System.Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 5)))
}

Invoke-KbJson http://127.0.0.1:8000/api/v1/memories @{ content = "User prefers dark theme"; tags = @("preference") }
Invoke-KbJson http://127.0.0.1:8000/api/v1/search @{ query = "UI preferences"; top_k = 5 }
```

Linux / macOS:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/memories \
  -H "Content-Type: application/json" \
  -d '{"content": "User prefers dark theme", "tags": ["preference"]}'
```

## Configuration

All settings use the `KB_` environment prefix or a local `.env` file. See
[`.env.example`](.env.example) for the full key list (copy to `.env`; it is
gitignored — real keys never enter version control).

| Key | Default | Description |
|---|---|---|
| `KB_LLM_MODE` | `off` | `off` / `local` / `auto` / `cloud` |
| `KB_DEVICE` | empty | Embedding device: empty = auto-detect; `cpu` / `cuda` to override |
| `KB_WATCH_DIR` | `data` | Directory watched in serve mode; empty or `.` = disabled |
| `KB_DATA_DIR` | `kb_data` | Data root (ChromaDB, runtime state) |
| `KB_API_HOST` / `KB_API_PORT` | `127.0.0.1` / `8000` | REST & MCP listen address |
| `KB_EMBED_MODEL` | `BAAI/bge-small-zh-v1.5` | Embedding model (lightweight default; `BAAI/bge-m3` optional) |
| `KB_LLM_MODEL` | empty | Local Ollama model name (exact name per `ollama list`) |
| `KB_LLM_API_KEY` / `KB_LLM_BASE_URL` / `KB_LLM_CLOUD_MODEL` | empty | Any OpenAI-compatible cloud provider |
| `KB_API_KEY` | empty | Empty = no auth (loopback); non-empty = Bearer / X-API-Key |
| `KB_SENSITIVE_NAMESPACES` | empty | Comma-separated namespaces forced to answer locally |

## CLI

```powershell
python -m kb add "memory" --tags preference --client ClaudeCode
python -m kb search "query" --client ClaudeCode
python -m kb stats
python -m kb ask "question" --client ClaudeCode
python -m kb audit --client ClaudeCode --days 7
python -m kb eval --file tests/eval_zh_50.jsonl
python -m kb forget --stale --days 90
python -m kb dedup --threshold 0.92
```

> `kb eval` uses an isolated temporary database and explicitly disables user
> governance settings (dedup/decay/freshness), so evaluation stays reproducible
> even when your `.env` enables them.

## Repository Layout

```
kb/            service source (config / models / embedder / storage / bm25 /
               retriever / service / llm / ingest / watcher / api / mcp / cli)
tests/         kb acceptance & regression tests (425 passed / 1 skipped, incl. eval_zh_50.jsonl)
skills/        standalone kb-memory skill package (Agensi deliverable)
scripts/       build/start/stop scripts (Windows one-click launchers)
docs/          design specs, developer handbook, user guides
```

## License

[Apache-2.0](LICENSE) — commercial use permitted.

## Links

- [README.md](README.md) — Chinese documentation (primary)
- [docs/USER_GUIDE_EN.md](docs/USER_GUIDE_EN.md) — user guide (English)
- [docs/AGENT_PROMPT_EN.md](docs/AGENT_PROMPT_EN.md) — plain-text agent prompt
