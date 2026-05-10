"""LLM prompt templates for market digest synthesis."""

SYSTEM_PROMPT = """You are a senior market analyst specializing in options income strategies \
(credit spreads, iron condors, theta decay). You produce a concise evening market digest for \
an experienced options trader. Your analysis should be actionable, direct, and focused on \
implications for selling premium.

Guidelines:
- Be thorough but focused: aim for 250–350 words, enough to complete all three sections
- Lead with the overall market posture
- Highlight any signals at extreme readings
- If insider trading AND congressional trades converge on the same ticker, treat this as a \
high-conviction signal and call it out explicitly
- Frame everything through the lens of theta/credit spread strategies
- Use the watchlist and CSP candidate data to make specific, ticker-level recommendations
- If signals conflict, say so explicitly
- End with a clear theta play recommendation and 2-3 watchlist items drawn from the ticker data
- Do NOT hedge excessively — give a directional lean"""

USER_PROMPT_TEMPLATE = """Generate an evening market digest analysis for {date}.

=== SIGNAL DATA ===
{signal_data}

=== COMPOSITE ===
Composite Score: {composite_score} (range: -1.0 bearish to +1.0 bullish)
Overall Posture: {posture}
Signals at extremes: {extreme_count}
{convergence_section}{watchlist_section}{csp_section}
=== FORMAT ===
Use this exact structure (do NOT restate the raw signals):

POSTURE: [Your posture assessment and brief analysis of the signals]

THETA PLAY: [specific recommendation for credit spread / premium selling, referencing specific tickers/strikes from the CSP candidates if available]

WATCHLIST: [2-3 tickers from the watchlist or CSP candidates, with brief rationale]"""


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


def build_synthesis_prompt(
    date_str: str,
    signal_summaries: list[str],
    composite_score: float,
    posture: str,
    extreme_count: int,
    convergence_alerts: list[str] | None = None,
    watchlist_stocks: list[dict] | None = None,
    csp_candidates: list[dict] | None = None,
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

    watchlist_section = format_watchlist_for_prompt(watchlist_stocks or [])
    csp_section = format_csp_candidates_for_prompt(csp_candidates or [])

    user_prompt = USER_PROMPT_TEMPLATE.format(
        date=date_str,
        signal_data=signal_data,
        composite_score=composite_score,
        posture=posture,
        extreme_count=extreme_count,
        convergence_section=convergence_section,
        watchlist_section=watchlist_section,
        csp_section=csp_section,
    )

    return SYSTEM_PROMPT, user_prompt
