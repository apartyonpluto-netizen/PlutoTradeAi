"""Setup detector registry, states, and the detector specification every
detector must fill in. Coverage is exactly what is registered here - nothing
is recognized that has no registered, rule-based detector."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

DEVELOPING = "developing"                  # structure partly formed; uses a provisional pivot or is still forming
AWAITING_CONFIRMATION = "awaiting_confirmation"  # every structural rule met; trigger not crossed yet
CONFIRMED = "confirmed"                    # trigger crossed by a close under the detector's rules; signal window open
INVALIDATED = "invalidated"                # closed beyond the invalidation level (before or after confirming)
EXPIRED = "expired"                        # never confirmed in time, signal window passed, or target already reached

LIVE_STATES = (DEVELOPING, AWAITING_CONFIRMATION, CONFIRMED)
STATE_RANK = {CONFIRMED: 3, AWAITING_CONFIRMATION: 2, DEVELOPING: 1, INVALIDATED: 0, EXPIRED: 0}

# Validation status of a detector version for a timeframe. Recognition is
# separate from permission to trade: only VALIDATED is execution-eligible.
RESEARCH = "research"                       # functional tests only - watch/research alerts
BACKTEST_SUPPORTED = "backtest_supported"   # out-of-sample after costs supports it; awaiting forward evidence
BACKTEST_REJECTED = "backtest_rejected"     # out-of-sample after costs does not support it
VALIDATED = "validated"                     # out-of-sample AND forward evidence support it
NEEDS_DEFINITION = "needs_definition"       # a named formation with no established rules yet

FAMILIES = {
    "reversal": "Reversal formations",
    "structure": "Trend structure",
    "breakout": "Breakouts, breakdowns and retests",
    "failure": "Failed breakouts, reclaims and rejections",
    "continuation": "Trend pullbacks and continuations",
    "converging": "Triangles and wedges",
    "range": "Ranges",
    "vwap": "VWAP reclaim / rejection (intraday)",
    "mean_reversion": "Mean reversion",
}


@dataclass(frozen=True)
class DetectorSpec:
    id: str
    version: str
    name: str
    family: str
    direction: str                  # long | short | both
    timeframes: Tuple[str, ...]
    min_bars: int
    specificity: int                # higher = more specific; preferred as an opportunity's primary setup
    structure: str                  # required price structure
    prior_trend: str
    swing_rules: str
    duration: str
    tolerances: str                 # all volatility-adjusted (ATR multiples) unless stated
    volume: str
    confirmation: str
    invalidation: str
    expiration: str
    regimes: Tuple[str, ...]        # trend regimes it applies to: uptrend / downtrend / range / any
    data_requirements: str
    interpretation: str             # which definition was chosen, and why
    ambiguity: str
    sources: Tuple[str, ...] = ()
    parameters: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["parameters"] = dict(self.parameters)
        return data


DetectorFn = Callable[[Any, DetectorSpec], List[Dict[str, Any]]]

_REGISTRY: Dict[str, Tuple[DetectorSpec, DetectorFn]] = {}
_PENDING: Dict[str, Dict[str, Any]] = {}


def detector(spec: DetectorSpec) -> Callable[[DetectorFn], DetectorFn]:
    """Registers a detector. A new setup is one new decorated function - the
    engine, grouping, evidence and UI need no changes."""
    def register(fn: DetectorFn) -> DetectorFn:
        if spec.id in _REGISTRY:
            raise ValueError(f"Duplicate detector id {spec.id}")
        if spec.family not in FAMILIES:
            raise ValueError(f"Unknown family {spec.family} for {spec.id}")
        _REGISTRY[spec.id] = (spec, fn)
        return fn
    return register


def register_pending_definition(name: str, *, aliases: Tuple[str, ...] = (), note: str) -> None:
    """A named formation the library knows about but cannot detect until its
    rules are established. Listed in coverage as not supported."""
    _PENDING[name] = {"name": name, "aliases": list(aliases), "status": NEEDS_DEFINITION, "note": note}


def registered() -> List[Tuple[DetectorSpec, DetectorFn]]:
    return list(_REGISTRY.values())


def spec_for(detector_id: str) -> Optional[DetectorSpec]:
    entry = _REGISTRY.get(detector_id)
    return entry[0] if entry else None


def applicable(timeframe: str, detector_ids: Optional[List[str]] = None) -> List[Tuple[DetectorSpec, DetectorFn]]:
    wanted = set(detector_ids) if detector_ids else None
    return [
        (spec, fn) for spec, fn in _REGISTRY.values()
        if timeframe in spec.timeframes and (wanted is None or spec.id in wanted)
    ]


def pending_definitions() -> List[Dict[str, Any]]:
    return [dict(entry) for entry in _PENDING.values()]
