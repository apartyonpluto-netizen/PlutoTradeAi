# Broker verification record

What has been verified against which broker, at which level. Kept separate by
level on purpose: an HTTP success is not a fill, an exit request is not a
closed position, and internal accounting agreeing with itself is not broker
reconciliation. No secrets are recorded here.

Accounts referred to below are identified only by broker, environment and
type. Production uses the Webull **sandbox**; live trading has never been
armed (two env vars, see `backend/integrations/webull.py::is_live_trading_armed`).

## Level 1 — Mocked tests (no broker contact)

| Area | Evidence |
|---|---|
| Order lifecycle, write-ahead persistence, crash recovery | `test_write_ahead_entry_persistence.py`, `test_partial_fill_resize_and_crash_recovery.py` |
| Ambiguous submission reconciliation | `test_ambiguous_*`, `test_environment_isolation.py::test_ambiguous_submissions_are_reconciled_against_their_own_account` |
| Protective stop placement per account | `test_environment_isolation.py::test_a_stop_is_never_placed_on_one_account_for_another_accounts_record` |
| Sandbox/live separation of records and decisions | `test_environment_isolation.py` (7 tests; live switch monkeypatched) |
| Option entry/exit (20% bid-based trigger supported) | `test_option_entry_and_exit.py`, `test_option_trade_plan.py` |
| Approval tickets + pre-submission recheck | `test_trade_tickets.py` |
| Event trail signal → exit | `test_event_journal.py` |

Full suite: 1403 tests passing (2026-09-29, local venv).

## Level 2 — Replay tests

| Payload | Captured | Used by |
|---|---|---|
| Webull sandbox cash-account positions + open orders (sanitized: broker position ids replaced, no account ids) | 2026-09-29, read-only | `tests/test_broker_reconciliation.py::test_replay_*` |

**Gap:** no captured order-detail, order-history or balance payloads yet;
no option position payload exists (no option has been placed).

## Level 3 — Authenticated sandbox / paper (Webull)

| Check | Result | Date | How |
|---|---|---|---|
| Account list (cash + margin sandbox accounts) | OK | 2026-09-29 | read-only probe |
| Balances, positions, open orders | OK | 2026-09-29 | read-only probe + admin readiness panel |
| Order history incl. same-day (`end_date` = tomorrow) | HTTP 200 | 2026-09-29 | read-only probe |
| Equity LIMIT entries filled | MRVL 2026-09-17, MSTR 2026-09-18, INTC 2026-09-21 | from broker order history |
| STOP_LOSS placement | accepted with those entries | broker order history |
| Duplicate client order id rejected (HTTP 417 REPEAT) | confirmed earlier (see `order_lifecycle.deterministic_client_order_id`) | re-confirm pending |
| Option preview | confirmed 2026-09-03 (code notes) | — |
| **Option order placement / fill / exit** | **never done in sandbox** | — |
| Broker-vs-internal reconciliation read path (accounts → positions → open orders) | OK - cash holds INTC 2, MSTR 5, MRVL 4; **0 working orders** (no protective stops at the broker); margin holds nothing | 2026-09-29 | read-only probe from local keys (same sandbox account as production) |
| Reconciliation report in production | not yet observed (deploys with this change; runs every 15 min from the fast-monitor timer) | — | — |

Known sandbox state (production): INTC, MSTR and MRVL positions are open at the
broker with no protective orders (orphans after the 2026-09-29 record-loss
bug, fixed in `dcba188`). Five records (MU, PLTR, COIN, SLB, ADBE) claim
positions the broker does not hold. Both need the owner in Admin.

## Level 4 — Production (real-money) read-only

None. No live Webull account is connected. E*TRADE: no adapter
(`backend/brokers/etrade_broker.py` is a stub that reports "not_connected").
**External dependency:** E*TRADE developer consumer key/secret and OAuth
authorization by the owner.

## Level 5 — Authorized live execution

None, and none will be attempted without explicit owner authorization naming
the account and the per-order / per-day dollar limits.
