# Market Intelligence — Next Steps

## Government / Policy News

**Expand Alpha Vantage news topics** (1-liner)
- Add `economy_fiscal` to `_AV_TOPICS` in `src/fetchers/news.py` — covers tariffs, government spending, trade policy
- Update `_RELEVANT_TOPICS` set to include `"Economy - Fiscal"`

**Dedicated policy/tariff RSS fetcher** (small new fetcher)
- Pull from Reuters Politics or AP News RSS (free, no API key)
- Produce a `Signal` with `value=0.0` (informational, like News)
- Add a `=== POLICY & GOVERNMENT NEWS ===` block to the LLM prompt
- Update `SYSTEM_PROMPT` to instruct LLM to call out tariff-sensitive sectors explicitly (materials XLB, autos, semis with TSMC exposure, retailers XRT)

---

## Treasury Yields

**New `TreasuryYieldFetcher`**
- Fetch 2yr (`^IRX`), 10yr (`^TNX`), 30yr (`^TYX`) via yfinance
- Compute 2s10s spread (yield curve inversion signal)
- Signal value = 10yr yield; extreme flag when curve inverts beyond -0.5%
- Add yield curve context to LLM prompt — inverted curve = caution on credit spreads

---

## DXY (US Dollar Index)

**Add DXY to thematic or market overview data**
- Ticker `DX-Y.NYB` via yfinance (or `UUP` ETF as proxy)
- 1D/1W/1M performance alongside thematic rotation
- LLM instruction: call out dollar strength impact on energy (XLE), materials (XLB), multinationals
- Low effort — fits naturally into `ThematicEtfFetcher` as an additional single-ticker entry

---

## CME FedWatch (Rate Expectations)

**New `FedWatchFetcher`**
- Scrape CME FedWatch public page for next-meeting cut/hold/hike probabilities
- Signal value = probability of cut (0.0–1.0)
- Bullish signal when cut probability rises; bearish when it falls or hike probability appears
- Directly actionable for premium sellers: higher cut probability → lower rates → spread compression
- Note: CME page scraping may need periodic maintenance if structure changes

---

## Earnings Calendar

**New `EarningsCalendarFetcher`**
- Pull upcoming earnings (next 5 trading days) for watchlist tickers and CSP candidates
- Source: yfinance `Ticker.calendar` or Alpha Vantage `EARNINGS_CALENDAR`
- Add `=== UPCOMING EARNINGS ===` block to LLM prompt
- LLM instruction: flag any watchlist/CSP ticker with earnings in the next week — avoid selling premium into earnings unless IV crush is the explicit play
- High value for theta traders: earnings = IV spike risk or crush opportunity

---

## Priority Order (suggested)

1. **Expand AV news topics** — 5 minutes, immediate value
2. **Treasury yields** — high signal value, straightforward yfinance fetch
3. **Earnings calendar** — directly actionable for options plays
4. **DXY** — easy add to ThematicEtfFetcher
5. **Policy RSS fetcher** — higher maintenance, but high value during active tariff/trade periods
6. **CME FedWatch** — most complex, but uniquely actionable for premium sellers
