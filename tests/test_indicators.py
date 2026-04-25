import numpy as np
import pandas as pd
from src.backtester.indicators import sma, ema, rsi, macd

def test_sma():
    data = {"Close": [10, 20, 30, 40, 50]}
    df = pd.DataFrame(data)
    
    result = sma(df["Close"], period=3)
    assert np.isnan(result.iloc[0])
    assert np.isnan(result.iloc[1])
    assert result.iloc[2] == 20.0
    assert result.iloc[3] == 30.0
    assert result.iloc[4] == 40.0

def test_macd():
    data = {"Close": np.linspace(10, 100, 50)}
    df = pd.DataFrame(data)
    
    result = macd(df["Close"], fast=12, slow=26, signal=9)
    assert "macd_line" in result.columns
    assert "signal_line" in result.columns
    assert "histogram" in result.columns
    assert len(result) == 50
