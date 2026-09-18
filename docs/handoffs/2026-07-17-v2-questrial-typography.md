# Market Intelligence v2 — Questrial Typography Pass

**Date:** 2026-07-17
**Branch:** `task-1-screener-fields` (uncommitted — changes are in the working tree only)
**Files touched:** `src/web/v2/index.html`, `src/web/v2/app.js`
**Status:** Implemented, deployed to dev via bind mount, **not yet visually verified in a browser**

---

## What Was Asked

Austin doesn't like how "robotic" the fonts feel in four places in the v2 dashboard and wants them switched to **Questrial**:

1. Everything on the **Watchlist** page
2. The small font on the **Overview** page (e.g. the Composite Signal readout)
3. The **parameter font** on the **Scanner** page
4. The font used **in the cards** on the **Charts** page

## What Was Done

`src/web/v2/index.html` is a single-file SPA shell — all CSS lives in one `<style>` block, and `IBM Plex Mono` / `IBM Plex Sans Condensed` were used pervasively and **reused across pages** (e.g. `.tr-symbol`/`.ticker-row` render both the Watchlist tickers tab *and* Scanner's "Stock Performance" strip; `.scn-field-label`/`.scn-field-input` render Scanner's filter sheet *and* Backtester *and* Charts controls; `.overview-card-title` renders Overview cards *and* Edit-Watchlist cards). A global font swap would have bled into pages nobody complained about, so the fix is **scoped by ancestor**, not by class alone.

### 1. Font loading
- Added `Questrial` to the existing Google Fonts `<link>` in `<head>`.
- Added `--font-ui: 'Questrial', sans-serif;` to `:root`.
- **Caveat:** Questrial only ships in a single (400/regular) weight on Google Fonts. Anywhere the old CSS asked for `font-weight: 600/700`, the browser will synthesize ("faux") bold. This is expected, not a bug — flag it to Austin if the synthetic bold looks off anywhere.

### 2. Watchlist page — full override
- Wrapped the entire render output of `renderWatchlistView()` and `renderEditWatchlistView()` in `app.js` with a new `<div class="watchlist-view">` container (previously no wrapping element existed).
- `.watchlist-view { font-family: var(--font-ui); }` handles everything that inherits the body font by default (ticker names, option-card names, list messages, etc).
- Everything with an *explicit* `font-family` override (mono symbols, tabs, pills, pagination, edit chips/buttons) needed an explicit descendant rule too, since inheritance never beats a direct match: `.watchlist-view .tr-symbol`, `.tr-col`, `.tr-ta-pill`, `.col-tab`, `.ch-col`, `.sub-tab`, `.oc-symbol`, `.oc-meta`, `.oc-highlight`, `.oc-metrics`, `.page-info-text`, `.overview-card-title`, `.scn-sheet-apply`, `.scn-sheet-section-title`, `.scn-field-label`, `.scn-field-input`, `.edit-back-btn`, `.edit-chip`, `.edit-chip-input`, `.edit-weight-sum-badge`, `.edit-status`.
- Because this is scoped to `.watchlist-view`, Scanner's reuse of `.tr-symbol`/`.col-tab` (Stock Performance strip) and Backtester/Charts' reuse of `.scn-field-*` are **untouched**.

### 3. Overview page — small labels only
- Left the big hero numbers alone (`.posture-detail-score`, `.vix-spot`, `.gex-value` — all 22px mono) since those weren't the complaint and mono serves a real purpose for tabular numerals at that size.
- Changed the small (10–13px) label/readout text: `.composite-score` (global status-bar badge, named explicitly by Austin), plus `.overview-section .overview-card-title`, `.posture-detail-label`, `.sector-pill-ticker`, `.sector-tf-btn`, `.sector-toggle-btn`, `.vix-changes`, `.gex-label`, `.gex-avg`, `.breadth-label`, `.breadth-value`, `.breadth-ad` — all scoped under `.overview-section` (the wrapper `renderOverviewView()` already emits) except `.composite-score`, which lives in the persistent status bar outside any page container and was changed globally on purpose.
- `.overview-card-title` is *also* used by Backtester ("Exit Reasons", "Parameter Sweep") — deliberately **not** touched there since Austin didn't mention Backtest.

### 4. Scanner page — parameter/filter sheet only
- Scanner's filter sheet renders inside `#scn-sheet-root` (id set in `scanner.js`), which is exclusive to Scanner — not shared with Backtester/Charts even though they use the *same CSS classes* (`.scn-field-label`, `.scn-field-input`, `.scn-sheet-section-title` are a generic reused form-field component). Scoped every rule to `#scn-sheet-root .scn-*` so Backtester and Charts' own use of those classes is untouched.
- Also changed `.scn-filters-btn` and `.scn-chip` (the "Filters" button and the active-param chip row below the header) globally — confirmed via grep they're exclusive to `scanner.js`.

### 5. Charts page — result cards only
- `.anlys-symbol`, `.anlys-subtitle`, `.anlys-chip-val`, `.anlys-chip-lbl` changed globally — confirmed exclusive to `analysis.js`'s `.anlys-card` component.
- Left `.anlys-ctrl-label`/`.anlys-ctrl-group` (the indicator controls row *above* the cards) alone since Austin said "in the cards" specifically.

## Verification Done

- `node -c app.js` — no syntax errors.
- Python brace-balance check on `index.html` — balanced.
- `curl https://dev-mi.austin10berge.com/v2/index.html` and `/v2/app.js` — dev is bind-mounting `src/web/v2` directly (per `docker-compose.local.yml`), confirmed the served files contain the new `Questrial` references and `watchlist-view` wrapper. No container rebuild/restart needed for these static-file edits.

## 2026-07-17 update — Austin reviewed the live page

Austin has now seen the Questrial pass live on dev (bind mount, so his browser picks it up directly). Verdict: **he likes the Watchlist page-wide change as-is — the only remaining complaint is the ticker symbol font** (`.tr-symbol` in the Tickers tab, `.oc-symbol` in CSP/LEAPS cards), which under this pass renders as Questrial with synthetic bold (the CSS asks for weight 600; Questrial only ships 400).

> Note: a mid-session misread briefly reverted the page-wide block down to ticker-only — the opposite of what Austin wanted. It has been restored exactly; the CSS in the tree now matches the original pass described above.

**Resolution:** ticker symbols (`.tr-symbol`, `.oc-symbol`, still scoped under `.watchlist-view`) were switched to **Outfit** at a true 600 weight — geometric like Questrial so the page stays cohesive, but with real bold weights so tickers don't get the smeared synthetic bold. Outfit (500/600/700) added to the Google Fonts link. Austin reviewed live on dev and approved; committed and pushed to `task-1-screener-fields`.

## Playwright blocker — root cause found, still unresolved

The earlier guess ("tools weren't registered mid-session, fresh session will fix it") was wrong. In this session `mcp__playwright__*` tools **are** registered and callable, but:

- `mcp__playwright__browser_navigate` to `https://dev-mi.austin10berge.com/v2` → `net::ERR_CONNECTION_REFUSED`, consistently, across retries.
- `curl https://dev-mi.austin10berge.com/v2/index.html` from this shell → `200`, real content.
- `dev-mi.austin10berge.com` resolves to `10.0.1.20` — an internal LAN address, not truly public despite the CLAUDE.md note calling it "public, no auth needed."
- `mcp__playwright__browser_navigate` to `https://example.com` → succeeds.

Conclusion: the Playwright browser process runs in a network context that has general internet access but **no route to the 10.0.1.0/24 LAN**, while this shell (where `curl` runs) does. This is an environment/sandboxing constraint, not a session-freshness issue — starting yet another fresh session will not fix it.

**Still no visual verification has happened.**

**Next step (needs a human or a differently-networked agent):**
1. Someone with LAN access (or a Playwright MCP instance actually routed onto 10.0.1.0/24) needs to screenshot all four pages: Watchlist (Tickers/CSP/LEAPS tabs + Edit Watchlist — ticker symbols only should look different now), Overview, Scanner (open the Filters sheet), Charts.
2. Check for: the Questrial ticker symbols rendering correctly (synthetic-bold from the 600-weight rule — Questrial only ships 400 — may look off, flag it), no layout shift from the `.watchlist-view` wrapper div, and that Scanner's Stock Performance strip did **not** pick up Questrial on its tickers (regression check on the scoping).
3. Once Austin confirms it looks right, commit `src/web/v2/index.html` and `src/web/v2/app.js` (currently uncommitted on `task-1-screener-fields` — do not sweep up the unrelated untracked files sitting in the working tree, e.g. `src/algo_detective/session*.py`, `docs/superpowers/`, `data/detective/` — those predate this change and aren't part of this work).
