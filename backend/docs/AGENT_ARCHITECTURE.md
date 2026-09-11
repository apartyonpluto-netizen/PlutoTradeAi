# PlutoTradeAI Agent Architecture

_Written 2026-09-11, verified against the code (not from memory) while scoping
the live-trading readiness push and the future learning-loop work. If this
drifts from the code, trust the code and fix this doc._

## The one-sentence version

**Every "brain" here is a deterministic rule engine that scores fixed technical
indicators.** None of them learn, adapt, or improve by running more. The only
thing that changes their output is fresher price data. There is a real
reporting layer that could feed outcomes back into decisions someday
(Tier 2 — see the bottom of this doc) — it does not exist yet.

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

## Why nothing here "gets better by running"

Every brain in layer 2 is stateless: same inputs in, same rule-based output
out, every time. `autonomy/performance_report.py`'s own docstring calls
itself **"Tier 1 of the 'make autonomy learn' roadmap"** — human-readable
reporting only — and explicitly says an automated system that *adjusts its
own trading parameters from real outcomes* (**Tier 2**) is "a substantially
bigger, riskier undertaking that needs real trade-history volume first."
Tier 2 does not exist in this codebase yet. Running the brains 24/7 gets you
more scan ticks against fresher data and a bigger sample for the efficiency/
performance reports to eventually analyze — it does not, on its own, make
any brain's judgment better. That's a separate, deliberate project; see the
learning-loop plan (tracked separately, not yet written as of this doc).
