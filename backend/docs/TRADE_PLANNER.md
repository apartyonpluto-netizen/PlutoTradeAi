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
- Equity only. Options keep their own premium-based rules (20% trigger on the
  option's own price) and are not planned here.

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

## Not built yet

Manual approval using the plan (with a re-check before submission), options
plans, sector/concentration and drawdown limits, error classification of
closed trades, offline strategy research with versioned promotion, and an
annotated chart of where the levels came from.
