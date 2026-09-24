# PlutoTrade Full-Market Research Architecture

## Symbol Universe

PlutoTrade now uses a refreshable symbol directory service:

- Primary source: Nasdaq Trader Symbol Directory
  - https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
  - https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt
- Refresh cadence: controlled by `PLUTO_UNIVERSE_REFRESH_SECONDS` (default: 12 hours)
- Storage: `data/symbol_universe.json`
- Filtering:
  - excludes test issues
  - excludes invalid or delisted financial statuses where available
- Classification:
  - keeps exchange metadata
  - separates stocks and ETFs

## Research Providers

Provider responsibilities are explicitly separated:

- Symbol directory provider: Nasdaq Trader directory feed
- Quote provider: yfinance
- Historical candles provider: yfinance
- Fundamentals provider: yfinance
- News provider: yfinance news feed
- Scanner engine: Pluto scanner snapshot engine
- Chart provider: TradingView (visual charting)
- Signal provider: TradingView webhook pipeline

## Scanner Snapshot Strategy

- Universe scanning is snapshot-based and cached.
- Snapshot refresh cadence: `PLUTO_SCANNER_REFRESH_SECONDS` (default: 5 minutes)
- Universe cap per refresh: `PLUTO_SCANNER_UNIVERSE_LIMIT` (default: 350)
- Controlled concurrency: `PLUTO_SCANNER_MAX_WORKERS` (default: 8)
- Scanner pages consume cached snapshots rather than recomputing full-universe scans on each request.

## API Contract Surface

- `GET /api/symbols/search?q=`
- `GET /api/stocks/<symbol>/resolve`
- `GET /api/stocks/<symbol>/overview`
- `GET /api/stocks/<symbol>/quote`
- `GET /api/stocks/<symbol>/history`
- `GET /api/stocks/<symbol>/technicals`
- `GET /api/stocks/<symbol>/analysis`
- `GET /api/stocks/<symbol>/news`
- `GET /api/market/movers`
- `GET /api/market/scanners`
- `GET /api/market/scanners/<scanner_name>`
- `GET /api/market/status`
