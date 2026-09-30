"""Pattern Brain: objective setup discovery for one ticker (see setups/).

Replaces the earlier heuristic that assigned fixed "confidence" numbers to
pattern names - those were not measured and are gone. Each row now lists the
rule-based setups found, their state, levels and a qualify/watch/reject
decision; historical evidence is shown only when it exists."""

from __future__ import annotations

from typing import Dict

try:
    from .setups import service
except ImportError:
    from setups import service


def analyze_patterns(ticker: str) -> Dict[str, object]:
    result = service.analyze([ticker])[0]
    if not result.get("as_of"):
        raise ValueError("; ".join(result.get("data_errors") or ["no market data"]))
    return {
        "ticker": result["symbol"],
        "last_updated": max(result["as_of"].values()),
        "summary": result["summary"],
        "liquidity": result.get("liquidity"),
        "regimes": result.get("regimes"),
        "opportunities": [{k: v for k, v in o.items() if k != "primary_detection"} for o in result["opportunities"]],
        "recently_ended": result.get("recently_ended", []),
        "data_errors": result.get("data_errors", []),
        # Legacy shape for older consumers: names and states, never a confidence.
        "patterns": [{"pattern": o["setup"]["name"], "state": o["state"], "decision": o["decision"]} for o in result["opportunities"]],
    }
