"""Stock screener for broad market tracking using yfinance."""

import logging
import yfinance as yf
import pandas as pd
from ..db import get_stock_watchlist

logger = logging.getLogger(__name__)

def _safe_pct(new_val, old_val):
    if pd.isna(new_val) or pd.isna(old_val) or old_val == 0:
        return 0.0
    return float(((new_val - old_val) / old_val) * 100)

def screen_stocks(tickers: list[str] | None = None) -> list[dict]:
    """Fetch stock fundamentals and performance metrics."""
    if tickers is None:
        tickers = get_stock_watchlist()
        
    logger.info(f"Screening {len(tickers)} stocks...")
    candidates = []
    
    for symbol in tickers:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            # Use history to get 1D, 1W, 1M performance
            hist = ticker.history(period="1mo")
            if hist.empty:
                continue
                
            current_price = hist['Close'].iloc[-1]
            
            # Calculate % changes
            # 1D change
            pct_1d = 0.0
            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[-2]
                pct_1d = _safe_pct(current_price, prev_close)
                
            # 1W change (approx 5 trading days)
            pct_1w = 0.0
            if len(hist) >= 5:
                week_ago = hist['Close'].iloc[-5]
                pct_1w = _safe_pct(current_price, week_ago)
                
            # 1M change (approx 21 trading days / first item in 1mo hist)
            pct_1m = 0.0
            if len(hist) > 0:
                month_ago = hist['Close'].iloc[0]
                pct_1m = _safe_pct(current_price, month_ago)
                
            pe_val = info.get("trailingPE")
            beta_val = info.get("beta")
            
            candidates.append({
                "symbol": symbol,
                "name": info.get("shortName", symbol) or symbol,
                "price": round(float(current_price), 2) if not pd.isna(current_price) else 0.0,
                "sector": info.get("sector", "N/A") or "N/A",
                "pct_1d": round(pct_1d, 2),
                "pct_1w": round(pct_1w, 2),
                "pct_1m": round(pct_1m, 2),
                "pe": round(float(pe_val), 2) if pd.notna(pe_val) else "N/A",
                "beta": round(float(beta_val), 2) if pd.notna(beta_val) else "N/A"
            })
            
        except Exception as e:
            logger.warning(f"Failed to screen stock {symbol}: {e}")
            
    return candidates
