# Overview Tab: LLM/VIX Reliability Fixes + GEX/Breadth Coloring

**Date:** 2026-07-15

## Summary

Four Overview-tab issues, three of which trace back to two root causes rather than four separate bugs:

1. **LLM summary not showing** — Claude CLI (prod's primary provider) fails on essentially every nightly pipeline run; when the Gemini fallback also hits a transient error, the digest is cached with a static "LLM unavailable" placeholder for the rest of the day.
2. **"Add" SaaS / Semiconductors-Memory themes** — not a missing-feature request; both themes are already defined in `SINGLE_TICKER_THEMES` but get silently dropped by the same flaky-batch-download bug as #3.
3. **VIX widget says unavailable** — an unretried 2-ticker `yf.download()` batch call fails outright when either leg is missing from the response.
4. **GEX/Breadth coloring** — new feature: color each widget only where the metric has a genuine, well-established bullish/bearish reading.

---

## 1. LLM Summary Reliability

**Root cause** (confirmed via Loki logs across 07-09, 07-10, 07-11, 07-14, 07-15): prod's `.env` sets `LLM_PROVIDER=claude`, making `_call_claude_cli()` the primary path in `src/synthesis/llm.py`. It has failed on every sampled night — either `exited with code 1` (empty stderr) or a 120s timeout — before falling back to Gemini. Gemini usually succeeds, but on 07-14 (`503 UNAVAILABLE`) and 07-15 (`Temporary failure in name resolution`) it also failed transiently, with no retry, producing the static fallback string from `_fallback_summary()`. That string is a valid non-empty `llm_summary`, so the frontend renders it as-is — nothing frontend-side is broken.

**Fix — code (`src/synthesis/llm.py`):**
- Add a small retry helper around `_call_gemini()`: up to 2 retries, ~2s then ~4s backoff.
- Retry only on transient failure classes — network/connection errors (e.g. `httpx`/DNS-style exceptions surfaced through the `google-genai` client) and `503`/`UNAVAILABLE` responses. Do **not** retry on auth or quota errors (4xx from the API) — those won't recover within the request lifetime and would just add latency before falling back.
- No change to `_call_claude_cli()`'s own retry behavior — its failures aren't transient-looking (consistent exit code 1 / timeout across many days), so retrying it would just extend the nightly pipeline runtime for no benefit.

**Fix — prod config (manual, user-applied):**
- Set `LLM_PROVIDER=gemini` in prod's `.env` (currently `claude`). This skips the 100%-failing Claude CLI attempt entirely, removing up to 120s of dead time from every nightly pipeline run and making Gemini (now with retry) the sole, sufficient path.
- Add `LLM_PROVIDER` to `.env.example` with a comment — it exists in `src/config.py` but was never documented, which is likely how prod ended up pinned to `claude` without anyone noticing it was broken.
- I have no SSH/prod access (per `CLAUDE.md`); I'll hand the user the exact `.env` diff and the restart command to run manually.

**Out of scope:** diagnosing *why* the Claude CLI itself fails on prod (auth/token issue inside the mounted `~/.claude.json`, most likely) — no prod access to iterate on this interactively, and switching primary provider to Gemini makes it moot for now.

---

## 2. SaaS / Semiconductors-Memory Themes

No code change beyond §3. `SINGLE_TICKER_THEMES` in `src/fetchers/thematic_etf.py` already contains `"SaaS": "IGV"` and `"Semis/Memory": "SMH"`. Verification: after §3 lands, confirm via the live dev Overview tab (Playwright) that both appear consistently across repeated loads.

---

## 3. VIX Unavailable + Flaky Theme Drops

**Root cause:** `src/fetchers/market_overview.py` fetches VIX (`_fetch_vix`, 2 tickers) and themes (`_fetch_themes`, ~21 tickers across singles + baskets) as single `yf.download()` batch calls with no retry. Live-testing `/api/market-overview` twice returned a different random subset of themes each time, and Loki shows VIX failing outright across three separate days (`VIX data missing from download: '^VIX'` / `'^VIX3M'` / `VIX or VIX3M returned no data`) — consistent with transient Yahoo Finance rate-limiting silently dropping tickers from large or unlucky batch requests.

**Fix (`src/fetchers/market_overview.py`):**
- Add a shared retry helper, e.g. `_download_with_retry(tickers: str, **kwargs)`, wrapping `yf.download`: 2 retries, ~1.5s backoff, reusing the same `asyncio.to_thread` pattern already in use.
- `_fetch_vix()` and `_fetch_sectors()` call the helper instead of `yf.download` directly — no other logic changes.
- `_fetch_themes()`: split the combined ticker list into chunks of ~6 tickers, issue one `_download_with_retry()` call per chunk, gather all chunks concurrently (`asyncio.gather`), then merge results before the existing `_extract()` logic runs. This bounds the blast radius of a single flaky call to ~6 tickers instead of all 21, on top of the retry.
- No change to the existing partial-failure caching behavior in `src/api/main.py` (`has_partial_failure` / 60s short-TTL cache) — retries happen inside the fetch, so a still-failing-after-retries field continues to behave exactly as it does today (returns `None`, gets the existing "Unavailable" treatment, self-heals within 60s).

---

## 4. GEX / Breadth Coloring

Color only where the metric has a genuine, one-directional bullish/bearish reading — not every numeric field.

**GEX** (`src/web/v2/app.js` `renderGex()` + CSS): color `.gex-value` — green (`var(--tv-green)`) when `value_b >= 0`, red (`var(--tv-red)`) when negative. Rationale: GEX *sign* is the standard genuine signal (negative gamma → dealers hedge by selling into drops and buying into rallies, amplifying moves and raising volatility risk; positive gamma → dealers dampen moves). The existing bucket label (`Low/Moderate/High/Extreme Positive`, `Negative`) already conveys intensity — no change there. The trend arrow (Rising/Falling/Flat) stays neutral-colored: direction of change in GEX doesn't reliably map to bullish or bearish on its own.

**Breadth** (`src/web/v2/app.js` `renderBreadth()` + CSS): the `pct_above_200ma` bar already has independent green/yellow/red thresholds (≥60 / 40–60 / <40) — left as-is, it's a reasonable per-metric gauge and not part of this change. Add color to the currently-neutral A/D ratio line only when both breadth signals agree:
- Green text when `pct_above_200ma > 50 AND ad_ratio > 1` (majority of stocks above their 200-day MA, more advancers than decliners — both bullish).
- Red text when `pct_above_200ma < 50 AND ad_ratio < 1` (both bearish).
- Default/muted color otherwise (signals disagree — genuinely mixed breadth, no color asserted).

---

## Implementation Scope

**`src/synthesis/llm.py`** — retry wrapper for `_call_gemini()`.

**`.env.example`** — document `LLM_PROVIDER`.

**`src/fetchers/market_overview.py`** — `_download_with_retry()` helper; use it in `_fetch_vix()` and `_fetch_sectors()`; chunk + parallelize `_fetch_themes()`.

**`src/web/v2/app.js`** — `renderGex()` sign-based coloring; `renderBreadth()` A/D-line agreement-based coloring.

**`src/web/v2/index.html`** (CSS) — `.gex-value.positive` / `.gex-value.negative`; `.breadth-ad.positive` / `.breadth-ad.negative` (or equivalent class names matching existing `.positive`/`.negative` convention used elsewhere, e.g. sector pills).

**Manual, user-applied (prod, no SSH access from here):**
- Edit prod `.env`: `LLM_PROVIDER=gemini`.
- Restart the affected prod service(s) — exact command to be provided at handoff.

---

## Testing

- `docker compose run --rm test python3 -m pytest tests/ --ignore=tests/test_stock_screener.py` — full suite, particularly any existing tests around `market_overview.py` fetch logic and `llm.py` provider fallback.
- Manual/Playwright verification against **dev** (`https://dev-mi.austin10berge.com`):
  - Reload the Overview tab several times; confirm VIX renders (not "Unavailable") and themes consistently include SaaS and Semis/Memory.
  - Confirm GEX value renders green/red matching current sign.
  - Confirm Breadth A/D line renders green/red/neutral matching the agreement logic, cross-checked against the raw `/api/market-overview` response.
- The LLM-summary code fix (Gemini retry) is difficult to trigger on-demand since failures were transient/rate-based; validate via Loki log review after the next nightly pipeline run(s) rather than a synthetic test, plus confirm existing `llm.py` unit tests (if any) still pass with the new retry path mocked.

---

## Out of Scope

- Investigating/fixing why the Claude CLI itself fails on the prod host.
- Backfilling or re-running past digests that got the "LLM unavailable" placeholder.
- Changing the existing `pct_above_200ma` bar's threshold coloring.
- Adding new themes beyond what's already defined in `SINGLE_TICKER_THEMES` / `BASKET_THEMES`.
- Persisting or exposing retry/chunk configuration as settings — values are hardcoded constants.
