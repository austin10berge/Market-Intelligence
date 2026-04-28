"""FastAPI backend — market-hours-aware Redis cache for all screener endpoints."""

import json
import logging
import os
import sqlite3
from contextlib import closing
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
    cache_get, cache_set, screener_ttl,
    invalidate_screener_cache, invalidate_market_posture,
    market_status_label, market_is_open,
    KEY_SCREENER_CSP, KEY_SCREENER_LEAPS, KEY_SCREENER_STOCKS, KEY_MARKET_POSTURE,
)
from ..screener.options import screen_csp_candidates, screen_leaps_candidates
from ..screener.stocks import screen_stocks
from ..main import run_pipeline

logger = logging.getLogger(__name__)

app = FastAPI(title="Market Intelligence API")

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
        # Run blocking screener in a thread so the event loop isn't starved
        candidates = await asyncio.to_thread(screen_csp_candidates)
        ttl = screener_ttl()
        await cache_set(KEY_SCREENER_CSP, candidates, ttl=ttl)
        return {"candidates": candidates, **_cache_meta(None)}
    except Exception as e:
        logger.exception("CSP screener failed")
        raise HTTPException(status_code=500, detail=str(e))


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
async def get_stock_candidates():
    """Return stock screener results. Market-hours-aware TTL caching."""
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
        await invalidate_screener_cache()  # Watchlist changed — stale data is now invalid
        # Kick off a fresh CSP scan against the new watchlist immediately
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
        # Invalidate stale cache immediately so no client gets old results
        from ..cache import cache_delete
        await cache_delete(KEY_SCREENER_CSP)
        # Kick off a fresh scan in the background so the dashboard updates promptly
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


# ── CSP Debug endpoint ───────────────────────────────────────────────────────

@app.get("/api/screener/csp/debug")
async def debug_csp_screener():
    """Run the CSP screener with verbose per-ticker rejection reasons.

    Bypasses the cache entirely and returns a full diagnostic breakdown:
    - Current settings being used
    - Per-ticker technical indicator values and pass/fail status
    - Contract-level rejection counts by reason
    - Sample of raw Alpaca snapshot data for the first ticker that passes tech filters

    This endpoint is intentionally slow (runs the full screener) — use only for debugging.
    """
    import yfinance as yf
    import pandas as pd
    import pandas_ta as ta
    import httpx
    from ..db import get_watchlist, get_csp_settings
    from ..screener.options import _get_valid_expirations, _compute_technicals
    from datetime import date, datetime

    settings_used = get_csp_settings()
    tickers = get_watchlist()

    report = {
        "settings": settings_used,
        "watchlist": tickers,
        "ticker_count": len(tickers),
        "technical_results": [],
        "tech_pass_count": 0,
        "tech_fail_count": 0,
        "contract_pipeline": {
            "potential_contracts": 0,
            "no_alpaca_snapshot": 0,
            "low_iv": 0,
            "low_premium": 0,
            "low_roc": 0,
            "wide_spread": 0,
            "passed": 0,
        },
        "alpaca_sample": None,
        "final_candidate_count": 0,
    }

    # ── Step 1: Technical filter — report per ticker ─────────────────────────
    qualifying = []
    for symbol in tickers:
        entry = {"symbol": symbol, "pass": False, "reason": None, "rsi": None, "adx": None, "return_5d": None}
        tech = await asyncio.to_thread(_compute_technicals, symbol)
        if tech is None:
            entry["reason"] = "could not compute technicals (insufficient history or yfinance error)"
            report["technical_results"].append(entry)
            report["tech_fail_count"] += 1
            continue

        entry["rsi"] = tech["rsi"]
        entry["adx"] = tech["adx"]
        entry["return_5d"] = tech["return_5d"]

        rsi, adx = tech["rsi"], tech["adx"]
        if not (settings_used["min_rsi"] <= rsi <= settings_used["max_rsi"]):
            entry["reason"] = f"RSI {rsi} outside [{settings_used['min_rsi']}, {settings_used['max_rsi']}]"
            report["technical_results"].append(entry)
            report["tech_fail_count"] += 1
            continue
        if not (settings_used["min_adx"] <= adx <= settings_used["max_adx"]):
            entry["reason"] = f"ADX {adx} outside [{settings_used['min_adx']}, {settings_used['max_adx']}]"
            report["technical_results"].append(entry)
            report["tech_fail_count"] += 1
            continue
        if settings_used.get("pullback_mode"):
            ret5d = tech.get("return_5d")
            if not (rsi <= 55 and ret5d is not None and ret5d < 0):
                entry["reason"] = f"pullback_mode: rsi={rsi} ret5d={ret5d} (need rsi<=55 and ret5d<0)"
                report["technical_results"].append(entry)
                report["tech_fail_count"] += 1
                continue

        entry["pass"] = True
        entry["reason"] = "passed"
        report["technical_results"].append(entry)
        report["tech_pass_count"] += 1
        qualifying.append(symbol)

    if not qualifying:
        return report

    # ── Step 2: Contract collection — count only, no full chain parse ─────────
    potential_contracts = []
    for symbol in qualifying[:5]:  # Limit to first 5 passing tickers for speed
        try:
            ticker = yf.Ticker(symbol)
            expirations = ticker.options
            current_price = ticker.fast_info.last_price
            valid_exps = _get_valid_expirations(expirations, settings_used["min_dte"], settings_used["max_dte"])
            if not valid_exps:
                continue
            max_strike = current_price * (1 - settings_used["min_otm_pct"] / 100)
            min_strike = current_price * (1 - settings_used["max_otm_pct"] / 100)
            for exp_str in valid_exps:
                try:
                    chain = ticker.option_chain(exp_str)
                    puts = chain.puts
                    valid_puts = puts[(puts["strike"] >= min_strike) & (puts["strike"] <= max_strike)]
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    dte = (exp_date - date.today()).days
                    for _, row in valid_puts.iterrows():
                        potential_contracts.append({
                            "symbol": symbol,
                            "occ_symbol": row["contractSymbol"],
                            "yf_premium": row["lastPrice"],
                            "current_price": current_price,
                            "strike": row["strike"],
                            "otm_pct": ((current_price - row["strike"]) / current_price) * 100,
                            "dte": dte,
                        })
                except Exception:
                    continue
        except Exception as e:
            pass

    report["contract_pipeline"]["potential_contracts"] = len(potential_contracts)

    if not potential_contracts:
        return report

    # ── Step 3: Alpaca snapshot — check a sample ──────────────────────────────
    occ_symbols = [c["occ_symbol"] for c in potential_contracts[:50]]
    url = f"{settings.alpaca_data_url}/v1beta1/options/snapshots"
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
        "accept": "application/json",
    }
    alpaca_snapshots = {}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, params={"symbols": ",".join(occ_symbols)})
            if resp.status_code == 200:
                alpaca_snapshots = resp.json().get("snapshots", {})
                report["alpaca_sample"] = {
                    "http_status": resp.status_code,
                    "symbols_requested": len(occ_symbols),
                    "symbols_returned": len(alpaca_snapshots),
                    "sample_keys": list(alpaca_snapshots.keys())[:5],
                    "sample_snapshot": next(iter(alpaca_snapshots.values()), None),
                }
            else:
                report["alpaca_sample"] = {"http_status": resp.status_code, "error": resp.text[:500]}
    except Exception as e:
        report["alpaca_sample"] = {"error": str(e)}

    # ── Step 4: Walk contracts through filters, count rejections ─────────────
    for c in potential_contracts:
        snap = alpaca_snapshots.get(c["occ_symbol"], {})
        if not snap:
            report["contract_pipeline"]["no_alpaca_snapshot"] += 1
            continue
        quote = snap.get("latestQuote", {})
        trade = snap.get("latestTrade", {})
        bid = quote.get("bp", 0.0)
        ask = quote.get("ap", 0.0)
        premium = trade.get("p", 0.0) or c["yf_premium"]
        iv_raw = snap.get("impliedVolatility", 0.0)
        iv = float(iv_raw) * 100 if iv_raw and float(iv_raw) <= 3.0 else float(iv_raw or 0)

        if iv > 0 and iv < settings_used.get("min_iv", 25.0):
            report["contract_pipeline"]["low_iv"] += 1
            continue
        if premium <= 0.15:
            report["contract_pipeline"]["low_premium"] += 1
            continue
        roc = (premium / c["strike"]) * 100 if c["strike"] > 0 else 0
        if roc < settings_used["min_roc"]:
            report["contract_pipeline"]["low_roc"] += 1
            continue
        spread_pct = 0.0
        if bid > 0 and ask > bid:
            spread_pct = ((ask - bid) / ((ask + bid) / 2)) * 100  # midpoint spread
            if spread_pct > settings_used["max_spread_pct"]:
                report["contract_pipeline"]["wide_spread"] += 1
                continue
        report["contract_pipeline"]["passed"] += 1

    report["final_candidate_count"] = report["contract_pipeline"]["passed"]
    return report


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
