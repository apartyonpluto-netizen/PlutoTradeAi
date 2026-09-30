# PlutoTrade AI — release checklist

Living document. Every row names its **verification level** and its
**evidence**; update both when something changes. Nothing here claims
profitability. Live (real-money) submission stays disabled until the owner
approves a specific account and explicit limits.

Last audited: **2026-09-30** (main app, `PlutoTradeAI/backend`; 1423 automated tests passing,
outbound network blocked in tests). Broker evidence by level: `docs/BROKER_VERIFICATION.md`.

## Verification levels

| Level | Meaning |
|---|---|
| **TESTED** | Implemented; automated tests with mocked brokers/data pass |
| **SIMULATED** | Runs only against simulated or demo data |
| **SANDBOX** | Exercised against the Webull paper (sandbox) API with real requests |
| **PROD-DATA** | Verified in production against a real data feed (not an order) |
| **LIVE-READ** | Verified read-only against a real-money account |
| **LIVE-EXEC** | Verified placing real-money orders |
| **INCOMPLETE** | Missing, partial, or scaffolding only |

## Gate 1 — Core correctness

| Capability | Level | Evidence | Gap / next |
|---|---|---|---|
| Order records survive every monitor pass (no cross-account deletion) | TESTED + SANDBOX | Fix `dcba188`; `test_write_ahead_entry_persistence.py::test_a_monitor_pass_for_one_account_never_deletes_the_other_accounts_records`; prod file had been reduced to orphan imports only | Watch prod: new entries' records should now persist |
| Write-ahead entry record before submission | TESTED | `dcba188`; worker-killed-mid-submission test | Not yet exercised in prod (no entries since deploy) |
| Stock sizing: risk, buying power, reservations, position/total/sector caps | TESTED | `test_trade_planner.py`, `test_portfolio_limits.py`, existing sizing tests | — |
| Option sizing by premium | TESTED | `test_option_trade_plan.py` | — |
| Decimal money math in sizing | TESTED | `_compute_position_quantity` / `_floor_shares` | Plans round for display only |
| Fees in net results | TESTED (replay) | Broker order history fees (SEC/FINRA) applied at close; unknown stays unknown | Earlier closed trades keep `fees: None`; option fee shape unverified |
| Closed-trade P&L for options counted in evidence | TESTED | `ea94e9b` (was silently excluded) | — |

## Gate 2 — Broker integration

| Capability | Level | Evidence | Gap / next |
|---|---|---|---|
| Webull sandbox: accounts, balances, positions, open orders, order history | SANDBOX | Read-only probe 2026-09-29 (cash + margin sandbox accounts); Admin readiness panel | — |
| Webull sandbox: equity LIMIT entry placement + fill | SANDBOX | Order history: MRVL 09-17, MSTR 09-18, INTC 09-21 filled | — |
| Webull sandbox: STOP_LOSS protective orders | SANDBOX | Placed alongside those entries | The three positions are now unprotected orphans — confirm how each stop ended before re-arming |
| Duplicate-safe resubmission (same client order id → 417) | SANDBOX | `order_lifecycle.deterministic_client_order_id` docstring: confirmed against the sandbox | Re-confirm in a controlled sandbox test before live |
| Webull sandbox: option preview | SANDBOX | `preview_option` confirmed 2026-09-03 (code notes) | — |
| Webull sandbox: option order placement / fill / exit | TESTED only | `test_option_full_scan_integration.py` (mocked) | **Never placed in sandbox** — no option candidate has reached submission |
| Order-history same-day visibility | SANDBOX | `end_date` tomorrow accepted (HTTP 200) 2026-09-29 | — |
| Webull live (real money) | INCOMPLETE | Code path gated by two env vars + dollar caps (`live_limits.py`) | Needs a live Webull account; owner decision |
| **E\*TRADE** | INCOMPLETE | `brokers/etrade_broker.py` is a stub ("not_connected") | OAuth 1.0a, accounts, quotes, preview, orders — needs developer keys from the owner |
| Market data (Alpaca IEX) | PROD-DATA | Readiness panel: live SPY quote on Render 2026-09-26/29 | Free tier: bars 15-min delayed; real-time latest trade only |

## Gate 3 — Order lifecycle and reconciliation

| Capability | Level | Evidence | Gap / next |
|---|---|---|---|
| Lifecycle states (submitted → filled → protection → closed/failed) | TESTED + SANDBOX | `order_lifecycle.py`; prod records | — |
| Ambiguous submission → reconcile before retry | TESTED + SANDBOX | `_reconcile_unknown_submissions`; prod orphan cases | — |
| Orphan discovery from broker history | TESTED + SANDBOX | Prod imports 09-18/19/22 | Imported orphans have no stop (never invented) — need human |
| Position-absent detection + admin close | TESTED | `test_position_absent_reconciliation.py`; Admin panel `dcba188` | 5 ghost records awaiting owner |
| Partial fills / protective-leg resize | TESTED | `test_partial_fill_resize_and_crash_recovery.py` | — |
| One correlation id linking signal → plan → ticket → order → fill → exit | TESTED | `autonomy/event_journal.py`; `test_event_journal.py` (autonomous + approval chains end to end, mocked broker); `/api/events`, `/api/events/chains`, `/api/events/trace/<id>` | Not yet seen in prod; records older than the journal trace via `rec-<record_id>` stand-ins |
| Broker-vs-app reconciliation report (positions, working stops, balances, close evidence) | PROD-DATA (sandbox) | `broker_reconciliation.py`; production 2026-09-30: 3 matched, 5 record-only (MU closed at broker), 3 unprotected | Owner to resolve the differences |
| Sandbox/live record separation; per-account reconciliation | TESTED | `test_environment_isolation.py` (two cross-account defects fixed) | — |

## Gate 4 — Risk enforcement

| Capability | Level | Evidence | Gap / next |
|---|---|---|---|
| Daily loss limit, max positions, risk per trade | TESTED + SANDBOX | Scan skip categories in prod (`max_positions` 3/3) | — |
| Portfolio limits: position, total, sector, drawdown | TESTED | `028b9fc`, 20 tests | Off until owner sets them |
| Live dollar caps (per order / per day) | TESTED | `live_limits.py`, `591f71f` | Only active when live armed |
| Option exit on executable bid, % of actual entry premium | TESTED | `_check_and_execute_option_exit`; `test_option_trade_plan.py` | **Owner's 20% rule not set** (app default 50%); never exercised on a real option |
| Approval bound to ticket version; recheck before submit | TESTED | `5000638`, `test_trade_tickets.py` | APPROVAL mode not yet enabled in prod |
| Emergency stop / kill switch / pause keeps monitoring | TESTED | Controller + scan gates | — |
| Overnight-holding permission | INCOMPLETE | No setting | Spec §7 — owner setting required |

## Gate 5 — Security

| Capability | Level | Evidence | Gap / next |
|---|---|---|---|
| Session auth, admin approval of accounts, per-user data | TESTED | `auth.py`, before_request login gate | — |
| Broker/API credentials encrypted at rest | TESTED | `docs/SECURITY.md`, Fernet | — |
| Secrets redacted in logs and Sentry | TESTED | Webull SDK log filter; `observability.py` scrubbing | — |
| CSRF | TESTED + prod | SameSite=Lax + Origin/Referer check on every write (`test_cross_site_writes.py`; same-site verified in production) | No per-form tokens |
| Rate limiting on login/webhooks | INCOMPLETE | Not found | Add |

## Gate 6 — Restart and failure recovery

| Capability | Level | Evidence | Gap / next |
|---|---|---|---|
| Crash mid-submission leaves a resumable record | TESTED | `dcba188` | — |
| Per-account scan lock across workers | TESTED + prod | `scan_lock.py` (flock) | — |
| Webull 429 handling / monitor load | PROD-DATA | `1409227`: 4 calls/tick, 0 × 429 after deploy | — |
| Storage | TESTED | JSON + flock + atomic writes; daily snapshots, verify/download, `docs/RUNBOOK_BACKUP_RESTORE.md` | Snapshots share the disk - download for off-site |

## Gate 7 — UI acceptance

| Capability | Level | Evidence | Gap / next |
|---|---|---|---|
| Trade plans (stock + option), tickets, settings | TESTED + browser (local preview) | Screenshots this session, desktop + phone | Not yet seen with real prod data |
| 2D/3D agent map | TESTED | Architecture view from research-log records | — |
| Observatory (recorded events) | TESTED + local preview | `/observatory`: structural vs observed flows, sectors, ticker chains | Sparse until the journal accumulates |
| Accessibility (reduced motion, keyboard) | INCOMPLETE (partial) | 3D map honors reduced motion | No full audit |

## Gate 8 — Deployment and operations

| Capability | Level | Evidence | Gap / next |
|---|---|---|---|
| Render web + worker + cron | PROD-DATA | Deploys this session | — |
| Sentry errors | PROD-DATA | Readiness panel "Reporting to Sentry" | Cron check-in: DSN on the cron service unconfirmed |
| Readiness report | PROD-DATA | Admin panel | — |
| Backup / restore runbook | DONE | `docs/RUNBOOK_BACKUP_RESTORE.md` | Rollback = redeploy previous commit on Render |

## Spec coverage (5th spec, 2026-09-29) — main app

| Spec section | Status |
|---|---|
| §1 Current state + checklist | This document |
| §2 Competitor research | Not started |
| §3 Shared research/evidence layer | Not started |
| §4 Market intelligence (news) | INCOMPLETE — Alpaca news + X wired, demo fallback labeled, not used in decisions, no dedup/corrections |
| §5 Objective patterns | TESTED — 36 versioned detectors, walk-forward + forward evidence; none validated yet (research/watch only) |
| §6 Account-aware plans | TESTED (advisory; `trade_plan_v1`) |
| §7 Financial requirements | Mostly TESTED; 20% rule and overnight permission need owner settings |
| §8 Broker execution | Webull SANDBOX (equity); E\*TRADE INCOMPLETE |
| §9 Lifecycle proof | Event journal TESTED; broker reconciliation PROD-DATA (sandbox) |
| §10 Honest research | Evidence rules in planner; research engine not started |
| §11 3D observatory | TESTED — driven by the event journal, three levels with breadcrumbs |
| §12 Security/ops | CSRF origin check, backups + runbook, network-isolated tests; rate limiting still missing |
| §13 Verification | Ongoing — this document |
| §14 Dependency order | Done: checklist, event journal. Next: evidence layer → patterns → news → observatory |

## Blockers that need the owner

1. Clear INTC / MSTR / MRVL (unprotected orphans holding all 3 position slots) and the 5 ghost records (Admin).
2. Set the option exit to 20% and any portfolio limits (Account Hub → Risk Limits).
3. E\*TRADE developer consumer key/secret (entered by the owner, never in chat) to start the adapter.
4. A live Webull account, if/when live trading is wanted — plus explicit per-order and per-day dollar limits.
5. Confirm `SENTRY_DSN` on the `plutotradeai-autonomous-scan-trigger` cron service.
