# Roadmap - "on pace" (set 2026-10-01)

Goal: one verified options trade cycle (entry -> protection/20% bid exit -> reconcile + broker fees)
in the Webull sandbox, then small, capped live trading on Webull. Everything else waits.

Decision 2026-10-01: keep PlutoTrade as the decision/risk/evidence engine. Robinhood (agentic MCP
account) is a future second broker once its agents support options; optional side-by-side test of
Robinhood Agents with a small stake. Not deleting PlutoTrade.

| When | Owner | Task | Done when |
|---|---|---|---|
| Tonight | Owner | Set stops on INTC/MRVL/MSTR (Mission Control -> Broker sync) | Broker sync shows a working stop for each after 9:30 ET |
| Tonight | Owner | Close MU/ADBE/COIN/PLTR/SLB records (Admin) | Broker sync: no record-only items |
| Tonight | Owner | Option stop-loss 20% (Account Hub) | setting saved |
| Tonight | Owner | Apply for Webull live API access (webull.com) | approval email |
| Tomorrow | Claude | Verify stops at broker; watch first scan with free slots | scan reaches plan/order stages (Observatory) |
| Tomorrow | Claude | First sandbox OPTION order; fix any blocker same day | option order accepted at broker |
| Tomorrow | Claude | Review first real-data setup evidence (Admin -> Setup Evidence) | run finished, statuses recorded |
| This week | Claude | Full sandbox options cycle + replay fixture of the real payload | closed trade with broker fees |
| This week | Owner | Enter live Webull keys in the app; give per-order + per-day $ limits | limits written down |
| This week | Claude | Arm live with those caps (owner's explicit go) | readiness panel all green |
| Next week | Both | First small live trades, watched via Broker sync + Observatory | live fills reconcile with broker |
| Later | Claude | Robinhood MCP adapter (when options supported); E*TRADE only if needed | - |
