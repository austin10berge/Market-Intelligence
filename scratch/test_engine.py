import asyncio
import pandas as pd
from datetime import date
from src.backtester.models import BacktestRequest, StrategyDefinition, PositionSizing, ExitStrategy
from src.backtester.engine import run_backtest
from src.backtester.data_provider import get_historical_data

def main():
    df = get_historical_data("SPY", date(2020, 1, 1), date(2023, 1, 1), "daily")
    
    cond = {
        "type": "threshold",
        "indicator": {"name": "CLOSE", "params": {}},
        "comparator": "gt",
        "value": 0.0
    }
    
    strategy = StrategyDefinition(
        name="Test",
        entry={"operator": "AND", "conditions": [cond]}, 
        exit=ExitStrategy(), # NO EXITS
        position_sizing=PositionSizing(method="percent_equity", value=100.0)
    )
    
    req = BacktestRequest(
        strategy=strategy,
        ticker="SPY",
        start_date="2020-01-01",
        end_date="2023-01-01",
        initial_capital=10000.0
    )
    
    res = run_backtest(req, df)
    print(f"Total trades: {len(res.trades)}")
    if len(res.trades) > 0:
        print(f"Trade 0: entry={res.trades[0].entry_date} exit={res.trades[0].exit_date}")

main()
