"""Tests for src/algo_detective/mlabs_scraper.py — parses mLabs Trading's
weekly recap posts (blog.mlabstrading.com) into (ticker, open_date) pairs
for is_prime labeling. HTML snippets below are trimmed excerpts of real
pages fetched 2026-07-19, not synthetic mockups.
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""

from __future__ import annotations

import httpx
import respx

from src.algo_detective.mlabs_scraper import fetch_post_index, fetch_recap_trades

_INDEX_HTML = """
<html><body>
<a href="/posts/results_boring_puts_2026_07_13">Results 7/13</a>
<a href="/posts/results_boring_puts_2026_07_06">Results 7/6</a>
<a href="/posts/boring_puts_watchlist_2026_07_14">Watchlist 7/14</a>
<a href="/posts/results_boring_puts_2025_09_01">Results 9/1/25</a>
</body></html>
"""

_SINGLE_TRADE_HTML = """
<html><body>
<h3>This Week's Opening Trades</h3>
<table class="trades-table"><thead><tr>
<th>Type</th><th>Open</th><th>Exp</th><th>Close</th><th>Ticker</th>
<th>Strike</th><th>Qty</th><th>Fill</th><th>Exit</th><th>Fee</th><th>Cap</th><th>P/L$</th><th>ROC</th>
</tr></thead><tbody>
<tr><td>CSP</td><td>7/15</td><td>7/17</td><td>7/17</td><td><strong>NVO</strong></td>
<td>49</td><td>1</td><td>0.16</td><td>0.00</td><td>1.04</td><td>4.9k</td><td>14.96</td>
<td class="positive">0.31%</td></tr>
</tbody></table>
</body></html>
"""

_MULTI_TRADE_HTML = """
<html><body>
<table class="trades-table"><thead><tr>
<th>Type</th><th>Open</th><th>Exp</th><th>Close</th><th>Ticker</th>
<th>Strike</th><th>Qty</th><th>Fill</th><th>Exit</th><th>Fee</th><th>Cap</th><th>P/L$</th><th>ROC</th>
</tr></thead><tbody>
<tr><td>CSP</td><td>2/2</td><td>2/20</td><td></td><td><strong>AEO</strong></td>
<td>22</td><td>3</td><td>0.40</td><td>0.00</td><td>1.36</td><td>6.6k</td><td>118.64</td><td>1.80%</td></tr>
<tr><td>CSP</td><td>2/3</td><td>2/6</td><td>2/5</td><td><strong>UAL</strong></td>
<td>106</td><td>1</td><td>0.71</td><td>1.74</td><td>1.34</td><td>10.6k</td><td>-104.34</td><td>-0.98%</td></tr>
<tr><td>CC</td><td>2/6</td><td>2/13</td><td></td><td><strong>NVDA</strong></td>
<td>192.5</td><td>1</td><td>0.38</td><td>0.00</td><td>0.67</td><td>19.15k</td><td>37.33</td><td>0.19%</td></tr>
</tbody></table>
</body></html>
"""

_NO_TABLE_HTML = """
<html><body>
<h2>Detailed Trading Log</h2>
<p><a href="/trade_logs/MLABS%20Trading.pdf">Download Trading Log (PDF)</a></p>
<ul><li>Cash Secured Puts (CSP) on DELL, NVDA*, UAL, GOOG*</li></ul>
</body></html>
"""


class TestFetchPostIndex:
    @respx.mock
    def test_returns_only_results_slugs_not_watchlist(self):
        respx.get("https://blog.mlabstrading.com/posts").mock(
            return_value=httpx.Response(200, text=_INDEX_HTML)
        )
        slugs = fetch_post_index()
        assert slugs == [
            "results_boring_puts_2025_09_01",
            "results_boring_puts_2026_07_06",
            "results_boring_puts_2026_07_13",
        ]


class TestFetchRecapTrades:
    @respx.mock
    def test_parses_single_csp_trade(self):
        respx.get("https://blog.mlabstrading.com/posts/results_boring_puts_2026_07_13").mock(
            return_value=httpx.Response(200, text=_SINGLE_TRADE_HTML)
        )
        trades = fetch_recap_trades("results_boring_puts_2026_07_13")
        assert trades == [{"ticker": "NVO", "open_date": "2026-07-15"}]

    @respx.mock
    def test_filters_out_non_csp_rows_and_keeps_column_order(self):
        respx.get("https://blog.mlabstrading.com/posts/results_boring_puts_2026_02_02").mock(
            return_value=httpx.Response(200, text=_MULTI_TRADE_HTML)
        )
        trades = fetch_recap_trades("results_boring_puts_2026_02_02")
        # NVDA (Type=CC) must be excluded; AEO and UAL (Type=CSP) kept
        assert trades == [
            {"ticker": "AEO", "open_date": "2026-02-02"},
            {"ticker": "UAL", "open_date": "2026-02-03"},
        ]

    @respx.mock
    def test_returns_empty_list_when_no_table_present(self):
        respx.get("https://blog.mlabstrading.com/posts/results_boring_puts_2025_09_01").mock(
            return_value=httpx.Response(200, text=_NO_TABLE_HTML)
        )
        trades = fetch_recap_trades("results_boring_puts_2025_09_01")
        assert trades == []

    @respx.mock
    def test_open_date_year_rolls_over_at_december_to_january_boundary(self):
        html = _SINGLE_TRADE_HTML.replace(">7/15<", ">1/2<")
        respx.get("https://blog.mlabstrading.com/posts/results_boring_puts_2025_12_29").mock(
            return_value=httpx.Response(200, text=html)
        )
        trades = fetch_recap_trades("results_boring_puts_2025_12_29")
        assert trades == [{"ticker": "NVO", "open_date": "2026-01-02"}]
