import pandas as pd
from datetime import date
from src.backtester.models import BacktestRequest, StrategyDefinition, PositionSizing, ExitStrategy, PyramidingConfig, OptionsConfig, OptionType
from src.backtester.engine import run_backtest
from src.backtester.data_provider import get_historical_data

def test_pyramiding():
    df = get_historical_data("SPY", date(2023, 1, 1), date(2024, 1, 1), "daily")
    
    # Simple threshold
    entry_cond = {
        "type": "threshold",
        "indicator": {"name": "RSI", "params": {"period": 14}},
        "comparator": "lt",
        "value": 40.0
    }
    
    strategy = StrategyDefinition(
        name="Pyramid Test",
        entry={"operator": "AND", "conditions": [entry_cond]}, 
        exit=ExitStrategy(max_hold_days=10),
        position_sizing=PositionSizing(method="percent_equity", value=30.0),
        pyramiding=PyramidingConfig(enabled=True, max_positions=3, scale_in_trigger="entry_signal")
    )
    
    req = BacktestRequest(
        strategy=strategy,
        ticker="SPY",
        start_date="2023-01-01",
        end_date="2024-01-01",
        initial_capital=10000.0
    )
    
    res = run_backtest(req, df)
    print(f"Pyramiding trades: {len(res.trades)}")
    
def test_options():
    df = get_historical_data("SPY", date(2023, 1, 1), date(2023, 6, 1), "daily")
    
    entry_cond = {
        "type": "threshold",
        "indicator": {"name": "RSI", "params": {"period": 14}},
        "comparator": "lt",
        "value": 40.0
    }
    
    strategy = StrategyDefinition(
        name="Option Test",
        entry={"operator": "AND", "conditions": [entry_cond]}, 
        exit=ExitStrategy(max_hold_days=5),
        position_sizing=PositionSizing(method="percent_equity", value=10.0),
        options=OptionsConfig(enabled=True, type=OptionType.CALL, target_dte=30, target_delta=0.05)
    )
    
    req = BacktestRequest(
        strategy=strategy,
        ticker="SPY",
        start_date="2023-01-01",
        end_date="2023-06-01",
        initial_capital=10000.0
    )
    
    res = run_backtest(req, df)
    print(f"Option trades: {len(res.trades)}")

test_pyramiding()
test_options()
