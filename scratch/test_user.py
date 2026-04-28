import pandas as pd
from datetime import date
from src.backtester.models import BacktestRequest, StrategyDefinition, PositionSizing, ExitStrategy, PyramidingConfig, OptionsConfig, OptionType, PyramidingExitMode
from src.backtester.engine import run_backtest
from src.backtester.data_provider import get_historical_data

def test_user_scenario():
    df = get_historical_data("SOFI", date(2022, 1, 1), date(2024, 1, 1), "daily")
    
    # Just enter on any simple condition to get the initial position, e.g. RSI < 50
    entry_cond = {
        "type": "threshold",
        "indicator": {"name": "RSI", "params": {"period": 14}},
        "comparator": "lt",
        "value": 50.0
    }
    
    strategy = StrategyDefinition(
        name="User Test",
        entry={"operator": "AND", "conditions": [entry_cond]}, 
        exit=ExitStrategy(take_profit_pct=50.0, pyramiding_exit_mode=PyramidingExitMode.SELL_ALL),
        position_sizing=PositionSizing(method="percent_equity", value=10.0),
        options=OptionsConfig(enabled=True, type=OptionType.CALL, target_dte=365, target_delta=0.70),
        pyramiding=PyramidingConfig(enabled=True, max_positions=5, scale_in_trigger="pullback", scale_in_value=15.0)
    )
    
    req = BacktestRequest(
        strategy=strategy,
        ticker="SOFI",
        start_date="2022-01-01",
        end_date="2024-01-01",
        initial_capital=10000.0
    )
    
    res = run_backtest(req, df)
    print(f"Total trades: {len(res.trades)}")
    for t in res.trades:
        print(f"Entry: {t.entry_date} @ {t.entry_price} | Exit: {t.exit_date} @ {t.exit_price} | PnL: {t.pnl_pct}% | Reason: {t.exit_reason}")

test_user_scenario()
