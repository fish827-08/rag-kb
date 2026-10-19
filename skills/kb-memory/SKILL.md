---
name: kb-memory
description: Local-first agent memory and knowledge service (RAG). Use when a task needs to store or recall long-term user preferences, project decisions, facts, documents or web pages, or when the user mentions "kb memory", "kb", or a local MCP memory service. Includes one-time setup, MCP configuration, HTTP fallback, and troubleshooting. Works fully offline without any LLM for memory write/search; only RAG answering needs an LLM.
---

# kb-memory — local agent memory & knowledge service

kb is a small single-process service that runs on the user's machine. It gives any
agent (Claude Code, Cursor, TraeWork, custom agents) persistent memory and document
RAG through **MCP** (`http://127.0.0.1:8000/mcp/` by default) or plain **HTTP**.

- Data stays local (`kb_data/`), no cloud account required.
- Memory write / search / document ingest work **without any LLM**.
- Only `/ask` (RAG answering) needs an LLM; when unconfigured it returns
  `503 LLM_DISABLED` with a configuration hint.
- Default embedding model is lightweight (`BAAI/bge-small-zh-v1.5`, ~92 MB, 512-dim).
  `BAAI/bge-m3` (~2.2 GB) is optional for stronger long-document / cross-lingual recall.
- Default device is CPU; no GPU is required.

## 1. One-time setup (for the human user)

> An agent should **not** silently install software. If kb is not reachable, show
> these steps to the user and wait for confirmation.

Prerequisites: Python 3.10+, Git.

```bash
git clone https://github.com/fish827-08/rag-kb.git
cd rag-kb
python -m venv venv
# Windows PowerShell: .\venv\Scripts\Activate.ps1
# Linux / macOS:     source venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

China / slow network (optional):

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
# HuggingFace mirror for the embedding model:
# Windows: $env:HF_ENDPOINT='https://hf-mirror.com'
# Linux/macOS: export HF_ENDPOINT=https://hf-mirror.com
```

Start the service (keep this process running):

```bash
python -m kb serve
# Windows one-click background start (repo root):
# scripts\windows\start_kb.bat
# Stop: scripts\windows\stop_kb.bat
```

The default endpoint is `http://127.0.0.1:8000`. To change it, copy `.env.example`
to `.env` and set `KB_API_HOST` / `KB_API_PORT` (the Windows scripts read
`KB_API_PORT` from `.env`).

First use downloads the embedding model (~92 MB) and caches it; after that kb works
offline. Startup itself is lightweight — the model loads on the first write/search.

### Point the agent at kb (MCP)

Use the included `mcp.json` template (copy its `mcpServers` block into the client's
MCP configuration, or copy the whole file where the client expects it):

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

Claude Code can also use: `claude mcp add --transport http kb http://127.0.0.1:8000/mcp/`.

If `KB_API_KEY` is set, add an auth header to the same server entry:

```json
"headers": { "Authorization": "Bearer <your-key>" }
```

### Verify

```bash
curl http://127.0.0.1:8000/api/v1/healthz
# expected: {"status":"ok","records":0,"device":"cpu","llm":"disabled","embedder":{...}}
```

The MCP endpoint must expose exactly these 8 tools:
`write_memory`, `search_memory`, `read_memory`, `update_memory`, `delete_memory`,
`add_document`, `add_webpage`, `ask_kb`.

## 2. How to use kb (behavior rules for the agent)

- **Everything is shared**: memories and knowledge written by any client/session are
  visible to all sessions. Do not pass client/project for isolation; they are
  audit-only. The server generates record IDs.
- **When to write** (`write_memory`, 1–3 concise sentences): user preferences,
  project decisions, durable facts and conventions, sensitive constraints, task
  progress worth resuming. Do **not** write greetings, temporary calculations, or
  implementation details already discoverable in code/docs.
- **Check before writing**: call `search_memory` first; if a near-duplicate exists,
  call `update_memory` instead of creating a new record.
- **Never store secrets**: API keys, credentials, ID numbers, tokens.
- **When to retrieve**: at task start, before answering questions about past
  decisions/preferences, or when resuming work across sessions — call
  `search_memory` proactively.
- **Documents vs memory**: use `add_document` for local files (txt/md/pdf/docx/…)
  and `add_webpage` for URLs. Re-ingesting the same source replaces its old chunks.
- **User-facing feedback is minimal**: on success, at most one short sentence
  (e.g. "Remembered your preference"); on failure, state the reason (duplicate,
  sensitive content, unsupported format, service unavailable). Never show health
  checks, tool names, record IDs, JSON payloads, or retrieval score details.
- **Do not probe the service before every call.** Just call it. Only when a tool or
  HTTP request actually fails with a connection error, tell the user kb is not
  running and point to `python -m kb serve` (or the Windows start script). Never
  fabricate results.

## 3. Tools and HTTP fallback

### MCP tools

| Tool | Purpose |
|---|---|
| `write_memory` | Store a short memory (`content`, optional `tags`, `project`) |
| `search_memory` | Hybrid search (vector + BM25 normalized fusion; `query`, `top_k`) |
| `read_memory` / `update_memory` / `delete_memory` | Manage a record by `id` |
| `add_document` | Ingest a local file path |
| `add_webpage` | Fetch and ingest a web page by URL |
| `ask_kb` | RAG answer (requires an LLM; otherwise `LLM_DISABLED`) |

### HTTP fallback (when MCP is not mounted)

- Write memory: `POST /api/v1/memories` — `{"content":"…","tags":["…"]}`
- Search: `POST /api/v1/search` — `{"query":"…","top_k":5,"mode":"hybrid"}`
- Ask: `POST /api/v1/ask` — `{"question":"…"}` (LLM required)
- Documents: `POST /api/v1/documents` (multipart `file`, or JSON `{"path":"…"}`)
- Web page: `POST /api/v1/ingest/web` — `{"url":"…"}`
- Delete a document: `DELETE /api/v1/documents?source=<urlencoded source>`
  (URLs contain slashes, so use the query parameter, not a path segment)
- Health: `GET /api/v1/healthz`

Errors use JSON `{"error":"<CODE>","message":"<reason>"}`. Codes include
`DUPLICATE` (semantic dedup enabled, not written), `EMBED_MODEL_UNAVAILABLE`
(model load/download failed), `LLM_DISABLED`, `NOT_FOUND`, `INVALID_ARGUMENT`.

### Windows PowerShell notes

`curl` is an alias for `Invoke-WebRequest` and does **not** accept `-X/-H/-d`.
Use `curl.exe` or `Invoke-RestMethod`. Chinese JSON must be sent as UTF-8 bytes:

```powershell
$json  = ConvertTo-Json -InputObject @{ content = "用户喜欢的动画片"; tags = @("偏好") }
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/memories -Method Post `
  -ContentType 'application/json; charset=utf-8' -Body $bytes
```

A command syntax error is not the same as "service down": probe with
`curl.exe --noproxy "*" -s http://127.0.0.1:8000/api/v1/healthz`.

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| `kb: command not found` | Activate the venv first, or run `python -m kb <subcommand>`; install the CLI with `pip install -e .` |
| Connection refused | Service not running: `python -m kb serve` (keep it running) |
| Model download hangs / fails | Set `HF_ENDPOINT=https://hf-mirror.com`, restart; cached models work offline afterwards |
| Write/search returns `503 EMBED_MODEL_UNAVAILABLE` | Check `KB_EMBED_MODEL`; fix network/mirror, restart. `/healthz` shows `embedder.state` |
| `ask_kb` returns `LLM_DISABLED` | Expected with the default `KB_LLM_MODE=off`; configure Ollama or an OpenAI-compatible provider to enable Q&A |
| Port 8000 already used | Set `KB_API_PORT` in `.env` (Windows scripts and MCP URL must use the same port) |
| No GPU | Nothing to do: CPU is the default. Set `KB_DEVICE=cuda` only if a CUDA build of torch is installed |
| Startup warning about watch directory | `KB_WATCH_DIR` defaults to `data`, which may not exist; set it to an existing folder or leave it empty to disable watching |

More documentation: <https://github.com/fish827-08/rag-kb> (README, user guide).
Links are optional; everything needed to run kb is in this skill package.
