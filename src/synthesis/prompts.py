"""LLM prompt templates for market digest synthesis."""

SYSTEM_PROMPT = """You are a senior market analyst specializing in options income strategies \
(credit spreads, iron condors, theta decay). You produce a concise evening market digest for \
an experienced options trader. Your analysis should be actionable, direct, and focused on \
implications for selling premium.

Guidelines:
- Be thorough but focused: aim for 350–450 words, enough to cover all sections meaningfully
- Lead with the overall market posture
- Highlight any signals at extreme readings
- If insider trading AND congressional trades converge on the same ticker, treat this as a \
high-conviction signal and call it out explicitly
- Frame everything through the lens of theta/credit spread strategies
- Use the watchlist and CSP candidate data to make specific, ticker-level recommendations
- If signals conflict, say so explicitly
- End with a clear theta play recommendation and 2-3 watchlist items drawn from the ticker data
- Do NOT hedge excessively — give a directional lean
- Call out specific sector trends with catalysts from the news — e.g., 'Technology leading (+1.2%) on AI infrastructure spending' or 'SaaS names under pressure from AI competition'. Explain WHY sectors are moving using the news context. Be specific.
- For insider trading, only note buy activity — insider buying is a meaningful signal worth highlighting. Do not mention sells.
- When thematic rotation data is present, weave the most significant theme moves into your analysis. \
Call out standout performers or laggards by name — e.g., 'Cybersecurity (CRWD +3.1%, PANW +2.4%) \
leading on the news of...' or 'Biotech (XBI -2.3%) breaking down ahead of...'. \
Prioritize themes with the largest 1D moves or meaningful divergence from the broad market. \
At least one thematic callout is required when the data is available.
- For Treasury yields: note the yield level and curve shape. An inverted curve (3mo > 10yr) \
signals credit stress — widen spread targets or reduce position size. Rising 10yr (>5%) \
compresses option premiums; falling 10yr is supportive of premium selling.
- For FedWatch data: integrate rate-cut expectations into your posture. Many priced-in cuts = \
accommodative backdrop for premium selling; hikes priced in = tighten up.
- For policy/government news: call out any tariff, trade, or regulatory headlines that move \
specific sectors (materials, semis, retailers, energy). Name the sector and the catalyst.
- For earnings: flag any watchlist or CSP candidate tickers reporting in the next 7 days. \
Selling premium into earnings is high-risk; buying back early or avoiding the name is the \
conservative theta play. An earnings flag is a required callout if the ticker appears in WATCHLIST."""

USER_PROMPT_TEMPLATE = """Generate an evening market digest analysis for {date}.

=== SIGNAL DATA ===
{signal_data}

=== COMPOSITE ===
Composite Score: {composite_score} (range: -1.0 bearish to +1.0 bullish)
Overall Posture: {posture}
Signals at extremes: {extreme_count}
{convergence_section}{sector_section}{thematic_section}{treasury_section}{fedwatch_section}{policy_section}{earnings_section}{news_section}{watchlist_section}{csp_section}
=== FORMAT ===
Use this exact structure (do NOT restate the raw signals):

POSTURE: [Your posture assessment and brief analysis of the signals]

THETA PLAY: [specific recommendation for credit spread / premium selling, referencing specific tickers/strikes from the CSP candidates if available]

WATCHLIST: [2-3 tickers from the watchlist or CSP candidates, with brief rationale]"""


ETF_NAMES = {
    "XLK": "Technology", "XLF": "Financials", "XLE": "Energy",
    "XLV": "Healthcare", "XLI": "Industrials", "XLB": "Materials",
    "XLU": "Utilities", "XLP": "Consumer Staples", "XLY": "Consumer Discretionary",
    "XLRE": "Real Estate", "XLC": "Communication"
}


def format_sector_breakdown(metadata: dict) -> str:
    """Format sector ETF performance data for the LLM prompt.

    Expects metadata dict with keys: performances, leaders, laggards,
    rotation, rotation_spread, defensive_avg, cyclical_avg.
    """
    if not metadata:
        return ""
    performances = metadata.get("performances", {})
    if not performances:
        return ""

    sorted_perf = sorted(performances.items(), key=lambda x: x[1], reverse=True)
    perf_parts = []
    for ticker, pct in sorted_perf:
        name = ETF_NAMES.get(ticker, ticker)
        sign = "+" if pct >= 0 else ""
        perf_parts.append(f"{name} ({ticker}): {sign}{pct:.1f}%")
    perf_line = " | ".join(perf_parts)

    rotation = metadata.get("rotation", "N/A")
    cyc_avg = metadata.get("cyclical_avg")
    def_avg = metadata.get("defensive_avg")
    cyc_str = f"{cyc_avg:+.1f}%" if cyc_avg is not None else "N/A"
    def_str = f"{def_avg:+.1f}%" if def_avg is not None else "N/A"

    leaders = metadata.get("leaders", {})
    laggards = metadata.get("laggards", {})
    leaders_str = ", ".join(f"{t} ({v:+.1f}%)" for t, v in leaders.items())
    laggards_str = ", ".join(f"{t} ({v:+.1f}%)" for t, v in laggards.items())

    lines = [
        "\n=== SECTOR PERFORMANCE (1D) ===",
        perf_line,
        f"Rotation: {rotation} | Cyclical avg: {cyc_str} | Defensive avg: {def_str}",
        f"Leaders: {leaders_str}",
        f"Laggards: {laggards_str}",
        "",
    ]
    return "\n".join(lines)


def format_thematic_breakdown(metadata: dict) -> str:
    """Format thematic ETF/basket performance data for the LLM prompt.

    Expects metadata dict with keys: singles, baskets.
    singles: {label: {ticker, pct_1d, pct_1w, pct_1m}}
    baskets: {label: {tickers: {sym: {pct_1d, pct_1w, pct_1m}}, avg_1d}}
    """
    if not metadata:
        return ""
    singles = metadata.get("singles", {})
    baskets = metadata.get("baskets", {})
    if not singles and not baskets:
        return ""

    lines = ["\n=== THEMATIC ROTATION (1D) ==="]

    def _p(v) -> str:
        return f"{v:+.1f}%" if v is not None else "N/A"

    # Single-ETF themes — one line each with 1D / 1W / 1M
    for label, d in singles.items():
        ticker = d.get("ticker", "")
        lines.append(
            f"{label} ({ticker}): {_p(d.get('pct_1d'))} 1D"
            f" | {_p(d.get('pct_1w'))} 1W"
            f" | {_p(d.get('pct_1m'))} 1M"
        )

    # Basket themes — group avg 1D/1W/1M, then individual tickers sorted by 1D
    for label, d in baskets.items():
        avg_line = (
            f"avg {_p(d.get('avg_1d'))} 1D"
            f" | {_p(d.get('avg_1w'))} 1W"
            f" | {_p(d.get('avg_1m'))} 1M"
        )
        ticker_data = d.get("tickers", {})
        ticker_parts = []
        for sym, perf in sorted(
            ticker_data.items(),
            key=lambda x: x[1].get("pct_1d") or 0,
            reverse=True,
        ):
            p = perf.get("pct_1d")
            if p is not None:
                ticker_parts.append(f"{sym} {p:+.1f}%")
        tickers_str = " | ".join(ticker_parts) if ticker_parts else "N/A"
        lines.append(f"{label} ({avg_line}): {tickers_str}")

    lines.append("")
    return "\n".join(lines)


def format_news_section(headlines: list[dict]) -> str:
    """Format macro news headlines for the LLM prompt.

    Each headline dict: {"title": str, "summary": str, "sentiment": str, "topics": [str]}
    """
    if not headlines:
        return ""

    lines = ["\n=== MACRO NEWS & MARKET CATALYSTS ==="]
    for item in headlines[:10]:
        title = item.get("title", "")
        sentiment = item.get("sentiment", "")
        topics = item.get("topics", [])
        topics_str = f" ({', '.join(topics)})" if topics else ""
        lines.append(f"• {title} — {sentiment}{topics_str}")
    lines.append("")
    return "\n".join(lines)


def _fmt_val(v, suffix: str = "", prefix: str = "", decimals: int = 1, sign: bool = False) -> str:
    if v is None or v == "N/A":
        return "N/A"
    try:
        f = float(v)
        sign_prefix = "+" if sign and f > 0 else ""
        return f"{prefix}{sign_prefix}{f:.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(v)


def format_watchlist_for_prompt(stocks: list[dict]) -> str:
    """Compact one-line-per-ticker summary of watchlist stock data."""
    if not stocks:
        return ""

    lines = []
    for s in stocks:
        sym = s.get("symbol", "?")
        price = _fmt_val(s.get("price"), prefix="$", decimals=2)
        sector = s.get("sector", "N/A")
        p1d = _fmt_val(s.get("pct_1d"), suffix="%", sign=True)
        p1w = _fmt_val(s.get("pct_1w"), suffix="%", sign=True)
        p1m = _fmt_val(s.get("pct_1m"), suffix="%", sign=True)
        pe = _fmt_val(s.get("pe"), decimals=1)
        fpe = _fmt_val(s.get("forward_pe"), decimals=1)
        beta = _fmt_val(s.get("beta"), decimals=2)
        eps = _fmt_val(s.get("eps"), prefix="$", decimals=2)
        eps_gr = _fmt_val(s.get("eps_growth"), suffix="%")
        rev = _fmt_val(s.get("revenue"), suffix="B", prefix="$")
        rev_gr = _fmt_val(s.get("revenue_growth"), suffix="%")
        fcf = _fmt_val(s.get("fcf"), suffix="B", prefix="$")
        de = _fmt_val(s.get("debt_to_equity"), decimals=1)
        iv = _fmt_val(s.get("atm_iv"), suffix="%", decimals=0)
        ivpct = _fmt_val(s.get("iv_percentile"), suffix="%", decimals=0)
        rv20 = _fmt_val(s.get("rv20"), suffix="%", decimals=0)

        lines.append(
            f"{sym} {price} [{sector}] | "
            f"1D:{p1d} 1W:{p1w} 1M:{p1m} | "
            f"P/E:{pe} FwdPE:{fpe} Beta:{beta} EPS:{eps} EPS-Gr:{eps_gr} "
            f"Rev:{rev} Rev-Gr:{rev_gr} FCF:{fcf} D/E:{de} | "
            f"IV:{iv} IVPct:{ivpct} RV20:{rv20}"
        )

    return "\n=== WATCHLIST SNAPSHOT ===\n" + "\n".join(lines) + "\n"


def format_csp_candidates_for_prompt(candidates: list[dict], top_n: int = 5) -> str:
    """Compact one-line-per-candidate summary of top CSP screener results."""
    if not candidates:
        return ""

    top = candidates[:top_n]
    lines = []
    for c in top:
        sym = c.get("symbol", "?")
        strike = _fmt_val(c.get("strike"), prefix="$", decimals=0)
        dte = c.get("dte", "?")
        prem = _fmt_val(c.get("premium"), prefix="$", decimals=2)
        ann_roc = _fmt_val(c.get("annualized_roc"), suffix="%", decimals=1)
        otm = _fmt_val(c.get("otm_percent"), suffix="%", decimals=1)
        delta = _fmt_val(c.get("delta"), decimals=2)
        iv = _fmt_val(c.get("impliedVolatility"), suffix="%", decimals=0)
        spread = _fmt_val(c.get("spread_pct"), suffix="%", decimals=1)
        vol = c.get("volume", "N/A")

        lines.append(
            f"{sym} {strike}P {dte}DTE | "
            f"Prem:{prem} AnnROC:{ann_roc} OTM:{otm} Delta:{delta} "
            f"IV:{iv} Spread:{spread} Vol:{vol}"
        )

    return "\n=== TOP CSP CANDIDATES ===\n" + "\n".join(lines) + "\n"


def format_treasury_section(metadata: dict) -> str:
    """Format Treasury yield curve and DXY data for the LLM prompt."""
    if not metadata:
        return ""
    yields = metadata.get("yields", {})
    if not yields:
        return ""

    lines = ["\n=== TREASURY YIELDS & US DOLLAR ==="]

    # Yield levels with 1D bps change
    parts = []
    for label in ("3mo", "5yr", "10yr", "30yr"):
        d = yields.get(label)
        if d:
            lvl = d.get("level")
            bps = d.get("bps_1d")
            bps_str = f" ({bps:+.0f}bps)" if bps is not None else ""
            parts.append(f"{label} {lvl:.2f}%{bps_str}")
    lines.append(" | ".join(parts))

    # Curve
    curve_label = metadata.get("curve_label", "N/A")
    lines.append(f"Yield Curve (3mo/10yr): {curve_label}")

    # DXY
    dxy = metadata.get("dxy")
    if dxy:
        p1d = dxy.get("pct_1d")
        p1w = dxy.get("pct_1w")
        p1d_str = f"{p1d:+.1f}%" if p1d is not None else "N/A"
        p1w_str = f"{p1w:+.1f}%" if p1w is not None else "N/A"
        lines.append(f"DXY (US Dollar): {dxy['level']:.2f} | {p1d_str} 1D | {p1w_str} 1W")

    lines.append("")
    return "\n".join(lines)


def format_fedwatch_section(metadata: dict) -> str:
    """Format CME FedWatch rate-expectations data for the LLM prompt."""
    if not metadata:
        return ""
    current = metadata.get("current_rate")
    implied = metadata.get("implied_rate")
    change = metadata.get("implied_change")
    label = metadata.get("direction_label", "")
    cuts = metadata.get("expected_cuts")
    if current is None or implied is None:
        return ""

    lines = [
        "\n=== CME FEDWATCH (RATE EXPECTATIONS) ===",
        f"Current FFTR Upper: {current:.2f}% | Futures-Implied Rate: {implied:.2f}%",
        f"Expected Change: {change:+.2f}% ({cuts:+.1f} cuts) — {label}",
        "",
    ]
    return "\n".join(lines)


def format_policy_news_section(headlines: list[dict]) -> str:
    """Format policy/government RSS headlines for the LLM prompt."""
    if not headlines:
        return ""
    lines = ["\n=== POLICY & GOVERNMENT NEWS ==="]
    for item in headlines:
        title = item.get("title", "")
        summary = item.get("summary", "")
        if summary:
            lines.append(f"• {title} — {summary[:120]}")
        else:
            lines.append(f"• {title}")
    lines.append("")
    return "\n".join(lines)


def format_earnings_section(upcoming: list[dict]) -> str:
    """Format upcoming earnings calendar for the LLM prompt."""
    if not upcoming:
        return ""
    lines = ["\n=== UPCOMING EARNINGS (next 7 days) ==="]
    for e in upcoming[:20]:
        sym = e.get("symbol", "")
        name = e.get("name", "")
        dt = e.get("report_date", "")
        est = e.get("estimate", "N/A")
        est_str = f" (est. EPS ${est})" if est != "N/A" else ""
        lines.append(f"• {sym} — {name} on {dt}{est_str}")
    lines.append("")
    return "\n".join(lines)


def build_synthesis_prompt(
    date_str: str,
    signal_summaries: list[str],
    composite_score: float,
    posture: str,
    extreme_count: int,
    convergence_alerts: list[str] | None = None,
    watchlist_stocks: list[dict] | None = None,
    csp_candidates: list[dict] | None = None,
    sector_metadata: dict | None = None,
    thematic_metadata: dict | None = None,
    treasury_metadata: dict | None = None,
    fedwatch_metadata: dict | None = None,
    policy_headlines: list[dict] | None = None,
    earnings_upcoming: list[dict] | None = None,
    news_headlines: list[dict] | None = None,
) -> tuple[str, str]:
    """Build the system + user prompt pair for LLM synthesis.

    Returns (system_prompt, user_prompt).
    """
    signal_data = "\n".join(f"• {s}" for s in signal_summaries)

    if convergence_alerts:
        convergence_section = (
            "\n=== CONVERGENCE ALERTS (insiders + politicians buying same ticker) ===\n"
            + "\n".join(convergence_alerts)
            + "\n"
        )
    else:
        convergence_section = ""

    sector_section = format_sector_breakdown(sector_metadata) if sector_metadata else ""
    thematic_section = format_thematic_breakdown(thematic_metadata) if thematic_metadata else ""
    treasury_section = format_treasury_section(treasury_metadata) if treasury_metadata else ""
    fedwatch_section = format_fedwatch_section(fedwatch_metadata) if fedwatch_metadata else ""
    policy_section = format_policy_news_section(policy_headlines or [])
    earnings_section = format_earnings_section(earnings_upcoming or [])
    news_section = format_news_section(news_headlines) if news_headlines else ""

    watchlist_section = format_watchlist_for_prompt(watchlist_stocks or [])
    csp_section = format_csp_candidates_for_prompt(csp_candidates or [])

    user_prompt = USER_PROMPT_TEMPLATE.format(
        date=date_str,
        signal_data=signal_data,
        composite_score=composite_score,
        posture=posture,
        extreme_count=extreme_count,
        convergence_section=convergence_section,
        sector_section=sector_section,
        thematic_section=thematic_section,
        treasury_section=treasury_section,
        fedwatch_section=fedwatch_section,
        policy_section=policy_section,
        earnings_section=earnings_section,
        news_section=news_section,
        watchlist_section=watchlist_section,
        csp_section=csp_section,
    )

    return SYSTEM_PROMPT, user_prompt
