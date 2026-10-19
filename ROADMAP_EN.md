# ROADMAP.md — Project Development Bus

> **For you**: the whole project at a glance — structure, roadmap, current progress.
> Tree-shaped, from root to leaves.
> AI assistants: read AGENTS.md + PROJECT.md (implementation details); this file is the human-facing bus view.
>
> Last updated: 2026-10-19 ｜ Bus version: v3.2 (kb single mainline; v2.0.0 delivery hardening shipped)

## 0. Vision (one sentence)

Local-first, completely free Agent memory & knowledge service: **kb is the core product**, giving Claude Code / Cursor / custom Agents memory writes, document & web ingestion, and hybrid retrieval; fully usable for storage and retrieval without an LLM.

> **2026-08-27 strategic adjustment** (per the root-folder 《Evaluation Report》 consensus + user decision): development focus is the kb core (retrieval quality / memory governance / usability / ecosystem compliance); the cross-Agent collaboration subsystem is no longer self-built. If that direction restarts, evaluate A2A protocol compatibility rather than building from scratch.

## 1. Bus Tree (branches + routes + progress)

Legend: ✅ done | 🚧 in progress/upcoming | 🔲 queued (top-down priority) | ❌ cancelled

```
rag-kb —— local Agent memory & knowledge service
│
├── 🧠 Mainline: kb memory service (resident foundation)
│   │
│   ├── A1 Service stability ……………………………… ✅ shipped
│   │   ├── ✅ v1.0.1 hotfix (param validation 422 / SSE charset)
│   │   ├── ✅ v1.0.2 JSON charset (TASK-0003/0038/0045)
│   │   ├── ✅ A1.1 watcher missing-dir tolerance (TASK-0005, 2026-08-25)
│   │   └── ✅ A1.2 structured logging (N17-N18 all delivered: config/middleware/endpoints/docs, TASK-0011~0016/0027)
│   │
│   ├── A2 Auth API Key N19-N20 ……………… ✅ delivered (2026-08-27)
│   │   └── ✅ N19 ApiKeyMiddleware + N20 client auto-key (TASK-0062/0064, empty-key no-auth zero-friction, spec §7 all green)
│   │
│   ├── A2.5 Ecosystem compliance & exposure …………………… 🚧 in progress (Eval-report P0 trifecta)
│   │   ├── ✅ LICENSE Apache-2.0 (2026-08-27, top-priority eval item)
│   │   ├── ✅ GitHub mirror migration (github.com/fish827-08/rag-kb, 443-port SSH, 2026-08-27)
│   │   ├── ✅ English README (README_EN.md, bilingual cross-links, 2026-08-27)
│   │   ├── ✅ Sensitive-info governance (git filter-repo history rewrite + pre-push hook + expanded gitignore, 2026-08-27)
│   │   ├── ✅ Agensi standalone skill package (skills/kb-memory + scripts/build_skill_package.py, SKILL.md at zip root, 2026-10-19)
│   │   └── 🔲 MCP official Registry / awesome-mcp-servers submission (awaiting user)
│   │
│   ├── A3 Memory governance (forget/decay/dedup) N21-N23 … ✅ delivered (2026-08-28, TASK-0066~0076)
│   │   ├── ✅ spec set up (dual-layer: no-LLM rules + LLM consolidation framework)
│   │   ├── ✅ access-frequency decay (λ=0.02) + freshness weight (β=0.05, cap 1.3×) — default off, zero behavior change
│   │   └── ✅ semantic dedup (threshold 0.92, 409 block) + kb forget/dedup CLI + governance audit log
│   │
│   ├── A3.5 Retrieval quality ………………………… ✅ delivered (2026-08-28, N24-N27, branch feature/a-line-remaining)
│   │   ├── ✅ reranker (bge-reranker-v2-m3 cross-encoder, KB_RERANK_ENABLED default off)
│   │   ├── ✅ BGE-M3 sparse third leg (sparse head direct-load + inverted index + 3-way RRF, KB_SPARSE_ENABLED default off)
│   │   ├── ✅ minimal eval bench (50-Chinese-QA, kb eval: Recall@1/@5 + MRR + by-difficulty)
│   │   └── ✅ N+1 query fix (store.get_many batch) + BM25 corpus persistence
│   │
│   ├── A4 Usability (CLI first)…………………… ✅ delivered (2026-08-28, N28; Web UI cancelled — eval-report conclusion)
│   │   └── ✅ kb stats (type/hot/stale distribution) + kb ask (terminal RAG; LLM-absent degrades to retrieval hits)
│   │
│   └── A5 v2.0.0 delivery hardening ………………………… ✅ shipped (2026-10-19)
│       ├── ✅ lightweight default model bge-small-zh-v1.5 + lazy embedder/healthz state
│       ├── ✅ actionable 503 on embedding failure + atomic same-source replacement + URL document deletion + blank-query 422
│       ├── ✅ Windows one-click start/stop scripts (.env port / no console prompt hang / three-pass stop)
│       └── ✅ user-perspective E2E 28/28 + 34/34; full regression 395 green
│
├── 🌱 Side lines (start on demand, no mainline resources)
│   └── 🔲 L2 terminal REPL (interactive point at system terminal, backup)
│
└── ❌ Cancelled
    ├── Qdrant (violates zero-resident-constraint)
    └── Web UI (eval-report conclusion; A4 shipped as CLI)
```

## 2. Current Progress (where you are)

```
A1 ✅ → A2 auth ✅ → A2.5 ecosystem 🚧 (only MCP Registry left, user action)
        → A3 memory governance ✅ → A3.5 retrieval quality ✅ → A4 usability ✅ (CLI shipped, Web UI cancelled)
        → A5 v2.0.0 delivery hardening ✅ (2026-10-19)
        —— all in-plan nodes done; new needs: update design docs first, then initiate
```

**Current position** (2026-10-19): **v2.0.0 delivery hardening shipped** — lightweight default model, actionable embedding failures, atomic same-source replacement, Windows one-click scripts and the Agensi standalone skill package all landed; user-perspective E2E 28/28 + 34/34, `tests/` full regression 395 green, manual acceptance pending. A2.5 only MCP Registry submission remains (user action). Strategy basis: root-folder 《Evaluation Report》 dual reports + user decision.

## 3. Priority Principles (why this order)

1. **Foundation first**: kb is every Agent's shared memory layer — if it's unstable everything fails → A1 ranks first
2. **Zero extra residents**: one resident process (`python -m kb serve`), no Qdrant or standalone vector service → lower install/ops cost
3. **Retrieval quality over feature count**: cross-language/proper-noun recall and reproducible evals first → A3.5
4. **Usability last but mandatory**: CLI (stats/ask/eval) first, Web UI explicitly cancelled → A4
5. **Ecosystem compliance in parallel**: LICENSE, bilingual README, secret governance, skill package → A2.5
6. **Fully usable without a local LLM**: storage/retrieval never depend on an LLM; monitor plain-text mode default off

## 4. Four Iron Rules Across the Project

1. **Local-first**: data and models stay on the machine; fully usable offline
2. **Zero extra resident services**: no Qdrant/standalone vector DB or other extra processes
3. **No LLM required for storage & retrieval**: the LLM only enhances `/ask`
4. **Design docs before code**: specs are the single source of truth; new needs update the spec first, advanced via node gates

## 5. Stage Acceptance Criteria

| Stage | Acceptance |
|---|---|
| A1 stable | 7×24 resident no crash; logs debug-able; known bugs zeroed |
| A2 auth | with a key configured, keyless requests are rejected; empty key stays zero-friction; REST/MCP/CLI all pass |
| A3 governance | decay/dedup/freshness default off with zero behavior change; stats and audit visible when enabled |
| A3.5 retrieval quality | 50-QA eval reproducible; rerank/sparse do not regress metrics; failures degrade gracefully |
| A4 usability | `kb stats/ask/eval` usable; ask degrades to retrieval hits without an LLM |
| A5 delivery hardening | a new user can install and run per README; E2E passes; full suite green; skill zip self-contained |

## 6. Risks & Pitfalls

| Core risk | Mitigation |
|---|---|
| Insufficient VRAM/RAM | default `bge-small-zh-v1.5` (~92MB); `KB_DEVICE=cpu` on OOM |
| Swapping embed models breaks dimensions | confirm the DB is empty before switching; the service reuses the build model |
| Embedding model unavailable | 503 `EMBED_MODEL_UNAVAILABLE` (REST/MCP); healthz exposes `embedder.state/error` |
| Same-source re-import loses old data | compute vectors first, then atomic replace; keep old data on failure |
| Windows start/stop leftovers | three-pass stop (PID + port + command line); non-interactive CPU fallback under pythonw |
| Dependency bloat | only packages listed in design doc §3; new deps need a rationale + spec update |

## 7. Detailed Plan Index

| Bus node | Detailed plan doc |
|---|---|
| A1.2 logging | `docs/superpowers/specs/2026-08-24-logging-design.md` (N17-N18) |
| A2-A4 | `docs/superpowers/plans/2026-08-24-p2-roadmap.md` |
| A2 | `docs/superpowers/specs/2026-08-26-p2-auth-design.md` (delivered, 2026-08-27) |
| A3.5 | `docs/superpowers/specs/2026-08-28-a35-retrieval-quality-design.md` (delivered, 2026-08-28) |
| A4 | `docs/superpowers/specs/2026-08-28-a4-cli-design.md` (delivered, 2026-08-28; Web UI cancelled) |
| A5 | `docs/superpowers/specs/2026-08-23-kb-memory-service-design.md` §3.1 (2026-10-19 lightweight-first revision) |
| Project status/relay | `PROJECT.md` (AI handoff entry) |
| Usage | `docs/USER_GUIDE.md` (human entry) |
