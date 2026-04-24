"""Options screener for CSPs and LEAPS using yfinance and Alpaca."""

import logging
from datetime import date, datetime

import httpx
import yfinance as yf
from dateutil.relativedelta import relativedelta

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


def screen_csp_candidates(tickers: list[str] | None = None) -> list[dict]:
    """Find the best-ROC CSP per (ticker, strike) across all valid expirations using live Alpaca pricing."""
    if tickers is None:
        tickers = get_watchlist()

    settings = get_csp_settings()
    logger.info(f"Screening CSP candidates across {len(tickers)} tickers with settings: {settings}")

    # 1. Collect all potential contracts from yfinance
    potential_contracts = []
    
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
                    strike = put_data['strike']
                    occ_symbol = put_data['contractSymbol']
                    otm_pct = ((current_price - strike) / current_price) * 100
                    
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    dte = (exp_date - date.today()).days

                    potential_contracts.append({
                        "symbol": symbol,
                        "occ_symbol": occ_symbol,
                        "current_price": current_price,
                        "expiration": exp_str,
                        "strike": strike,
                        "otm_pct": otm_pct,
                        "dte": dte
                    })

        except Exception as e:
            logger.warning(f"Failed to fetch initial yf chain for {symbol}: {e}")

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

    for c in potential_contracts:
        snapshot = alpaca_snapshots.get(c["occ_symbol"], {})
        if not snapshot:
            continue
            
        quote = snapshot.get("latestQuote", {})
        trade = snapshot.get("latestTrade", {})
        
        bid = quote.get("bp", 0.0)
        ask = quote.get("ap", 0.0)
        premium = trade.get("p", 0.0)
        vol = snapshot.get("v", 0)  # Daily volume is sometimes at the root
        iv = snapshot.get("impliedVolatility", 0.0)

        if premium <= 0.15:
            continue

        roc = (premium / c["strike"]) * 100 if c["strike"] > 0 else 0

        if roc < settings["min_roc"]:
            continue

        spread_pct = 0
        if bid > 0 and ask > bid:
            spread_pct = ((ask - bid) / bid) * 100
            if spread_pct > settings["max_spread_pct"]:
                continue
        else:
            # If there is no valid bid/ask on Alpaca, skip it for CSP safety
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

        key = (c["symbol"], float(c["strike"]))
        existing = best_by_strike.get(key)
        # Keep the candidate with the highest Annualized ROC (Return per day normalized)
        if existing is None or annualized_roc > existing["annualized_roc"]:
            best_by_strike[key] = candidate

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
