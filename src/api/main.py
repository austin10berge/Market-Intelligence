"""FastAPI backend — market-hours-aware Redis cache for all screener endpoints."""

import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager, closing
from typing import Dict, Any

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio
import httpx

from ..config import settings
from ..db import (
    get_watchlist, update_watchlist,
    get_csp_settings, update_csp_settings,
    get_stock_watchlist, update_stock_watchlist,
    get_insider_cache, get_congressional_cache, get_signal_history,
    save_strategy, get_strategies, get_strategy, delete_strategy,
)
from ..backtester.models import BacktestRequest, WalkForwardRequest, StrategyDefinition
from ..backtester.engine import run_backtest
from ..backtester.walk_forward import run_walk_forward
from ..backtester.data_provider import get_historical_data
from ..cache import (
    cache_get, cache_set, cache_delete, screener_ttl, scanner_ttl,
    invalidate_screener_cache, invalidate_market_posture,
    market_status_label, market_is_open,
    KEY_SCREENER_CSP, KEY_SCREENER_LEAPS, KEY_SCREENER_STOCKS,
    KEY_SCREENER_CSP_SCAN, KEY_MARKET_POSTURE,
)
from ..screener.options import screen_csp_candidates, screen_leaps_candidates
from ..screener.stocks import screen_stocks
from ..screener.csp_scanner import run_csp_scan, ScannerParams, AVAILABLE_CONDITIONS
from ..market_data.store import get_store_status, ensure_tables as ensure_market_data_tables
from ..market_data.refresh import refresh_universe
from ..main import run_pipeline

logger = logging.getLogger(__name__)


def _background_backfill() -> None:
    """Run a full 2-year backfill in a background thread. Called once on first startup."""
    try:
        logger.info("Market data store empty — starting initial full backfill (2y)...")
        result = refresh_universe(full=True)
        logger.info(
            "Initial backfill complete: %d OHLCV rows, %d fundamentals in %.1fs",
            result.get("ohlcv_rows_upserted", 0),
            result.get("fundamentals_upserted", 0),
            result.get("total_elapsed_s", 0),
        )
    except Exception as exc:
        logger.error("Initial backfill failed: %s", exc, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_market_data_tables()
    status = get_store_status()
    if status["ohlcv_ticker_count"] == 0:
        # Store is empty — kick off the initial 2-year backfill in the background
        # so the API starts immediately and the backfill runs concurrently (~2-3 min).
        asyncio.get_event_loop().run_in_executor(None, _background_backfill)
    else:
        logger.info(
            "Market data store ready: %d tickers, last update %s",
            status["ohlcv_ticker_count"],
            status.get("ohlcv_latest_date", "unknown"),
        )
    yield


app = FastAPI(title="Market Intelligence API", lifespan=lifespan)

# Allow local frontend to access API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic models ───────────────────────────────────────────────────────────

class WatchlistUpdate(BaseModel):
    tickers: list[str]

class CspSettingsUpdate(BaseModel):
    min_dte: int
    max_dte: int
    min_otm_pct: float
    max_otm_pct: float
    min_roc: float
    max_spread_pct: float
    # Technical filter fields (optional so old clients don't break)
    min_iv: float = 25.0
    min_rsi: float = 38.0
    max_rsi: float = 65.0
    min_adx: float = 15.0
    max_adx: float = 40.0
    pullback_mode: bool = False
    score_weight_ay: float = 0.35
    score_weight_pop: float = 0.20
    score_weight_iv_pct: float = 0.20
    score_weight_rsi: float = 0.15
    score_weight_adx: float = 0.10


# ── Cache metadata helper ─────────────────────────────────────────────────────

def _cache_meta(envelope: dict | None) -> dict:
    """Extract cache metadata from a Redis envelope for inclusion in API responses."""
    if envelope is None:
        return {"cached": False, "cached_at": None, "market_status": market_status_label()}
    return {
        "cached": True,
        "cached_at": envelope.get("cached_at"),
        "market_status": envelope.get("market_status", market_status_label()),
    }


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health_check():
    return {"status": "ok"}


# ── Discord Bot Endpoints ─────────────────────────────────────────────────────

class ScanTriggerRequest(BaseModel):
    channel_id: str
    requested_by: str = "unknown"


async def _run_and_post_to_discord(channel_id: str, discord_bot_url: str) -> None:
    """Background task: run pipeline, POST results back to the Discord bot."""
    if not discord_bot_url:
        discord_bot_url = os.getenv("DISCORD_BOT_CALLBACK_URL", "http://discord-bot:9000")
    try:
        result = await run_pipeline(output_mode="on-demand")
        # Invalidate market-posture cache so the dashboard reflects the new digest immediately
        await invalidate_market_posture()
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
    token = req.headers.get("x-bot-token")
    if not token or token != settings.discord_bot_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    discord_bot_url = req.headers.get("x-bot-callback-url", "")
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


@app.get("/api/insider")
def get_insider_data(req: Request):
    """Return latest insider trading and congressional trades for the /insider Discord command."""
    token = req.headers.get("x-bot-token")
    if not token or token != settings.discord_bot_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    insider = get_insider_cache(max_age_hours=48)
    congressional = get_congressional_cache(max_age_hours=48)
    insider_history = get_signal_history("insider_trading", days=7)
    congressional_history = get_signal_history("congressional_trades", days=7)

    return {
        "insider_trading": insider,
        "congressional_trades": congressional,
        "insider_history": insider_history,
        "congressional_history": congressional_history,
    }


# ── Market Posture ────────────────────────────────────────────────────────────

@app.get("/api/market-posture")
async def get_market_posture():
    """Return the latest aggregate market posture, signals, and LLM summary.

    Cached indefinitely in Redis; invalidated explicitly by the nightly pipeline
    after a new digest is written to the DB.
    """
    # Try Redis first
    envelope = await cache_get(KEY_MARKET_POSTURE)
    if envelope is not None:
        payload = envelope["data"]
        payload.update(_cache_meta(envelope))
        return payload

    # Cache miss — fetch from SQLite
    try:
        with closing(sqlite3.connect(settings.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM digests ORDER BY date DESC LIMIT 1")
            digest = cursor.fetchone()
            if not digest:
                raise HTTPException(status_code=404, detail="No digest found")

            date_str = digest["date"]
            cursor.execute("SELECT * FROM daily_signals WHERE date = ?", (date_str,))
            signals_rows = cursor.fetchall()

            signals = []
            for row in signals_rows:
                s_dict = dict(row)
                if s_dict.get("metadata"):
                    try:
                        s_dict["metadata"] = json.loads(s_dict["metadata"])
                    except Exception:
                        pass
                signals.append(s_dict)

            data = {
                "date": date_str,
                "composite_score": digest["composite_score"],
                "posture": digest["posture"],
                "llm_summary": digest["llm_summary"],
                "full_text": digest["full_text"],
                "signals": signals,
            }

        # Market posture only changes when the pipeline runs, so we use a long TTL
        # as a safety-net fallback (24h). The pipeline will explicitly invalidate this
        # key via invalidate_market_posture() after each successful run.
        await cache_set(KEY_MARKET_POSTURE, data, ttl=86400)

        data.update(_cache_meta(None))
        return data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Screener: CSP ─────────────────────────────────────────────────────────────

@app.get("/api/screener/csp")
async def get_csp_candidates():
    """Return top Cash Secured Put candidates. Market-hours-aware TTL caching."""
    envelope = await cache_get(KEY_SCREENER_CSP)
    if envelope is not None:
        return {"candidates": envelope["data"], **_cache_meta(envelope)}

    try:
        candidates = await asyncio.to_thread(screen_csp_candidates)
        ttl = screener_ttl()
        await cache_set(KEY_SCREENER_CSP, candidates, ttl=ttl)
        return {"candidates": candidates, **_cache_meta(None)}
    except Exception as e:
        logger.exception("CSP screener failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── Screener: CSP Scan (broad universe) ─────────────────────────────────────

@app.get("/api/screener/csp-scan/conditions")
def get_scan_conditions():
    """Return the full list of available technical conditions for the scanner UI."""
    return {"conditions": AVAILABLE_CONDITIONS}


@app.get("/api/screener/csp-scan")
async def get_csp_scan_candidates(
    min_cap:    float | None = None,
    max_price:  float | None = None,
    min_beta:   float | None = None,
    max_beta:   float | None = None,
    min_vol:    float | None = None,
    max_rsi:    float | None = None,
    min_dte:    int   | None = None,
    max_dte:    int   | None = None,
    conditions: str   | None = None,
):
    """Run the broad-universe CSP scanner (S&P 500 + NASDAQ 100).

    All parameters are optional and default to the scanner defaults when omitted.
    Different parameter combinations produce independent cache entries so prior
    results are never overwritten by a different param set.

    Query params
    ------------
    min_cap    : minimum market cap in billions (default 10)
    max_price  : maximum stock price (default 150)
    min_beta   : minimum beta (default 0.8)
    max_beta   : maximum beta (default 2.4)
    min_vol    : minimum IV/RV threshold % (default 30)
    conditions : comma-separated list of technical condition IDs to apply

    Cache TTL: 23 hours regardless of market status (EOD snapshot design).
    Zero-universe results are never cached.
    Use DELETE /api/screener/csp-scan to bust any cached result.
    """
    params = ScannerParams.from_query(
        min_cap=min_cap,
        max_price=max_price,
        min_beta=min_beta,
        max_beta=max_beta,
        min_vol=min_vol,
        max_rsi=max_rsi,
        min_dte=min_dte,
        max_dte=max_dte,
        conditions=conditions,
    )
    # Each unique param combination gets its own cache key
    cache_key = f"{KEY_SCREENER_CSP_SCAN}:{params.cache_key_suffix()}"

    envelope = await cache_get(cache_key)
    if envelope is not None:
        payload = envelope["data"]
        payload.update(_cache_meta(envelope))
        return payload

    try:
        result = await asyncio.to_thread(run_csp_scan, params)

        summary = result.get("filter_summary", {})
        if summary.get("combined_unique", 0) > 0:
            await cache_set(cache_key, result, ttl=scanner_ttl())
        else:
            logger.warning(
                "CSP scan returned zero universe tickers — result not cached. "
                "Params: %s", params
            )

        result.update(_cache_meta(None))
        return result
    except Exception as e:
        logger.exception("CSP scan failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/screener/csp-scan")
async def invalidate_csp_scan_cache(
    min_cap:    float | None = None,
    max_price:  float | None = None,
    min_beta:   float | None = None,
    max_beta:   float | None = None,
    min_vol:    float | None = None,
    max_rsi:    float | None = None,
    min_dte:    int   | None = None,
    max_dte:    int   | None = None,
    conditions: str   | None = None,
):
    """Bust the cache for a specific param combination (or the default set)."""
    params = ScannerParams.from_query(
        min_cap=min_cap, max_price=max_price,
        min_beta=min_beta, max_beta=max_beta,
        min_vol=min_vol, max_rsi=max_rsi,
        min_dte=min_dte, max_dte=max_dte,
        conditions=conditions,
    )
    cache_key = f"{KEY_SCREENER_CSP_SCAN}:{params.cache_key_suffix()}"
    await cache_delete(cache_key)
    return {"status": "ok", "message": f"Cache cleared for key {cache_key}"}


# ── Screener: LEAPS ───────────────────────────────────────────────────────────

@app.get("/api/screener/leaps")
async def get_leaps_candidates():
    """Return top LEAPS call candidates. Market-hours-aware TTL caching."""
    envelope = await cache_get(KEY_SCREENER_LEAPS)
    if envelope is not None:
        return {"candidates": envelope["data"], **_cache_meta(envelope)}

    try:
        candidates = await asyncio.to_thread(screen_leaps_candidates)
        ttl = screener_ttl()
        await cache_set(KEY_SCREENER_LEAPS, candidates, ttl=ttl)
        return {"candidates": candidates, **_cache_meta(None)}
    except Exception as e:
        logger.exception("LEAPS screener failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── Screener: Stocks ──────────────────────────────────────────────────────────

@app.get("/api/screener/stocks")
async def get_stock_candidates(tickers: str | None = None):
    """Return stock screener results. Market-hours-aware TTL caching.

    When ``tickers`` is provided (comma-separated symbols), only those tickers
    are screened and results are cached under a ticker-specific key so the
    static-watchlist cache is never polluted.
    """
    if tickers:
        ticker_list = sorted(set(t.strip().upper() for t in tickers.split(",") if t.strip()))
        cache_key = f"{KEY_SCREENER_STOCKS}:{'_'.join(ticker_list)}"
        envelope = await cache_get(cache_key)
        if envelope is not None:
            return {"candidates": envelope["data"], **_cache_meta(envelope)}
        try:
            candidates = await asyncio.to_thread(screen_stocks, ticker_list)
            ttl = screener_ttl()
            await cache_set(cache_key, candidates, ttl=ttl)
            return {"candidates": candidates, **_cache_meta(None)}
        except Exception as e:
            logger.exception("Stock screener (dynamic tickers) failed")
            raise HTTPException(status_code=500, detail=str(e))

    envelope = await cache_get(KEY_SCREENER_STOCKS)
    if envelope is not None:
        return {"candidates": envelope["data"], **_cache_meta(envelope)}

    try:
        candidates = await asyncio.to_thread(screen_stocks)
        ttl = screener_ttl()
        await cache_set(KEY_SCREENER_STOCKS, candidates, ttl=ttl)
        return {"candidates": candidates, **_cache_meta(None)}
    except Exception as e:
        logger.exception("Stock screener failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── Watchlist: Options (CSP/LEAPS) ────────────────────────────────────────────

@app.get("/api/watchlist")
def api_get_watchlist():
    try:
        return {"watchlist": get_watchlist()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watchlist")
async def api_update_watchlist(data: WatchlistUpdate, background_tasks: BackgroundTasks):
    try:
        updated_tickers = [t.strip().upper() for t in data.tickers if t.strip()]
        update_watchlist(updated_tickers)
        await invalidate_screener_cache()
        background_tasks.add_task(_rescan_csp_background)
        return {"status": "success", "watchlist": updated_tickers}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── CSP Settings ──────────────────────────────────────────────────────────────

@app.get("/api/settings/csp")
def api_get_csp_settings():
    try:
        return {"settings": get_csp_settings()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _rescan_csp_background() -> None:
    """Re-run the CSP screener with the freshly saved settings and repopulate the cache."""
    try:
        candidates = await asyncio.to_thread(screen_csp_candidates)
        ttl = screener_ttl()
        await cache_set(KEY_SCREENER_CSP, candidates, ttl=ttl)
        logger.info("Background CSP re-scan complete: %d candidates cached", len(candidates))
    except Exception as exc:
        logger.error("Background CSP re-scan failed: %s", exc)


@app.post("/api/settings/csp")
async def api_update_csp_settings(data: CspSettingsUpdate, background_tasks: BackgroundTasks):
    try:
        update_csp_settings(data.model_dump())
        from ..cache import cache_delete
        await cache_delete(KEY_SCREENER_CSP)
        background_tasks.add_task(_rescan_csp_background)
        return {"status": "success", "settings": data.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Watchlist: Stocks ─────────────────────────────────────────────────────────

@app.get("/api/watchlist/stock")
def api_get_stock_watchlist():
    try:
        return {"watchlist": get_stock_watchlist()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/watchlist/stock")
async def api_update_stock_watchlist(data: WatchlistUpdate):
    try:
        updated_tickers = [t.strip().upper() for t in data.tickers if t.strip()]
        update_stock_watchlist(updated_tickers)
        # Stock watchlist changed — invalidate stock screener cache only
        from ..cache import cache_delete
        await cache_delete(KEY_SCREENER_STOCKS)
        return {"status": "success", "watchlist": updated_tickers}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Cache status endpoint (dev/debug) ─────────────────────────────────────────

@app.get("/api/cache/status")
async def cache_status():
    """Return the current market status and remaining TTL for each cached key.

    Useful for debugging cache behavior from the command line or in dev tools.
    """
    from ..cache import get_redis, market_is_open, seconds_until_next_open
    client = get_redis()
    keys = [KEY_SCREENER_CSP, KEY_SCREENER_LEAPS, KEY_SCREENER_STOCKS, KEY_MARKET_POSTURE]
    result = {
        "market_open": market_is_open(),
        "market_status": market_status_label(),
        "seconds_until_next_open": None if market_is_open() else seconds_until_next_open(),
        "keys": {},
    }
    for key in keys:
        try:
            ttl = await client.ttl(key)
            exists = await client.exists(key)
            result["keys"][key] = {
                "cached": bool(exists),
                "ttl_seconds": ttl if ttl >= 0 else None,
            }
        except Exception as exc:
            result["keys"][key] = {"error": str(exc)}
    return result


# ── Backtester ────────────────────────────────────────────────────────────────

@app.post("/api/backtest/run")
async def api_run_backtest(req: BacktestRequest):
    try:
        # Run in thread pool to avoid blocking the event loop
        df = await asyncio.to_thread(
            get_historical_data,
            symbol=req.ticker,
            start_date=req.start_date,
            end_date=req.end_date,
            timeframe=req.timeframe
        )
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for {req.ticker}")
            
        result = await asyncio.to_thread(run_backtest, req, df)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Backtest run failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/backtest/walk-forward")
async def api_run_walk_forward(req: WalkForwardRequest):
    try:
        df = await asyncio.to_thread(
            get_historical_data,
            symbol=req.ticker,
            start_date=req.start_date,
            end_date=req.end_date,
            timeframe=req.timeframe
        )
        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data found for {req.ticker}")
            
        result = await asyncio.to_thread(run_walk_forward, req, df)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Walk-forward run failed")
        raise HTTPException(status_code=500, detail=str(e))


class SaveStrategyRequest(BaseModel):
    name: str
    definition: dict


@app.post("/api/backtest/strategies")
def api_save_strategy(req: SaveStrategyRequest):
    try:
        strategy_id = save_strategy(req.name, req.definition)
        return {"status": "success", "id": strategy_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/backtest/strategies")
def api_get_strategies():
    try:
        return {"strategies": get_strategies()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/backtest/strategies/{strategy_id}")
def api_delete_strategy(strategy_id: int):
    try:
        success = delete_strategy(strategy_id)
        if not success:
            raise HTTPException(status_code=404, detail="Strategy not found")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Market Data Store ─────────────────────────────────────────────────────────

@app.get("/api/market-data/status")
def api_market_data_status():
    """Return metadata about the local OHLCV + fundamentals store.

    Includes ticker count, row count, last update time, and staleness warning.
    """
    try:
        ensure_market_data_tables()
        return get_store_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/market-data/refresh")
async def api_market_data_refresh(
    background_tasks: BackgroundTasks,
    full: bool = False,
):
    """Trigger an OHLCV + fundamentals data refresh.

    Query params:
        full: If true, download 2 years of history (initial backfill).
              If false (default), download last 5 trading days (incremental).

    The refresh runs as a background task — this endpoint returns immediately.
    """
    def _run_refresh():
        try:
            result = refresh_universe(full=full)
            logger.info("Market data refresh complete: %s", result)
        except Exception as exc:
            logger.error("Market data refresh failed: %s", exc, exc_info=True)

    background_tasks.add_task(asyncio.to_thread, _run_refresh)
    return {
        "status": "started",
        "mode": "full" if full else "incremental",
        "message": "Data refresh started in the background. Check /api/market-data/status for progress.",
    }
