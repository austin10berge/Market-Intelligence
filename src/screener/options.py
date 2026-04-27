"""Options screener for CSPs and LEAPS using yfinance and Alpaca."""

import logging
import math
from datetime import date, datetime

import httpx
import pandas as pd
import yfinance as yf
from dateutil.relativedelta import relativedelta

from ..backtester.indicators import rsi as compute_rsi
from ..config import settings as app_settings
from ..db import get_csp_settings, get_stock_iv_history, get_watchlist
from .stocks import (
    IV_RANK_LOOKBACK_DAYS,
    _build_alpaca_client,
    _calculate_iv_percentile,
    _calculate_rv20,
    _fetch_alpaca_atm_iv_percent,
)

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


def _compute_adx(hist: pd.DataFrame, period: int = 14) -> float | None:
    """Compute Wilder's ADX from daily OHLC history."""
    if hist.empty or len(hist) < (period * 2):
        return None

    high = hist["High"]
    low = hist["Low"]
    close = hist["Close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        [up if pd.notna(up) and up > down and up > 0 else 0.0 for up, down in zip(up_move, down_move)],
        index=hist.index,
    )
    minus_dm = pd.Series(
        [down if pd.notna(down) and down > up and down > 0 else 0.0 for up, down in zip(up_move, down_move)],
        index=hist.index,
    )

    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr)

    denom = (plus_di + minus_di).replace(0, pd.NA)
    dx = ((plus_di - minus_di).abs() / denom) * 100
    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    last = adx.iloc[-1]
    if pd.isna(last):
        return None
    return round(float(last), 2)


def _compute_p_free_cash_flow(info: dict) -> float | str:
    market_cap = info.get("marketCap")
    free_cash_flow = info.get("freeCashflow")

    try:
        market_cap_val = float(market_cap)
        free_cash_flow_val = float(free_cash_flow)
    except (TypeError, ValueError):
        return "N/A"

    if market_cap_val <= 0 or free_cash_flow_val <= 0:
        return "N/A"

    return round(market_cap_val / free_cash_flow_val, 2)


def _compute_symbol_metrics(
    symbol: str,
    ticker: yf.Ticker,
    current_price: float,
    alpaca_client: httpx.Client | None,
) -> dict:
    metrics = {
        "rsi": "N/A",
        "adx": "N/A",
        "atm_iv_rv20": "N/A",
        "iv_percentile": "N/A",
        "pe": "N/A",
        "p_free_cash_flow": "N/A",
    }

    try:
        hist = ticker.history(period="3mo")
        info = ticker.info

        if not hist.empty:
            rsi_series = compute_rsi(hist["Close"], period=14)
            rsi_val = rsi_series.iloc[-1] if not rsi_series.empty else None
            if pd.notna(rsi_val):
                metrics["rsi"] = round(float(rsi_val), 2)

            adx_val = _compute_adx(hist)
            if adx_val is not None:
                metrics["adx"] = adx_val

            rv20_val = _calculate_rv20(hist)
            atm_iv_val = _fetch_alpaca_atm_iv_percent(alpaca_client, symbol, float(current_price))
            if rv20_val is not None and atm_iv_val is not None:
                metrics["atm_iv_rv20"] = round(atm_iv_val / rv20_val, 2)

            iv_history = get_stock_iv_history(symbol, lookback_days=IV_RANK_LOOKBACK_DAYS)
            iv_percentile_val = (
                _calculate_iv_percentile(atm_iv_val, iv_history)
                if atm_iv_val is not None
                else None
            )
            if iv_percentile_val is not None:
                metrics["iv_percentile"] = round(iv_percentile_val, 2)

        pe_val = info.get("trailingPE")
        if pe_val is not None and not pd.isna(pe_val):
            metrics["pe"] = round(float(pe_val), 2)

        metrics["p_free_cash_flow"] = _compute_p_free_cash_flow(info)
    except Exception as exc:
        logger.debug("Failed to compute extra stock metrics for %s: %s", symbol, exc)

    return metrics


def screen_csp_candidates(tickers: list[str] | None = None) -> list[dict]:
    """Find the best-ROC CSP per (ticker, strike) across all valid expirations using live Alpaca pricing."""
    if tickers is None:
        tickers = get_watchlist()

    settings = get_csp_settings()
    logger.info(f"Screening CSP candidates across {len(tickers)} tickers with settings: {settings}")

    # 1. Collect all potential contracts from yfinance
    potential_contracts = []
    extra_metrics_by_symbol: dict[str, dict] = {}
    alpaca_client = _build_alpaca_client()
    
    try:
        for symbol in tickers:
            try:
                ticker = yf.Ticker(symbol)
                expirations = ticker.options
                current_price = ticker.fast_info.last_price

                valid_exps = _get_valid_expirations(expirations, settings["min_dte"], settings["max_dte"])
                if not valid_exps:
                    continue

                extra_metrics_by_symbol[symbol] = _compute_symbol_metrics(
                    symbol=symbol,
                    ticker=ticker,
                    current_price=float(current_price),
                    alpaca_client=alpaca_client,
                )

                max_strike = current_price * (1 - (settings["min_otm_pct"] / 100))
                min_strike = current_price * (1 - (settings["max_otm_pct"] / 100))

                for exp_str in valid_exps:
                    try:
                        chain = ticker.option_chain(exp_str)
                        puts = chain.puts
                    except Exception as e:
                        logger.debug(f"Could not fetch chain for {symbol} {exp_str}: {e}")
                        continue

                    if puts.empty:
                        continue

                    valid_puts = puts[(puts['strike'] >= min_strike) & (puts['strike'] <= max_strike)]

                    for _, put_data in valid_puts.iterrows():
                        strike = put_data['strike']
                        occ_symbol = put_data['contractSymbol']
                        yf_premium = put_data['lastPrice']
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
                            "dte": dte
                        })

            except Exception as e:
                logger.warning(f"Failed to fetch initial yf chain for {symbol}: {e}")
    finally:
        if alpaca_client is not None:
            alpaca_client.close()

    if not potential_contracts:
        return []

    # 2. Batch fetch live snapshots from Alpaca
    alpaca_snapshots = {}
    occ_symbols = [c["occ_symbol"] for c in potential_contracts]
    
    url = f"{app_settings.alpaca_data_url}/v1beta1/options/snapshots"
    headers = {
        "APCA-API-KEY-ID": app_settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": app_settings.alpaca_api_secret,
        "accept": "application/json"
    }

    # Alpaca allows up to 1000 symbols per request
    chunk_size = 1000
    with httpx.Client() as client:
        for i in range(0, len(occ_symbols), chunk_size):
            chunk = occ_symbols[i:i + chunk_size]
            try:
                response = client.get(url, headers=headers, params={"symbols": ",".join(chunk)})
                if response.status_code == 200:
                    data = response.json()
                    alpaca_snapshots.update(data.get("snapshots", {}))
                else:
                    logger.error(f"Alpaca API error: {response.text}")
            except Exception as e:
                logger.error(f"Failed to reach Alpaca API: {e}")

    # 3. Evaluate candidates with live pricing
    best_by_strike: dict[tuple, dict] = {}

    # Rejection counters for diagnostics
    rejected = {"no_alpaca_snapshot": 0, "low_premium": 0, "low_roc": 0, "wide_spread": 0}

    for c in potential_contracts:
        snapshot = alpaca_snapshots.get(c["occ_symbol"], {})
        if not snapshot:
            rejected["no_alpaca_snapshot"] += 1
            logger.debug(f"No Alpaca snapshot for {c['occ_symbol']} ({c['symbol']} {c['expiration']} {c['strike']}P)")
            continue
            
        quote = snapshot.get("latestQuote", {})
        trade = snapshot.get("latestTrade", {})
        
        bid = quote.get("bp", 0.0)
        ask = quote.get("ap", 0.0)
        premium = trade.get("p", 0.0)
        if premium == 0.0:
            premium = c.get("yf_premium", 0.0)
            
        vol = snapshot.get("v", 0)  # Daily volume is sometimes at the root
        iv = snapshot.get("impliedVolatility", 0.0)

        if premium <= 0.15:
            rejected["low_premium"] += 1
            logger.debug(f"Low premium filtered: {c['symbol']} {c['expiration']} {c['strike']}P — premium={premium:.2f}")
            continue

        roc = (premium / c["strike"]) * 100 if c["strike"] > 0 else 0

        if roc < settings["min_roc"]:
            rejected["low_roc"] += 1
            logger.debug(f"Low ROC filtered: {c['symbol']} {c['expiration']} {c['strike']}P — roc={roc:.2f}% (min={settings['min_roc']}%)")
            continue

        spread_pct = 0
        if bid > 0 and ask > bid:
            spread_pct = ((ask - bid) / bid) * 100
            if spread_pct > settings["max_spread_pct"]:
                rejected["wide_spread"] += 1
                logger.debug(f"Wide spread filtered: {c['symbol']} {c['expiration']} {c['strike']}P — spread={spread_pct:.1f}% (max={settings['max_spread_pct']}%)")
                continue

        safe_dte = max(1, c["dte"])
        annualized_roc = (roc / safe_dte) * 365

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
            "impliedVolatility": round(float(iv) * 100, 2) if iv else 0.0,
            "volume": int(vol) if vol else 0,
        }
        candidate.update(extra_metrics_by_symbol.get(c["symbol"], {}))

        key = (c["symbol"], float(c["strike"]))
        existing = best_by_strike.get(key)
        # Keep the candidate with the highest Annualized ROC (Return per day normalized)
        if existing is None or annualized_roc > existing["annualized_roc"]:
            best_by_strike[key] = candidate

    # Sort by highest Annualized Return on Capital
    results = sorted(best_by_strike.values(), key=lambda x: x["annualized_roc"], reverse=True)
    logger.info(
        f"CSP screen complete: {len(potential_contracts)} contracts evaluated, "
        f"{len(results)} candidates returned. "
        f"Rejected — no_alpaca_snapshot={rejected['no_alpaca_snapshot']}, "
        f"low_premium={rejected['low_premium']}, "
        f"low_roc={rejected['low_roc']}, "
        f"wide_spread={rejected['wide_spread']}"
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
