"""Options screener for CSPs and LEAPS using yfinance."""

import logging
from datetime import date, datetime

import yfinance as yf
from dateutil.relativedelta import relativedelta

logger = logging.getLogger(__name__)

# Default watchlist to screen
WATCHLIST = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "SPY", "QQQ", "IWM", "XLE", "XLF"]


def _get_target_expiry(expirations: tuple[str, ...], target_days: int) -> str | None:
    """Find the expiration date closest to target_days from today."""
    if not expirations:
        return None
        
    today = date.today()
    best_diff = float("inf")
    best_exp = None
    
    for exp_str in expirations:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        diff = (exp_date - today).days
        
        # Only consider future expirations
        if diff > 0 and abs(diff - target_days) < best_diff:
            best_diff = abs(diff - target_days)
            best_exp = exp_str
            
    return best_exp


def screen_csp_candidates(tickers: list[str] = WATCHLIST, target_dte: int = 45) -> list[dict]:
    """Find Cash Secured Put candidates (~45 DTE, OTM)."""
    logger.info("Screening CSP candidates...")
    candidates = []
    
    for symbol in tickers:
        try:
            ticker = yf.Ticker(symbol)
            expirations = ticker.options
            
            target_exp = _get_target_expiry(expirations, target_dte)
            if not target_exp:
                continue
                
            chain = ticker.option_chain(target_exp)
            puts = chain.puts
            
            # Get current price
            current_price = ticker.fast_info.last_price
            
            # Simple Delta proxy: strike / current_price. 
            # Real delta requires Black-Scholes, but for an MVP, targeting ~10-15% OTM works well.
            target_strike = current_price * 0.85
            
            # Find the put closest to the target strike
            if not puts.empty:
                closest_put = puts.iloc[(puts['strike'] - target_strike).abs().argsort()[:1]]
                if not closest_put.empty:
                    put_data = closest_put.iloc[0]
                    premium = put_data['lastPrice']
                    strike = put_data['strike']
                    roc = (premium / strike) * 100 if strike > 0 else 0
                    
                    candidates.append({
                        "symbol": symbol,
                        "type": "CSP",
                        "current_price": round(current_price, 2),
                        "expiration": target_exp,
                        "strike": float(strike),
                        "premium": float(premium),
                        "roc_percent": round(roc, 2),
                        "impliedVolatility": round(float(put_data['impliedVolatility']) * 100, 2),
                        "volume": int(put_data['volume']) if not type(put_data['volume']) is float or put_data['volume'] == put_data['volume'] else 0
                    })
        except Exception as e:
            logger.warning(f"Failed to screen CSP for {symbol}: {e}")
            
    # Sort by highest Return on Capital
    return sorted(candidates, key=lambda x: x["roc_percent"], reverse=True)


def screen_leaps_candidates(tickers: list[str] = WATCHLIST, min_dte: int = 365) -> list[dict]:
    """Find LEAPS call candidates (>365 DTE, Deep ITM)."""
    logger.info("Screening LEAPS candidates...")
    candidates = []
    
    for symbol in tickers:
        try:
            ticker = yf.Ticker(symbol)
            expirations = ticker.options
            
            # Find an expiration roughly 1+ year out
            target_exp = _get_target_expiry(expirations, min_dte)
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
