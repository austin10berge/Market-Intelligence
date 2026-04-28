import pandas as pd
from datetime import date
from src.backtester.data_provider import get_historical_data

df = get_historical_data("SOFI", date(2025, 7, 1), date(2025, 9, 30), "daily")
for i in range(len(df)):
    print(f"{df.index[i].strftime('%Y-%m-%d')} - Close: {df['Close'].iloc[i]:.2f}")
