# Generic Agent Onboarding Prompt (kb Memory Service)

> Purpose: copy the entire "Prompt Body" below into any AI agent
> (TraeWork / Claude Code / Cursor / self-built agent, etc.) and that agent
> will be connected to the kb memory & knowledge service. The text is plain
> text and depends on no framework.
>
> **Packaged as a client-agnostic skill**: the same spec also lives at
> `skills/kb-memory/SKILL.md` (Anthropic Skills open format) for skill-capable
> clients to auto-trigger; see `scripts/README.md` for per-client install
> commands. Ultimate fallback: paste this prompt — no skill mechanism needed.

---

## Prompt Body (copy from here)

You are connected to "kb" — a local-first agent memory & knowledge service. Please use it as follows:

### 1. Service positioning

kb is a resident memory service on your machine (default `http://127.0.0.1:8000`) with two capabilities:

1. **Memory**: write facts, preferences, and decisions worth remembering long-term during conversations; they can be semantically retrieved later.
2. **Knowledge**: chunk local documents and web pages into the store, then run hybrid retrieval and RAG Q&A against the knowledge base.

It is fully local, free, and works offline; writing and retrieval need no LLM — only `ask` Q&A requires one.

### 2. Connection (by priority)

1. **MCP (preferred)**: if the current environment has kb's MCP server configured, use the MCP tools directly.
2. **HTTP (fallback when MCP unavailable)**: when MCP tools don't exist, fail to connect, or time out, use the REST endpoints (same address); after a successful call, prefer MCP again.
3. Before starting, confirm the service is up: `GET http://127.0.0.1:8000/api/v1/healthz` returning 200 means it is healthy.

### 3. MCP tools (8, all accept `agent_id` / `client`)

> **Identity contract (important)**:
> - `agent_id`: states "who you are" — **MUST be your task name** (e.g. `TASK-0076`, `worker-1`);
>   it cannot be omitted or set to placeholders like `default`/`unknown` (the server enforces this and
>   rejects it with `INVALID_ARGUMENT`). Each agent sticks to one task name and never mixes.
> - `client`: source client — usually omit it; the server auto-detects it from the MCP clientInfo
>   handshake (TraeWork / Claude Code / Cursor...). If passed, it must be a valid client name
>   (letters/digits/CJK/underscore/hyphen/space/dot, ≤64 chars).
> - `project` (optional): project name, used only to bucket audit files.
> - Memories (`memory`) are **only visible to their owning agent** — `search_memory` returns only
>   what you wrote; reading/updating/deleting another agent's memory is rejected (`FORBIDDEN`).
>   Shared knowledge (document/web chunks) is visible to **all** agents.

| Tool | Parameters | Description |
|---|---|---|
| `write_memory` | `content: str`, `agent_id: str` (required), `tags?: list[str]`, `client?: str`, `project?: str` | Write a short memory (owned by agent_id); returns `{id}` |
| `search_memory` | `query: str`, `agent_id: str` (required), `top_k?: int=5`, `client?: str`, `project?: str` | Hybrid retrieval; `memory` only returns those owned by this agent_id, doc/web shared; returns hits `{id, content, score, type, source}` |
| `read_memory` | `record_id: str`, `agent_id: str` (required), `client?: str`, `project?: str` | Read a single memory's full content by ID; another agent's memory → `FORBIDDEN` |
| `update_memory` | `record_id: str`, `content: str`, `agent_id: str` (required), `client?: str`, `project?: str` | Update memory content (auto re-embed); non-owner → `FORBIDDEN` |
| `delete_memory` | `record_id: str`, `agent_id: str` (required), `client?: str`, `project?: str` | Delete a single memory; non-owner → `FORBIDDEN` |
| `add_document` | `path: str`, `agent_id: str` (required), `client?: str`, `project?: str` | Import a local document (PDF/DOCX/MD/TXT/Office), chunked into the store (shared; ownership audit-only) |
| `add_webpage` | `url: str`, `agent_id: str` (required), `client?: str`, `project?: str` | Fetch a web page's body and chunk it into the store (shared; ownership audit-only) |
| `ask_kb` | `question: str`, `agent_id: str` (required), `client?: str`, `project?: str` | RAG Q&A; returns `{answer, sources}`; returns `LLM_DISABLED` when no LLM is configured |

> Access audit: every write/search/read/update/delete/ask/ingest writes a JSON line to
> `logs/agent-audit/<client>__<project>__<task>.log` (one file per agent; the line records the action
> and content snippet only — identity lives in the file name; renaming a task = renaming its file).
> A human can query what an agent stored/read via `GET /api/v1/audit?agent=<task name>` or the CLI
> `kb audit <task name>`.

> Note: MCP tools only expose the parameters above; finer params like `namespace` are HTTP-only (see §4).

### 4. HTTP fallback endpoints (when MCP is unavailable)

> Identity contract same as MCP: pass `agent_id` (**use your task name**) and optional `client`;
> `memory` is strictly isolated, doc/web shared; both travel in write/read/update/delete/search/ask requests.

- Health check: `GET /api/v1/healthz`
- Write memory: `POST /api/v1/memories`, JSON `{"content": "…", "tags": ["偏好"], "source": "…", "namespace": "…", "agent_id": "TASK-0076", "client": "TraeWork"}`
- Read one: `GET /api/v1/memories/{id}?agent_id=TASK-0076` (another agent's memory → 404)
- Update: `PATCH /api/v1/memories/{id}?agent_id=TASK-0076`, `{"content": "…"}` (non-owner → 404)
- Delete: `DELETE /api/v1/memories/{id}?agent_id=TASK-0076` (non-owner → 404)
- List: `GET /api/v1/memories?type=&tag=&q=&limit=`
- Hybrid search: `POST /api/v1/search`, `{"query": "…", "top_k": 5, "mode": "hybrid", "agent_id": "TASK-0076"}` (mode: hybrid/vector/keyword; `memory` only returns those owned by this agent_id)
- Document ingestion: `POST /api/v1/documents` (multipart `file` or JSON `{"path": "本地路径", "agent_id": "TASK-0076"}`)
- Web ingestion: `POST /api/v1/ingest/web`, `{"url": "…"}`
- RAG Q&A: `POST /api/v1/ask`, `{"question": "…", "agent_id": "TASK-0076"}`
- **Access-audit query**: `GET /api/v1/audit?agent=TASK-0076&action=write&days=7&limit=100` (what an agent stored/read)

curl examples (PowerShell):

```powershell
# write (with agent identity)
curl -X POST http://127.0.0.1:8000/api/v1/memories -H "Content-Type: application/json" -d '{"content": "用户偏好深色主题", "tags": ["偏好"], "agent_id": "worker-1"}'
# search (only your own memory + all shared knowledge)
curl -X POST http://127.0.0.1:8000/api/v1/search -H "Content-Type: application/json" -d '{"query": "用户界面偏好", "top_k": 5, "agent_id": "worker-1"}'
# ask
curl -X POST http://127.0.0.1:8000/api/v1/ask -H "Content-Type: application/json" -d '{"question": "用户喜欢什么主题？", "agent_id": "worker-1"}'
# query an agent's access audit
curl -X GET "http://127.0.0.1:8000/api/v1/audit?agent=worker-1&limit=20"
```

### 5. Memory-writing rules (what to write)

Three hard rules (review before every write):

- **Check for duplicates first**: run `search_memory` before writing; skip it if similar content exists, or use `update_memory` to overwrite — never add duplicate records.
- **Use update for corrections**: when an old memory is outdated/wrong, use `update_memory` to change it; don't write a new one (avoids multiple conflicting versions of the same fact).
- **No sensitive data**: keys, API keys, credentials, ID numbers, etc. must never be written to the memory store (it persists locally in `kb_data/`).

Within the above, during the session proactively identify and write the following (`write_memory`, condensed to 1~3 sentences):

1. **User preferences**: themes, toolchains, code style, naming conventions, communication style, etc.
2. **Project decisions**: settled tech choices, architecture trade-offs, conventions — record background and conclusion.
3. **Facts & conventions**: reusable facts like key paths, commands, dependencies, environment details, version constraints.
4. **Sensitive constraints**: red lines (e.g., "do not modify the design doc"), security/privacy requirements.
5. **Current task progress**: key progress and next steps of complex tasks, for cross-session handoff.

Do NOT write: small talk, temporary computation, or implementation details directly obtainable from code/docs (unless needed across sessions).

**When to retrieve**: at task start, when answering involves past decisions/preferences, or on cross-session handoff — run `search_memory` proactively; don't wait to be asked.

**Retrieval tip**: phrase the `search_memory` query in natural language describing the "semantics" you're after (e.g., "用户的主题偏好"), not just keywords; hybrid retrieval does semantic matching — then `read_memory` for full text.

### 6. Notes

- When the service is down (healthz fails): don't fabricate results — tell the user to start kb first (`python -m kb serve`).
- If auth is enabled (`KB_API_KEY` non-empty), every HTTP request needs `Authorization: Bearer <key>`.
- `namespace` is HTTP-only (MCP tools don't take it); namespaces matching the sensitive config force local answers for `ask` (no egress).
- After `add_document`/`add_webpage`, content is searchable via `search_memory`/`ask_kb`; unsupported formats return `UNSUPPORTED_FORMAT`.

---