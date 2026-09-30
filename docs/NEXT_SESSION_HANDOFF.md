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

- `backend/login_throttle.py`: 8 failed sign-ins per username or per client
  address in 15 min -> 429 (hashed keys, file-backed, shared by workers);
  post-sign-in redirect limited to same-site paths (fixed `//host` open redirect).
- Broker balances in the reconciliation report + Broker sync panel;
  `backend/docs/RELEASE_CHECKLIST.md` brought up to date.
- `backend/setups/forward.py`: daily (21:00-21:55 UTC cron, weekdays) records
  setups confirmed on the newest completed daily bar and resolves earlier ones;
  feeds `evidence.FORWARD_FILE`. Admin Setup Evidence shows open/resolved counts.
- `backend/observatory.py`, `templates/observatory.html`, `static/js/observatory_3d.js`,
  `/api/observatory`, nav link, `tests/test_observatory.py`. Verified in local
  preview (seeded events): agent flow, sectors, ticker chains, no console errors.
- `app.py::_reject_cross_site_writes` (Origin/Referer check), `tests/test_cross_site_writes.py`.
- `broker_reconciliation.py`: record-only items carry `broker_close_evidence`
  from 30-day order history. `tests/conftest.py`: outbound network blocked in
  all tests. `news/news_service.py`: an unreachable provider is a reported
  error, not an HTTP 500.
- `backend/broker_fees.py` (new), `app.py::_apply_broker_fees` at the three
  close points: fees from broker ORDER HISTORY (the detail endpoint has no fee
  fields - verified). `tests/test_broker_fees.py` + history fixture.
- `broker_reconciliation.py`: `unprotected_at_broker` (verified in production).
- `backend/backups.py` (new), admin Backups panel, `/api/admin/backups*`,
  daily snapshot from the cron after 20:00 UTC, `docs/RUNBOOK_BACKUP_RESTORE.md`,
  `tests/test_backups.py`.
- `backend/broker_reconciliation.py` (new), `app.py` (`/api/broker/reconciliation`,
  15-minute run from the fast-monitor cron), dashboard "Broker sync" panel,
  `tests/test_broker_reconciliation.py` + `tests/fixtures/webull_sandbox_cash_2026-09-29.json`
  (sanitized real sandbox payload).
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

- Full suite: 1430 passed (local, mocked, outbound network blocked).
- Production (Webull sandbox, read-only, owner's signed-in session), 2026-09-30:
  Broker sync 3 matched / 5 record-only (MU closed at the broker 2026-09-17) /
  3 unprotected; balances read; same-site writes pass the cross-site guard;
  /login 200; /observatory shows 65 real events (signal stage only).
- Read-only local probes: positions/open orders (INTC 2, MSTR 5, MRVL 4, no
  working orders); order history has fee fields, order detail does not.
- Setup engine fuzz (30 random walks): no errors, nothing passes the gate.
- Local preview (synthetic data): Setup Discovery, Broker sync, Observatory.
- Not run: any sandbox or live order; real-data setup validation and forward
  tracking (first runs happen automatically tonight after 20:00/21:00 UTC).

## Running local services

None left running (preview harness stopped). Harness for the Setup Discovery
page lives in the session scratchpad only.

## Next concrete actions (in order)

1. After tonight's cron: Admin -> Setup Evidence - confirm the walk-forward
   run finished (trade counts per setup, statuses) and forward tracking ran.
   If a run failed, the panel shows the error.
2. Once the owner clears the orphans (slots free), watch /observatory for the
   first chains past `signal` and check Broker sync after the first fill.
3. Rate-limit the TradingView webhook (logged-only today, low risk).
4. Capture a real option order payload once an option is placed in the sandbox
   (entry, fees shape, position symbol) and add replay tests.
5. E*TRADE read-only adapter when the owner provides developer keys (entered
   in the app, never in chat); then production read-only checks at Level 4.

## Blockers needing the owner

- Close/re-protect INTC, MSTR, MRVL and clear 5 stale records (Admin).
- Set option stop to 20% (default 50%).
- E*TRADE developer keys + OAuth; a live account and explicit limits before
  any live check.
