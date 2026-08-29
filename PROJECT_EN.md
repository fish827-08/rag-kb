# PROJECT.md — Project Handoff Document

> **For any AI assistant taking over**: first read [AGENTS.md](AGENTS.md) (work rules & red lines), then this document (project status); then act per "Chapter 5: Handoff Guide".
> Reading these two is enough to take over — no need to ask the user for history.
>
> Last updated: 2026-08-28 ｜ Maintainer: docs/test AI (per AGENTS.md role split) ｜ Updated with every milestone/node

## 1. What This Project Is (two subsystems)

```
┌─────────────────────────────────────────────────┐
│  rag-kb repo (this repo, Windows local-first)    │
│                                                 │
│  ① kb —— local Agent memory & knowledge service  │
│     (core product)                              │
│     python -m kb serve resident; REST + MCP      │
│     dual protocol                               │
│     memory write / hybrid retrieval / doc-web    │
│     ingestion for AI assistants                 │
│                                                 │
│  ② orchestra —— cross-task AI collaboration     │
│     (experimental)                              │
│     kb as shared task board; coordinator AI      │
│     splits cards; worker AIs (any model, other   │
│     TraeWork tasks) claim and execute           │
└─────────────────────────────────────────────────┘
```

- **Design principles**: local-first, completely free, offline-capable; GPU budget 6GB (BGE-M3 + qwen3:4b = 4.3GB)
- **Single source of truth**: design docs under `docs/superpowers/specs/` (see Chapter 6 index)

## 2. Current Progress (dashboard)

### ① kb memory service — v1.0.1 (production-usable, P1 shipped)

| Milestone | Content | Status |
|---|---|---|
| M1 (N1-N6) | BGE-M3 embedding, ChromaDB storage, BM25+jieba, RRF hybrid retrieval, CLI | ✅ 2026-08-23 |
| M2 (N7-N8) | REST API (memories/search/documents/healthz), 4-level device detection | ✅ |
| M3 (N9-N12) | MCP 8 tools, /ask smart routing (classify/compress/cache/privacy-isolate/cloud-degrade) | ✅ |
| M4 (N13-N16) | doc ingestion (pdf/docx/md/txt), web fetch, dir watcher, README | ✅ 2026-08-24 |
| v1.0.1 hotfix | top_k/mode/empty-content validation → 422, MCP entry validation, SSE charset | ✅ 2026-08-24 |

- Tests: **339 green** (`tests/`; +68 added by A3.5+A4: reranker/sparse/eval/perf/cli five test files, branch `feature/a-line-remaining`)
- Bench: hybrid search 26ms, /ask end-to-end ~2s (local qwen3:4b)
- Tags: `node-01`~`node-16`, `v1.0.0`, `v1.0.1` (all pushed to Gitee)
- v1.0.2: JSON charset fixed (TASK-0003/0038/0045, with test asserts)
- **A2 auth delivered** (N19 ApiKeyMiddleware + N20 client auto-key, TASK-0062/0064; empty-key no-auth zero-friction)
- **monitor plain-text mode default off** (TASK-0065): fully usable without local LLM (KB_MONITOR_LLM=off/auto)
- **A3 memory governance all delivered** (2026-08-28, TASK-0066~0076, spec+dual-layer impl):
  - Rules layer: access-frequency decay (λ=0.02) + semantic dedup (threshold 0.92, 409 block) + freshness weight (β=0.05, cap 1.3×) — all default off, zero behavior change
  - Ops surface: `kb forget/dedup` CLI + `/api/v1/governance/stats|config` endpoints + structured audit log (kb/audit.py)
  - Smart layer: consolidation framework (kb/consolidation.py + spec, confidence gate + human fallback, default off)
- **A3.5 retrieval quality delivered** (2026-08-28, N24-N27, branch `feature/a-line-remaining`, spec: `2026-08-28-a35-retrieval-quality-design.md`):
  - N24 cross-encoder rerank: kb/reranker.py (bge-reranker-v2-m3) + pipeline hookup, `KB_RERANK_ENABLED` default off
  - N25 sparse third leg: kb/sparse.py (BGE-M3 sparse head direct-load + inverted index) + 3-way RRF, `KB_SPARSE_ENABLED` default off, failure degrades to dual
  - N26 eval bench: kb/eval.py + 50-Chinese-QA (tests/eval_zh_50.jsonl) + `kb eval` CLI (Recall@1/@5 + MRR + by-difficulty)
  - N27 perf: N+1 fix (store.get_many batch) + BM25 corpus persistence
- **A4 usability delivered** (2026-08-28, N28, CLI-first; **Web UI cancelled** — eval-report conclusion, spec: `2026-08-28-a4-cli-design.md`):
  - `kb stats`: type distribution / hot top / stale distribution (read-only)
  - `kb ask`: terminal RAG Q&A; LLM-absent degrades to retrieval hits (exit code 1)

### ② agent-orchestra — frozen to maintenance after B1/B2/B3 (2026-08-28)

| Milestone | Content | Status |
|---|---|---|
| MVP (T1-T10) | board.py CLI 6 cmds, protocol trio, orchestra-worker skill, 20 unit tests | ✅ 2026-08-24 |
| Real-machine exp. | worker-1 (another TraeWork task) TDD'd list-pending subcommand, full pending→verified chain | ✅ 2026-08-24 |
| B1 three-role loop | message window/batch mode (v1.2)/dashboard (watch+HTML+nav)/monitor agent (on-demand)/worktree isolation (v1.3)/registry 6 members | ✅ 2026-08-26 |
| Packaging | board.py split into client/cards/registry/comm/worktree/watch/feedback modules (TASK-0028~0031) | ✅ 2026-08-26 |
| B2 feedback loop | FBK card 3-type iron rules + dynamic quota + over-limit arbitration + comm:feedback archive (protocol v1.4; FBK-0001~0006 six drills) | ✅ 2026-08-26 |
| Scheduling/monitor arch | DispatchAgent 4 rules + comm:dispatch broadcast + LLM decoupling + coordinator loop resident auto-verify (protocol v1.5) | ✅ 2026-08-26 |
| B3 cost control | protocol v1.6 §14 + b3.py (dynamic quota/rounds/summary tags) + relation window + interrupt recovery + watch rounds column + round warning (5/6, model tiering pending) | 🚧 2026-08-27 |
| B5 mount residency | mount.py mount-state module (6 CLIs) + mount-loop protocol + sub/parent coordinator layering + robotic-arm heartbeat monitoring (separate branch `feature/orchestra-mount`, B5-0~B5-4 delivered) | 🚧 2026-08-28 |

- Tests: **245 green** (`orchestra/tests/`; incl. relation 15 + b3 22 + client auth 7 + mount 30 + coordinator_loop lost-contact 3 etc.; B5 on separate branch `feature/orchestra-mount`)
- Task board: TASK-0001~0065, 65 cards (63 verified; 0056/0063 duplicate cards voided)
- Coordinator loop: **resident auto-verify running** (60s/round: done cards merge→test→verify→push; open-FBK block + auto new-FBK broadcast)
- Coordinator relay: **coordinator-prompt.md wake prompt + relay-state section established** (must update after every closeout), sole handoff entry
- Skills installed locally `~\.trae-cn\skills\orchestra-worker\` (**hard links**, may break after checkout; reinstall needed)

### ③ Development bus — v3 (2026-08-27 strategic shift: A-line only mainline, B-line frozen)

Project bus: **[ROADMAP.md](ROADMAP.md)**. Single mainline after shift:

| Mainline | Current stage | Next |
|---|---|---|
| **A: kb memory service** (only development mainline, 2026-08-27 shift) | **all in-plan nodes delivered**: A1/A2/A3 ✅, A3.5+A4 ✅ (branch `feature/a-line-remaining`, N24-N28, 584 green awaiting merge); A2.5 only MCP Registry submission left (user action) | new needs: update design docs then initiate (AGENTS.md §6) |
| **B: orchestra** (❄️ maintenance) | B1/B2/B3/B3+ frozen-shipped; B4 cancelled; B5 mount residency exception (separate branch) | bug fixes + tests green + docs sync; keep running for personal use; A-line keeps using it for collaboration |
| Side lines | — | frozen (eval-report: no Web UI/multi-user/commercialization; A4 Web UI officially cancelled) |

- **Strategic shift (2026-08-27, per 《Evaluation Report》 dual reports + user decision)**: A-line kb is the core product ("individual dev's local memory MCP server"), B-line orchestra maintenance (personal scaffold); if cross-Agent direction restarts, evaluate A2A protocol compat rather than self-build
- **LICENSE Apache-2.0 added** (eval-report P0 top item, legal-compliance closed)
- Protocol **v1.6** (last pre-freeze version): task-branch/worktree isolation, message window, feedback nodes (3-type/dynamic quota), B3 cost-control discipline (§14), scheduler monitoring
- Runtime shape: **coordinator loop resident** (60s/round, done cards auto merge→test→verify→push); **monitor plain-text default off** — fully usable without local LLM

## 3. Directory Guide

```
rag-kb/
├── AGENTS.md            # AI work rules (handoff read #1)
├── PROJECT.md           # this doc (handoff read #2)
├── ROADMAP.md           # project bus (human entry, tree+progress)
├── README.md            # kb product doc (quick start/MCP mount/endpoint cheat-sheet)
├── kb/                  # ① kb service source (see design doc 4.2; incl. reranker/sparse/eval)
├── tests/               # kb acceptance tests (339, incl. eval_zh_50.jsonl dataset)
├── scripts/             # one-click start/stop scripts (windows/ + linux/ reserved)
├── orchestra/           # ② collaboration system (board.py + protocol + skill + 245 tests)
│   └── docs/superpowers/  # orchestra design docs & plans
├── docs/superpowers/    # kb design docs (specs) & node plans (plans)
├── kb_data/             # kb runtime data (ChromaDB + runtime.json, gitignored)
├── _archive/            # old learning-project archive (do not reference)
└── .mcp.json            # project-level MCP mount (Claude Code/TraeWork direct kb)
```

## 4. Key Command Cheat-Sheet

```powershell
# start kb service (resident, before any MCP mount)
python -m kb serve                    # default 127.0.0.1:8000
# or one-click: scripts\windows\start_kb.bat / start_kb_console.bat / stop_kb.bat

# kb tests / orchestra tests (inside venv)
venv\Scripts\python.exe -m pytest tests/ -q            # 339
venv\Scripts\python.exe -m pytest orchestra/tests/ -q  # 245

# orchestra task board (coordinator tools)
venv\Scripts\python.exe orchestra\board.py status        # one-line-per-card board
venv\Scripts\python.exe orchestra\board.py add --assignee worker-1 --title ... --goal ... --input ... --constraints ... --acceptance ...
venv\Scripts\python.exe orchestra\board.py verify TASK-0001 --pass|--reject --note reason
venv\Scripts\python.exe orchestra\board.py new-worker worker-1   # generate worker wake prompt
```

## 5. Handoff Guide (new AI act per this)

1. **Read rules**: AGENTS.md (role split, red lines, node-gate system)
2. **Read status**: this doc + the design doc currently in implementation (Chapter 6 index)
3. **Verify environment**: `python -m kb serve` starts, both test suites green, git clean
4. **Confirm role**: are you the dev AI (write impl) or docs/test AI (write acceptance/verify)? Act per AGENTS.md §5
5. **Collaborative dev** (orchestra flow):
   - As coordinator: user request → you split cards (board.py add, 5 fields + assignee) → user opens worker task with new-worker prompt → worker claims → you verify (diff+test+real-service re-verify) → verify transition → unified commit
   - As worker (when pasted a wake prompt): load orchestra-worker skill → look up card → one card one round → write back → stop
6. **Continue the bus**: read `ROADMAP.md` for current position (currently active: see bus §2), follow the plan nodes with TDD + node-gate

## 6. Document Index (only valid set)

| Doc | Content |
|---|---|
| `ROADMAP.md` | **project dev bus** (human entry: tree+routes+progress) |
| `docs/USER_GUIDE.md` | **user manual** (human user entry: install/mount/collab flow/FAQ) |
| `docs/superpowers/specs/2026-08-23-kb-memory-service-design.md` | kb only design (req/arch/API/bench) |
| `docs/superpowers/plans/2026-08-23-kb-dev-nodes.md` | kb node plan N1-N16 (all ✅) |
| `orchestra/docs/superpowers/specs/2026-08-24-agent-orchestra-mvp-design.md` | orchestra design |
| `orchestra/docs/superpowers/plans/2026-08-24-agent-orchestra-mvp.md` | orchestra plan (all ✅) |
| `docs/superpowers/specs/2026-08-24-logging-design.md` | logging design (bus A1.2, N17-N18) |
| `docs/superpowers/plans/2026-08-24-p2-roadmap.md` | kb feature route (bus A2-A4) |
| `orchestra/docs/superpowers/plans/2026-08-24-orchestra-v2-iteration.md` | orchestra v2 iteration plan (bus B1 carrier) |
| `orchestra/protocol.md` + `worker-prompt.md` + `coordinator-prompt.md` | collab protocol trio (v1.1: branch/restart-control/message window) |
| `orchestra/EXPERIMENT.md` | real-machine experiment guide (reusable as next template) |

## 7. Environment Notes (see AGENTS.md §7)

- torch 2.11.0+cu128 (from Aliyun mirror); HF via hf-mirror.com; Ollama models at `D:\ollama_models`
- qwen3 local MUST use `think:false` (Ollama native /api/chat, not SDK)
- Ollama must be started from Start menu/tray (launching from sandbox terminal causes DB read/write failure)
- BGE-M3 & Ollama models offline-cached; fully usable offline (acceptance criterion)