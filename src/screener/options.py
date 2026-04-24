"""Options screener for CSPs and LEAPS using yfinance."""

import logging
from datetime import date, datetime

import yfinance as yf
from dateutil.relativedelta import relativedelta

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


def screen_csp_candidates(tickers: list[str] | None = None) -> list[dict]:
    """Find the best-ROC CSP per (ticker, strike) across all valid expirations."""
    if tickers is None:
        tickers = get_watchlist()

    settings = get_csp_settings()
    logger.info(f"Screening CSP candidates across {len(tickers)} tickers with settings: {settings}")

    # Map of (symbol, strike) -> best candidate dict
    best_by_strike: dict[tuple, dict] = {}

    for symbol in tickers:
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
                    logger.debug(f"Could not fetch chain for {symbol} {exp_str}: {e}")
                    continue

                if puts.empty:
                    continue

                valid_puts = puts[(puts['strike'] >= min_strike) & (puts['strike'] <= max_strike)]

                for _, put_data in valid_puts.iterrows():
                    premium = put_data['lastPrice']
                    if premium <= 0.15:
                        continue

                    strike = put_data['strike']
                    roc = (premium / strike) * 100 if strike > 0 else 0

                    if roc < settings["min_roc"]:
                        continue

                    bid = put_data.get('bid', 0)
                    ask = put_data.get('ask', 0)
                    spread_pct = 0

                    if bid > 0 and ask > bid:
                        spread_pct = ((ask - bid) / bid) * 100
                        if spread_pct > settings["max_spread_pct"]:
                            continue

                    otm_pct = ((current_price - strike) / current_price) * 100
                    vol = put_data['volume']
                    is_valid_vol = not type(vol) is float or vol == vol
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    dte = (exp_date - date.today()).days
                    safe_dte = max(1, dte)
                    annualized_roc = (roc / safe_dte) * 365

                    candidate = {
                        "symbol": symbol,
                        "type": "CSP",
                        "current_price": round(current_price, 2),
                        "expiration": exp_str,
                        "dte": dte,
                        "strike": float(strike),
                        "premium": float(premium),
                        "roc_percent": round(roc, 2),
                        "annualized_roc": round(annualized_roc, 2),
                        "otm_percent": round(otm_pct, 2),
                        "bid": round(bid, 2),
                        "ask": round(ask, 2),
                        "spread_pct": round(spread_pct, 2),
                        "impliedVolatility": round(float(put_data['impliedVolatility']) * 100, 2),
                        "volume": int(vol) if is_valid_vol else 0,
                    }

                    key = (symbol, float(strike))
                    existing = best_by_strike.get(key)
                    # Keep the candidate with the highest Annualized ROC (Return per day normalized)
                    if existing is None or annualized_roc > existing["annualized_roc"]:
                        best_by_strike[key] = candidate

        except Exception as e:
            logger.warning(f"Failed to screen CSP for {symbol}: {e}")

    # Sort by highest Annualized Return on Capital
    return sorted(best_by_strike.values(), key=lambda x: x["annualized_roc"], reverse=True)


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
