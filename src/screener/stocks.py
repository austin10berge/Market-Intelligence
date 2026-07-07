"""Stock screener for broad market tracking using yfinance and Alpaca."""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

import httpx
import pandas as pd
import pandas_ta as ta
import yfinance as yf

from ..config import settings
from ..db import get_stock_iv_history, get_stock_watchlist, store_stock_iv_snapshot
from ..indicators import compute_adr20_pct

logger = logging.getLogger(__name__)

ALPACA_TARGET_DTE = 37
ALPACA_MIN_DTE = 30
ALPACA_MAX_DTE = 47
ALPACA_STRIKE_WINDOW_PCT = 0.10
DEFAULT_RISK_FREE_RATE = 0.045
IV_RANK_LOOKBACK_DAYS = 252
MIN_IV_RANK_POINTS = 20
ALPACA_OPTION_CONTRACTS_URL = "https://paper-api.alpaca.markets/v2/options/contracts"


def _safe_pct(new_val, old_val):
    if pd.isna(new_val) or pd.isna(old_val) or old_val == 0:
        return 0.0
    return float(((new_val - old_val) / old_val) * 100)


def _to_float(value: object) -> float | None:
    """Convert loosely typed API values into floats, returning None for NaN."""
    if value is None:
        return None
    try:
        result = float(value)
        return None if math.isnan(result) else result
    except (TypeError, ValueError):
        return None


def _calculate_rv20(hist: pd.DataFrame) -> float | None:
    """Compute 20-day realized volatility from daily close-to-close returns."""
    closes = hist.get("Close")
    if closes is None or len(closes) < 21:
        return None

    daily_returns = closes.pct_change().dropna()
    if len(daily_returns) < 20:
        return None

    rv20 = daily_returns.tail(20).std(ddof=1) * math.sqrt(252) * 100
    if pd.isna(rv20) or rv20 <= 0:
        return None
    return float(rv20)


def _calculate_adr20(hist: pd.DataFrame) -> float | None:
    """Compute 20-day average daily range as % of close (ADR)."""
    if len(hist) < 20:
        return None
    adr = compute_adr20_pct(hist["High"].iloc[-20:], hist["Low"].iloc[-20:], hist["Close"].iloc[-20:])
    return round(adr, 2) if adr is not None else None


def _calculate_volume_ratio(hist: pd.DataFrame) -> float | None:
    """Compute current day volume relative to 20-day average volume."""
    if len(hist) < 20:
        return None
    avg_vol_20d = hist["Volume"].iloc[-20:-1].mean()
    current_vol = hist["Volume"].iloc[-1]
    if pd.isna(avg_vol_20d) or avg_vol_20d <= 0 or pd.isna(current_vol):
        return None
    return round(float(current_vol / avg_vol_20d), 2)


def _norm_cdf(value: float) -> float:
    """Normal CDF used for Black-Scholes pricing."""
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _black_scholes_price(
    spot: float,
    strike: float,
    time_to_expiry: float,
    risk_free_rate: float,
    volatility: float,
    option_type: str,
) -> float:
    """Approximate European option value for IV back-solving."""
    if time_to_expiry <= 0 or volatility <= 0 or spot <= 0 or strike <= 0:
        return 0.0

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (
        math.log(spot / strike) + (risk_free_rate + 0.5 * volatility * volatility) * time_to_expiry
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t

    if option_type == "C":
        return (spot * _norm_cdf(d1)) - (
            strike * math.exp(-risk_free_rate * time_to_expiry) * _norm_cdf(d2)
        )

    return (strike * math.exp(-risk_free_rate * time_to_expiry) * _norm_cdf(-d2)) - (
        spot * _norm_cdf(-d1)
    )


def _quote_midpoint(snapshot: dict) -> float | None:
    """Use the most reliable available quote midpoint or last trade."""
    latest_quote = snapshot.get("latestQuote") or snapshot.get("latest_quote") or {}
    bid = _to_float(
        latest_quote.get("bp") if "bp" in latest_quote else latest_quote.get("bid_price")
    )
    ask = _to_float(
        latest_quote.get("ap") if "ap" in latest_quote else latest_quote.get("ask_price")
    )

    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        return (bid + ask) / 2

    latest_trade = snapshot.get("latestTrade") or snapshot.get("latest_trade") or {}
    trade_price = _to_float(
        latest_trade.get("p") if "p" in latest_trade else latest_trade.get("price")
    )
    if trade_price is not None and trade_price > 0:
        return trade_price

    return None


def _parse_occ_symbol(contract_symbol: str) -> tuple[date, str, float] | None:
    """Parse OCC option contract symbols like AAPL241220C00300000."""
    if len(contract_symbol) < 15:
        return None

    expiry_raw = contract_symbol[-15:-9]
    option_type = contract_symbol[-9]
    strike_raw = contract_symbol[-8:]

    try:
        expiration = datetime.strptime(expiry_raw, "%y%m%d").date()
        strike = int(strike_raw) / 1000
    except ValueError:
        return None

    if option_type not in {"C", "P"}:
        return None

    return expiration, option_type, strike


def _extract_implied_volatility(snapshot: dict) -> float | None:
    """Read IV from Alpaca snapshot payloads, tolerating naming variants."""
    raw_iv = snapshot.get("implied_volatility")
    if raw_iv is None:
        raw_iv = snapshot.get("impliedVolatility")
    if raw_iv is None and isinstance(snapshot.get("greeks"), dict):
        raw_iv = snapshot["greeks"].get("implied_volatility")
    if raw_iv is None and isinstance(snapshot.get("greeks"), dict):
        raw_iv = snapshot["greeks"].get("impliedVolatility")

    iv = _to_float(raw_iv)
    if iv is None or iv <= 0:
        return None

    return iv * 100 if iv <= 3 else iv


def _solve_implied_volatility(
    option_price: float,
    spot: float,
    strike: float,
    time_to_expiry: float,
    option_type: str,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> float | None:
    """Back out IV from an option midpoint using bisection on Black-Scholes."""
    if option_price <= 0 or spot <= 0 or strike <= 0 or time_to_expiry <= 0:
        return None

    if option_type == "C":
        intrinsic = max(spot - strike, 0.0)
    else:
        intrinsic = max(strike - spot, 0.0)

    if option_price <= intrinsic:
        return None

    low = 0.0001
    high = 5.0
    for _ in range(80):
        mid = (low + high) / 2
        model_price = _black_scholes_price(
            spot=spot,
            strike=strike,
            time_to_expiry=time_to_expiry,
            risk_free_rate=risk_free_rate,
            volatility=mid,
            option_type=option_type,
        )

        if abs(model_price - option_price) < 0.0001:
            return mid * 100

        if model_price > option_price:
            high = mid
        else:
            low = mid

    return ((low + high) / 2) * 100


def _build_alpaca_client() -> httpx.Client | None:
    """Create a shared Alpaca market data client if credentials are configured."""
    if not settings.alpaca_api_key or not settings.alpaca_api_secret:
        return None

    return httpx.Client(
        base_url=settings.alpaca_data_url.rstrip("/"),
        headers={
            "APCA-API-KEY-ID": settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
            "accept": "application/json",
        },
        timeout=10.0,
    )


def _build_alpaca_trading_client() -> httpx.Client | None:
    """Create Alpaca trading client for option contract discovery."""
    if not settings.alpaca_api_key or not settings.alpaca_api_secret:
        return None

    return httpx.Client(
        base_url="https://paper-api.alpaca.markets",
        headers={
            "APCA-API-KEY-ID": settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
            "accept": "application/json",
        },
        timeout=20.0,
    )


def _fetch_alpaca_atm_iv_percent(
    client: httpx.Client | None,
    symbol: str,
    current_price: float,
) -> float | None:
    """Use Alpaca option chain snapshots to estimate 30-45 DTE ATM IV."""
    if client is None or current_price <= 0:
        return None

    today = date.today()
    params = {
        "feed": "indicative",
        "limit": 200,
        "expiration_date_gte": (today + timedelta(days=ALPACA_MIN_DTE)).isoformat(),
        "expiration_date_lte": (today + timedelta(days=ALPACA_MAX_DTE)).isoformat(),
        "strike_price_gte": round(current_price * (1 - ALPACA_STRIKE_WINDOW_PCT), 2),
        "strike_price_lte": round(current_price * (1 + ALPACA_STRIKE_WINDOW_PCT), 2),
    }

    chain_points: dict[tuple[date, float], list[float]] = {}
    next_page_token: str | None = None

    while True:
        request_params = dict(params)
        if next_page_token:
            request_params["page_token"] = next_page_token

        try:
            response = client.get(f"/v1beta1/options/snapshots/{symbol}", params=request_params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("Alpaca option chain failed for %s: %s", symbol, exc)
            return None
        except httpx.HTTPError as exc:
            logger.warning("Alpaca request failed for %s: %s", symbol, exc)
            return None

        payload = response.json()
        snapshots = payload.get("snapshots", {})

        for contract_symbol, snapshot in snapshots.items():
            parsed = _parse_occ_symbol(contract_symbol)
            if parsed is None:
                continue

            expiration, option_type, strike = parsed
            time_to_expiry = max((expiration - today).days, 1) / 365.0

            iv = _extract_implied_volatility(snapshot or {})
            if iv is None:
                option_price = _quote_midpoint(snapshot or {})
                if option_price is not None:
                    iv = _solve_implied_volatility(
                        option_price=option_price,
                        spot=current_price,
                        strike=strike,
                        time_to_expiry=time_to_expiry,
                        option_type=option_type,
                    )
            if iv is None:
                continue

            chain_points.setdefault((expiration, strike), []).append(iv)

        next_page_token = payload.get("next_page_token")
        if not next_page_token:
            break

    if not chain_points:
        return None

    best_key = min(
        chain_points,
        key=lambda item: (
            abs((item[0] - today).days - ALPACA_TARGET_DTE),
            abs(item[1] - current_price),
        ),
    )
    iv_values = chain_points[best_key]
    return sum(iv_values) / len(iv_values)


def _calculate_iv_percentile(current_iv: float, iv_history: list[dict]) -> float | None:
    """Compute IV Percentile from stored ATM IV history.

    IV Percentile = % of historical observations where IV was *below* the current IV.
    This is fundamentally different from IV Rank:

      IV Rank      = (current - min) / (max - min)  — sensitive to a single spike
      IV Percentile = count(history < current) / count(history)  — distribution-based

    Example: IV mostly trades 20-30, one spike to 80.
      At current IV = 30: IV Rank ≈ 20%  |  IV Percentile ≈ 80%
    """
    history_values = [float(row["atm_iv"]) for row in iv_history if row.get("atm_iv") is not None]
    if current_iv <= 0:
        return None

    if len(history_values) < MIN_IV_RANK_POINTS:
        return None

    below = sum(1 for v in history_values if v < current_iv)
    iv_percentile = (below / len(history_values)) * 100
    return round(iv_percentile, 2)


def _fetch_option_contracts(
    trading_client: httpx.Client | None,
    symbol: str,
    expiration_gte: date,
    expiration_lte: date,
    strike_price_gte: float | None = None,
    strike_price_lte: float | None = None,
) -> list[dict]:
    """Fetch active and inactive option contracts for an underlying across an expiration range."""
    if trading_client is None:
        return []

    contracts: list[dict] = []
    seen_symbols: set[str] = set()

    for status in ("active", "inactive"):
        next_page_token: str | None = None
        while True:
            params = {
                "underlying_symbols": symbol,
                "status": status,
                "expiration_date_gte": expiration_gte.isoformat(),
                "expiration_date_lte": expiration_lte.isoformat(),
                "limit": 1000,
            }
            if strike_price_gte is not None:
                params["strike_price_gte"] = round(strike_price_gte, 2)
            if strike_price_lte is not None:
                params["strike_price_lte"] = round(strike_price_lte, 2)
            if next_page_token:
                params["page_token"] = next_page_token

            try:
                response = trading_client.get("/v2/options/contracts", params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("Failed to fetch option contracts for %s: %s", symbol, exc)
                break

            payload = response.json()
            for contract in payload.get("option_contracts", []):
                contract_symbol = contract.get("symbol")
                if not contract_symbol or contract_symbol in seen_symbols:
                    continue
                seen_symbols.add(contract_symbol)
                contracts.append(contract)

            next_page_token = payload.get("next_page_token")
            if not next_page_token:
                break

    return contracts


def _fetch_option_bars(
    data_client: httpx.Client | None,
    contract_symbols: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, dict[str, float]]:
    """Fetch daily close prices for option contracts keyed by symbol/date."""
    if data_client is None or not contract_symbols:
        return {}

    symbol_to_bars: dict[str, dict[str, float]] = defaultdict(dict)
    batch_size = 100
    for idx in range(0, len(contract_symbols), batch_size):
        batch = contract_symbols[idx : idx + batch_size]
        next_page_token: str | None = None
        while True:
            params = {
                "symbols": ",".join(batch),
                "timeframe": "1Day",
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "limit": 10000,
                "feed": "indicative",
            }
            if next_page_token:
                params["page_token"] = next_page_token

            try:
                response = data_client.get("/v1beta1/options/bars", params=params)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("Failed to fetch historical option bars: %s", exc)
                break

            payload = response.json()
            for contract_symbol, bars in payload.get("bars", {}).items():
                for bar in bars:
                    bar_date = str(bar.get("t", ""))[:10]
                    close_price = _to_float(bar.get("c"))
                    if bar_date and close_price is not None and close_price > 0:
                        symbol_to_bars[contract_symbol][bar_date] = close_price

            next_page_token = payload.get("next_page_token")
            if not next_page_token:
                break

    return symbol_to_bars


def _build_underlying_history(symbol: str, days: int) -> pd.DataFrame:
    """Fetch enough underlying history to support IV backfill date selection."""
    buffer_days = days + ALPACA_MAX_DTE + 30
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=f"{buffer_days}d")
    if hist.empty:
        return hist

    hist = hist.copy()
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    return hist


def backfill_stock_iv_history(
    tickers: list[str] | None = None,
    trading_days: int = 60,
) -> dict[str, int]:
    """Backfill daily ATM IV snapshots for the most recent trading days."""
    if tickers is None:
        tickers = get_stock_watchlist()

    data_client = _build_alpaca_client()
    trading_client = _build_alpaca_trading_client()
    backfilled_counts: dict[str, int] = {}

    try:
        for symbol in tickers:
            try:
                hist = _build_underlying_history(symbol, trading_days)
                if hist.empty:
                    backfilled_counts[symbol] = 0
                    continue

                trading_rows = hist.tail(trading_days)
                trading_dates = [ts.date() for ts in trading_rows.index]
                if not trading_dates:
                    backfilled_counts[symbol] = 0
                    continue

                contract_expiry_start = min(trading_dates) + timedelta(days=ALPACA_MIN_DTE)
                contract_expiry_end = max(trading_dates) + timedelta(days=ALPACA_MAX_DTE)
                min_close = _to_float(trading_rows["Close"].min())
                max_close = _to_float(trading_rows["Close"].max())
                contracts = _fetch_option_contracts(
                    trading_client=trading_client,
                    symbol=symbol,
                    expiration_gte=contract_expiry_start,
                    expiration_lte=contract_expiry_end,
                    strike_price_gte=(min_close * 0.75) if min_close is not None else None,
                    strike_price_lte=(max_close * 1.25) if max_close is not None else None,
                )
                if not contracts:
                    backfilled_counts[symbol] = 0
                    continue

                contracts_by_expiry: dict[date, list[dict]] = defaultdict(list)
                for contract in contracts:
                    expiration_str = contract.get("expiration_date")
                    strike_price = _to_float(contract.get("strike_price"))
                    option_type = contract.get("type")
                    contract_symbol = contract.get("symbol")
                    if (
                        not expiration_str
                        or strike_price is None
                        or option_type not in {"call", "put"}
                    ):
                        continue
                    expiration = datetime.strptime(expiration_str, "%Y-%m-%d").date()
                    contracts_by_expiry[expiration].append(
                        {
                            "symbol": contract_symbol,
                            "strike": strike_price,
                            "type": "C" if option_type == "call" else "P",
                            "expiration": expiration,
                        }
                    )

                selected_contracts: dict[tuple[date, float], dict[str, str]] = {}
                unique_symbols: set[str] = set()
                for ts, row in trading_rows.iterrows():
                    trade_date = ts.date()
                    close_price = _to_float(row.get("Close"))
                    if close_price is None or close_price <= 0:
                        continue

                    eligible_expiries = [
                        expiry
                        for expiry in contracts_by_expiry
                        if ALPACA_MIN_DTE <= (expiry - trade_date).days <= ALPACA_MAX_DTE
                    ]
                    if not eligible_expiries:
                        continue

                    target_expiry = min(
                        eligible_expiries,
                        key=lambda expiry: abs((expiry - trade_date).days - ALPACA_TARGET_DTE),
                    )
                    contracts_for_expiry = contracts_by_expiry[target_expiry]
                    strikes = sorted({contract["strike"] for contract in contracts_for_expiry})
                    if not strikes:
                        continue
                    target_strike = min(strikes, key=lambda strike: abs(strike - close_price))

                    selected = selected_contracts.setdefault((target_expiry, target_strike), {})
                    for contract in contracts_for_expiry:
                        if contract["strike"] != target_strike:
                            continue
                        selected[contract["type"]] = contract["symbol"]
                        unique_symbols.add(contract["symbol"])

                option_bars = _fetch_option_bars(
                    data_client=data_client,
                    contract_symbols=sorted(unique_symbols),
                    start_date=min(trading_dates),
                    end_date=max(trading_dates),
                )

                stored_count = 0
                for ts, row in trading_rows.iterrows():
                    trade_date = ts.date()
                    close_price = _to_float(row.get("Close"))
                    if close_price is None or close_price <= 0:
                        continue

                    eligible_expiries = [
                        expiry
                        for expiry in contracts_by_expiry
                        if ALPACA_MIN_DTE <= (expiry - trade_date).days <= ALPACA_MAX_DTE
                    ]
                    if not eligible_expiries:
                        continue
                    target_expiry = min(
                        eligible_expiries,
                        key=lambda expiry: abs((expiry - trade_date).days - ALPACA_TARGET_DTE),
                    )
                    contracts_for_expiry = contracts_by_expiry[target_expiry]
                    strikes = sorted({contract["strike"] for contract in contracts_for_expiry})
                    if not strikes:
                        continue
                    target_strike = min(strikes, key=lambda strike: abs(strike - close_price))

                    iv_values: list[float] = []
                    for option_type in ("C", "P"):
                        contract = next(
                            (
                                contract
                                for contract in contracts_for_expiry
                                if contract["strike"] == target_strike
                                and contract["type"] == option_type
                            ),
                            None,
                        )
                        if contract is None:
                            continue
                        option_price = option_bars.get(contract["symbol"], {}).get(
                            trade_date.isoformat()
                        )
                        if option_price is None:
                            continue
                        iv = _solve_implied_volatility(
                            option_price=option_price,
                            spot=close_price,
                            strike=target_strike,
                            time_to_expiry=max((target_expiry - trade_date).days, 1) / 365.0,
                            option_type=option_type,
                        )
                        if iv is not None:
                            iv_values.append(iv)

                    if not iv_values:
                        continue

                    store_stock_iv_snapshot(
                        snapshot_date=trade_date,
                        symbol=symbol,
                        atm_iv=sum(iv_values) / len(iv_values),
                    )
                    stored_count += 1

                backfilled_counts[symbol] = stored_count
            except Exception as exc:
                logger.warning("Failed to backfill IV history for %s: %s", symbol, exc)
                backfilled_counts[symbol] = 0
    finally:
        if data_client is not None:
            data_client.close()
        if trading_client is not None:
            trading_client.close()

    return backfilled_counts


def screen_stocks(tickers: list[str] | None = None, persist_history: bool = True) -> list[dict]:
    """Fetch stock fundamentals, performance metrics, ATM IV/RV20, and IV Rank."""
    if tickers is None:
        tickers = get_stock_watchlist()

    logger.info("Screening %s stocks...", len(tickers))
    candidates = []
    alpaca_client = _build_alpaca_client()
    snapshot_date = date.today()

    try:
        for symbol in tickers:
            try:
                ticker = yf.Ticker(symbol)
                info = ticker.info

                # 1 year gives enough history for 200 SMA (needs ~200 trading days).
                hist = ticker.history(period="1y")
                if hist.empty:
                    continue

                current_price = hist["Close"].iloc[-1]

                pct_1d = 0.0
                if len(hist) >= 2:
                    prev_close = hist["Close"].iloc[-2]
                    pct_1d = _safe_pct(current_price, prev_close)

                pct_1w = 0.0
                if len(hist) >= 5:
                    week_ago = hist["Close"].iloc[-5]
                    pct_1w = _safe_pct(current_price, week_ago)

                pct_1m = 0.0
                if len(hist) >= 21:
                    month_ago = hist["Close"].iloc[-21]
                    pct_1m = _safe_pct(current_price, month_ago)
                elif len(hist) > 0:
                    month_ago = hist["Close"].iloc[0]
                    pct_1m = _safe_pct(current_price, month_ago)

                pe_val = info.get("trailingPE")
                forward_pe_val = info.get("forwardPE")
                peg_ratio_val = _to_float(info.get("trailingPegRatio"))
                beta_val = info.get("beta")
                fcf_raw = _to_float(info.get("freeCashflow"))
                fcf_val = round(fcf_raw / 1e9, 2) if fcf_raw is not None else None
                debt_to_equity_val = _to_float(info.get("debtToEquity"))
                revenue_raw = _to_float(info.get("totalRevenue"))
                revenue_val = round(revenue_raw / 1e9, 2) if revenue_raw is not None else None
                revenue_growth_val = _to_float(info.get("revenueGrowth"))
                eps_val = _to_float(info.get("trailingEps"))
                eps_growth_val = _to_float(info.get("earningsGrowth"))
                rv20_val = _calculate_rv20(hist)
                adr20_val = _calculate_adr20(hist)
                atm_iv_val = _fetch_alpaca_atm_iv_percent(
                    alpaca_client, symbol, float(current_price)
                )
                if atm_iv_val is not None and persist_history:
                    store_stock_iv_snapshot(
                        snapshot_date=snapshot_date, symbol=symbol, atm_iv=atm_iv_val
                    )

                iv_history = get_stock_iv_history(symbol, lookback_days=IV_RANK_LOOKBACK_DAYS)
                if atm_iv_val is not None and len(iv_history) < MIN_IV_RANK_POINTS:
                    logger.info(
                        "Auto-backfilling IV history for %s (%d points)", symbol, len(iv_history)
                    )
                    backfill_stock_iv_history([symbol])
                    iv_history = get_stock_iv_history(symbol, lookback_days=IV_RANK_LOOKBACK_DAYS)
                iv_percentile_val = (
                    _calculate_iv_percentile(atm_iv_val, iv_history)
                    if atm_iv_val is not None
                    else None
                )
                atm_iv_rv20_val = (
                    atm_iv_val / rv20_val
                    if atm_iv_val is not None and rv20_val is not None
                    else None
                )

                # Build 1-month price history for sparkline chart
                price_history_1m = []
                history_slice = hist["Close"].tail(21) if len(hist) >= 21 else hist["Close"]
                if len(history_slice) > 0:
                    price_history_1m = [
                        round(float(p), 2) for p in history_slice.tolist() if not pd.isna(p)
                    ]

                # Technical indicators for TA annotation pills
                sma_200_val: float | None = None
                bb_upper_val: float | None = None
                bb_mid_val: float | None = None
                bb_lower_val: float | None = None
                rsi_val: float | None = None
                sma_50_val: float | None = None
                ema_200_val: float | None = None
                volume_ratio_val: float | None = None
                try:
                    close = hist["Close"]
                    if len(close) >= 200:
                        v = float(ta.sma(close, length=200).iloc[-1])
                        sma_200_val = None if math.isnan(v) else round(v, 2)
                    if len(close) >= 20:
                        bbands = ta.bbands(close, length=20, std=2)
                        if bbands is not None and not bbands.empty:
                            upper_col = next((c for c in bbands.columns if "BBU" in c), None)
                            mid_col = next((c for c in bbands.columns if "BBM" in c), None)
                            lower_col = next((c for c in bbands.columns if "BBL" in c), None)
                            if upper_col:
                                v = float(bbands[upper_col].iloc[-1])
                                bb_upper_val = None if math.isnan(v) else round(v, 2)
                            if mid_col:
                                v = float(bbands[mid_col].iloc[-1])
                                bb_mid_val = None if math.isnan(v) else round(v, 2)
                            if lower_col:
                                v = float(bbands[lower_col].iloc[-1])
                                bb_lower_val = None if math.isnan(v) else round(v, 2)
                    if len(close) >= 14:
                        rsi_s = ta.rsi(close, length=14)
                        if rsi_s is not None and not rsi_s.empty:
                            v = float(rsi_s.iloc[-1])
                            rsi_val = None if math.isnan(v) else round(v, 1)
                    if len(close) >= 50:
                        sma_50_s = ta.sma(close, length=50)
                        if sma_50_s is not None and not sma_50_s.empty:
                            v = float(sma_50_s.iloc[-1])
                            sma_50_val = None if math.isnan(v) else round(v, 2)
                    if len(close) >= 200:
                        ema_200_s = ta.ema(close, length=200)
                        if ema_200_s is not None and not ema_200_s.empty:
                            v = float(ema_200_s.iloc[-1])
                            ema_200_val = None if math.isnan(v) else round(v, 2)
                    volume_ratio_val = _calculate_volume_ratio(hist)
                except Exception as exc:
                    logger.debug("TA indicators failed for %s: %s", symbol, exc)

                # Derived fields — computed from already-fetched data
                high_52wk = _to_float(info.get("fiftyTwoWeekHigh"))
                pct_from_52wk_high_val: float | None = None
                if high_52wk and high_52wk > 0 and not pd.isna(current_price):
                    pct_from_52wk_high_val = round(
                        ((float(current_price) - high_52wk) / high_52wk) * 100, 1
                    )

                market_cap_val = _to_float(info.get("marketCap"))

                sma_200_pct_val: float | None = None
                if sma_200_val is not None and sma_200_val > 0:
                    sma_200_pct_val = round(
                        ((float(current_price) - sma_200_val) / sma_200_val) * 100, 1
                    )

                ema_200_pct_val: float | None = None
                if ema_200_val is not None and ema_200_val > 0:
                    ema_200_pct_val = round(
                        ((float(current_price) - ema_200_val) / ema_200_val) * 100, 1
                    )

                bb_width_pct_val: float | None = None
                if (
                    bb_upper_val is not None
                    and bb_lower_val is not None
                    and bb_mid_val is not None
                    and bb_mid_val > 0
                ):
                    bb_width_pct_val = round(
                        ((bb_upper_val - bb_lower_val) / bb_mid_val) * 100, 1
                    )

                candidates.append(
                    {
                        "symbol": symbol,
                        "name": info.get("shortName", symbol) or symbol,
                        "price": round(float(current_price), 2)
                        if not pd.isna(current_price)
                        else 0.0,
                        "sector": info.get("sector", "N/A") or "N/A",
                        "pct_1d": round(pct_1d, 2),
                        "pct_1w": round(pct_1w, 2),
                        "pct_1m": round(pct_1m, 2),
                        "price_history_1m": price_history_1m,
                        "pe": round(float(pe_val), 2) if pd.notna(pe_val) else "N/A",
                        "forward_pe": round(float(forward_pe_val), 2)
                        if pd.notna(forward_pe_val)
                        else "N/A",
                        "peg_ratio": round(peg_ratio_val, 2)
                        if peg_ratio_val is not None
                        else "N/A",
                        "beta": round(float(beta_val), 2) if pd.notna(beta_val) else "N/A",
                        "fcf": fcf_val,
                        "debt_to_equity": round(debt_to_equity_val, 2)
                        if debt_to_equity_val is not None
                        else "N/A",
                        "revenue": revenue_val,
                        "revenue_growth": round(revenue_growth_val * 100, 1)
                        if revenue_growth_val is not None
                        else "N/A",
                        "eps": round(eps_val, 2) if eps_val is not None else "N/A",
                        "eps_growth": round(eps_growth_val * 100, 1)
                        if eps_growth_val is not None
                        else "N/A",
                        "atm_iv": round(atm_iv_val, 2) if atm_iv_val is not None else "N/A",
                        "rv20": round(rv20_val, 2) if rv20_val is not None else "N/A",
                        "adr20": adr20_val if adr20_val is not None else "N/A",
                        "atm_iv_rv20": round(atm_iv_rv20_val, 2)
                        if atm_iv_rv20_val is not None
                        else "N/A",
                        "iv_percentile": round(iv_percentile_val, 2)
                        if iv_percentile_val is not None
                        else "N/A",
                        "iv_history_points": len(iv_history),
                        "sma_200": sma_200_val,
                        "bb_upper": bb_upper_val,
                        "bb_mid": bb_mid_val,
                        "bb_lower": bb_lower_val,
                        "rsi": rsi_val if rsi_val is not None else "N/A",
                        "sma_50": sma_50_val,
                        "ema_200": ema_200_val,
                        "volume_ratio": volume_ratio_val if volume_ratio_val is not None else "N/A",
                        "pct_from_52wk_high": pct_from_52wk_high_val
                        if pct_from_52wk_high_val is not None
                        else "N/A",
                        "market_cap": market_cap_val,
                        "sma_200_pct": sma_200_pct_val if sma_200_pct_val is not None else "N/A",
                        "ema_200_pct": ema_200_pct_val if ema_200_pct_val is not None else "N/A",
                        "bb_width_pct": bb_width_pct_val if bb_width_pct_val is not None else "N/A",
                    }
                )

            except Exception as exc:
                logger.warning("Failed to screen stock %s: %s", symbol, exc)
    finally:
        if alpaca_client is not None:
            alpaca_client.close()

    return candidates
