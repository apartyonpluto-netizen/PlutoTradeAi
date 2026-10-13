# One sandbox options cycle

This is the next milestone from the Oct 1 roadmap: one option order in the **Webull sandbox** that enters, gets a protective exit, closes, and shows up in reconciliation. No option order has been placed yet, including in sandbox. Do this on the sandbox account only. Do not set `PLUTO_ENABLE_LIVE_TRADING` or `PLUTO_LIVE_TRADING_CONFIRM`.

The code path that places a buy, watches the bid, and sells is already wired. Two things used to stop a clean cycle, and both are addressed in code:

- The option stop default is **20% of the premium paid** (Tre's rule). A settings file that still has the old untouched default of 50% is treated as 20% on the next read. If you later save 50% yourself, that choice sticks.
- If the closing sell does not fill on the first check, the monitor keeps polling **that** order. It does not place another sell on the next 10-second tick. A cancelled sell with zero fill is cleared so one new attempt can be placed later.

What is still in your hands is account state, not another code change.

## Before you trigger it

1. **Free a position slot.** `max_positions` defaults to 3. INTC, MSTR, and MRVL already occupy those slots, so a new option entry is blocked until one of them is closed or the limit is raised. Closing them is a broker action. This app will not close them for you. The 10-second monitor can attach a stop to those equity positions when it has a safe price (see below); that does not free the slot.

2. **Use the margin sandbox account.** Option orders go to the `INDIVIDUAL_MARGIN` account. The cash account is equities only. If the margin account is missing, the scan skips options and falls through to equity. Confirm it on the account diagnostic you already use; do not point this at production.

3. **Check that one contract fits the paper budget.** Size uses the configured paper balance (about $3000), not Webull's large paper buying power. One contract costs the ask times 100. If that is more than the risk budget, quantity is 0 and the candidate becomes an equity trade instead. The research log and the scan summary's `option_attempted` / `option_contract_found` counters show which of those happened. A preview scan (next section) is the way to see it without placing anything.

4. **Run it during regular market hours.** Equity stops can only be placed in the CORE session. Place the option cycle in that same window so the entry and the later exit are not sitting on a closed gateway.

5. **Autonomy on, emergency stop off.** Mode `PAPER` or `AUTONOMOUS` (sandbox still — live stays locked unless you arm it yourself, which this runbook does not ask you to do). Approval mode proposes a ticket and does not submit.

The trade plan is advisory. It does not block an option submission.

## Trigger

Prefer a dry run first. Signed in, during market hours:

- `POST /api/autonomy/preview-scan` runs the real scan and sizing and **does not** send an order or save one. Look for an item with `instrument_type: OPTION` and `status: preview`. If you only see equity, read `reason_skipped` / the research log: no contract, quantity 0, or no free position slot are the usual causes.

When the preview shows one option contract you are willing to let the sandbox buy:

- `POST /api/autonomy/run-overnight-scan` is the same scan **with** submission, or leave autonomy on and let the 5-minute cron do it.

Either one can place a real sandbox order. Do not call it against production keys.

## What "done" looks like

1. **Entry.** The overnight-orders record for that ticker has `instrument_type: OPTION`, a real `option_symbol`, and `lifecycle_state` of `protection_confirmed_active` after the buy fills. The premium stored is the fill price when the broker reports one.

2. **Protection.** There is no resting broker stop on a long option. Protection is the monitor: every 10 seconds it reads the option bid and sells if the bid is at or below premium × (1 − 0.20), or at the target, or inside the expiration safety window (default 3 days). That sell is a limit slightly under the bid.

3. **Exit.** After the sell fills, the record is `closed` and the trade journal has one closed option row with `exit_type` of `option_stop_reached`, `option_target_reached`, or `option_expiration_safety_close`. Fees are copied from order history when that payload includes them. Order detail often does not include fees; history does. If the fee fields are missing, P&L stays gross and that is recorded as such.

4. **Reconciliation.** `compare()` matches an option by `option_symbol` when `instrument_type` is `OPTION`. No option position payload has been captured yet, so do not assume the field names. After this first fill, save the positions response for that account (secrets and account id removed) as a fixture under `backend/tests/fixtures/` so the next pass can replay it. Until that fixture exists, treat a broker-side option close that this app did not place as unverified.

## Equity stops on INTC, MSTR, and MRVL

Those three are open in the sandbox with no working stop. The 10-second monitor now checks every open equity position:

- If a working stop is already at the broker, it leaves it.
- If the local record has a stop price, or the newest **unfilled** `STOP_LOSS` in the last 30 days of order history is still **below** the last price, it places that stop during CORE hours and writes the order id onto the record. The next tick confirms it instead of placing a second one. INTC's cancelled stop at 104 (from the 2026-09-30 history) is the kind of price this will reuse, and only while 104 is still under the market.
- If the saved stop is already through the market, or there is no price at all, it flags the record every tick (`naked_position`) and does not invent a price. Use Account Hub → Protect with a price you choose, or close the position. Outside CORE it waits and says it will attach on the next CORE session.

This monitor does not place entries and does not close those three positions.

## If the cycle does not start

| What you see | What it means |
|---|---|
| Preview has no option row | No free slot, no margin account, no contract in the 7–35 day / 8% strike / 35% spread window, or one contract does not fit the paper risk budget |
| `option_contract_found` but quantity 0 | Ask × 100 is larger than the risk budget or option buying power |
| Entry goes `entry_failed` | The broker rejected the buy. The record's `error` is the reason. Do not retry by hand with a second order until you know the first one is not resting |
| Exit stays open and the bid is through the stop | The sell was sent. Check that `option_exit_client_order_id` is set and that a second sell was **not** added. The monitor polls the first one |
