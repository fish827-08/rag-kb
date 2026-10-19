# kb-memory skill package

A self-contained Agent Skill for **kb**, a local-first agent memory and knowledge
service (RAG). This package contains everything needed to install, start,
configure and verify kb locally:

```
SKILL.md       # skill instructions (standard YAML front matter, at package root)
mcp.json       # MCP client configuration template
env.example    # server configuration template (no secrets)
README.md      # this file
LICENSE        # Apache-2.0
```

## What it gives an agent

- Persistent memory across sessions and clients (shared by design, local only).
- Document and web-page ingestion with hybrid retrieval (vector + BM25 fusion).
- 8 MCP tools: `write_memory`, `search_memory`, `read_memory`, `update_memory`,
  `delete_memory`, `add_document`, `add_webpage`, `ask_kb`.
- HTTP fallback endpoints for clients without MCP.
- No LLM required for write/search; `/ask` is optional.

## Quick start

```bash
git clone https://github.com/fish827-08/rag-kb.git
cd rag-kb
python -m venv venv
# Windows: .\venv\Scripts\Activate.ps1    Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
pip install -e .
python -m kb serve
```

Verify: `curl http://127.0.0.1:8000/api/v1/healthz` → `"status":"ok"`.

Mount the MCP server using `mcp.json` (default `http://127.0.0.1:8000/mcp/`).
If you change the port, update both `.env` (`KB_API_PORT`) and the MCP URL.

Optional settings: copy `env.example` to `.env` in the repo root. Never commit
real keys; `.env` is gitignored.

## Package scope

This package is intentionally limited to kb. The repository's experimental
multi-agent collaboration subsystem is **not** part of this skill package and is
not required to run or use kb.

## License

Apache-2.0. See `LICENSE`.
