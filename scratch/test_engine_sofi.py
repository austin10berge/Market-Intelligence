import asyncio
import pandas as pd
from datetime import date
from src.backtester.models import BacktestRequest, StrategyDefinition, PositionSizing, ExitStrategy
from src.backtester.engine import run_backtest
from src.backtester.data_provider import get_historical_data

def main():
    # Last 2 years
    df = get_historical_data("SOFI", date(2024, 4, 25), date(2026, 4, 25), "daily")
    
    entry_cond = {
        "type": "threshold",
        "indicator": {"name": "RSI", "params": {"period": 14}},
        "comparator": "lt",
        "value": 35.0
    }
    
    exit_cond = {
        "type": "threshold",
        "indicator": {"name": "RSI", "params": {"period": 14}},
        "comparator": "gt",
        "value": 65.0
    }
    
    strategy = StrategyDefinition(
        name="SOFI Test",
        entry={"operator": "AND", "conditions": [entry_cond]}, 
        exit=ExitStrategy(conditions={"operator": "AND", "conditions": [exit_cond]}),
        position_sizing=PositionSizing(method="percent_equity", value=100.0)
    )
    
    req = BacktestRequest(
        strategy=strategy,
        ticker="SOFI",
        start_date="2024-04-25",
        end_date="2026-04-25",
        initial_capital=10000.0
    )
    
    res = run_backtest(req, df)
    print(f"Total trades: {len(res.trades)}")
    for i, t in enumerate(res.trades):
        print(f"Trade {i}: entry={t.entry_date} exit={t.exit_date} entry_price={t.entry_price} exit_price={t.exit_price}")

main()
