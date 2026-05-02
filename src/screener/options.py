"""Options screener for CSPs and LEAPS using yfinance and Alpaca."""

import logging
from datetime import date, datetime

import httpx
import pandas as pd
import pandas_ta as ta  # noqa: F401 — extends pd.DataFrame with .ta accessor
import yfinance as yf
from dateutil.relativedelta import relativedelta  # noqa: F401 — kept for any future use

from ..config import settings as app_settings
from ..db import get_watchlist, get_csp_settings

logger = logging.getLogger(__name__)


def _get_valid_expirations(expirations: tuple[str, ...], min_days: int, max_days: int) -> list[str]:
    """Return all expiration dates that fall within the specified min_days and max_days window."""
    if not expirations:
        return []
    today = date.today()
    valid = []
    for exp_str in expirations:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        diff = (exp_date - today).days
        if min_days <= diff <= max_days:
            valid.append(exp_str)
    return valid


def _compute_technicals(symbol: str) -> dict | None:
    """Fetch ~90 days of price history and compute RSI(14), ADX(14), and 50-day SMA
    using pandas-ta.  Returns None if there isn't enough history to be reliable.

    All three indicators need at least ~14-20 bars to warm up; ADX needs 2×period
    (28 bars) minimum.  With 90 calendar days we typically get 60-63 trading days,
    well above that floor.
    """
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo")  # ~63 trading days
        if hist.empty or len(hist) < 30:
            logger.debug("Insufficient history for %s (%d bars)", symbol, len(hist))
            return None

        # pandas-ta expects a clean DatetimeIndex with tz stripped
        hist = hist.copy()
        if hist.index.tz is not None:
            hist.index = hist.index.tz_convert(None)
        else:
            hist.index = pd.to_datetime(hist.index)

        # ── RSI(14) ────────────────────────────────────────────────────────────────────
        rsi_series = ta.rsi(hist["Close"], length=14)
        rsi_val = float(rsi_series.iloc[-1]) if rsi_series is not None and not rsi_series.empty else None

        # ── ADX(14) ────────────────────────────────────────────────────────────────────
        # pandas-ta returns a DataFrame with columns ADX_14, DMP_14, DMN_14
        adx_df = ta.adx(hist["High"], hist["Low"], hist["Close"], length=14)
        adx_val = None
        if adx_df is not None and not adx_df.empty:
            adx_col = [c for c in adx_df.columns if c.upper().startswith("ADX")]
            if adx_col:
                adx_val = float(adx_df[adx_col[0]].iloc[-1])

        # ── SMA(50) — used only when pullback_mode is active ─────────────────────────
        sma50_val = None
        if len(hist) >= 50:
            sma50_series = ta.sma(hist["Close"], length=50)
            if sma50_series is not None and not sma50_series.empty:
                sma50_val = float(sma50_series.iloc[-1])

        # ── 5-day return — used in pullback_mode ───────────────────────────────────
        recent_return_5d = None
        if len(hist) >= 6:
            recent_return_5d = float(
                (hist["Close"].iloc[-1] - hist["Close"].iloc[-6]) / hist["Close"].iloc[-6] * 100
            )

        if rsi_val is None or adx_val is None:
            logger.debug("Could not compute RSI/ADX for %s", symbol)
            return None

        return {
            "rsi": round(rsi_val, 2),
            "adx": round(adx_val, 2),
            "sma50": round(sma50_val, 2) if sma50_val is not None else None,
            "return_5d": round(recent_return_5d, 2) if recent_return_5d is not None else None,
        }
    except Exception as exc:
        logger.warning("Technical indicator fetch failed for %s: %s", symbol, exc)
        return None


def _score_candidate(
    annualized_roc: float,
    otm_pct: float,
    iv: float,
    rsi: float,
    adx: float,
    settings: dict,
) -> float:
    """Compute a 0–100 composite score blending yield, PoP proxy, IV, RSI quality,
    and trend strength.

    Weights are read from settings so they can be tuned from the dashboard without
    a code deploy.

    Score components
    ----------------
    ay_score      — Annualized yield, soft-capped at 60% AY -> 100 pts
    pop_score     — PoP proxy: lower OTM% = closer to ATM = higher delta ~= lower PoP.
                    We reward the sweet-spot of 5-10% OTM (roughly 0.25-0.30 delta).
    iv_pct_score  — IV as a percentile of the 25-60 range we consider healthy.
    rsi_score     — Rewards RSI near 50 (healthy pullback), penalises extremes.
    adx_score     — Rewards ADX 18-35 (trend present but not exhausted).
    """
    w_ay  = settings.get("score_weight_ay",     0.35)
    w_pop = settings.get("score_weight_pop",    0.20)
    w_iv  = settings.get("score_weight_iv_pct", 0.20)
    w_rsi = settings.get("score_weight_rsi",    0.15)
    w_adx = settings.get("score_weight_adx",    0.10)

    # AY: 0 -> 0 pts, 60%+ -> 100 pts (linear up to cap)
    ay_score = min(annualized_roc / 60.0, 1.0) * 100

    # PoP proxy: ideal OTM 5-10%, penalise outside that band
    if 5.0 <= otm_pct <= 10.0:
        pop_score = 100.0
    elif otm_pct < 5.0:  # Too close to ATM — higher assignment risk
        pop_score = max(0.0, (otm_pct / 5.0) * 80)
    else:  # > 10% OTM — very low premium
        pop_score = max(0.0, 100 - (otm_pct - 10.0) * 8)

    # IV: linear across 25-60 range
    iv_pct_score = min(max((iv - 25.0) / 35.0, 0.0), 1.0) * 100

    # RSI: peak at 50, decay towards 0 at extremes (20 or 80)
    rsi_score = max(0.0, 100 - abs(rsi - 50) * 3.33)

    # ADX: peak at 25, decay outside 15-35
    if 15.0 <= adx <= 35.0:
        adx_score = 100.0 - abs(adx - 25.0) * 4
    else:
        adx_score = max(0.0, 100 - abs(adx - 25.0) * 8)

    composite = (
        w_ay  * ay_score
        + w_pop * pop_score
        + w_iv  * iv_pct_score
        + w_rsi * rsi_score
        + w_adx * adx_score
    )
    return round(composite, 1)


def screen_csp_candidates(
    tickers: list[str] | None = None,
    min_dte: int | None = None,
    max_dte: int | None = None,
) -> list[dict]:
    """Find CSP candidates with live Alpaca pricing, technical quality filters
    (RSI, ADX via pandas-ta), and a composite score modelled on mLabs methodology.

    Flow
    ----
    1. Compute RSI / ADX / SMA-50 per ticker from yfinance history (pandas-ta).
    2. Apply technical pre-filters — skip tickers that fail RSI/ADX/IV gates
       before touching the option chain at all.
    3. Collect candidate put contracts from yfinance option chains.
    4. Batch-fetch live snapshots from Alpaca.
    5. Apply premium / ROC / spread filters.
    6. Compute composite score and return results sorted by score.
    """
    if tickers is None:
        tickers = get_watchlist()

    settings = get_csp_settings()
    if min_dte is not None:
        settings["min_dte"] = min_dte
    if max_dte is not None:
        settings["max_dte"] = max_dte
    logger.info(
        "Screening CSP candidates across %d tickers with settings: %s",
        len(tickers), settings,
    )

    # ── Step 1: Technical pre-filter ───────────────────────────────────────────────────
    technicals: dict[str, dict] = {}
    tech_rejected = 0
    for symbol in tickers:
        tech = _compute_technicals(symbol)
        if tech is None:
            tech_rejected += 1
            logger.debug("Skipping %s — could not compute technicals", symbol)
            continue

        rsi = tech["rsi"]
        adx = tech["adx"]

        # RSI gate
        if not (settings["min_rsi"] <= rsi <= settings["max_rsi"]):
            tech_rejected += 1
            logger.debug(
                "RSI filter: %s rsi=%.1f (allowed %.0f-%.0f)",
                symbol, rsi, settings["min_rsi"], settings["max_rsi"],
            )
            continue

        # ADX gate
        if not (settings["min_adx"] <= adx <= settings["max_adx"]):
            tech_rejected += 1
            logger.debug(
                "ADX filter: %s adx=%.1f (allowed %.0f-%.0f)",
                symbol, adx, settings["min_adx"], settings["max_adx"],
            )
            continue

        # Pullback-in-trend mode: RSI in the 38-55 "pulled back from overbought"
        # zone and price must have had a recent negative 5-day return.
        if settings.get("pullback_mode"):
            sma50 = tech.get("sma50")
            ret5d = tech.get("return_5d")
            if sma50 is None:
                tech_rejected += 1
                continue
            if not (
                rsi <= 55
                and ret5d is not None
                and ret5d < 0  # recent pullback
            ):
                tech_rejected += 1
                logger.debug(
                    "Pullback mode filter: %s rsi=%.1f ret5d=%s",
                    symbol, rsi, ret5d,
                )
                continue

        technicals[symbol] = tech

    qualifying_tickers = list(technicals.keys())
    logger.info(
        "Technical pre-filter: %d/%d tickers passed (%d rejected)",
        len(qualifying_tickers), len(tickers), tech_rejected,
    )

    # ── Step 2: Collect candidate contracts from yfinance ─────────────────────────
    potential_contracts = []

    for symbol in qualifying_tickers:
        try:
            ticker = yf.Ticker(symbol)
            expirations = ticker.options
            current_price = ticker.fast_info.last_price

            valid_exps = _get_valid_expirations(expirations, settings["min_dte"], settings["max_dte"])
            if not valid_exps:
                continue

            max_strike = current_price * (1 - (settings["min_otm_pct"] / 100))
            min_strike = current_price * (1 - (settings["max_otm_pct"] / 100))

            for exp_str in valid_exps:
                try:
                    chain = ticker.option_chain(exp_str)
                    puts = chain.puts
                except Exception as e:
                    logger.debug("Could not fetch chain for %s %s: %s", symbol, exp_str, e)
                    continue

                if puts.empty:
                    continue

                valid_puts = puts[
                    (puts["strike"] >= min_strike) & (puts["strike"] <= max_strike)
                ]

                for _, put_data in valid_puts.iterrows():
                    strike = put_data["strike"]
                    occ_symbol = put_data["contractSymbol"]
                    yf_premium = put_data["lastPrice"]
                    otm_pct = ((current_price - strike) / current_price) * 100

                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    dte = (exp_date - date.today()).days

                    potential_contracts.append({
                        "symbol": symbol,
                        "occ_symbol": occ_symbol,
                        "yf_premium": yf_premium,
                        "current_price": current_price,
                        "expiration": exp_str,
                        "strike": strike,
                        "otm_pct": otm_pct,
                        "dte": dte,
                    })

        except Exception as e:
            logger.warning("Failed to fetch yf chain for %s: %s", symbol, e)

    if not potential_contracts:
        return []

    # ── Step 3: Batch-fetch live snapshots from Alpaca ───────────────────────────
    alpaca_snapshots: dict[str, dict] = {}
    occ_symbols = [c["occ_symbol"] for c in potential_contracts]

    url = f"{app_settings.alpaca_data_url}/v1beta1/options/snapshots"
    headers = {
        "APCA-API-KEY-ID": app_settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": app_settings.alpaca_api_secret,
        "accept": "application/json",
    }

    chunk_size = 100  # Alpaca options/snapshots hard limit is 100 symbols per request
    with httpx.Client(timeout=30.0) as client:
        for i in range(0, len(occ_symbols), chunk_size):
            chunk = occ_symbols[i : i + chunk_size]
            try:
                response = client.get(url, headers=headers, params={"symbols": ",".join(chunk)})
                if response.status_code == 200:
                    data = response.json()
                    batch = data.get("snapshots", {})
                    alpaca_snapshots.update(batch)
                    logger.info(
                        "Alpaca snapshots: requested %d symbols, received %d (chunk %d/%d)",
                        len(chunk), len(batch), i // chunk_size + 1,
                        (len(occ_symbols) - 1) // chunk_size + 1,
                    )
                    if not batch:
                        logger.warning(
                            "Alpaca returned 0 snapshots for chunk. "
                            "Status: %d. First 3 symbols: %s. Response: %s",
                            response.status_code, chunk[:3], response.text[:300],
                        )
                else:
                    logger.error(
                        "Alpaca API error: status=%d body=%s",
                        response.status_code, response.text[:500],
                    )
            except httpx.TimeoutException:
                logger.error("Alpaca API timed out after 30s for chunk of %d symbols", len(chunk))
            except Exception as e:
                logger.error("Failed to reach Alpaca API: %s", e)

    # ── Step 4: Evaluate candidates ────────────────────────────────────────────────
    best_by_strike: dict[tuple, dict] = {}
    rejected = {
        "no_alpaca_snapshot": 0,
        "low_premium": 0,
        "low_roc": 0,
        "wide_spread": 0,
        "low_iv": 0,
    }

    for c in potential_contracts:
        snapshot = alpaca_snapshots.get(c["occ_symbol"], {})
        if not snapshot:
            rejected["no_alpaca_snapshot"] += 1
            logger.debug(
                "No Alpaca snapshot for %s (%s %s %.0fP)",
                c["occ_symbol"], c["symbol"], c["expiration"], c["strike"],
            )
            continue

        quote = snapshot.get("latestQuote", {})
        trade = snapshot.get("latestTrade", {})

        bid = quote.get("bp", 0.0)
        ask = quote.get("ap", 0.0)
        premium = trade.get("p", 0.0)
        if premium == 0.0:
            premium = c.get("yf_premium", 0.0)

        vol = snapshot.get("dailyBar", {}).get("v", 0)
        iv_raw = snapshot.get("impliedVolatility", 0.0)
        # Alpaca sometimes returns IV as a decimal (0.35) and sometimes as a
        # percentage (35.0) — normalise to percentage.
        iv = float(iv_raw) * 100 if iv_raw and float(iv_raw) <= 3.0 else float(iv_raw or 0)

        # IV floor
        min_iv = settings.get("min_iv", 25.0)
        if iv > 0 and iv < min_iv:
            rejected["low_iv"] += 1
            logger.debug(
                "Low IV filtered: %s %s %.0fP — iv=%.1f (min=%.0f)",
                c["symbol"], c["expiration"], c["strike"], iv, min_iv,
            )
            continue

        if premium <= 0.15:
            rejected["low_premium"] += 1
            logger.debug(
                "Low premium filtered: %s %s %.0fP — premium=%.2f",
                c["symbol"], c["expiration"], c["strike"], premium,
            )
            continue

        roc = (premium / c["strike"]) * 100 if c["strike"] > 0 else 0

        if roc < settings["min_roc"]:
            rejected["low_roc"] += 1
            logger.debug(
                "Low ROC filtered: %s %s %.0fP — roc=%.2f%% (min=%.1f%%)",
                c["symbol"], c["expiration"], c["strike"], roc, settings["min_roc"],
            )
            continue

        spread_pct = 0.0
        if bid > 0 and ask > bid:
            # Use midpoint-relative spread: standard for options
            spread_pct = ((ask - bid) / ((ask + bid) / 2)) * 100
            if spread_pct > settings["max_spread_pct"]:
                rejected["wide_spread"] += 1
                logger.debug(
                    "Wide spread filtered: %s %s %.0fP — spread=%.1f%% (max=%.0f%%)",
                    c["symbol"], c["expiration"], c["strike"], spread_pct,
                    settings["max_spread_pct"],
                )
                continue

        safe_dte = max(1, c["dte"])
        annualized_roc = (roc / safe_dte) * 365

        tech = technicals[c["symbol"]]
        rsi_val = tech["rsi"]
        adx_val = tech["adx"]

        composite_score = _score_candidate(
            annualized_roc=annualized_roc,
            otm_pct=c["otm_pct"],
            iv=iv if iv > 0 else 30.0,  # fallback to mid-range if IV unavailable
            rsi=rsi_val,
            adx=adx_val,
            settings=settings,
        )

        candidate = {
            "symbol": c["symbol"],
            "type": "CSP",
            "current_price": round(c["current_price"], 2),
            "expiration": c["expiration"],
            "dte": c["dte"],
            "strike": float(c["strike"]),
            "premium": float(premium),
            "roc_percent": round(roc, 2),
            "annualized_roc": round(annualized_roc, 2),
            "otm_percent": round(c["otm_pct"], 2),
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "spread_pct": round(spread_pct, 2),
            "impliedVolatility": round(iv, 2),
            "volume": int(vol) if vol else 0,
            # Technical indicators
            "rsi": rsi_val,
            "adx": adx_val,
            "sma50": tech.get("sma50"),
            "return_5d": tech.get("return_5d"),
            # Composite score — primary sort key
            "composite_score": composite_score,
        }

        key = (c["symbol"], float(c["strike"]))
        existing = best_by_strike.get(key)
        if existing is None or composite_score > existing["composite_score"]:
            best_by_strike[key] = candidate

    results = sorted(best_by_strike.values(), key=lambda x: x["composite_score"], reverse=True)
    logger.info(
        "CSP screen complete: %d contracts evaluated, %d candidates returned. "
        "Rejected — no_snapshot=%d, low_premium=%d, low_roc=%d, wide_spread=%d, low_iv=%d",
        len(potential_contracts),
        len(results),
        rejected["no_alpaca_snapshot"],
        rejected["low_premium"],
        rejected["low_roc"],
        rejected["wide_spread"],
        rejected["low_iv"],
    )
    return results


def screen_leaps_candidates(tickers: list[str] | None = None, min_dte: int = 365) -> list[dict]:
    """Find LEAPS call candidates (>365 DTE, Deep ITM)."""
    if tickers is None:
        tickers = get_watchlist()
        
    logger.info(f"Screening LEAPS candidates across {len(tickers)} tickers...")
    candidates = []
    
    for symbol in tickers:
        try:
            ticker = yf.Ticker(symbol)
            expirations = ticker.options
            
            # Find an expiration roughly 1+ year out
            # Reusing original target helper but just checking minimum dates manually
            target_exp = None
            today = date.today()
            best_diff = float("inf")
            for exp_str in expirations:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                diff = (exp_date - today).days
                if diff > 0 and abs(diff - min_dte) < best_diff and diff >= 300: # Slightly loose bounds
                    best_diff = abs(diff - min_dte)
                    target_exp = exp_str
                    
            if not target_exp:
                continue
                
            chain = ticker.option_chain(target_exp)
            calls = chain.calls
            
            # Get current price
            current_price = ticker.fast_info.last_price
            
            # Real LEAPS delta target is ~0.80. As proxy, look ~20% ITM.
            target_strike = current_price * 0.80
            
            if not calls.empty:
                closest_call = calls.iloc[(calls['strike'] - target_strike).abs().argsort()[:1]]
                if not closest_call.empty:
                    call_data = closest_call.iloc[0]
                    premium = call_data['lastPrice']
                    strike = call_data['strike']
                    
                    # Compute break-even
                    break_even = strike + premium
                    premium_over_stock = ((break_even - current_price) / current_price) * 100
                    
                    candidates.append({
                        "symbol": symbol,
                        "type": "LEAPS Call",
                        "current_price": round(current_price, 2),
                        "expiration": target_exp,
                        "strike": float(strike),
                        "premium": float(premium),
                        "break_even": round(break_even, 2),
                        "premium_markup_percent": round(premium_over_stock, 2),
                        "volume": int(call_data['volume']) if not type(call_data['volume']) is float or call_data['volume'] == call_data['volume'] else 0
                    })
        except Exception as e:
            logger.warning(f"Failed to screen LEAPS for {symbol}: {e}")
            
    # Sort by lowest markup over current stock price
    return sorted(candidates, key=lambda x: x["premium_markup_percent"])
