# PlutoTradeAI Agent Architecture

_Written 2026-09-11, verified against the code (not from memory) while scoping
the live-trading readiness push and the future learning-loop work. If this
drifts from the code, trust the code and fix this doc._

## The one-sentence version

**Every "brain" here is a deterministic rule engine that scores fixed technical
indicators.** None of them learn from experience, and there is exactly one
narrow exception worth knowing precisely (see `calibration.py` below) — it
is not the general learning loop this doc originally implied didn't exist
at all. Correction logged 2026-09-11: the first version of this doc said
"Tier 2 doesn't exist... zero lines of code," which understated what's
actually there. It's still true that nothing learns from *real* trade
outcomes — that part hasn't changed.

## The five layers

```
1. DATA SOURCES        Alpaca (real-time+historical bars, real-time trade price)
   (senses)             Webull (broker: balances, positions, orders, options chain)
                         X API (trusted-account tweets)
                         yfinance (⚠ 3 modules still on this - see Gaps below)

2. ANALYSIS ENGINES     strategy_brain · charting_brain · extended_hours_brain
   ("brains")           llm_reasoning (LLM second opinion + news)
                         candle_brain · pattern_brain · analytics (reversal/trend)
                         regime (VIX shadow) · neural_engine · options_brain (research)
                         options_selector (real execution)

3. ASSEMBLY             app.py: _build_page_context()
                         Runs the brains for up to 6 tickers, assembles
                         upcoming_opportunities (the one list that matters)

4. DECISION+EXECUTION   app.py: _run_autonomous_trade_scan_locked()
                         Filters, sizes, submits, protects. Talks to Webull.

5. RECORD + REPORT      overnight_orders / research_log / scan_run_log /
   (Tier 1 - reporting   closed_trades  -->  performance_report / efficiency_report
    only, dead-ends      / daily_digest  -->  a HUMAN reads it.
    into a human)         Nothing here writes back into layer 2 or 4.
```

## Layer 2 in detail: which brains actually drive a trade, which are display-only

| Module | Real input | Feeds a live decision? | Data source |
|---|---|---|---|
| `brains/strategy_brain.py` | daily+intraday OHLCV, extended-hours signal | **Yes** — `confidence`, `recommendation`, `market_context.current_price` (real-time since `374c11d`) all flow into the opportunity | Alpaca |
| `brains/charting_brain.py` | daily+intraday OHLCV | **Yes** — `breakout_level`/`breakdown_level` are the only source of `stop`/`target`/`ideal_entry` (see the `faaa0ee`/`c532f6c` fixes, which reject/downgrade when these are 0) | Alpaca |
| `brains/extended_hours_brain.py` | intraday+daily OHLCV | **Yes, indirectly** — computed first, passed *into* `strategy_brain`'s gap/extended signals; also shown on the opportunity card | Alpaca |
| `brains/llm_reasoning.py` | the opportunity dict + up to 3 recent news headlines (`fetch_news_bundle`) | **Yes** — only if the user has their own Anthropic key; can veto or adjust confidence ±20, after sizing, before submission | Claude (Sonnet 5) + news/x_news.py |
| `autonomy/options_selector.py` | `current_price`, Webull's live option chain + snapshot | **Yes** — the only path that ever calls the broker for a *real* option order | Webull |
| `market_scanner.py` (`scan_market`) | a fixed 9-ticker universe + watchlist | **Yes, indirectly** — its output (`scanner_rows`) decides *which* tickers get the deep (strategy/chart/extended-hours) treatment at all, via `_resolve_analysis_tickers` | Alpaca |
| `candle_brain.py` | daily OHLCV | **No** — computed only when `include_patterns=True`, which the autonomous scan never passes | ⚠ yfinance |
| `pattern_brain.py` | daily OHLCV | **No** — same as candle_brain | ⚠ yfinance |
| `analytics.py` (reversal/trend) | daily OHLCV | **No** — see "computed but discarded" in Gaps below | ⚠ yfinance |
| `regime.py` | VIX snapshot | **No, by explicit design** — shadow-mode only, recorded on every entry for future backtesting, never reads by anything that sizes or gates a trade. This is a deliberate safety boundary (see the module's own docstring), not a gap — don't "fix" it into an active signal without a real review. | (VIX feed) |
| `neural/neural_engine.py` | scanner_rows + watchlist + news + options payloads | **No** — feeds the dashboard's "Neural Core" stat card only | (aggregates others) |
| `options/options_brain.py` | option chain + Greeks | **No** — this is the *legacy research* options page, explicitly disclaimed "no options execution is enabled." Not the same system as `options_selector.py`. Easy to confuse by name; don't. | yfinance |
| `integrations/tradingview.py` | inbound webhook payloads | **No** — stored and shown in the mission feed / alerts only, never read by the scan | (external webhook) |
| `calibration.py` + `calibration_store.py` | **Updated 2026-09-11 (see "What changed" below).** Backtested per-strategy win rate / avg return (`backtest_engine.run_ticker_backtest`), OR this deployment's own real closed trades (`autonomy/closed_trades.py`, joined across every account) when trusted — real is now preferred over backtest. Either way, ≥15 trades required to be "trusted." | **Yes** — `strategy_brain.py:375` multiplies every candidate's raw score by `score_multiplier(strategy_name)`, ±25% cap. Updates automatically every 10 newly-closed trades (any account), in addition to the existing manual admin trigger (`/api/admin/recalibrate-strategies`, which now refreshes both sources). | Alpaca (via backtest_engine) + this account's own real fills (real-outcomes path) |

## What "the scan" actually does, in order

`_run_autonomous_trade_scan_locked` (app.py):
1. Reconciles existing positions (independent of everything below — runs even when autonomy is OFF).
2. Calls `_build_page_context(include_reversal=True, include_trend=True, include_options=False)` → gets `upcoming_opportunities` (up to 6 tickers deep-analyzed, each already scored by strategy_brain/charting_brain/extended_hours_brain).
3. Filters to `qualifying`: recommendation is CALL/PUT, confidence ≥ threshold, not already placed today.
4. Per candidate, in order: reject if no usable stop/target (`c532f6c`) → margin-account check for shorts → **try a real option first** (`options_selector.py`) → fall back to equity sizing → LLM veto (`llm_reasoning.py`, optional) → fresh real-time price drift check (Alpaca) → submit to Webull → confirm protection.
5. Every outcome (placed or skipped, and why) is durably logged (`research_log`, `overnight_orders`, `scan_run_log`).

## Gaps found while writing this map (2026-09-11, not yet fixed)

- **`candle_brain.py`, `pattern_brain.py`, `analytics.py` still call `yfinance` directly.** Everything on the live-decision path (strategy/chart/extended-hours/scanner) was migrated to Alpaca after the 2026-08-28 Yahoo rate-limit incident (see the `alpaca-market-data-migration` memory); these three were not, because they don't feed live trades. They likely still degrade under the same rate-limiting that motivated the migration — worth a follow-up if the Analysis pages that use them (Candle Brain, Pattern Brain, Reversal Map, Trend Detection) matter to you day-to-day.
- **The scan computes reversal/trend analytics on every 5-minute tick and never reads the result.** `_run_autonomous_trade_scan_locked` passes `include_reversal=True, include_trend=True` into `_build_page_context`, but nothing in the scan function reads `reversal_rows`/`trend_rows` afterward. That's a real yfinance call burned on every tick for output nobody uses in that path — a candidate for `include_reversal=False, include_trend=False` there specifically (dashboard/page callers that actually display these would keep `True`).
- **Two `options_brain.py` modules exist** — a legacy top-level one and `options/options_brain.py`, which literally imports from the legacy one (`from options_brain import build_options_outlook as legacy_build_options_outlook`). Neither is the real execution path (that's `autonomy/options_selector.py`). Naming collision risk for future work — grep both before touching "options brain" anything.
- **`get_market_data`'s deadline-fallback error string still says "Scanner timed out - Yahoo Finance is rate limiting"** even though `market_scanner.scan_market` has been Alpaca-backed since the migration. Stale message, not a stale mechanism — cosmetic, but worth fixing so a real timeout doesn't misdirect debugging toward the wrong provider.

## Why nothing here "gets better by running" — precisely stated

Every brain in layer 2 is stateless within a single call: same inputs in,
same rule-based output out. The **one** feedback path is `calibration.py`'s
`score_multiplier`, and it's narrower than it might sound: it adjusts a
strategy's score based on how that strategy performed in a **backtest**
(simulated fills against historical bars), not on how this account's real
orders actually turned out, and it only updates when an admin manually
triggers it — there's no automatic recalibration, and it's never looked at
`closed_trades.py` (this account's real, closed P&L) at all.

`autonomy/performance_report.py`'s own docstring calls itself **"Tier 1 of
the 'make autonomy learn' roadmap"** — human-readable reporting only — and
explicitly says an automated system that *adjusts its own trading
parameters from real outcomes* (**Tier 2**) is "a substantially bigger,
riskier undertaking that needs real trade-history volume first." That
specific thing — real closed trades feeding back into live scoring,
automatically — does not exist. Running the brains 24/7 gets you more scan
ticks against fresher data and a bigger sample for the efficiency/
performance reports to eventually analyze; it does not make any brain's
judgment better on its own. That's a separate, deliberate project; see the
learning-loop plan (tracked separately, not yet written as of this doc).

**Update, same day, later:** the paragraph above is now partially out of
date — see "What changed" below. The core claim still holds in spirit
(no brain's rule-based *judgment* changes; nothing here is a learning
model), but "real closed trades feeding back into live scoring,
automatically — does not exist" is no longer true as stated.

## What changed (2026-09-11, later the same day): memory, real outcomes, multi-source data

Implemented the plan this doc's own gaps motivated, in five small,
separately committed, separately tested pieces:

1. **A real memory layer.** `autonomy/research_log.py` (schema v2) now
   captures `skip_category` (previously computed but silently dropped
   before reaching this log) and `signal_snapshot` — `strategy_brain`'s
   full `market_context` (RSI, EMA stack, VWAP, relative volume, etc.) and
   `strategies_evaluated`, pass-through at zero marginal cost, for EVERY
   candidate the scan evaluates. Shadow-mode only, same guarantee as
   `regime_shadow` — nothing reads this back into a decision. Deliberately
   does **not** yet capture `candle_brain`/`pattern_brain`/`neural_engine`
   output — see their own table rows above; wiring them in would add real
   yfinance calls to every candidate on every scan tick.
2. **A second, broker-side price check.** `integrations/market_data_aggregator.py`
   cross-checks Alpaca's real-time price against Webull's own equity
   snapshot (`integrations/webull.py`'s new `get_equity_snapshot`, mirroring
   the options-side snapshot already used live) immediately before
   submission. Fails closed only on genuine disagreement beyond tolerance
   (new `skip_category`: `price_sources_disagree`); Webull being
   unavailable falls back to Alpaca alone, unchanged from before — this can
   only make the system *more* conservative, never less available.
   Deliberately **not** wired into `strategy_brain.py`'s own real-time read
   (would double Webull call volume across every scanned ticker for a read
   with no direct trade consequence).
3. **`autonomy/outcomes_analysis.py`** — Tier 1 reporting, joins
   `closed_trades.py` back to the new `signal_snapshot` and reports
   realized win rate/P&L by RSI-at-entry, EMA-stack alignment, relative
   volume, and price-vs-VWAP, each with `sufficient_sample` gating. Never
   called from the scan. `candle_brain`/`pattern_brain`/`neural_engine`
   buckets aren't here either, for the same reason as (1).
4. **Real-outcomes calibration** — `calibration_store.score_multiplier`
   now prefers a multiplier derived from this deployment's own real closed
   trades over the backtested one, gated by the same `MIN_TRADES_TO_TRUST`
   and ±25% cap, with automatic recalibration every 10 newly-closed trades
   (any account) plus the existing manual admin trigger. See the table row
   above — this is the one row in this doc that's genuinely more capable
   now than when this doc was first written.
5. **A brain-validation gate** — `autonomy/autonomous_controller.py` gained
   `validated_brains` (default `[]` per account) and `is_brain_validated`.
   Infrastructure only: **nothing in this codebase adds a name to this list
   automatically**, and as of this writing there is still no live call site
   that reads a shadow-only brain's output into scoring at all (see (1) —
   `candle_brain`/`pattern_brain`/`neural_engine` aren't even in
   `signal_snapshot` yet), so this gate currently guards nothing. It exists
   so that promoting one of them later is a deliberate, auditable,
   per-account decision made with `outcomes_analysis.py` evidence in hand —
   not a hardcoded change.

**Still true, unchanged by any of the above:** no currently-silent brain
was promoted to live-decision-influencing. `backtest_engine.py` still uses
yfinance and a fixed-hold-days exit, a real methodology gap between what
calibration validates and what live trading does — not addressed here,
deliberately (see the plan's own out-of-scope list). Portfolio-level
correlation/concentration risk and real-time human paging remain open.
