# User Guide — rag-kb

> A guide for human users. AI assistants: read [AGENTS.md](../AGENTS.md) + [PROJECT.md](../PROJECT.md) instead.
>
> Version: v1 (2026-08-24) ｜ Updated with releases

## Contents

1. [Overview](#1-overview)
2. [kb service: install & start](#2-kb-service-install--start)
3. [kb service: daily use](#3-kb-service-daily-use)
4. [orchestra: multi-Agent collaboration](#4-orchestra-multi-agent-collaboration)
5. [Configuration reference](#5-configuration-reference)
6. [FAQ & troubleshooting](#6-faq--troubleshooting)

---

## 1. Overview

Two subsystems in one sentence each:

- **kb**: an "AI memory bank" service running on your machine. Once your AI assistants (TraeWork / Claude Code / Cursor) mount it, they gain cross-session long-term memory — written memories and ingested documents are queryable any time in later conversations.
- **agent-orchestra**: a "task board" for multiple AI assistants to collaborate. One AI (coordinator) splits tasks, other AIs (workers) execute; handoff via task cards on the kb board. You only give instructions and do acceptance.

**Daily minimum**: keep kb running → mount MCP on AI clients → just chat.

## 2. kb service: install & start

### 2.1 Install (one-time)

```powershell
cd rag-kb
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2.2 Start the service

```powershell
python -m kb serve
```

- Default listen `http://127.0.0.1:8000`; loads the local embedding model at startup (~10s first time)
- Verify: open `http://127.0.0.1:8000/api/v1/healthz` in another terminal/browser, `{"status": "ok", ...}` means healthy
- **Keep that terminal window open** (or run it in the background); closing it stops the service

> One-click: `scripts\windows\start_kb.bat` (background), `scripts\windows\start_kb_console.bat` (debug console), `scripts\windows\stop_kb.bat` (stop).

### 2.3 Stop & restart

- Stop: `Ctrl+C` in the service terminal
- After restart: MCP-mounted AI clients may show disconnected; refresh/reconnect in the client's MCP panel, or open a new session

## 3. kb service: daily use

### 3.1 Mount AI clients (recommended)

MCP endpoint: `http://127.0.0.1:8000/mcp/`

| Client | How to mount |
|---|---|
| **TraeWork (desktop)** | Settings → MCP → runtime env **local** → create/manual configure, paste the JSON below |
| **Claude Code** | repo ships `.mcp.json`; start `claude` inside the repo to auto-mount |
| **Cursor** | add the JSON below to `~/.cursor/mcp.json` or project `.cursor/mcp.json` |
| **Other MCP clients** | same as below; Streamable HTTP type, URL = MCP endpoint |

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

Once mounted, AI gets 8 tools: `write_memory` (write memory), `search_memory` (hybrid search),
`read_memory` / `update_memory` / `delete_memory`, `add_document` (file ingestion),
`add_webpage` (web ingestion), `ask_kb` (RAG Q&A).

**Usage examples** (just tell the AI):

```
Remember "the user prefers dark themes, primary language Python" in the memory bank
Search for project decisions I stored before
Ingest D:\docs\design-spec.pdf into the knowledge base
Ask the KB: what is this project's retrieval approach?
```

> Note: TraeWork only invokes MCP tools in **Agent mode** conversation;
> the web-version task runs in the cloud and cannot reach local 127.0.0.1 — use desktop + local env.

### 3.2 Direct terminal use (no AI)

```powershell
# write a memory (supports comma-separated --tags, --source, --namespace)
python -m kb add "user prefers dark theme" --tags "pref,UI"

# hybrid search (--mode hybrid/vector/keyword)
python -m kb search "UI prefs" --top-k 5

# runtime info (record count, device, etc.)
python -m kb info

# statistics (N28): type distribution / hot records / stale distribution
python -m kb stats

# terminal RAG Q&A (N28): falls back to retrieval hits if LLM unavailable
python -m kb ask "what theme does the user prefer?"
```

### 3.3 REST API (self-built agents / scripts)

Full endpoint table: see [README](../README.md#rest-端点速查). Common examples:

```powershell
# write
curl -X POST http://127.0.0.1:8000/api/v1/memories -H "Content-Type: application/json" -d '{"content": "用户偏好深色主题", "tags": ["偏好"]}'

# search
curl -X POST http://127.0.0.1:8000/api/v1/search -H "Content-Type: application/json" -d '{"query": "界面偏好", "top_k": 5}'

# RAG Q&A (needs LLM, see section 5)
curl -X POST http://127.0.0.1:8000/api/v1/ask -H "Content-Type: application/json" -d '{"question": "用户喜欢什么主题？"}'
```

#### 3.3.1 Viewing logs (N18, without an open terminal)

Two read-only endpoints showing service logs (incl. per-request `request.start`/`request.end` events):

```powershell
# latest 20 entries (limit default 100, max 1000)
curl "http://127.0.0.1:8000/api/v1/logs?limit=20"

# only request events (event substring match)
curl "http://127.0.0.1:8000/api/v1/logs?limit=20&event=request"

# filter by level (DEBUG/INFO/WARNING/ERROR/CRITICAL, abbreviable warn/err/fatal)
curl "http://127.0.0.1:8000/api/v1/logs?limit=50&level=WARNING"

# event statistics (by level and by logger)
curl "http://127.0.0.1:8000/api/v1/logs/events"
```

Returns `items` (file order; each with time/level/logger/message/line) plus `total`/`truncated`; `/logs/events` returns `by_level`/`by_logger`.

### 3.4 Knowledge ingestion

| Way | Action |
|---|---|
| Tell the AI | "Ingest D:\docs\xxx.pdf" / "Save this webpage to the KB: <URL>" |
| REST | `POST /api/v1/documents` (multipart file or JSON path); `POST /api/v1/ingest/web` (URL) |
| Dir watcher | set `KB_WATCH_DIR` (default `data`); new files auto-ingested, deletions auto-cleaned |

Supported formats: txt / md / pdf / docx.

### 3.5 Memory governance (A3: dedup / decay / freshness)

Memory grows noisy over time. A3 governance offers three optional mechanisms, **all default OFF (zero behavior change)**, enabled in `.env` as needed:

| Mechanism | Switch | Effect |
|---|---|---|
| Semantic dedup (N22a) | `KB_DEDUP_ENABLED=true` | pre-write vector search top1; cosine ≥ `KB_DEDUP_THRESHOLD` (default 0.92) → duplicate, **409 block, no write** |
| Access-frequency decay (N21b) | `KB_DECAY_ENABLED=true` | hybrid/vector rank: long-unread demoted, high-frequency boosted |
| Freshness weight (N22b) | `KB_FRESHNESS_ENABLED=true` | hybrid/vector rank: recently-updated boosted up to 30% |

#### 3.5.1 Semantic dedup: 409 block (no write)

After enabling, `POST /api/v1/memories` runs a pre-write dedup check; on hit returns **409, content not persisted**:

```json
HTTP 409 Conflict
Content-Type: application/json; charset=utf-8

{
  "error": "DUPLICATE",
  "message": "语义重复，已存在相似记录",
  "duplicate_of": "<existing record id>",
  "similarity": 0.9537
}
```

**How callers handle it**:
- From the 409 body take `duplicate_of` (existing record id) and `similarity` (4-decimal)
- Want to update the old record → `PATCH /api/v1/memories/{duplicate_of}`; pure duplicate → skip (idempotent)
- On embed/retrieval errors, degrades to no-block (normal write; WARNING in service log) — a check failure never blocks writes
- With `KB_DEDUP_ENABLED` off, behavior is identical to before; tune `KB_DEDUP_THRESHOLD` (lower = stricter)

#### 3.5.2 Freshness & decay (retrieval ranking)

They multiply orthogonally, independent switches, **only affect `hybrid`/`vector` ranking** (keyword/BM25 path unaffected); both off = ranking unchanged:

- **Freshness weight** (`KB_FRESHNESS_ENABLED`): looks at `updated_at` — just-updated records boosted up to 1+α× (default α=0.3, i.e. 1.3×), decaying exponentially (half-life ≈14d, β=0.05). Params: `KB_FRESHNESS_BETA` / `KB_FRESHNESS_ALPHA`
- **Access decay** (`KB_DECAY_ENABLED`): looks at `last_accessed`/`access_count` — long-unread demoted (half-life ≈35d, λ=0.02), high-frequency boosted (γ=0.3, access_count=10 → ~2.0×). Params: `KB_DECAY_LAMBDA` / `KB_DECAY_GAMMA`

#### 3.5.3 Governance endpoints (read-only)

```powershell
# stats: total records / avg access / stale(>90d) count
curl.exe http://127.0.0.1:8000/api/v1/governance/stats
Invoke-RestMethod http://127.0.0.1:8000/api/v1/governance/stats

# config: current decay + freshness switches & params
curl.exe http://127.0.0.1:8000/api/v1/governance/config
Invoke-RestMethod http://127.0.0.1:8000/api/v1/governance/config
```

Example shapes:

- `/governance/stats`: `{"total_count": 279, "avg_access_count": 1.25, "stale_90d_count": 3}`
- `/governance/config`: `{"decay_enabled": false, "decay_lambda": 0.02, "decay_gamma": 0.3, "freshness_enabled": false, "freshness_beta": 0.05, "freshness_alpha": 0.3}`

> CLI maintenance commands (bulk cleanup etc., N23a) are in progress; see §6 for `forget`/`dedup`.

## 4. orchestra: multi-Agent collaboration

### 4.1 What it solves

Different AI sessions don't know what others are doing. orchestra uses kb as a **shared task board**: task cards are records in kb, all AIs see the same board — coordinator splits, workers claim & execute, write back, coordinator verifies. **Each AI only works when woken; mounting costs zero otherwise.**

### 4.2 Three roles

| Role | Who | What |
|---|---|---|
| **You** | human | request, open worker tasks, final acceptance |
| **Coordinator** | one AI task (e.g. this session) | split requests into cards, assign to workers, verify results, commit code |
| **Worker** | other AI tasks (any model) | claim → execute → write back → stop, **one card one round** |

### 4.3 Standard flow (five steps)

**Step 1: request to coordinator**

Just say it in the coordinator task, e.g. "add a `list-pending` subcommand to orchestra".
Coordinator splits cards (each has title/goal/input/constraints/acceptance, five fields) and assigns an assignee.

**Step 2: open a worker task**

Open a new TraeWork task (same project, any model — this is exactly the "any-model-as-worker" thesis),
paste the coordinator's wake prompt, or run:

```powershell
venv\Scripts\python.exe orchestra\board.py new-worker worker-1
```

Send the output wake prompt verbatim to that new task. The worker auto: looks up cards → claims → works → writes back → stops.

**Step 3: watch progress**

In the coordinator task (or any terminal):

```powershell
venv\Scripts\python.exe orchestra\board.py status          # board, one card per line
venv\Scripts\python.exe orchestra\board.py show TASK-0001  # a card's detail & result
venv\Scripts\python.exe orchestra\board.py list-pending    # pending only
```

**Step 4: multi-card handoff**

Workers follow one-card-one-round discipline: stop after finishing a card. Say "**继续**" (continue) in the worker task to claim the next.

**Step 5: coordinator acceptance**

After all workers finish, say "**核验**" (verify) in the coordinator task. Coordinator independently checks (code diff, full test run, real-service re-verify); pass → verify transition (pending → claimed → done → verified) + unified commit/push; fail → reject and cycle back.

### 4.4 Multiple parallel workers

Supported now:

1. Coordinator assigns by assignee when splitting (worker-1 edits A, worker-2 writes B doc, worker-3 runs tests)
2. Open N tasks, paste each worker's `new-worker` prompt
3. Workers claim their own cards in parallel, no interference

**Known limits (MVP, honest)**:

- Wake is manual (no auto-scheduler)
- No dependency graph — ordering controlled by coordinator when splitting
- **Parallel cards must not touch the same file** (coordinator avoids at split time)
- Multi-worker parallelism not yet real-machine tested (single worker verified); try small-scale first

### 4.5 Collaboration discipline (protocol red lines, workers & coordinator share)

- One card one round: one card per wake, stop after completion
- No polling, no chit-chat: zero cost while mounted, act only when woken
- Cards have field length caps; write results to a file, put the path in the card
- Workers don't git commit/push directly — coordinator commits after verification
- Test discipline: acceptance test first (red) → implement (green) → full regression

### 4.6 Mount-resident mode (B5, new)

Let workers/designers **mount-listen after finishing** (idle 15 min auto-stop, new card auto-continue); sub-coordinators **stay mounted**; parent coordinator **woken on demand**:

```powershell
# mount (worker/designer default TTL=900 = 15 min; sub-coordinator --ttl 0 resident)
venv\Scripts\python.exe orchestra\board.py mount worker-1 --role worker --ttl 900
venv\Scripts\python.exe orchestra\board.py mount designer-1 --role designer --ttl 900
venv\Scripts\python.exe orchestra\board.py mount subcoordinator --role subcoordinator --ttl 0

# heartbeat / exit / board / lost-contact
venv\Scripts\python.exe orchestra\board.py heartbeat worker-1
venv\Scripts\python.exe orchestra\board.py unmount worker-1 --reason idle-timeout
venv\Scripts\python.exe orchestra\board.py mount-status            # one agent per line
venv\Scripts\python.exe orchestra\board.py mount-check --threshold 300

# called by worker/designer inside the mount loop
venv\Scripts\python.exe orchestra\board.py mount-claim worker-1 --topic kb-A3
venv\Scripts\python.exe orchestra\board.py mount-idle worker-1
```

- **Mount loop**: after mounting, workers/designers self-loop — check cards → claim & work & write back → `mount-idle`; no card → `heartbeat`+sleep 60s; accumulated idle ≥ TTL → `unmount` stop.
- **Consecutive-related ≤5**: done to the 5th consecutive card of the same "topic", write summary then `unmount` to reset context (prevent bloat).
- Parent coordinator doesn't mount: when woken by the user, creates a "split-card" (assignee=subcoordinator), see `orchestra/parent-coordinator-prompt.md`.
- Full protocol: `orchestra/protocol.md` §15 and `orchestra/docs/superpowers/specs/2026-08-28-orchestra-mount-design.md`.

## 5. Configuration reference

Copy `.env.example` to `.env` and fill as needed (`.env` isn't committed; keys stay local):

| Scenario | Config |
|---|---|
| Memory/retrieval only (no Q&A) | zero config, works out of the box (`KB_LLM_MODE=off` default — LLM never loaded/called) |
| Local RAG Q&A | install Ollama + `ollama pull` a model fitting your machine (no preset — pick by VRAM); `.env` → `KB_LLM_MODE=local` + `KB_LLM_MODEL=<actual `ollama list` name>` |
| Cloud Q&A fallback | `.env` → `KB_LLM_API_KEY` / `KB_LLM_BASE_URL` / `KB_LLM_CLOUD_MODEL` (**any OpenAI-compatible provider**, three generic keys, not tied to DeepSeek); `KB_LLM_MODE=auto` (local-first, cloud fallback) |
| Privacy isolation | `KB_SENSITIVE_NAMESPACES=私人笔记` (comma-separated); matched → local-only answers, no egress |
| Performance tuning | `KB_DEVICE=cuda/cpu`; `KB_CHUNK_SIZE/KB_CHUNK_OVERLAP` |
| LAN/multi-agent auth | `KB_API_KEY=<≥32 random chars>` enables Bearer/X-API-Key auth (see 5.1) |
| Memory governance | `KB_DEDUP_ENABLED` / `KB_DECAY_ENABLED` / `KB_FRESHNESS_ENABLED`, all default off (see 3.5) |
| Multi-agent identity isolation | every write/read/update/delete/search/ask carries `agent_id` (default `default`): `memory` visible only to its owning agent; doc/web shared knowledge visible to all (see 5.3) |
| Agent access audit | `KB_ACCESS_AUDIT_ENABLED` (default on): every access writes a JSON line to `logs/access-audit.log`; query via REST `GET /api/v1/audit?agent=<identity>` or `kb audit <identity>` (see 5.3) |

Full key list: [`.env.example`](../.env.example).

### Agent onboarding (client-agnostic)

- **Recommended**: use `skills/kb-memory/SKILL.md` (Anthropic open-format skill, client-agnostic);
  skill-capable clients auto-trigger it; multi-client one-click install: [scripts/README.md](../scripts/README.md).
- **Fallback**: the plain-text prompt in `docs/AGENT_PROMPT.md` — paste it into any agent to connect;
  no skill mechanism required. Both sources stay in sync.

### 5.3 Agent identity isolation & access audit (A-node)

**Identity isolation** (multi-agent coworkers never cross-read each other's memories):

- Every operation carries `agent_id` (use your task name, e.g. `TASK-0076` / `worker-1` — avoid vague
  codes); MCP/CLI/REST all support it
- **Source client (`client`)**: MCP auto-detects it from the handshake clientInfo (TraeWork / Claude
  Code / Cursor) when omitted; REST/CLI pass it explicitly; recorded on writes and in the audit
- **Identity field regulation (A-node)**: MCP and REST both validate `agent_id`/`client`/`project`
  formats — `agent_id`/`project`: letters/digits/CJK/underscore/hyphen, 1-64 chars; **on MCP `agent_id`
  is mandatory and cannot be placeholders like `default`/`unknown`** (audit would be untraceable);
  `client` additionally allows spaces and dots (e.g. `Claude Code`) and auto-detects when omitted;
  invalid values are rejected (REST 422 / MCP `INVALID_ARGUMENT`) — agents can no longer pass arbitrary strings
- **Personal memory (`memory`) is strictly isolated**: retrieval only returns what you wrote; reading/updating/deleting another agent's memory is rejected (REST 404 / MCP `FORBIDDEN`)
- **Shared knowledge (doc/web chunks) is visible to all**: documents/web pages ingested by any agent are searchable by every agent (RAG Q&A is unaffected)
- Old records without `agent_id`/`client` are treated as `default` — zero migration

**Access audit** (who stored/read what, from which client/project):

- **Per-agent files**: each write/search/read/update/delete/ask/ingest writes a JSON line to
  `logs/agent-audit/<client>__<project>__<task>.log` (without a project: `<client>__<task>.log`,
  e.g. `TraeWork__kb__TASK-0076.log`, `Claude Code__worker-1.log`) — one file per agent instead of
  one shared log; renaming a task = renaming its file
- Line JSON: `{"timestamp","action","type","record_id","namespace","content 前50-char snippet","query 前50-char snippet","hits"}`
  (client/agent/project live in the file name, not repeated per line; the query layer re-derives them)
- Sensitive red line: content/query store only the first 50 chars; full text never hits the log
- Human query entry points:
  ```powershell
  curl -X GET "http://127.0.0.1:8000/api/v1/audit?agent=TASK-0076&action=write&days=7&limit=100"
  kb audit TASK-0076 --action write --days 7 --limit 100
  ```

### 5.1 API Key auth (N19)

Local loopback is zero-friction by default (`KB_API_KEY` empty = no auth). When exposing kb to LAN, phone MCP mount, or parallel multi-agent access, enable auth:

1. `.env` → `KB_API_KEY=<random string>` (≥32 chars recommended, e.g. `openssl rand -hex 16`)
2. Restart `python -m kb serve`; startup log records "auth enabled" (no key echo)
3. All client requests carry the key (either):
   - `Authorization: Bearer <key>` (recommended, MCP client headers config)
   - `X-API-Key: <key>` (script/curl convenience)
4. Only whitelist: `GET /api/v1/healthz` (liveness probe; coordinator/monitor can probe without key)
5. Missing or wrong key → `401 {"error":"UNAUTHORIZED","message":"missing or invalid api key"}` (undifferentiated, anti-probing)

Empty key leaves all existing behavior unchanged (v1.x compatible); key comparison uses `hmac.compare_digest` (timing-attack safe).

### 5.2 Client adaptation (N20: how each client carries the key)

| Client | Config |
|---|---|
| **orchestra CLI** (`orchestra/board.py` etc.) | zero-change auto-adopt: reads `KB_API_KEY` from env (then repo-root `.env`), non-empty → auto `X-API-Key` header, empty → no auth; on 401 suggests "check KB_API_KEY" |
| **MCP clients** (Claude Code / Cursor / TraeWork) | add `headers` in connection config (real key NOT written into committed `.mcp.json`; keep it local / use env vars or local override): |
| **REST scripts / curl** | header `X-API-Key: <key>` or `Authorization: Bearer <key>` (either; Bearer preferred) |
| **Dashboard `/dashboard`** | after key enabled, frontend needs the key to load data (fill manually) |

MCP config with key (local personal use, **don't commit**):

```json
{
  "mcpServers": {
    "kb": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp/",
      "headers": { "Authorization": "Bearer <your-key>" }
    }
  }
}
```

Repo `.mcp.json` stays keyless (JSON has no comments; doc lives here); health probe `GET /api/v1/healthz` never needs a key.

## 6. Maintenance commands (forget / dedup)

N23a adds two maintenance CLIs (`python -m kb forget` / `dedup`), default **dry-run safe-first** (only lists candidates, never mutates), touching the storage layer directly (not via REST/service).

### 6.1 forget — scan stale memory

```bash
# preview records not accessed in >90 days (dry-run default, no delete)
python -m kb forget --stale --days 90 --dry-run

# confirmed delete (requires typing yes)
python -m kb forget --stale --days 90 --no-dry-run
```

- `--stale`: stale-not-touched mode (currently the only mode, required)
- `--days N`: not-touched day threshold, default 90
- `--dry-run/--no-dry-run`: default dry-run (candidate table: id/content-summary/last-accessed/days); `--no-dry-run` requires typing `yes` to confirm before deleting
- Day calc: empty `last_accessed` falls back to `created_at` (never touched = creation time)

### 6.2 dedup — scan duplicate pairs

```bash
# preview pairs with similarity > 0.85 (dry-run default, no change)
python -m kb dedup --dry-run

# custom threshold
python -m kb dedup --threshold 0.90 --dry-run
```

- `--threshold FLOAT`: cosine sim threshold, default 0.85
- `--dry-run/--no-dry-run`: default dry-run (candidate table: record-A/record-B/sim/content-summary); `--no-dry-run` auto-merge not implemented yet (N23c smart consolidation later), prompts manual review
- embeds each record then pairwise cosine compare; cost grows O(n²) with record count

## 7. FAQ & troubleshooting

**Q: healthz unreachable?**
Service isn't up. Check: is the terminal still open, is the port occupied (`netstat -ano | findstr 8000`), or use another port `KB_API_PORT=8001 python -m kb serve`.

**Q: AI says MCP unreachable / empty tool list?**
① confirm kb serve is running; ② refresh/reconnect in the client MCP panel (old connection dies after service restart); ③ TraeWork must be desktop + local runtime env; ④ make sure you're in Agent mode.

**Q: Ollama error / /ask returns 503?**
① confirm Ollama was started from **Start menu/tray** (not from an AI sandbox terminal — that causes DB read/write failure); ② `ollama list` shows a model pulled and set as `.env` `KB_LLM_MODEL` (no preset — pick by your VRAM, e.g. qwen3:4b / qwen3:1.7b, rename with `ollama cp` if needed); ③ for cloud LLM check `KB_LLM_API_KEY` / `KB_LLM_BASE_URL` (any OpenAI-compatible provider, see §5); ④ memory access & retrieval work fine without any LLM (`KB_LLM_MODE=off` default).

**Q: Chinese mojibake in PowerShell curl/Invoke-RestMethod?**
Known issue (JSON response lacks charset; v1.0.2 candidate fix): use `curl.exe` instead of the `curl` alias, or a Python client, or in PowerShell 7 set `$OutputEncoding = [Text.Encoding]::UTF8`.

**Q: Does it work offline?**
Yes. Models & data are all local; memory write/retrieval/local Q&A fully work offline (an acceptance criterion). Only cloud fallback needs network.

**Q: Where is the data, how to back up?**
All in `kb_data/` (ChromaDB + runtime state). Backup = copy that directory; restore = copy it back.

**Q: Want to wipe the memory bank and start over?**
Stop the service, delete `kb_data/chroma`, restart (irreversible — back up first).

**Q: Worker claimed a card but stuck/claimed forever?**
Say "继续" (continue) in the worker task to wake it; if the task is truly dead, have the coordinator handle the card return on the board.

---

**Feedback**: if any description here doesn't match reality, tell any AI task "update USER_GUIDE section X".