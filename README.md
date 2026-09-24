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

- `POST /api/webhooks/tradingview` (shared secret)
- `GET /api/signals`, `GET /api/signals/<signal_id>`
- `POST /api/signals/<signal_id>/approve`, `POST /api/signals/<signal_id>/decline` (**sign-in required**)
- `GET /api/paper/account`, `/positions`, `/trades`, `/performance`, `/alerts`, `/monitor`
- `POST /api/paper/alerts/<id>/dismiss`, `/resolve`, `POST /api/paper/monitor/run` (**sign-in required**)
- `GET /api/health` (reports `degraded` if the paper ledger is corrupted)

Every entry needs a human approval and a protective stop. The internal simulated broker is persistent and
price driven (targets, stops and manual exits actually fill), and all state survives restarts.

- `docs/OPERATIONS.md` - running, configuring, backing up and deploying; restart/corruption behavior; known limits.
- `docs/tradingview_integration.md` - alert templates, webhook validation rules, signal lifecycle, secret rotation.

Run the tests with `venv/bin/python -m pytest -q` (they use temp storage and mocked prices).

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