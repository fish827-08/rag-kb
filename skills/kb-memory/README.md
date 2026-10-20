# kb-memory skill package

A self-contained Agent Skill for **kb**, a local-first agent memory and knowledge
service (RAG). This package contains everything needed to install, discover,
start, configure and verify kb locally:

```
SKILL.md       # skill instructions (standard YAML front matter, at package root)
bootstrap.py   # gated installer/discovery script (stdlib only; requires --yes)
mcp.json       # MCP client configuration template
env.example    # server configuration template (no secrets)
README.md      # this file
LICENSE        # Apache-2.0
```

## What it gives an agent

- Persistent memory across sessions and clients (local only), with physical
  isolation per project: `kb_data/projects/<project>/` plus a shared global store.
- Document and web-page ingestion with hybrid retrieval (vector + BM25 fusion).
- Memory language follows the operating-system language by default
  (`KB_MEMORY_LANG=auto`), with user-defined rules in `~/.kb/rules.md`.
- 8 MCP tools: `write_memory`, `search_memory`, `read_memory`, `update_memory`,
  `delete_memory`, `add_document`, `add_webpage`, `ask_kb`.
- HTTP fallback endpoints for clients without MCP.
- No LLM required for write/search; `/ask` is optional.

## Quick start (ask the user before installing)

Prerequisite: Python 3.10+. Git is not required — kb installs from PyPI.

```bash
python bootstrap.py --status   # discover an existing install
python bootstrap.py            # show the install plan only
python bootstrap.py --yes      # after user confirmation: install + start
```

Installs into `~/.kb/venv`, data in `~/.kb/data`, manifest at `~/.kb/install.json`.
On Linux the CPU torch wheel is installed first to avoid a multi-GB CUDA download.

Manual alternative:

```bash
pip install "kb-memory[ingest]"
python -m kb serve
```

Verify: `curl http://127.0.0.1:8000/api/v1/healthz` → `"status":"ok"`.

Find or restart later: `python bootstrap.py --status` / `python bootstrap.py --start-only`
or `kb where` / `kb autostart enable|disable|status`.

Mount the MCP server using `mcp.json` (default `http://127.0.0.1:8000/mcp/`).
If you change the port, update both the service config and the MCP URL.

Optional settings: copy `env.example` to `.env` next to the working directory or
set the variables in the environment. Never commit real keys; `.env` is gitignored.

## Package scope

This package is intentionally limited to kb. It contains no tests, runtime data,
or secrets, and is not related to any multi-agent collaboration subsystem.

## License

Apache-2.0. See `LICENSE`.
