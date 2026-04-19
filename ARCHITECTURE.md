Cache layer — both InsiderTradingFetcher and CongressionalTradesFetcher now read from a 12-hour SQLite cache on fetch, and write to it on a successful API call. This means the pipeline won't hammer Finnhub or download the large congressional JSON files on every run — only once every 12 hours. The /insider Discord command uses a 48-hour fallback so it always has data even if the pipeline hasn't run today.

/insider command has four views selectable via dropdown:
OptionWhat it showsOverviewBoth insider and congressional summaries side by sideCorporate InsidersFull Form 4 detail — who bought/sold, how many shares, dollar value, dateCongressional TradesFull STOCK Act detail — politician name, chamber, amount range, dateConvergence AlertOnly tickers where both execs AND politicians are on the same side.

All four views accept an optional ticker parameter — so /insider view:convergence ticker:NKE shows you exactly the Nike convergence scenario you described.

