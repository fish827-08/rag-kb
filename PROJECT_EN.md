# PROJECT.md — Project Handoff Document

> **For any AI assistant taking over**: first read [AGENTS.md](AGENTS.md) (work rules & red lines), then this document (project status); then act per "Chapter 5: Handoff Guide".
> Reading these two is enough to take over — no need to ask the user for history.
>
> Last updated: 2026-10-19 ｜ Maintainer: docs/test AI (per AGENTS.md role split) ｜ Updated with every milestone/node

## 1. What This Project Is

```
┌─────────────────────────────────────────────────┐
│  rag-kb repo (this repo, Windows local-first)    │
│                                                 │
│  kb —— local Agent memory & knowledge service    │
│     python -m kb serve resident; REST + MCP      │
│     dual protocol                               │
│     memory write / hybrid retrieval / doc-web    │
│     ingestion for AI assistants                 │
└─────────────────────────────────────────────────┘
```

> **Delivery boundary (2026-10-19)**: kb is the only product and external deliverable.
> The default embedding model is the lightweight `BAAI/bge-small-zh-v1.5` (~92MB).

- **Design principles**: local-first, completely free, offline-capable; GPU budget 6GB (default bge-small; BGE-M3 + qwen3:4b = 4.3GB when opted in)
- **Single source of truth**: design docs under `docs/superpowers/specs/` (see Chapter 6 index)

## 2. Current Progress (dashboard)

### kb memory service — v2.0.0 delivery hardening (P1/P2 continuous iteration)

| Milestone | Content | Status |
|---|---|---|
| M1 (N1-N6) | BGE-M3 embedding, ChromaDB storage, BM25+jieba, RRF hybrid retrieval, CLI | ✅ 2026-08-23 |
| M2 (N7-N8) | REST API (memories/search/documents/healthz), 4-level device detection | ✅ |
| M3 (N9-N12) | MCP 8 tools, /ask smart routing (classify/compress/cache/privacy-isolate/cloud-degrade) | ✅ |
| M4 (N13-N16) | doc ingestion (pdf/docx/md/txt), web fetch, dir watcher, README | ✅ 2026-08-24 |
| v1.0.1 hotfix | top_k/mode/empty-content validation → 422, MCP entry validation, SSE charset | ✅ 2026-08-24 |

- Test baseline: **395 green** (full regression on 2026-10-19; historically +68 added by A3.5+A4: reranker/sparse/eval/perf/cli five test files, branch `feature/a-line-remaining`)
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
- **v3 shared memory shipped** (2026-08-31): (client, project) isolation removed; all
  memories/knowledge are shared and client/project remain audit metadata only.
- **Hybrid fusion hardening** (2026-09-01, N32/N33): min-max normalized, evenly weighted
  fusion across active retrieval legs; fixes cross-language/proper-noun recall regressions.
- **v2.0.0 delivery hardening** (2026-10-19, for real first-time users and Agensi delivery):
  - Lightweight default `BAAI/bge-small-zh-v1.5` (~92MB / 512-dim), BGE-M3 still opt-in;
    lazy embedder with `embedder.state/error` exposed on healthz
  - Actionable 503 `EMBED_MODEL_UNAVAILABLE` (REST/MCP) instead of an opaque 500
  - Atomic same-source replacement (vectors computed before deletion); document deletion
    by query parameter supports URLs
  - Blank search/question → 422; `kb eval` isolates governance switches for reproducible runs
  - Windows one-click scripts: `.env` port parsing, non-interactive CPU fallback under
    pythonw, three-pass stop (PID + port + venv-scoped command line)
  - Standalone skill package: `skills/kb-memory/` + `scripts/build_skill_package.py`
    (SKILL.md at zip root; no tests, runtime data or secrets)
  - User-perspective E2E: baseline 28/28 + supplemental 34/34 (isolated auth/bad-model
    instances, CLI, MCP stdio, skill zip)

### Development bus — kb single mainline (2026-08-27 strategic shift)

Project bus: **[ROADMAP.md](ROADMAP.md)**. Single mainline after shift:

| Mainline | Current stage | Next |
|---|---|---|
| **kb memory service** (only development mainline) | **all in-plan nodes delivered**: A1/A2/A3 ✅, A3.5+A4 ✅ (branch `feature/a-line-remaining`, N24-N28); A2.5 only MCP Registry submission left (user action) | new needs: update design docs then initiate (AGENTS.md §6) |
| Side lines | — | frozen (eval-report: no Web UI/multi-user/commercialization; A4 Web UI officially cancelled) |

- **Strategic shift (2026-08-27, per 《Evaluation Report》 dual reports + user decision)**: kb is the core product ("individual dev's local memory MCP server"); if the cross-Agent direction restarts, evaluate A2A protocol compat rather than self-build
- **LICENSE Apache-2.0 added** (eval-report P0 top item, legal-compliance closed)
- Runtime shape: **monitor plain-text default off** — fully usable without local LLM

## 3. Directory Guide

```
rag-kb/
├── AGENTS.md            # AI work rules (handoff read #1)
├── PROJECT.md           # this doc (handoff read #2)
├── ROADMAP.md           # project bus (human entry, tree+progress)
├── README.md            # kb product doc (quick start/MCP mount/endpoint cheat-sheet)
├── kb/                  # kb service source (see design doc 4.2; incl. reranker/sparse/eval)
├── tests/               # kb acceptance tests (395, incl. eval_zh_50.jsonl dataset)
├── scripts/             # one-click start/stop scripts (windows/ + linux/ reserved)
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

# kb tests (inside venv)
venv\Scripts\python.exe -m pytest tests/ -q            # 395
```

## 5. Handoff Guide (new AI act per this)

1. **Read rules**: AGENTS.md (role split, red lines, node-gate system)
2. **Read status**: this doc + the design doc currently in implementation (Chapter 6 index)
3. **Verify environment**: `python -m kb serve` starts, tests green, git clean
4. **Confirm role**: are you the dev AI (write impl) or docs/test AI (write acceptance/verify)? Act per AGENTS.md §5
5. **Continue the bus**: read `ROADMAP.md` for current position (currently active: see bus §2), follow the plan nodes with TDD + node-gate

## 6. Document Index (only valid set)

| Doc | Content |
|---|---|
| `ROADMAP.md` | **project dev bus** (human entry: tree+routes+progress) |
| `docs/USER_GUIDE.md` | **user manual** (human user entry: install/mount/FAQ) |
| `docs/superpowers/specs/2026-08-23-kb-memory-service-design.md` | kb only design (req/arch/API/bench) |
| `docs/superpowers/plans/2026-08-23-kb-dev-nodes.md` | kb node plan N1-N16 (all ✅) |
| `docs/superpowers/specs/2026-08-24-logging-design.md` | logging design (bus A1.2, N17-N18) |
| `docs/superpowers/plans/2026-08-24-p2-roadmap.md` | kb feature route (bus A2-A4) |

## 7. Environment Notes (see AGENTS.md §7)

- torch 2.11.0+cu128 (from Aliyun mirror); HF via hf-mirror.com; Ollama models at `D:\ollama_models`
- qwen3 local MUST use `think:false` (Ollama native /api/chat, not SDK)
- Ollama must be started from Start menu/tray (launching from sandbox terminal causes DB read/write failure)
- BGE-M3 & Ollama models offline-cached; fully usable offline (acceptance criterion)