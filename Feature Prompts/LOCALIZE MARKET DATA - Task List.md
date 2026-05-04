Local OHLCV Data Store — Task List

Data Layer
 DONE - Create src/market_data/__init__.py
 DONE - Create src/market_data/store.py — SQLite tables + CRUD
 DONE - Create src/market_data/refresh.py — Bulk download + upsert

Scanner Integration
 IN PROGRESS - Modify csp_scanner.py — Use local store for fundamental/vol/technical filters
 Add >48h staleness warning to scanner output

API & Infrastructure
 Add GET /api/market-data/status endpoint
 Add POST /api/market-data/refresh endpoint
 Add market-data-refresh service to docker-compose.yml

UI
 Add data freshness badge to scanner UI
Tests

 Unit tests for store.py
 Unit tests for refresh.py (mock yf.download)
 Integration test: scanner with local data
 
Verification
 Build and run in Docker
 Run a refresh, verify DB grows
 Run a scan, verify speed improvement
