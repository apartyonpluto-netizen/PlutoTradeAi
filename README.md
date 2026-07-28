# 🪐 PlutoTrade AI

Private Founder Preview

PlutoTrade AI is an AI-powered trading research and market intelligence platform designed to help traders make better-informed decisions through technical analysis, market intelligence, and AI-assisted trade planning.

## Current Features

- Live Market Scanner
- AI Watchlist
- Strategy Brain
- Support & Resistance Detection
- Confidence Engine
- Options Research
- Paper Trade Logger
- Market Readiness Engine
- Mission Brief
- Futures Framework
- TradingView webhook signal intake (Phase 1)
- Signal approval workflow with internal paper execution
- Internal paper account, positions, trades, and performance APIs

## TradingView-First Architecture

TradingView is used for charting and alert signals.

Pipeline:

TradingView chart/alerts -> PlutoTrade webhook -> signal normalization -> AI analysis -> approval -> internal paper ledger

Current defaults:

- `PLUTO_SIGNAL_PROVIDER=tradingview`
- `PLUTO_CHART_PROVIDER=tradingview`
- `PLUTO_MARKET_PROVIDER=yfinance`
- `PLUTO_PAPER_BROKER=internal`
- `PLUTO_EXECUTION_PROVIDER=disabled`

Alpaca remains optional and inactive unless explicitly enabled.

## Key APIs

- `POST /api/webhooks/tradingview`
- `GET /api/signals`
- `GET /api/signals/<signal_id>`
- `POST /api/signals/<signal_id>/approve`
- `POST /api/signals/<signal_id>/decline`
- `GET /api/paper/account`
- `GET /api/paper/positions`
- `GET /api/paper/trades`
- `GET /api/paper/performance`

See `docs/tradingview_integration.md` for alert templates, webhook auth options, and secret-rotation guidance.

## Current Status

Version: 0.2 Founder Preview

This application is intended for research and educational purposes.

### Safety

- No live trading
- No autonomous execution
- Paper trading only
- Research assistance only
- Not financial advice

Built by Curtis Hartfield