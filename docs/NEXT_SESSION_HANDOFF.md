# Next session handoff

Updated: 2026-09-29. Repository: `/Users/curtissmith/Downloads/PROJECTTRADINGWITHPLUTOAI/PlutoTradeAI`
(branch `main`, pushes auto-deploy to Render). Work goes into this app, not
`PlutoTradeAI_v02` (owner decision).

## How to run checks

```
cd backend && ../venv/bin/python -m pytest -q -p no:cacheprovider
```
Use `PlutoTradeAI/venv` - the system Python lacks the Webull SDK. Tests use a
temp `PLUTO_DATA_DIR` and mocked brokers; they never touch real records.

Local `.env` Alpaca keys return HTTP 401 (stale); production's work.
Local Webull keys point at the SAME sandbox account as production: read-only
probes only, never orders.

## Changed this session (newest first)

- `backend/broker_env.py` (new), `autonomy/overnight_orders.py`,
  `autonomy/closed_trades.py`, `autonomy/performance_report.py`,
  `autonomy/trade_tickets.py`, `app.py`: environment stamping and isolation;
  `_reconcile_unknown_submissions` and manual-resolution recovery use each
  record's own account; `_reconcile_exit_orders` never places a stop for
  another account's record. Tests: `tests/test_environment_isolation.py`
  (both cross-account tests fail on the previous code).
- `backend/setups/` (new package): objective setup engine, opportunities,
  evidence, walk-forward validation, service; Pattern Brain page replaced;
  `/api/setups/*`; admin "Setup Evidence" panel; cron starts a weekly
  validation after 20:00 UTC.
- `backend/autonomy/event_journal.py` (new): correlation-id event trail;
  `/api/events*`.
- `backend/docs/RELEASE_CHECKLIST.md`, `docs/IMPLEMENTATION_STATUS.md`,
  `docs/BROKER_VERIFICATION.md`, this file.

## Checks actually run

- Full suite: 1390 passed (local, mocked).
- Setup engine fuzz on 30 random-walk series: 0 detector errors; no detector
  passed the validation gate (expected on noise).
- Setup Discovery page: local preview with synthetic bars, desktop + 375px.
- Not run: any sandbox order; real-data setup validation (needs the Render
  run - Admin → Setup Evidence → Run now, or wait for the weekly cron).

## Running local services

None left running (preview harness stopped). Harness for the Setup Discovery
page lives in the session scratchpad only.

## Next concrete actions (in order)

1. Build the read-only broker reconciliation report: for the current
   environment, broker positions / open orders / balances vs internal records
   → matched, broker-only (orphan or manual holding), record-only (stale),
   quantity mismatch; persist `last_reconciled_at`; show it on Admin and the
   dashboard. Replay tests from sanitized sandbox payloads.
2. Take fees from broker transactions (Webull order detail/transactions) into
   closed trades; mark estimates as estimates.
3. Automated backup of `/var/data` + restore runbook.
4. CSRF tokens on state-changing routes.
5. Drive the /agent-map observatory from `event_journal` (observed flows) vs
   the static architecture (structural), per the spec.
6. E*TRADE read-only adapter once the owner provides developer keys (entered
   in the app, never in chat).

## Blockers needing the owner

- Close/re-protect INTC, MSTR, MRVL and clear 5 stale records (Admin).
- Set option stop to 20% (default 50%).
- E*TRADE developer keys + OAuth; a live account and explicit limits before
  any live check.
