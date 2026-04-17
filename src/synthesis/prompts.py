"""LLM prompt templates for market digest synthesis."""

SYSTEM_PROMPT = """You are a senior market analyst specializing in options income strategies \
(credit spreads, iron condors, theta decay). You produce a concise evening market digest for \
an experienced options trader. Your analysis should be actionable, direct, and focused on \
implications for selling premium.

Guidelines:
- Be concise: target ~150 words maximum
- Lead with the overall market posture
- Highlight any signals at extreme readings
- Frame everything through the lens of theta/credit spread strategies
- If signals conflict, say so explicitly
- End with a clear theta play recommendation and 2-3 watchlist items
- Do NOT hedge excessively — give a directional lean"""

USER_PROMPT_TEMPLATE = """Generate an evening market digest for {date}.

=== SIGNAL DATA ===
{signal_data}

=== COMPOSITE ===
Composite Score: {composite_score} (range: -1.0 bearish to +1.0 bullish)
Overall Posture: {posture}
Signals at extremes: {extreme_count}

=== FORMAT ===
Use this exact structure:

POSTURE: [posture assessment]
[signal summaries with values and direction arrows]

THETA PLAY: [specific recommendation for credit spread / premium selling]

WATCHLIST: [2-3 tickers or instruments to watch]"""


def build_synthesis_prompt(
    date_str: str,
    signal_summaries: list[str],
    composite_score: float,
    posture: str,
    extreme_count: int,
) -> tuple[str, str]:
    """Build the system + user prompt pair for LLM synthesis.

    Returns (system_prompt, user_prompt).
    """
    signal_data = "\n".join(f"• {s}" for s in signal_summaries)

    user_prompt = USER_PROMPT_TEMPLATE.format(
        date=date_str,
        signal_data=signal_data,
        composite_score=composite_score,
        posture=posture,
        extreme_count=extreme_count,
    )

    return SYSTEM_PROMPT, user_prompt
