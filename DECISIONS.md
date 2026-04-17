# Architectural Decisions

Running log of major decisions made during development. Each entry captures the context, choice, and reasoning so future sessions can understand why things are built this way.

---

## ADR-001: Notification System — NTFY.sh Primary

**Date:** 2026-04-16
**Status:** Accepted

**Context:** Need a push notification system for iOS delivery. Options considered:
- Home Assistant notify service (already running on homelab)
- Pushover (dedicated push service, paid)
- NTFY.sh (open-source, self-hostable, free)

**Decision:** NTFY.sh as primary, Home Assistant as fallback.

**Reasoning:** NTFY.sh is simpler (single HTTP POST), doesn't require HA to be running, supports markdown, and can be self-hosted later if needed. HA notification is kept as fallback for reliability.

---

## ADR-002: Project Structure — Single Python Package

**Date:** 2026-04-16
**Status:** Accepted

**Context:** How to organize code for a project that will grow to include multiple pipelines (evening sentiment, daily market data, etc.).

**Decision:** Single `src/` package with subpackages (`fetchers/`, `processing/`, `synthesis/`, `notify/`). Each pipeline gets its own orchestrator in `src/`.

**Reasoning:** Shared infrastructure (DB, config, notification, models) is used across pipelines. A monorepo with shared packages reduces duplication while keeping pipelines independent.

---

## ADR-003: LLM Provider — Gemini Free Tier Primary

**Date:** 2026-04-16
**Status:** Accepted

**Context:** Need an LLM for ~150 word market digest synthesis. Budget target: <$1/month.

**Decision:** Gemini 2.0 Flash (free tier) as primary. GPT-4o-mini as future fallback. Raw signal summary as offline fallback when no API key is configured.

**Reasoning:** Gemini free tier covers our token budget easily (~150 input + 200 output per day). The offline fallback ensures the pipeline still delivers useful output even without LLM access.

---

## ADR-004: Data Fetcher Architecture — Async with Graceful Degradation

**Date:** 2026-04-16
**Status:** Accepted

**Context:** Data is fetched from 4+ external sources. Any source can fail at any time.

**Decision:** Async fetchers via `httpx.AsyncClient`, run in parallel via `asyncio.gather`. Each fetcher has a `safe_fetch()` wrapper that catches all exceptions and returns `None` on failure.

**Reasoning:** Pipeline should never fail completely because one source is down. Partial data is still useful — the LLM synthesis adapts to whatever signals are available.

---

## ADR-005: Storage — SQLite with WAL Mode

**Date:** 2026-04-16
**Status:** Accepted

**Context:** Need historical storage for signal tracking, rolling averages, and future backtesting.

**Decision:** SQLite via stdlib `sqlite3`, WAL mode, stored in Docker volume.

**Reasoning:** Zero additional infrastructure. WAL mode allows concurrent reads during writes. Migration to PostgreSQL is straightforward later if needed for Grafana integration.
