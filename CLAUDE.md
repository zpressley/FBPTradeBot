# CLAUDE.md

This repo's agent instructions live in `WARP.md` (originally written for Warp, but it's the canonical rules/architecture doc for any AI agent working in this repo, Claude included). Read it before making changes.

In particular, see the "External API Rate Limits (Yahoo especially)" rule under `## Rules` — added 2026-08-08 after an 11-day Yahoo outage that a retry-forever loop with no backoff likely made worse. Any code touching Yahoo's Fantasy Sports API (or any third-party API) needs rate-limit/backoff handling considered from the start, not bolted on after an outage.
