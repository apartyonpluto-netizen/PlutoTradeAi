# TradingView Integration Guide

## Purpose
PlutoTrade uses TradingView for charting and alert signals.
TradingView is not an execution broker.
Execution remains disabled unless a future adapter is enabled.

## Provider Defaults
Set these values in your environment:

- `PLUTO_SIGNAL_PROVIDER=tradingview`
- `PLUTO_CHART_PROVIDER=tradingview`
- `PLUTO_MARKET_PROVIDER=yfinance`
- `PLUTO_PAPER_BROKER=internal`
- `PLUTO_EXECUTION_PROVIDER=disabled`
- `TRADINGVIEW_WEBHOOK_SECRET=<your-shared-secret>`
- `TRADINGVIEW_WEBHOOK_MAX_AGE_SECONDS=300`

## Webhook Endpoint
- `POST /api/webhooks/tradingview`
- Content-Type: `application/json`
- Expected response: `202` when accepted and queued

## Authentication Modes
Use one of the following methods:

1. Header-based secret:
- Header: `X-Pluto-Webhook-Secret: <secret>`

2. JSON fallback token (for TradingView setups without custom headers):
- Include a body field: `secret` or `token`

Notes:
- The secret is compared server-side and never returned in responses.
- Secret values are redacted before payload storage.
- Do not put real secrets in Pine script source files checked into git.

## TradingView Alert JSON Template
```json
{
  "token": "{{CONFIGURED_OUTSIDE_MESSAGE_IF_POSSIBLE}}",
  "signal_id": "{{ticker}}-{{interval}}-{{time}}-pluto-trend-breakout-v1",
  "symbol": "{{ticker}}",
  "exchange": "{{exchange}}",
  "asset_type": "STOCK",
  "strategy": "pluto-trend-breakout-v1",
  "direction": "LONG",
  "timeframe": "{{interval}}",
  "price": {{close}},
  "bar_time": "{{time}}",
  "indicator_values": {
    "ema_fast": "{{plot_0}}",
    "ema_slow": "{{plot_1}}"
  }
}
```

## Webhook Validation Rules
The webhook rejects (each with a 4xx JSON error, never a 500):
- malformed JSON, a body that is not a JSON object, or a body larger than `PLUTO_MAX_REQUEST_BYTES` (413)
- missing or invalid secret (401); a server with no `TRADINGVIEW_WEBHOOK_SECRET` configured answers 503
- invalid ticker format, or a non-string symbol
- invalid direction (must be LONG, SHORT, EXIT, WATCH)
- invalid strategy or timeframe
- timestamp outside `TRADINGVIEW_WEBHOOK_MAX_AGE_SECONDS` (naive timestamps are read as UTC)
- `price`, `stop_loss`, `take_profit` that are not finite numbers greater than zero (NaN/Infinity rejected)
- for LONG the stop must be below price and the target above it; for SHORT the reverse
- `confidence_hint` outside 0-100, `signal_id` with characters other than letters, digits and `_ . : + -`
- duplicate signal IDs and duplicate payload hashes (409)

TradingView cannot send custom headers, so put the secret in the alert message body as `"token"` (the
starter Pine script ships a `PASTE_WEBHOOK_SECRET_IN_ALERT_DIALOG` placeholder - replace it in the alert
dialog, not in source control). Entries need a protective stop: send `stop_loss` (and optionally
`take_profit`) or let the analysis supply one.

## Replay, Timestamp, and Idempotency Protections
- Replay protection via strict timestamp tolerance.
- Idempotency via `signal_id` and stable payload hash deduplication.
- Duplicate and replayed alerts return structured error payloads.

## Signal Lifecycle
```
VALIDATED -> ANALYZING -> AWAITING_APPROVAL -> APPROVED -> PAPER_EXECUTED
                 |               |    |             \-> AWAITING_APPROVAL  (retryable rejection, e.g. entries frozen)
                 |               |    \-> EXPIRED      (not approved within PLUTO_SIGNAL_APPROVAL_MAX_AGE_SECONDS)
                 |               \-> DECLINED
                 \-> FAILED       (analysis error, or a hard execution rejection)
```
- Every transition is an atomic compare-and-set under a file lock, so two clicks, two tabs or two workers
  can never both win: an approved signal cannot be declined, a declined one cannot be approved, and a
  late analysis result never overwrites a decision.
- Approve and decline are idempotent: repeating them returns the recorded outcome (`"idempotent": true`)
  and never creates a second paper trade or a duplicate audit event.
- After a restart, signals that were accepted but not yet analyzed are analyzed again, interrupted
  analyses are released after `PLUTO_SIGNAL_ANALYSIS_TIMEOUT_SECONDS`, and an `APPROVED` signal whose
  execution outcome was never recorded is either marked `PAPER_EXECUTED` (if a lifecycle exists) or
  returned to `AWAITING_APPROVAL` so a human decides again. Nothing is auto-executed on restart.

## Approval Workflow
After analysis, review pending signals via:
- `GET /api/signals`
- `GET /api/signals/<signal_id>`

Approve or decline (both require a signed-in user; `PLUTO_APPROVER_USERS` can restrict who):
- `POST /api/signals/<signal_id>/approve` body: optional `note`, optional `acknowledge_stale` (boolean)
- `POST /api/signals/<signal_id>/decline` body: optional `reason`
- The approver recorded in the audit trail is the authenticated user, not a client-supplied name.

Approval is refused with 409 when analysis is not finished, the signal is not pending, it expired, or the
analysis used STALE/UNAVAILABLE market data (resubmit with `"acknowledge_stale": true` to override).
It is refused with 422 when the live price is more than `PLUTO_MAX_ENTRY_DEVIATION_PERCENT` away from the
entry (the signal is stale), when no fresh price is available, when risk limits or buying power reject the
size, or when entries are frozen. Rejections that a human can retry later put the signal back to
`AWAITING_APPROVAL`; the rest mark it `FAILED`.

On approval:
- an internal paper trade is created, its entry is filled, and a stop (plus optional target) is placed
- the approval event is recorded
- no Alpaca/Webull order is ever sent

## Internal Paper Broker APIs
- `GET /api/paper/account`  (cash, equity, realized/unrealized P&L, buying power)
- `GET /api/paper/positions`
- `GET /api/paper/trades`
- `GET /api/paper/performance`
- `GET /api/paper/alerts`, `GET /api/paper/monitor`
- `POST /api/paper/alerts/<id>/dismiss`, `POST /api/paper/alerts/<id>/resolve` (sign-in required; resolving re-evaluates the entry freeze)
- `POST /api/paper/monitor/run` (sign-in or `X-Pluto-Monitor-Token` when `PLUTO_MONITOR_TRIGGER_TOKEN` is set)

The simulated broker is persistent and price driven. Its orders and positions live in
`<ledger>.broker.json`, so they survive restarts. Market exits fill immediately at the market price; a
resting target (limit) fills at its limit once the market reaches it, and a stop fills at the market
price once it is crossed. Prices are evaluated whenever the monitor runs (default every 45 s), so a wick
that crosses a level between two monitor runs and comes back is not seen. A fill can only close an
existing position; it can never open or flip one.

## Two-Factor Authentication
Enable 2FA on TradingView and your email account used for alerts.
Suggested controls:
- TOTP app-based 2FA
- unique password manager-generated password
- separate alert-only mailbox for operations

## Local Development: Expose Webhook
Use a tunnel service such as `ngrok` or `cloudflared`:

```bash
ngrok http 8000
```

Then configure TradingView webhook URL to:
`https://<your-domain>/api/webhooks/tradingview`

## Sample Local Test
`bar_time` must be within `TRADINGVIEW_WEBHOOK_MAX_AGE_SECONDS` of now, so generate it when you send:
```bash
curl -X POST http://127.0.0.1:8000/api/webhooks/tradingview \
  -H "Content-Type: application/json" \
  -H "X-Pluto-Webhook-Secret: $TRADINGVIEW_WEBHOOK_SECRET" \
  -d '{
    "symbol": "TSLA",
    "exchange": "NASDAQ",
    "strategy": "pluto-trend-breakout-v1",
    "direction": "LONG",
    "timeframe": "60",
    "price": 250.12,
    "stop_loss": 246.0,
    "take_profit": 262.0,
    "bar_time": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
  }'
```

## Secret Rotation
1. Generate a new secret.
2. Set `TRADINGVIEW_WEBHOOK_SECRET` on the server.
3. Update TradingView alert message/header source.
4. Keep overlap window short and monitor rejected webhook metrics.

## Why TradingView Is Not the Broker
TradingView provides charting and alerts only.
PlutoTrade handles analysis and internal paper ledger operations.
Execution adapters are separate components.

## Webull Future Adapter
Webull paper execution will be integrated as a future adapter behind the approval pipeline.
The current release intentionally uses only PlutoTrade internal paper execution.
