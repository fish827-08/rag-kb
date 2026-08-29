# ROADMAP.md — Project Development Bus

> **For you**: the whole project at a glance — structure, roadmap, current progress.
> Tree-shaped, from root to leaves.
> AI assistants: read AGENTS.md + PROJECT.md (implementation details); this file is the human-facing bus view.
>
> Last updated: 2026-08-28 ｜ Bus version: v3 (strategic shift: B-line frozen to maintenance, A-line is the only development mainline)

## 0. Vision (one sentence)

Local-first, completely free Agent memory & knowledge service: **kb memory service is the core product** (main development); the orchestra collaboration system is in **maintenance mode** (personal scaffold, no new features).

> **2026-08-27 strategic adjustment** (per the root-folder 《Evaluation Report》 consensus + user decision): development focus is on A-line kb core (retrieval quality / forgetting / ecosystem compliance); B-line orchestra stays personal-use (automation is sufficient for personal collaboration), new features frozen (B4 adaptive not done, B3 model tiering cancelled).

## 1. Bus Tree (branches + routes + progress)

Legend: ✅ done | 🚧 in progress/upcoming | 🔲 queued (top-down priority) | ❌ cancelled

```
rag-kb production-grade local multi-Agent system
│
├── 🧠 Main Line A: kb memory service (resident foundation, never down)
│   │
│   ├── A1 Service stability ……………………………… ✅ shipped
│   │   ├── ✅ v1.0.1 hotfix (param validation 422 / SSE charset)
│   │   ├── ✅ v1.0.2 JSON charset (dual-worker experiment delivery)
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
│   │   └── 🔲 MCP official Registry / awesome-mcp-servers submission (awaiting user)
│   │
│   ├── A3 Memory governance (forget/decay/dedup) N21-N23 … ✅ delivered (2026-08-28, TASK-0066~0076)
│   │   ├── ✅ spec set up (dual-layer: no-LLM rule TTL+similarity dedup / LLM smart consolidation)
│   │   ├── ✅ access-frequency decay (λ=0.02) + freshness weight (β=0.05, cap 1.3×) — default off, zero behavior change
│   │   └── ✅ semantic dedup (threshold 0.92, 409 block) + kb forget/dedup CLI + governance audit log
│   │
│   ├── A3.5 Retrieval quality ………………………… ✅ delivered (2026-08-28, N24-N27, branch feature/a-line-remaining)
│   │   ├── ✅ reranker (bge-reranker-v2-m3 cross-encoder, KB_RERANK_ENABLED default off)
│   │   ├── ✅ BGE-M3 sparse third leg (sparse head direct-load + inverted index + 3-way RRF, KB_SPARSE_ENABLED default off)
│   │   ├── ✅ minimal eval bench (50-Chinese-QA, kb eval: Recall@1/@5 + MRR + by-difficulty)
│   │   └── ✅ N+1 query fix (store.get_many batch) + BM25 corpus persistence
│   │
│   └── A4 Usability (CLI first)…………………… ✅ delivered (2026-08-28, N28; Web UI cancelled — eval-report conclusion)
│       └── ✅ kb stats (type distribution/hot records/stale distribution) + kb ask (terminal RAG; LLM-absent degrades to retrieval hits)
│
├── 🤖 Main Line B: orchestra collaboration (❄️ maintenance mode — frozen since 2026-08-27, personal scaffold)
│   │
│   ├── ✅ v1 MVP: task board + single/dual worker parallelism (real-machine verified)
│   │
│   ├── B1 P0 three-role loop ………………………… ✅ shipped (difficulty ★☆☆)
│   │   ├── ✅ B1.1 designer role (designer-prompt.md + first run, TASK-0007)
│   │   ├── ✅ B1.2 worker registry (register/workers cmds, TASK-0006)
│   │   ├── ✅ B1.3 kb message window (protocol live; command-ized TASK-0009)
│   │   ├── ✅ B1.4 batch kickoff + round fuse (protocol v1.2, ≤5 rounds per wake, TASK-0018/0020 field-validated)
│   │   ├── ✅ B1.5 restart control (protocol v1.1 live, first application accepted)
│   │   ├── ✅ B1.6 task-branch mode (first three-branch merge; worktree physical isolation, TASK-0025, protocol v1.3)
│   │   ├── 🚧 B1.7 coordinator skill (relay mechanism: coordinator-prompt.md wake + relay-state section; skill-ization pending)
│   │   ├── ✅ B1.8 simple dashboard (watch + HTML /dashboard + nav page, TASK-0010/0014/0019)
│   │   └── ✅ B1.9 local monitor agent (on-demand, qwen3:4b digest into monitor channel, TASK-0017/0021)
│   │
│   ├── B2 P1 bidirectional feedback loop ……………………… ✅ shipped (2026-08-26, difficulty ★★☆)
│   │   ├── ✅ three feedback nodes (pre-execution/summary/milestone) → precheck/review field-validated (FBK-0001~0003)
│   │   ├── ✅ structured feedback types (feedback.py: objection/risk/clarify w/ required fields + iron rules, TASK-0032)
│   │   └── ✅ per-node round quotas (2-2-5 hard gate + over-limit arbitration + comm:feedback archive, TASK-0033; dynamic in B3)
│   │
│   ├── B3 P2 cost refinement ……………………… ✅ frozen-shipped (2026-08-27, difficulty ★★★; 5/6 landed, model tiering cancelled)
│   │   ├── ✅ staged context slicing (protocol v1.6 §14.1, three-role prompts, TASK-0051/0052)
│   │   ├── ✅ rolling window (keep recent 3 rounds, older auto-digested, §14.2)
│   │   ├── ✅ dynamic round quota (simple 3/medium 5/complex 8, b3.py + card link + watch rounds column + DispatchAgent warning, TASK-0053/0054/0060/0058)
│   │   ├── ✅ relation window (≤5 related cards, 6th archived as summary, relation.py, TASK-0055)
│   │   └── ✅ auto incremental digest (every 2 rounds to kb, resume on interrupt, TASK-0053/0054)
│   │
│   ├── B3+ Scheduler/monitor architecture ………………………… ✅ delivered (2026-08-26, dialogue-finalized)
│   │   ├── ✅ DispatchAgent 4 anomaly rules (pool shortfall/claimed timeout/done backlog/open FBK no verdict, TASK-0048)
│   │   ├── ✅ comm:dispatch broadcast + list-comm support (TASK-0049/0057)
│   │   ├── ✅ LLM-decoupled degradation + plain-text default off (monitor_llm=off/auto, TASK-0059/0065) → fully usable without local LLM by default
│   │   └── ✅ coordinator loop resident auto-verify (merge→test→verify→push, incl. open-FBK block & auto broadcast)
│   │
│   └── B4 P3 adaptive optimization ……………………… ❌ cancelled (2026-08-27 strategic shift; if restarted, evaluate A2A protocol compat rather than self-build)
│       (round-threshold self-learning/complexity prediction/cost-quality balance/experience reuse all cancelled along with it)
│
│   ── ❄️ B-line maintenance discipline (since 2026-08-27):
│       bug fixes only + keep tests green + docs sync; coordinator loop/dashboard/message window keep running;
│       no new features, no protocol extension, no performance optimization. A-line tasks may keep using B-line collaboration (dogfooding).
│
│   ── 🔧 B5 mount-residency refactor (2026-08-28 user instruction restarted B-line dev, the exception to maintenance freeze):
│       separate branch feature/orchestra-mount; B5-0~B5-4 delivered (mount.py mount-state module + 6 CLIs +
│       mount-loop protocol + sub/parent coordinator layering + robotic-arm heartbeat monitoring, orchestra 240 tests green);
│       B5-5 real-machine experiment awaiting human verification before merge to main.
│
├── 🌱 Side lines (start on demand, no mainline resources)
│   ├── 🔲 L2 terminal REPL (interactive point at system terminal, backup)
│   └── 🔲 local stats worker (qwen3 daily report/dashboard stats, depends on B1.2)
│
└── ❌ Cancelled
    └── Qdrant (violates zero-resident-constraint)
```

## 2. Current Progress (where you are)

```
Main A: A1 ✅ → A2 auth ✅ → A2.5 ecosystem 🚧 (only MCP Registry left, user action)
        → A3 memory governance ✅ → A3.5 retrieval quality ✅ → A4 usability ✅ (CLI shipped, Web UI cancelled)
        —— all in-plan A-line nodes done; new needs: update design docs first, then initiate
Main B: ❄️ maintenance mode (B1/B2/B3/B3+ frozen-shipped, B4 cancelled) — 2026-08-27 strategic shift
```

**Current position** (2026-08-28): **all in-plan A-line nodes delivered** — A3 governance (TASK-0066~0076), A3.5 retrieval quality and A4 CLI (branch `feature/a-line-remaining`, N24-N28, full regression 584 green) all landed; A2.5 only MCP Registry submission remains (user action). B-line ❄️ maintenance (B5 mount residency is the exception, on a separate branch). Strategy basis: root-folder 《Evaluation Report》 dual reports + user decision. See coordinator-prompt.md "later plans".

## 3. Priority Principles (why this order)

1. **Foundation first**: kb is every agent's shared memory & task board — if it's unstable everything fails → A1 ranks first
2. **Demand first**: multi-agent kickoff immediately hits "shouting for communication, unclear identity, restart collisions" → B1 registry/message-window/restart-control done first
3. **Easy to hard**: B1(★) → B2(★★) → B3(★★★) → B4(★★★★), each stage verified before advancing (at least a week stable)
4. **Hard & low-frequency later**: P3 adaptive, Web UI are nice-to-haves, do last
5. **Cost grows with capability**: each upgrade must land matching cost control (B1 round fuse, B2 feedback quotas, B3 context management) — forbid "capability up, cost flying"

## 4. Four Iron Rules Across All Stages

1. **Task-level context isolation**: agent holds only the current single-task context at a time; switching clears it; no cross-task accumulation
2. **Round hard limit**: max rounds per task/discussion node; force truncation to arbitration on exceed; no infinite debate
3. **Memory auto-persist**: on completion/termination/round-exceed, key conclusions auto-written to kb, agent context cleared — **stateless Agent + stateful knowledge base**
4. **Incremental loading**: discussions don't fully retain history; core conclusions retrieved by kb search

## 5. Stage Acceptance Criteria

| Stage | Acceptance |
|---|---|
| A1 stable | 7×24 resident no crash; logs debug-able; known bugs zeroed |
| B1 P0 | full three-role single-task chain: human→coordinator→designer split→worker exec→designer accept→coordinator deliver; no role overreach, no round overrun, key info in kb |
| B2 P1 | worker feedback drives plan improvement; never over quota; feedback traceable |
| B3 P2 | same-complexity token total 40%+ lower than B2; interrupted tasks resume context from kb |
| B4 P3 | task success up, token continuous improvement, key params no manual tuning |

## 6. Risks & Pitfalls

| Stage | Core risk | Mitigation |
|---|---|---|
| B1 | role boundaries blur; coordinator meddles in tech | coordinator has no tech-tool permissions; tech judgment all via designer |
| B2 | feedback spam becomes infinite debate | structured feedback (objection without alternative auto-rejected) + hard rounds |
| B3 | auto-digest loses key info | core decisions/params/acceptance keep required tags; post-digest validation |
| B4 | over-optimization rigidity | keep manual intervention entry; monthly strategy calibration |
| All | worker restart kills others' DB writes | restart right only to coordinator (B1.5 protocol) |

## 7. Detailed Plan Index

| Bus node | Detailed plan doc |
|---|---|
| A1.1 / B1.2-B1.7 | `orchestra/docs/superpowers/plans/2026-08-24-orchestra-v2-iteration.md` |
| A1.2 logging | `docs/superpowers/specs/2026-08-24-logging-design.md` (N17-N18) |
| A2-A4 | `docs/superpowers/plans/2026-08-24-p2-roadmap.md` |
| B2 | `docs/superpowers/specs/2026-08-26-b2-feedback-design.md` (shipped, 2026-08-26) |
| B3 | `docs/superpowers/specs/2026-08-26-b3-cost-control-design.md` (in progress, 5/6) |
| A2 | `docs/superpowers/specs/2026-08-26-p2-auth-design.md` (delivered, 2026-08-27) |
| A3.5 | `docs/superpowers/specs/2026-08-28-a35-retrieval-quality-design.md` (delivered, 2026-08-28) |
| A4 | `docs/superpowers/specs/2026-08-28-a4-cli-design.md` (delivered, 2026-08-28; Web UI cancelled) |
| B4 | spec after B3 ships (design-then-code) |
| coordinator relay | `orchestra/coordinator-prompt.md` (wake prompt + relay-state section, sole coordinator handoff entry) |
| project status/relay | `PROJECT.md` (AI handoff entry) |
| usage | `docs/USER_GUIDE.md` (human entry) |