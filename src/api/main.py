"""FastAPI backend exposing SQLite market intelligence data."""

import json
import sqlite3
from contextlib import closing

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..config import settings
from ..db import get_watchlist, update_watchlist, get_csp_settings, update_csp_settings, get_stock_watchlist, update_stock_watchlist
from ..screener.options import screen_csp_candidates, screen_leaps_candidates
from ..screener.stocks import screen_stocks

app = FastAPI(title="Market Intelligence API")

# Pydantic models for DB config
class WatchlistUpdate(BaseModel):
    tickers: list[str]

class CspSettingsUpdate(BaseModel):
    min_dte: int
    max_dte: int
    min_otm_pct: float
    max_otm_pct: float
    min_roc: float
    max_spread_pct: float

# Allow local frontend to access API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For dev only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/market-posture")
def get_market_posture():
    """Return the latest aggregate market posture and components."""
    try:
        with closing(sqlite3.connect(settings.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Fetch latest digest
            cursor.execute("SELECT * FROM digests ORDER BY date DESC LIMIT 1")
            digest = cursor.fetchone()
            
            if not digest:
                raise HTTPException(status_code=404, detail="No digest found")
                
            date_str = digest["date"]
            
            # Fetch all signals from that date
            cursor.execute("SELECT * FROM daily_signals WHERE date = ?", (date_str,))
            signals_rows = cursor.fetchall()
            
            signals = []
            for row in signals_rows:
                s_dict = dict(row)
                # Parse JSON metadata if present
                if s_dict.get("metadata"):
                    try:
                        s_dict["metadata"] = json.loads(s_dict["metadata"])
                    except Exception:
                        pass
                signals.append(s_dict)
                
            return {
                "date": date_str,
                "composite_score": digest["composite_score"],
                "posture": digest["posture"],
                "llm_summary": digest["llm_summary"],
                "full_text": digest["full_text"],
                "signals": signals
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/screener/csp")
def get_csp_candidates():
    """Returns top Cash Secured Put candidates currently active."""
    try:
        candidates = screen_csp_candidates()
        return {"candidates": candidates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/screener/leaps")
def get_leaps_candidates():
    """Returns top LEAPS call candidates currently active."""
    try:
        candidates = screen_leaps_candidates()
        return {"candidates": candidates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/watchlist")
def api_get_watchlist():
    try:
        return {"watchlist": get_watchlist()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watchlist")
def api_update_watchlist(data: WatchlistUpdate):
    try:
        updated_tickers = [t.strip().upper() for t in data.tickers if t.strip()]
        update_watchlist(updated_tickers)
        return {"status": "success", "watchlist": updated_tickers}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/settings/csp")
def api_get_csp_settings():
    try:
        return {"settings": get_csp_settings()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/settings/csp")
def api_update_csp_settings(data: CspSettingsUpdate):
    try:
        update_csp_settings(data.model_dump())
        return {"status": "success", "settings": data.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Stock Screener ---
@app.get("/api/screener/stocks")
def get_stock_candidates():
    """Returns top stock candidates from the watchlist."""
    try:
        candidates = screen_stocks()
        return {"candidates": candidates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/watchlist/stock")
def api_get_stock_watchlist():
    try:
        return {"watchlist": get_stock_watchlist()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/watchlist/stock")
def api_update_stock_watchlist(data: WatchlistUpdate):
    try:
        updated_tickers = [t.strip().upper() for t in data.tickers if t.strip()]
        update_stock_watchlist(updated_tickers)
        return {"status": "success", "watchlist": updated_tickers}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
