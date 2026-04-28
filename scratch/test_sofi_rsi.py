import pandas as pd
from datetime import date
from src.backtester.data_provider import get_historical_data
from src.backtester.indicators import rsi

df = get_historical_data("SOFI", date(2024, 4, 25), date(2026, 4, 25), "daily")
r = rsi(df["Close"], 14)

below = r[r < 35]
above = r[r > 65]

print("Total bars:", len(df))
print("Days below 35:", len(below))
print("Days above 65:", len(above))

# Simulate the trades manually
holding = False
trades = 0
for i in range(len(r)):
    val = r.iloc[i]
    if pd.isna(val): continue
    
    if not holding and val < 35:
        holding = True
        trades += 1
        print(f"Buy at {r.index[i].strftime('%Y-%m-%d')} (RSI: {val:.2f})")
    elif holding and val > 65:
        holding = False
        print(f"Sell at {r.index[i].strftime('%Y-%m-%d')} (RSI: {val:.2f})")

print("Total Simulated Trades:", trades)
