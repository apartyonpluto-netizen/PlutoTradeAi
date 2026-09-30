# Implementation status

Canonical codebase: this repository (`PlutoTradeAI`, deployed to Render from
`main`). `PlutoTradeAI_v02` is not being developed (owner decision
2026-09-29). Detailed per-capability evidence: `backend/docs/RELEASE_CHECKLIST.md`.
Broker evidence by level: `docs/BROKER_VERIFICATION.md`.

Levels: **mocked** (tests only) · **sandbox** (Webull paper, real requests) ·
**prod-read** (real-money account, read only) · **live** (real-money orders).

## Priority order (master prompt) and state

| # | Area | State | Level reached |
|---|---|---|---|
| 1 | Account/environment isolation, durable storage | New records stamped broker+environment; decisions use only the current environment's records; each record reconciled against its own account (two cross-account defects fixed). Storage: JSON + flock + atomic writes; **no automated backup** | mocked |
| 2 | Broker auth + read-only account retrieval | Webull sandbox accounts/balances/positions/orders/history read | sandbox |
| 2 | E*TRADE read-only | **Not built** - needs owner's developer keys + OAuth | — |
| 3 | Order state machine + execution adapter | Webull equities: lifecycle states, deterministic ids, 417 idempotency | sandbox (equity) / mocked (options) |
| 4 | Reconciliation, idempotency, restart recovery | Ambiguous submissions, orphan import, position-absent detection, write-ahead records | sandbox + mocked |
| 4 | Broker-vs-internal discrepancy report with last-reconciled time | `broker_reconciliation.py`: matched / quantity mismatch / record-only / broker-only / protection missing; disconnected state keeps last good time; dashboard "Broker sync" panel; every 15 min + on demand | sandbox read path + replay |
| 5 | Protection, sizing, deterministic risk | Stop placement, risk sizing, portfolio limits, dollar caps, 20% option trigger (owner must set) | mocked + sandbox (equity stops) |
| 6 | Broker-derived performance | Closed trades from broker fills; per-environment reports; **fees not from broker** | mocked |
| 7 | Strategy / options / patterns / news / evidence | Setup engine (36 detectors, validation harness, weekly refresh), plans, tickets; news not in decisions | mocked (patterns on synthetic data only - real-data run pending on Render) |
| 8 | 3D observatory on recorded events | Event journal with correlation ids exists; observatory still reads research log | mocked |
| 9 | Browser verification, runbooks, release checks | Setup Discovery page verified in local preview (desktop + phone) | — |

## Known defects / gaps (highest first)

1. Production sandbox holds 3 unprotected orphan positions and 5 stale records (owner action in Admin).
2. Reconciliation report not yet observed in production (just deployed).
3. Fees are not taken from broker transaction records (options fees recorded as `None`).
4. No replay tests from recorded broker payloads.
5. No automated backup/restore of `/var/data`.
6. CSRF tokens absent (SameSite=Lax only).
7. E*TRADE adapter absent.
8. Observatory not driven by the event journal yet.

## Session log

- 2026-09-29: release checklist; event journal (correlation ids); objective
  setup engine + Setup Discovery page + walk-forward validation; completed-bar
  detection; environment isolation + cross-account reconciliation fixes;
  read-only broker reconciliation report + dashboard panel + replay fixture.
