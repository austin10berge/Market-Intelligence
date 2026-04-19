"""FastAPI backend exposing SQLite market intelligence data."""

import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from typing import Dict, Any

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import httpx

from ..config import settings
from ..db import get_watchlist, update_watchlist, get_csp_settings, update_csp_settings, get_stock_watchlist, update_stock_watchlist
from ..screener.options import screen_csp_candidates, screen_leaps_candidates
from ..screener.stocks import screen_stocks
from ..main import run_pipeline

logger = logging.getLogger(__name__)

app = FastAPI(title="Market Intelligence API")

# Simple in-memory cache to prevent yfinance from pegging CPU on every refresh
_cache: Dict[str, Dict[str, Any]] = {}
CACHE_TTL = 600  # 10 minutes

def get_cached(key: str):
    if key in _cache:
        entry = _cache[key]
        if time.time() - entry["timestamp"] < CACHE_TTL:
            return entry["data"]
    return None

def set_cache(key: str, data: Any):
    _cache[key] = {
        "timestamp": time.time(),
        "data": data
    }

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


# ── Discord Bot Endpoints ─────────────────────────────────────────────────────

class ScanTriggerRequest(BaseModel):
    channel_id: str
    requested_by: str = "unknown"


async def _run_and_post_to_discord(channel_id: str, discord_bot_url: str) -> None:
    """Background task: run pipeline, POST results back to the Discord bot."""
    # Fall back to container name if no explicit URL was provided
    if not discord_bot_url:
        discord_bot_url = os.getenv("DISCORD_BOT_CALLBACK_URL", "http://discord-bot:9000")
    try:
        result = await run_pipeline(output_mode="discord")
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{discord_bot_url}/callback",
                json={"channel_id": channel_id, "result": result},
                headers={"x-bot-secret": settings.discord_bot_secret},
            )
    except Exception as e:
        logger.error(f"Discord scan callback failed: {e}")


@app.post("/api/scan/trigger")
async def trigger_scan(req: Request, body: ScanTriggerRequest, background_tasks: BackgroundTasks):
    """Trigger a market sentiment scan from the Discord bot."""
    # Validate shared secret
    token = req.headers.get("x-bot-token")
    if not token or token != settings.discord_bot_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    discord_bot_url = req.headers.get("x-bot-callback-url", "")

    # Kick off the pipeline in the background so HTTP response returns immediately
    background_tasks.add_task(_run_and_post_to_discord, body.channel_id, discord_bot_url)

    return {"status": "queued", "message": "Scan started. Results will post to Discord shortly."}


@app.get("/api/scan/history")
def get_scan_history(req: Request, limit: int = 5):
    """Return the last N digest results for /scan-history Discord command."""
    token = req.headers.get("x-bot-token")
    if not token or token != settings.discord_bot_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        with closing(sqlite3.connect(settings.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT date, composite_score, posture, llm_summary FROM digests ORDER BY date DESC LIMIT ?",
                (min(limit, 10),),
            )
            rows = cursor.fetchall()
            return {"history": [dict(r) for r in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    cached = get_cached("csp")
    if cached is not None:
        return {"candidates": cached, "cached": True}

    try:
        candidates = screen_csp_candidates()
        set_cache("csp", candidates)
        return {"candidates": candidates, "cached": False}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/screener/leaps")
def get_leaps_candidates():
    """Returns top LEAPS call candidates currently active."""
    cached = get_cached("leaps")
    if cached is not None:
        return {"candidates": cached, "cached": True}

    try:
        candidates = screen_leaps_candidates()
        set_cache("leaps", candidates)
        return {"candidates": candidates, "cached": False}
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
        # Invalidate cache
        _cache.pop("csp", None)
        _cache.pop("leaps", None)
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
        # Invalidate cache
        _cache.pop("csp", None)
        return {"status": "success", "settings": data.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Stock Screener ---
@app.get("/api/screener/stocks")
def get_stock_candidates():
    """Returns top stock candidates from the watchlist."""
    cached = get_cached("stocks")
    if cached is not None:
        return {"candidates": cached, "cached": True}

    try:
        candidates = screen_stocks()
        set_cache("stocks", candidates)
        return {"candidates": candidates, "cached": False}
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
        # Invalidate cache
        _cache.pop("stocks", None)
        return {"status": "success", "watchlist": updated_tickers}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
