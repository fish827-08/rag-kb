---
name: kb-memory
description: Local-first agent memory and knowledge service (RAG) installed from PyPI. Use when a task needs to store or recall long-term user preferences, project decisions, facts, documents or web pages, when different projects need separate memories, or when the user mentions "kb memory", "kb", or a local MCP memory service. Covers gated installation (always ask first), service discovery and start, login autostart, MCP configuration, memory language, custom memory rules, and troubleshooting. Works fully offline without any LLM for memory write/search.
---

# kb-memory — local agent memory & knowledge service

kb is a small single-process service that runs on the user's machine. It gives any
agent (Claude Code, Cursor, TraeWork, custom agents) persistent memory and document
RAG through **MCP** (`http://127.0.0.1:8000/mcp/` by default) or plain **HTTP**.

- Data stays local (`~/.kb/data` for a skill install, `./kb_data` for a source checkout).
- Memory write / search / document ingest work **without any LLM**.
- Only `/ask` (RAG answering) needs an LLM; when unconfigured it returns
  `503 LLM_DISABLED` with a configuration hint.
- Default embedding model is lightweight (`BAAI/bge-small-zh-v1.5`, ~92 MB, 512-dim);
  the model loads on first use and is unloaded after 10 idle minutes.
- Default device is CPU; no GPU is required.

## 1. Installation (always ask the user first)

> **Never install or start software without explicit user approval.** First check
> whether kb is already reachable (`bootstrap.py --status` or `GET /api/v1/healthz`).
> If it is not, show the install plan below and wait for confirmation before
> running any install command.

Prerequisite: Python 3.10+. Git is **not** required — kb is installed from PyPI.

### A. Skill bootstrap (recommended)

The skill package includes `bootstrap.py` (standard library only). It installs into
an isolated venv, so it never touches the user's global Python:

```bash
python bootstrap.py --status   # where is kb? interpreter, version, data dir, running?
python bootstrap.py            # print the install plan only; changes NOTHING
python bootstrap.py --yes      # only after the user confirms: install + start
```

Options: `--extras ingest|llm|full|none` (default `ingest`), `--autostart` (enable
login start), `--no-start` (install only), `--start-only` (start an existing
install), `--dir` / `--data-dir` / `--host` / `--port`.

What `--yes` does: creates `~/.kb/venv` → installs `kb-memory[ingest]` from PyPI →
on Linux installs the CPU torch wheel first (avoids a multi-GB CUDA download) →
writes `~/.kb/install.json` → starts the service detached with `KB_DATA_DIR`.

### B. Manual install (pip)

```bash
pip install "kb-memory[ingest]"    # extras: [llm] for /ask, [full] for both
python -m kb serve                 # interactive first run (device detection)
python -m kb serve --non-interactive   # for scripts / autostart
```

Linux CPU-only machines should install the CPU torch wheel first to keep the
download small:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install "kb-memory[ingest]"
```

Missing optional extras produce a readable error naming the extra to install
(e.g. PDF parsing needs `kb-memory[ingest]`; `/ask` needs `kb-memory[llm]`).
China / slow network: add `-i https://mirrors.aliyun.com/pypi/simple/` and set
`HF_ENDPOINT=https://hf-mirror.com` before the first run.

## 2. Find, start, and autostart the service

There is no need to guess where kb lives:

| Need | Command |
|---|---|
| Where is kb installed? | `python bootstrap.py --status` |
| Same, from the CLI | `kb where` or `kb where --json` |
| Start an existing install | `python bootstrap.py --start-only` |
| Start manually | `<interpreter> -m kb serve --non-interactive` |
| Login autostart | `kb autostart enable` (`disable` / `status`) |
| Running? | `GET http://127.0.0.1:8000/api/v1/healthz` |

`bootstrap.py --status` reads `~/.kb/install.json` (interpreter, version, venv,
data dir). `kb where` uses the discovery order: manifest → `kb` on `PATH` →
installed `kb-memory` package. If no manifest exists but a `kb` executable is on
`PATH`, `kb where --json` still reports its location.

Autostart targets: Windows Startup folder `.cmd`; Linux user systemd unit
(`~/.config/systemd/user/kb.service`); macOS LaunchAgent. Autostart runs with
`--non-interactive` and the same data directory.

Do **not** probe the service before every tool call — just call it. Only when a
tool or HTTP request actually fails with a connection error, tell the user kb is
not running and offer `python bootstrap.py --start-only` (or the command above).

## 3. Mount kb in the agent (MCP)

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

The MCP endpoint exposes exactly these 8 tools:
`write_memory`, `search_memory`, `read_memory`, `update_memory`, `delete_memory`,
`add_document`, `add_webpage`, `ask_kb`.

## 4. Memory behavior rules (for the agent)

### Projects and sharing

- All stores are local and readable by every connected client; `client` is
  **audit-only** and usually auto-detected — do not self-report identity.
- Memories with **no project** go to the global store and are visible everywhere.
  Use global for user preferences, cross-project facts, and personal conventions.
- Memories with `project="<name>"` go to `kb_data/projects/<name>/` and are
  **physically isolated** from other projects. Use the project for codebase
  decisions, architecture facts, task progress, and project documents.
- When working inside a project, pass a **stable project name** (a short name for
  the repository/workspace, e.g. `rag-kb`). Never invent a new name per task.
- `search_memory(project="X")` searches project X **plus** the global store, with
  project hits slightly boosted. Omitting `project` searches only the global store.
- Project names: letters/digits/CJK/underscore/hyphen, 1–64 chars; Windows
  reserved names (CON, NUL, COM1…) are rejected.
- Documents and web pages follow the same rule: `add_document` / `add_webpage`
  accept `project` and store their chunks in that project's sub-store.

### Memory language

The server tells the agent which language to use via MCP instructions:
`KB_MEMORY_LANG=auto` (default) follows the operating-system language, so memories
are written in the user's system language. Set `KB_MEMORY_LANG=zh|en|ja|...` to
force one language. Proper nouns, identifiers, commands, and code stay verbatim.

### Custom rules (when/what to remember)

Users can define their own memory policy; it is injected into MCP instructions and
takes precedence over the defaults below:

1. `~/.kb/rules.md` — user-level rules, apply to every project.
2. `<data_dir>/rules.md` — rules for this kb installation.

Both are concatenated and truncated to `KB_RULES_MAX_CHARS` (default 4000).
Example content: "Never remember anything about my employer", "Always record
database migration decisions", "Reply in Chinese but store memories in English".

### When to write / not write

- Write (`write_memory`, 1–3 concise sentences): user preferences, project
  decisions with context, durable facts and conventions, sensitive constraints,
  task progress worth resuming across sessions.
- Do **not** write: greetings, temporary calculations, secrets (API keys,
  credentials, tokens, ID numbers), or implementation details already
  discoverable in code/docs.
- Dedupe first: call `search_memory`; if a near-duplicate exists, call
  `update_memory` instead of creating a new record.
- Retrieve proactively at task start, before answering questions about past
  decisions/preferences, or when resuming work across sessions.

### User-facing feedback

- On success: at most one short sentence ("Remembered your preference").
- On failure: state the reason (duplicate, sensitive content, unsupported format,
  service unavailable).
- Never show health checks, tool names, record IDs, JSON payloads, or retrieval
  score details.

## 5. Tools and HTTP fallback

### MCP tools

| Tool | Purpose |
|---|---|
| `write_memory` | Store a short memory (`content`, optional `tags`, `project`) |
| `search_memory` | Hybrid search (`query`, `top_k`, optional `project` = project + global) |
| `read_memory` / `update_memory` / `delete_memory` | Manage a record by `id` (optional `project`) |
| `add_document` | Ingest a local file path (optional `project`) |
| `add_webpage` | Fetch and ingest a web page by URL (optional `project`) |
| `ask_kb` | RAG answer (requires an LLM; otherwise `LLM_DISABLED`) |

### HTTP fallback (when MCP is not mounted)

- Write memory: `POST /api/v1/memories` — `{"content":"…","tags":["…"],"project":"…"}`
- Search: `POST /api/v1/search` — `{"query":"…","top_k":5,"mode":"hybrid","project":"…"}`
- Ask: `POST /api/v1/ask` — `{"question":"…","project":"…"}` (LLM required)
- Documents: `POST /api/v1/documents` (multipart `file` + optional `project`, or JSON `{"path":"…","project":"…"}`)
- Web page: `POST /api/v1/ingest/web` — `{"url":"…","project":"…"}`
- Delete a document: `DELETE /api/v1/documents?source=<urlencoded source>`
  (URLs contain slashes, so use the query parameter, not a path segment)
- Memory list: `GET /api/v1/memories?project=…`
- Health: `GET /api/v1/healthz`

Errors use JSON `{"error":"<CODE>","message":"<reason>"}`. Codes include
`DUPLICATE` (semantic dedup enabled, not written), `EMBED_MODEL_UNAVAILABLE`
(model load/download failed), `LLM_DISABLED`, `NOT_FOUND`, `INVALID_ARGUMENT`.

### Windows PowerShell notes

`curl` is an alias for `Invoke-WebRequest` and does **not** accept `-X/-H/-d`.
Use `curl.exe` or `Invoke-RestMethod`. Chinese JSON must be sent as UTF-8 bytes:

```powershell
$json  = ConvertTo-Json -InputObject @{ content = "用户喜欢的动画片"; tags = @("偏好"); project = "rag-kb" }
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/memories -Method Post `
  -ContentType 'application/json; charset=utf-8' -Body $bytes
```

A command syntax error is not the same as "service down": probe with
`curl.exe --noproxy "*" -s http://127.0.0.1:8000/api/v1/healthz`.

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `kb: command not found` | Use the venv interpreter from `bootstrap.py --status`, or `python -m kb <subcommand>` |
| Don't know where kb is | `python bootstrap.py --status` or `kb where` |
| Connection refused | Service not running: `python bootstrap.py --start-only` (or `python -m kb serve`) |
| Port already in use | Set `KB_API_PORT` for both the service and the MCP URL; stop the old instance first |
| Model download hangs / fails | Set `HF_ENDPOINT=https://hf-mirror.com`, restart; cached models work offline afterwards |
| `503 EMBED_MODEL_UNAVAILABLE` | Check `KB_EMBED_MODEL`; fix network/mirror, restart. `/healthz` shows `embedder.state` |
| `ask_kb` returns `LLM_DISABLED` | Expected with default `KB_LLM_MODE=off`; configure Ollama (local) or any OpenAI-compatible provider |
| PDF/DOCX upload says unsupported | Install the ingest extra: `pip install "kb-memory[ingest]"` |
| High memory when idle | Expected: the embedding model is unloaded after `KB_IDLE_UNLOAD_SECONDS=600`; `/healthz` shows `embedder.state` |
| Memories appear in the wrong scope | Preferences belong in the global store (omit `project`); project facts need a stable `project` name |
| Autostart doesn't run | `kb autostart status`; re-run `kb autostart enable` from the installed interpreter |

More documentation: <https://github.com/fish827-08/rag-kb> (README, user guide).
Links are optional; everything needed to install and run kb is in this skill package.
