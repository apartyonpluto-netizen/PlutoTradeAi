# Trade planner (`trade_plan_v1`)

`backend/trade_planner.py` turns one scan candidate into an explainable plan:
**trade**, **watch** or **reject**, with the numbers behind it. It is built
inside the autonomous scan (`_trade_plan_for_candidate` in `app.py`) from the
scan's own inputs and from `_compute_position_quantity`, the same sizing
function that sizes real orders. A plan's quantity is therefore always the
quantity the scan would submit.

## Status in this version

- **Advisory.** Plans are recorded with every evaluated candidate (the order
  record and the research log) and shown in Account Hub → Preview Scan. They
  do not gate submission; the scan's existing checks still decide. Gating is a
  later step, behind the manual-approval flow.
- Equity and options (calls and puts bought to open) each have their own
  plan; see "Option plans" below.

## Definitions

| Field | Meaning |
|---|---|
| `exposure` | Position market value: entry price × quantity |
| `allocation_percent` | Exposure ÷ plan equity |
| `planned_loss` | Loss if the stop fills exactly at the stop, including the per-share cost allowance |
| `risk_percent` | Planned loss ÷ plan equity |
| `leverage_after` | (Existing positions' market value + this exposure) ÷ plan equity |
| `buying_power_used` / `_after` | Buying power this order consumes / what remains, after reservations already made this scan |
| `reward_if_target`, `reward_to_risk` | Gain if the target fills, net of costs; that gain ÷ planned loss |
| `plan_equity`, `equity_source` | The balance sizing used: the configured paper balance when one is set, otherwise the broker's net liquidation value |

A 20% allocation is not a 20% risk and not a 20% chance of winning; the plan
shows each one separately.

## Probability and expected value

- A **win** is a closed trade of this strategy with positive net realized P&L
  after fees, under its own stop/target exits.
- Evidence is this deployment's own fully reconciled closed trades
  (`calibration.closed_trade_returns_by_strategy`). Backtests use a different
  exit rule (fixed hold) and are not used as a probability.
- Below **30** closed trades for the strategy the plan says *Probability not
  reliably estimated* and shows no expected value.
- At 30 or more: win rate with a 95% Wilson interval, and expected value =
  the strategy's mean net return × this position's exposure, with a 95%
  interval. A mean net return at or below zero makes the plan **watch**.
- The strategy engine's confidence is shown as a *setup score*, labeled as not
  a probability.

## Decision rules

**Reject** when any hold: short selling not permitted; no valid entry; no
valid stop on the correct side of entry; no current quote, or a quote older
than 120 s (previews defer this check, which the scan performs immediately
before submission); zero or unknown equity; the sized quantity is below one
share ("Trade does not fit this account", with the binding limit).

**Watch** when none of the above, but: no valid target; reward-to-risk below
1.0 after costs; or the strategy's closed trades show no positive mean return.

**Trade** otherwise, still subject to approval / the scan's own gates.

## Adverse scenarios

Every sized plan lists the loss if the stop fills at the stop, 0.5% worse,
and after a gap through the stop (2× the stop distance, or 1 ATR beyond the
stop when ATR is supplied). A stop is not a guaranteed exit price; for shorts
the loss is not capped at all.

## Tests

- `tests/test_trade_planner.py` — synthetic balances of $0, $100, $500, $3,000
  and $100,000 (test fixtures, not suggested allocations): no-fit accounts,
  reservations, position caps, costs, gaps, stale quotes, invalid stops, short
  permission, reward-to-risk, small samples, calibrated evidence.
- `tests/test_trade_plan_scan_integration.py` — the plan built inside a real
  scan matches the submitted quantity, reaches the research log, stays
  advisory, and a preview makes no extra market-data call.

## Option plans

`build_option_trade_plan` prices a long call or put from the contract's own
premium. The stock risk-per-share formula never applies: the most a long
option can lose is the premium paid.

The exit rule is shown exactly as `_check_and_execute_option_exit` runs it.
Each monitor pass reads the option's live **bid**. It sells when the bid is
at or below `entry premium × (1 − stop %)`, at or above
`entry × (1 + target %)`, or once expiration is within the close window. The
sell is a limit 0.5% under the bid. Nothing rests at the broker, so
protection depends on the monitor running and a live bid existing.

| Field | Meaning |
|---|---|
| `premium_at_risk` | ask × 100 × contracts (+ fees when known): the most that can be lost |
| `exit_trigger_premium` | ask × (1 − stop %); a $1.00 premium at 20% triggers at $0.80 |
| `loss_at_trigger` | estimated loss selling 0.5% under the trigger bid - an estimate, not a cap |
| `gain_at_target`, `reward_to_risk` | gain selling 0.5% under the target bid; ÷ loss at trigger |
| `breakeven_at_expiration` | strike + premium (call) or strike − premium (put) |
| `underlying_notional`, `delta_adjusted_notional` | shares controlled × price, and × \|delta\| - exposure, not a probability |

Adverse scenarios: exit at the trigger, exit one bid-ask spread below it,
and the premium going to zero.

**Reject** also when: no two-sided quote; the bid is already at or below the
exit trigger (buying at the ask would be sold on the first pass); expiration
falls inside the close window; one contract does not fit the account.
**Watch** also when: the bid is within 5% above the trigger; the spread is
wider than 10% of the mid; gain at target is below the loss at the trigger.

Option evidence comes only from closed **option** trades of the strategy;
equity plans use only equity trades. The exit percentages are set in
Account Hub → Risk Limits → Options (default 50% / 50% / 3 days).

Approving an option ticket also refuses when the fresh bid is at or below
the trigger computed from the ticket's premium.

## Approval flow (APPROVAL mode)

Set the autonomy mode to **APPROVAL** (Account Hub → Autonomous Mode). The
scan then runs exactly as in AUTONOMOUS mode, but a candidate that reaches
submission becomes a **trade ticket** (`backend/autonomy/trade_tickets.py`)
instead of an order, shown on Mission Control under *Awaiting Your
Approval* and announced in the alert bell.

- A ticket carries the exact terms (ticker, quantity, limit, stop, target,
  account; option contract for options), the thesis, and the plan. Its
  `version` is a hash of those terms. Approving sends the version you saw;
  if a later scan changed the terms (a new ticket supersedes the old one),
  the stale approval is refused.
- A ticket lapses after 20 minutes undecided (the next scan proposes it
  again if it still qualifies). The same setup on the next scan refreshes the
  open ticket rather than duplicating it. A declined ticker is not proposed
  again that trading day.
- **Approve** claims the ticket atomically (a double click or a second tab
  gets a conflict), then, under the account's scan lock, re-checks: trading
  day, kill switch, CORE hours, emergency stop, ambiguous/stuck freezes,
  duplicate position, max positions, daily loss limit, buying power, and the
  live price (2% drift for equity via the Alpaca/Webull cross-check; 10% for
  an option premium). Any failure marks the ticket RECHECK_FAILED with the
  reasons and nothing is sent. Otherwise the order goes through the scan's
  own submission path (write-ahead record, deterministic client order id,
  fill polling, protection), and the ticket records the outcome.
- Previews never create tickets. Live-money arming is unchanged and separate.

## Not built yet

Sector/concentration and drawdown limits, error
classification of closed trades, offline strategy research with versioned
promotion, and an annotated chart of where the levels came from.
