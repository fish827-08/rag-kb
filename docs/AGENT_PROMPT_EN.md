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
3. **No pre-probing needed**: kb is a resident service, normally running. Call tools/endpoints directly and keep
   working when things are normal; only when a call actually reports a **connection failure**, verify whether a
   **proxy** is intercepting localhost (retry with `curl --noproxy "*"` / `Invoke-RestMethod`), and only then
   suggest starting the service if it is truly down.
   - Clients with kb MCP mounted get the global conventions (when to write, dedupe, feedback format) injected
     automatically via server `instructions`; the rest of this guide mainly serves the HTTP fallback path.

### 3. MCP tools (8, identity is audit-only — no agent_id)

> **Identity contract (v3, 2026-08-31)**: **Memory and knowledge are fully shared; you do NOT
> need to self-report identity** — `client` (source client) is auto-detected from the MCP clientInfo
> handshake when passed (framework-level, cannot be spoofed); `project` (project/task) is **audit-only
> metadata** and never isolates reads/writes. The record primary key is generated server-side.
> You simply store and query memories — everything works without passing identity at all.
> - `client`: usually omit it — auto-detected; if passed, must be a valid client name
>   (letters/digits/CJK/underscore/hyphen/space/dot, ≤64 chars).
> - `project` (optional): audit categorization only; omitting it changes nothing for reads/writes.
> - **All memories and knowledge are fully shared (2026-08-31 v3)** — `search_memory` returns
>   every record regardless of client/project; reading/updating/deleting has no ownership check
>   (no `FORBIDDEN`).

| Tool | Parameters | Description |
|---|---|---|
| `write_memory` | `content: str`, `tags?: list[str]`, `project?: str`, `client?: str` | Write a short memory (client/project are audit-only); returns `{id}` |
| `search_memory` | `query: str`, `top_k?: int=5`, `project?: str`, `client?: str` | Hybrid retrieval; memory and knowledge are fully shared — no client/project filtering; returns hits `{id, content, score, type, source}` |
| `read_memory` | `record_id: str`, `project?: str`, `client?: str` | Read a single memory's full content by ID; no ownership restriction |
| `update_memory` | `record_id: str`, `content: str`, `project?: str`, `client?: str` | Update memory content (auto re-embed); no ownership restriction |
| `delete_memory` | `record_id: str`, `project?: str`, `client?: str` | Delete a single memory; no ownership restriction |
| `add_document` | `path: str`, `project?: str`, `client?: str` | Import a local document (PDF/DOCX/MD/TXT/Office), chunked into the store |
| `add_webpage` | `url: str`, `project?: str`, `client?: str` | Fetch a web page's body and chunk it into the store |
| `ask_kb` | `question: str`, `project?: str`, `client?: str` | RAG Q&A; returns `{answer, sources}`; returns `LLM_DISABLED` when no LLM is configured |

> Access audit: every write/search/read/update/delete/ask/ingest writes a JSON line to
> `logs/agent-audit/<client>__<project>.log` (one file per client+project; the line records the action
> and content snippet only — identity lives in the file name).
> A human can query what a client/project stored/read via `GET /api/v1/audit?client=<name>[&project=<name>]`
> or the CLI `kb audit --client <name> [--project <name>]`.

> Note: MCP tools only expose the parameters above; finer params like `namespace` are HTTP-only (see §4).

### 4. HTTP fallback endpoints (when MCP is unavailable; v3 — fully shared, identity audit-only)

> Identity contract: `client` (default `HTTP`) and optional `project` are **audit-only** —
> memory and knowledge are fully shared; omitting/mismatching them never affects reads, writes or search.

- Health check: `GET /api/v1/healthz`
- Write memory: `POST /api/v1/memories`, JSON `{"content": "…", "tags": ["偏好"], "source": "…", "namespace": "…", "client": "TraeWork", "project": "kb"}`
- Read one: `GET /api/v1/memories/{id}?project=kb` (record missing → 404)
- Update: `PATCH /api/v1/memories/{id}?project=kb`, `{"content": "…"}` (record missing → 404)
- Delete: `DELETE /api/v1/memories/{id}?project=kb` (record missing → 404)
- List: `GET /api/v1/memories?type=&tag=&q=&limit=`
- Hybrid search: `POST /api/v1/search`, `{"query": "…", "top_k": 5, "mode": "hybrid", "client": "TraeWork", "project": "kb"}` (mode: hybrid/vector/keyword; memory and knowledge are fully shared — no client/project filtering)
- Document ingestion: `POST /api/v1/documents` (multipart `file` or JSON `{"path": "本地路径", "client": "TraeWork", "project": "kb"}`)
- Web ingestion: `POST /api/v1/ingest/web`, `{"url": "…"}`
- RAG Q&A: `POST /api/v1/ask`, `{"question": "…", "client": "TraeWork", "project": "kb"}`
- **Access-audit query**: `GET /api/v1/audit?client=TraeWork&project=kb&action=write&days=7&limit=100` (what a client/project stored/read)

curl examples (PowerShell):

```powershell
# write (client defaults to HTTP; project optional = project bucket, omitted default bucket)
curl -X POST http://127.0.0.1:8000/api/v1/memories -H "Content-Type: application/json" -d '{"content": "用户偏好深色主题", "tags": ["偏好"], "client": "TraeWork", "project": "kb"}'
# search (only your client+project memory + all shared knowledge)
curl -X POST http://127.0.0.1:8000/api/v1/search -H "Content-Type: application/json" -d '{"query": "用户界面偏好", "top_k": 5, "client": "TraeWork", "project": "kb"}'
# ask
curl -X POST http://127.0.0.1:8000/api/v1/ask -H "Content-Type: application/json" -d '{"question": "用户喜欢什么主题？", "client": "TraeWork", "project": "kb"}'
# query a client/project's access audit
curl -X GET "http://127.0.0.1:8000/api/v1/audit?client=TraeWork&project=kb&limit=20"
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

**Telling the user (keep it minimal)**: on a successful write/update, mention it in one light sentence (e.g., "Got it — saved your preference"); on failure, state the reason (e.g., "Not saved: duplicate of an existing memory / sensitive content / unsupported format").
**Do NOT show**: health-check process, tool names, record IDs, JSON, hit details; duplicate check hits or misses are handled quietly either way.

**When to retrieve**: at task start, when answering involves past decisions/preferences, or on cross-session handoff — run `search_memory` proactively; don't wait to be asked.

**Retrieval tip**: phrase the `search_memory` query in natural language describing the "semantics" you're after (e.g., "用户的主题偏好"), not just keywords; hybrid retrieval does semantic matching — then `read_memory` for full text.

### 6. Notes

- Service unreachable (a call reports a connection failure): first rule out a **proxy** blocking localhost (retry with `curl --noproxy "*"`
  or `Invoke-RestMethod`); only tell the user to start kb (`python -m kb serve`) once it is truly down — don't fabricate results, and don't probe health first.
- If auth is enabled (`KB_API_KEY` non-empty), every HTTP request needs `Authorization: Bearer <key>`.
- `namespace` is HTTP-only (MCP tools don't take it); namespaces matching the sensitive config force local answers for `ask` (no egress).
- After `add_document`/`add_webpage`, content is searchable via `search_memory`/`ask_kb`; unsupported formats return `UNSUPPORTED_FORMAT`.

---