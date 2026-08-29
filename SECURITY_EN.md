# Security Policy

> kb is a local-first agent memory & knowledge service, designed by default for
> **single-machine, single-user** scenarios.
> This document outlines the default security boundary, known attack surface,
> and hardening measures.

## Default Security Boundary

| Layer | Default Behavior | Notes |
|---|---|---|
| Network | `127.0.0.1:8000` (loopback only) | Override via `KB_API_HOST`; binding non-loopback without auth prints a loud warning + warning log (N29) |
| Auth | `KB_API_KEY` empty = no auth (zero-friction local) | Non-empty = Bearer / X-API-Key auth (N19), `/healthz` whitelisted |
| Data | All local (ChromaDB + logs), nothing uploaded | Fully usable offline; no telemetry |
| Sensitive namespaces | `KB_SENSITIVE_NAMESPACES` forces local | Matched namespaces never go to a cloud LLM |

## Hardening Recommendations

1. **Always set a key when binding externally**: if you need `KB_API_HOST=0.0.0.0` (LAN/container access), set `KB_API_KEY` and terminate TLS behind a reverse proxy (nginx/caddy).
2. **Key handling**: keep the key only in your local `.env` (gitignored). Never commit it to the repo, code, or commit history.
3. **Web ingestion**: `/api/v1/webpages` fetches arbitrary URLs — don't expose it to untrusted callers.
4. **Sensitive data**: add privacy-bearing namespaces to `KB_SENSITIVE_NAMESPACES` so related Q&A stays on the local model.
5. **Directory watcher**: `KB_WATCH_DIR` is auto-ingested; avoid pointing it at directories containing sensitive system files.

## Known Limitations (Honest Disclosure)

- Single API key; no per-user permissions / multi-tenant isolation (local single-user positioning).
- No built-in TLS; terminate at an external reverse proxy.
- No rate limiting or request audit (limited impact in a loopback scenario).
- MCP tools share the same auth stack as REST: covered by `KB_API_KEY`.

## Reporting Vulnerabilities

Report via the private channels of the GitHub repo (Security Advisories / direct message to the maintainer) — do not open a public issue.
Fixes will be released as soon as verified, with credit to the reporter in the release notes.