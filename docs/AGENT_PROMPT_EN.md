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

### 3. MCP tools (8, no agent_id)

> **Identity contract (v2.1)**: **`client` is audit-only** — it is normally auto-detected from the
> MCP clientInfo handshake (framework-level, cannot be spoofed); you do NOT need to self-report.
> - `client`: usually omit it; if passed, must be a valid client name
>   (letters/digits/CJK/underscore/hyphen/space/dot, ≤64 chars).
> - `project` (**not an audit field**): when passed, memories/documents go to that project's
>   isolated sub-store `kb_data/projects/<project>/`; omit it to use the global store
>   (visible to all projects). Inside a repository/workspace, pass a **stable project name**
>   (e.g. `rag-kb`) instead of a new name per task. User preferences and cross-project facts
>   should omit `project`. Names: letters/digits/CJK/underscore/hyphen, 1–64 chars.

| Tool | Parameters | Description |
|---|---|---|
| `write_memory` | `content: str`, `tags?: list[str]`, `project?: str`, `client?: str` | Write a short memory (project selects the sub-store, empty = global; client audit-only); returns `{id}` |
| `search_memory` | `query: str`, `top_k?: int=5`, `project?: str`, `client?: str` | Hybrid retrieval; with project = that sub-store + global (project hits boosted), without = global only; returns hits `{id, content, score, type, source}` |
| `read_memory` | `record_id: str`, `project?: str`, `client?: str` | Read a memory by ID; with project, check that sub-store then global; without, global then each project store |
| `update_memory` | `record_id: str`, `content: str`, `project?: str`, `client?: str` | Update memory content (auto re-embed); same lookup rule |
| `delete_memory` | `record_id: str`, `project?: str`, `client?: str` | Delete a memory; same lookup rule |
| `link_memory` | `from_id: str`, `to_id: str`, `relation?: str="related"`, `project?: str`, `client?: str` | Link two memories in the same scope (memory only); relation: related/supersedes/depends_on/part_of/contradicts; idempotent |
| `unlink_memory` | `from_id: str`, `to_id: str`, `relation?: str`, `project?: str`, `client?: str` | Remove links in either direction; omit relation to remove all relations of the pair |
| `list_links` | `record_id: str`, `relation?: str`, `project?: str`, `client?: str` | List one-hop neighbors with `content/type/tags/direction(out/in/both)` |
| `add_document` | `path: str`, `project?: str`, `client?: str` | Import a local document (PDF/DOCX/MD/TXT/Office); chunks go to the project sub-store (empty = global) |
| `add_webpage` | `url: str`, `project?: str`, `client?: str` | Fetch and chunk a web page into the project sub-store (empty = global) |
| `ask_kb` | `question: str`, `project?: str`, `client?: str` | RAG Q&A; returns `{answer, sources}`; scope same as `search_memory`; `LLM_DISABLED` without an LLM |

> Access audit: every write/search/read/update/delete/link/unlink/ask/ingest writes a JSON line to
> `logs/agent-audit/<client>__<project>.log` (one file per client+project; the line records the action
> and content snippet only — identity lives in the file name).
> A human can query what a client/project stored/read via `GET /api/v1/audit?client=<name>[&project=<name>]`
> or the CLI `kb audit --client <name> [--project <name>]`.

> Note: MCP tools only expose the parameters above; finer params like `namespace` are HTTP-only (see §4).

### 4. HTTP fallback endpoints (when MCP is unavailable; v2.1 project sub-stores)

> Identity contract: `client` (default `HTTP`) is audit-only; `project` selects the project
> sub-store (omit = global). Search with project = that sub-store + the global store.

- Health check: `GET /api/v1/healthz`
- Write memory: `POST /api/v1/memories`, JSON `{"content": "…", "tags": ["偏好"], "source": "…", "namespace": "…", "client": "TraeWork", "project": "kb"}`
- Read one: `GET /api/v1/memories/{id}?project=kb` (record missing → 404)
- Update: `PATCH /api/v1/memories/{id}?project=kb`, `{"content": "…"}` (record missing → 404)
- Delete: `DELETE /api/v1/memories/{id}?project=kb` (record missing → 404)
- List: `GET /api/v1/memories?type=&tag=&q=&limit=`
- Link: `POST /api/v1/memories/{id}/links`, `{"to_id": "…", "relation": "related"}`
- List links: `GET /api/v1/memories/{id}/links?project=kb&relation=related`
- Unlink: `DELETE /api/v1/memories/{id}/links/{to_id}?project=kb`
- Hybrid search: `POST /api/v1/search`, `{"query": "…", "top_k": 5, "mode": "hybrid", "client": "TraeWork", "project": "kb"}` (mode: hybrid/vector/keyword; with project = sub-store + global, without = global only; one-hop expansion items carry `via: {id, relation}`)
- Document ingestion: `POST /api/v1/documents` (multipart `file` or JSON `{"path": "本地路径", "client": "TraeWork", "project": "kb"}`)
- Web ingestion: `POST /api/v1/ingest/web`, `{"url": "…"}`
- RAG Q&A: `POST /api/v1/ask`, `{"question": "…", "client": "TraeWork", "project": "kb"}`
- **Access-audit query**: `GET /api/v1/audit?client=TraeWork&project=kb&action=write&days=7&limit=100` (what a client/project stored/read)

curl examples (PowerShell):

```powershell
# write (client defaults to HTTP; project optional = project sub-store, omit for global)
curl -X POST http://127.0.0.1:8000/api/v1/memories -H "Content-Type: application/json" -d '{"content": "用户偏好深色主题", "tags": ["偏好"], "client": "TraeWork", "project": "kb"}'
# search (project sub-store + global store)
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

**Project scoping**: memories produced inside a repository/workspace should pass a stable
`project` name (e.g. `rag-kb`); user preferences and cross-project facts should omit it
(global store, readable by every project). Searches inside the project should pass the same
project; the scope automatically includes the global store.

**Memory language**: write in the language specified by the server's MCP instructions
(by default the OS language); keep proper nouns, commands and code verbatim. If the server
injects user-defined memory rules (`~/.kb/rules.md` / `<data dir>/rules.md`), follow them first.

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