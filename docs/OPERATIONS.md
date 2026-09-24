# PlutoTrade AI - Operating Guide (paper trading only)

Scope: TradingView webhook signals -> analysis -> **human approval** -> internal simulated paper broker.
There is no live trading, no autonomous entry and no external broker order path in this codebase.

## 1. Run it

```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env            # then edit: at minimum TRADINGVIEW_WEBHOOK_SECRET and PLUTO_SECRET_KEY
venv/bin/python -m backend.app  # local, http://127.0.0.1:8000
gunicorn backend.app:app --workers 1   # production (see Procfile / render.yaml)
```

Create an account at `/auth/register`, sign in, then approve signals from the Alerts / Portfolio pages.
Approve, decline, alert dismiss/resolve and the manual monitor trigger all require a signed-in user.

## 2. Configuration that matters

| Variable | Meaning |
| --- | --- |
| `TRADINGVIEW_WEBHOOK_SECRET` | Shared secret. Unset = every webhook is refused with 503. |
| `PLUTO_SECRET_KEY` | Flask session key. **Set it**: otherwise logins are lost on every restart and differ per worker. |
| `PLUTO_DATA_DIR` | Directory for all state (default `./data`). On Render it must be a persistent disk. |
| `PLUTO_ALLOW_REGISTRATION` | `false` closes public sign-up. Do this once your account exists. |
| `PLUTO_APPROVER_USERS` | Comma-separated usernames allowed to approve/decline (empty = any signed-in user). |
| `PLUTO_ADMIN_MODE` | `true` makes every visitor "signed in". Local debugging only. |
| `PLUTO_MAX_RISK_PER_TRADE`, `PLUTO_MAX_POSITION_PERCENT` | Position sizing caps (share of equity). |
| `PLUTO_MAX_ENTRY_DEVIATION_PERCENT` | Refuse approval if the market moved this far from the signal entry. |
| `PLUTO_PAPER_FEE_PER_SHARE` | Simulated commission per share on every fill. |
| `PLUTO_SIGNAL_APPROVAL_MAX_AGE_SECONDS` | Unapproved signals expire after this long. |
| `PLUTO_ENABLE_ORDER_MONITOR`, `PLUTO_ORDER_MONITOR_INTERVAL_SECONDS` | Background monitor (fills, protection, reconciliation). |

`PLUTO_AUTO_PAPER_EXECUTION` is reported by `/api/runtime/config` but **not implemented**; entries always need approval.

## 3. State files and backups

All in `PLUTO_DATA_DIR`:

| File | Content |
| --- | --- |
| `signals.json`, `signal_approvals.json` | Signals and the approval audit trail |
| `paper_ledger.json` | Account cash, positions, trades, lifecycles, alerts, equity curve |
| `paper_ledger.broker.json` | Simulated broker orders and positions (survives restarts) |
| `*.lock` | Lock files for cross-process safety - safe to delete when the app is stopped |
| `*.corrupt-<hash>` | Forensic copy made when a file failed to parse |

Back up `paper_ledger.json` **together with** `paper_ledger.broker.json` (they must match). Writes are
atomic (temp file + rename) and serialised across threads and processes with file locks, so a second
worker or a CLI run cannot lose updates.

**Corruption policy.** If a state file exists but cannot be parsed the app never replaces it with an empty
account: the affected APIs answer `503`, `/api/health` reports `degraded`, and the file is left untouched
(plus a `.corrupt-*` copy). Restore it from backup (or move it aside on purpose) and restart. A ledger that
disappears while the app runs is treated the same way.

## 4. Restart behavior

On start the engine reconciles: every non-closed trade is re-checked against the broker file, resting
protection is re-created if missing, an entry whose broker call never completed is re-sent idempotently
(deterministic client order id), and any broker position no live trade accounts for raises an
`ORPHAN-<symbol>` alert. Signals are recovered as described in `docs/tradingview_integration.md`.
Nothing is executed automatically on restart.

## 5. Alerts and the entry freeze

Uncertain broker state (missing order, malformed response, conflicting exit evidence, position mismatch,
internal error, missed monitor heartbeat) raises an alert and **freezes new entries**. Transient causes
clear themselves on the next good monitor pass, and `ORPHAN-*` / `POSITION_MISMATCH` alerts clear once the
ledger and broker agree again. Alerts that need a human decision (`EXIT_EVIDENCE_MISSING`,
`EXIT_EVIDENCE_CONFLICT`, `SIBLING_CANCEL_UNCERTAIN` on a trade that never closes) stay until you fix the cause
and call `POST /api/paper/alerts/<id>/resolve`. Dismissing an alert only hides it; it does not lift the freeze.
An approval rejected because entries are frozen returns to `AWAITING_APPROVAL` so you can retry.

## 4a. Accounting rules (so the numbers can be audited)

- Cash moves per fill at the actual fill price: long buys reduce cash, short sales credit proceeds.
- Realized P&L is booked per exit fill against the average entry price (partial exits at different
  prices are each booked once); fees are charged on every fill and included in net P&L.
- Equity = cash + signed market value of OPEN positions (shorts are negative). Closed positions never count.
- Buying power = cash minus the current market value of open shorts (no leverage, no margin).

## 6. Deployment (Render)

- `render.yaml` gives the web service a persistent disk at `/var/data` (`PLUTO_DATA_DIR`). Render disks attach
  to one service only, so the **web service owns all trading state and runs the order monitor**; the worker
  is configured with `PLUTO_ENABLE_ORDER_MONITOR=false` and must not be pointed at the ledger.
- `--workers 1` is still recommended (the market stream and monitor are per process), but the file locks make
  extra workers safe for the ledger and signals.
- Set `TRADINGVIEW_WEBHOOK_SECRET` (secret) and keep `PLUTO_SECRET_KEY` generated once and stable.
- Health: `GET /api/health` (`data.paper_storage.ok` must be true).

## 7. Tests

```bash
venv/bin/python -m pytest -q
```

The suite redirects every data path and the working directory into a temp folder (`tests/conftest.py`) and
mocks market prices, so it never touches `./data`. Passing tests are necessary, not sufficient: see the
limits below before relying on the system.

## 8. Known limits and unverified integrations

- Market data comes from yfinance (delayed, rate limited). Nothing here was verified against live
  TradingView webhooks, a real Render deployment, a real disk mount, gunicorn worker classes or the
  Socket.IO market stream.
- Fills are evaluated on monitor ticks (default 45 s), not tick by tick, and use a single quote.
- Two paper positions from an older engine version (TSLA, IWM in the original `data/paper_ledger.json`) have no
  lifecycle record. They are still marked to market, new entries in those symbols are refused, and they cannot
  be exited through the broker; close them by editing the ledger with the app stopped.
- User accounts (`auth_users.json`) are a simple file store without rate limiting or password reset.
- The signal analysis (brains) is research tooling; its output is not validated as a trading edge.
