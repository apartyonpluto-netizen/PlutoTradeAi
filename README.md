# PlutoTrade AI MVP

Run the Flask app:

```bash
python3 backend/app.py
```

Main pages:

- `/`
- `/dashboard`
- `/mission-control`
- `/watchlist`
- `/scanner`
- `/volume-scanner`
- `/reversal-map`
- `/support-resistance`
- `/trend-detection`
- `/candle-brain`
- `/pattern-brain`
- `/options`
- `/news-intelligence`
- `/notifications`
- `/account-hub`
- `/trade-journal`
- `/neural-engine`
- `/settings`

Main APIs:

- `/api/watchlist`
- `/api/watchlist/add`
- `/api/watchlist/update`
- `/api/watchlist/delete`
- `/api/suggestions`
- `/api/news/x`
- `/api/options/<ticker>`
- `/api/strategy/<ticker>`
- `/api/chart-levels/<ticker>`
- `/api/chart-levels/watchlist`
- `/api/alerts`
- `/api/accounts`
- `/api/accounts/connect`
- `/api/accounts/disconnect`
- `/api/accounts/test`
- `/api/tradingview/webhook`

## Deprecated environment variables

`LIVE_TRADING_ENABLED` does nothing. Live trading is armed only when both `PLUTO_ENABLE_LIVE_TRADING` and `PLUTO_LIVE_TRADING_CONFIRM` are `true`. Remove `LIVE_TRADING_ENABLED` if it is still set on Render.

`ETRADE_ENABLED` was never wired. This app does not trade through E*TRADE. Remove it if it is set.

The Market Scanner screens the Nasdaq-listed universe (cached from Nasdaq's public symbol directory) and shows the top `PLUTO_SCANNER_TOP_N` names (default 48). Optional screen settings: `PLUTO_SCANNER_MIN_PRICE`, `PLUTO_SCANNER_MAX_PRICE`, `PLUTO_SCANNER_MIN_DOLLAR_VOLUME`, `PLUTO_SCANNER_MIN_SHARE_VOLUME`, `PLUTO_SCANNER_SCORE_CAP`, `PLUTO_SCANNER_CACHE_SECONDS`, `PLUTO_SCANNER_SNAPSHOT_GAP_SECONDS`.
