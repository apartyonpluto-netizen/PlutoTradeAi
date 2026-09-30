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

- Full suite: 1422 passed (local, mocked, outbound network blocked).
- Production 2026-09-30 14:28 UTC: Broker sync 3 matched / 5 record-only (MU with broker close evidence) / 3 unprotected; same-site writes pass the new guard.
- Production (sandbox) Broker sync 2026-09-30 01:52 UTC: 3 matched, 5 record-only, 3 unprotected_at_broker.
- Read-only probe: order history has fee fields; order detail does not.
- Read-only sandbox probe 2026-09-29: cash INTC 2 / MSTR 5 / MRVL 4, no working orders.
- Setup engine fuzz on 30 random-walk series: 0 detector errors; no detector
  passed the validation gate (expected on noise).
- Setup Discovery page: local preview with synthetic bars, desktop + 375px.
- Not run: any sandbox order; real-data setup validation (needs the Render
  run - Admin → Setup Evidence → Run now, or wait for the weekly cron).

## Running local services

None left running (preview harness stopped). Harness for the Setup Discovery
page lives in the session scratchpad only.

## Next concrete actions (in order)

1. (done) Admin "Positions No Longer Held" shows broker close evidence. Next:
   add balances to the reconciliation report.
2. Tonight after 20:00 UTC the cron starts the first real-data setup
   validation and the first forward-tracking run; check Admin -> Setup Evidence
   (walk-forward trade counts, per-setup status, forward open/resolved).
3. Drive the /agent-map observatory from `event_journal` (observed flows) vs
   the static architecture (structural), per the spec.
4. E*TRADE read-only adapter once the owner provides developer keys (entered
   in the app, never in chat).

## Blockers needing the owner

- Close/re-protect INTC, MSTR, MRVL and clear 5 stale records (Admin).
- Set option stop to 20% (default 50%).
- E*TRADE developer keys + OAuth; a live account and explicit limits before
  any live check.
