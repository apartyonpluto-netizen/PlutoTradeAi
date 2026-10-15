from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import auth
import app as pluto_app
import market_intel
import suggestion_ledger
from market_intel import edgar, mentions, scoring, support, unusual
from market_intel.support import HttpError, SecTransport
from watchlist import add_stock

FORM4_XML = b"""<?xml version="1.0"?>
<ownershipDocument xmlns="http://www.sec.gov/edgar/document/ownership">
  <nonDerivativeTransaction>
    <transactionDate><value>2026-10-01</value></transactionDate>
    <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
    <transactionAmounts>
      <transactionShares><value>1000</value></transactionShares>
      <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
    </transactionAmounts>
  </nonDerivativeTransaction>
  <nonDerivativeTransaction>
    <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
    <transactionAmounts><transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts>
  </nonDerivativeTransaction>
  <nonDerivativeTransaction>
    <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
    <transactionAmounts><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
  </nonDerivativeTransaction>
  <derivativeTransaction>
    <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
    <transactionAmounts><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
  </derivativeTransaction>
</ownershipDocument>
"""

INFO_TABLE = """<?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>APPLE HOSPITALITY REIT INC</nameOfIssuer>
    <shrsOrPrnAmt><sshPrnamt>99999</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>OTHER CO</nameOfIssuer>
    <shrsOrPrnAmt><sshPrnamt>50</sshPrnamt><sshPrnamtType>PRN</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
</informationTable>
"""


class _Response:
    def __init__(self, status, body):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


def _quiet_limiter():
    support.configure_clock(time.monotonic, lambda _seconds: None)


def test_sec_user_agent_includes_the_configured_contact_and_requests_are_spaced():
    sleeps = []
    now = [0.0]

    def clock():
        return now[0]

    def sleeper(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    support.configure_clock(clock, sleeper)
    support.acquire_sec_slot()
    support.acquire_sec_slot()
    assert sleeps == [support.SEC_MIN_INTERVAL_SECONDS]
    assert support.SEC_MIN_INTERVAL_SECONDS >= 0.1

    seen = {}

    def getter(url, headers=None, timeout=None):
        seen.update(headers or {})
        return _Response(200, b"{}")

    SecTransport("ops@example.com", getter=getter).get("https://www.sec.gov/files/company_tickers.json")
    assert seen["User-Agent"] == "PlutoTrade research ops@example.com"
    assert timeout_was_passed(getter)


def timeout_was_passed(getter):
    recorded = {}

    def wrapped(url, headers=None, timeout=None):
        recorded["timeout"] = timeout
        return _Response(200, b"{}")

    SecTransport("ops@example.com", getter=wrapped).get("https://www.sec.gov/files/company_tickers.json")
    return recorded["timeout"] == support.HTTP_TIMEOUT


def test_a_contact_email_with_whitespace_or_no_at_sign_is_not_used(monkeypatch):
    monkeypatch.setenv("PLUTO_SEC_CONTACT_EMAIL", "not-an-email")
    assert support.contact_email() == ""
    monkeypatch.setenv("PLUTO_SEC_CONTACT_EMAIL", "bad\nemail@example.com")
    assert support.contact_email() == ""
    monkeypatch.setenv("PLUTO_SEC_CONTACT_EMAIL", "ops@example.com")
    assert support.contact_email() == "ops@example.com"


def test_edgar_is_not_called_without_a_contact_email(monkeypatch):
    monkeypatch.delenv("PLUTO_SEC_CONTACT_EMAIL", raising=False)
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    called = []
    monkeypatch.setattr(edgar, "SecTransport", lambda email, getter=None: called.append(email) or SecTransport(email, getter=getter))
    assert edgar.form4_signal("AAPL")["reason"] == "sec_contact_email_unconfigured"
    assert edgar.holdings_signal("AAPL")["reason"] == "sec_contact_email_unconfigured"
    assert called == []


def test_a_timeout_marks_sec_down_and_the_next_call_does_not_hit_the_network():
    calls = {"n": 0}

    def getter(url, headers=None, timeout=None):
        calls["n"] += 1
        raise TimeoutError("timed out")

    _quiet_limiter()
    transport = SecTransport("ops@example.com", getter=getter)
    with pytest.raises(HttpError):
        transport.get("https://data.sec.gov/submissions/CIK0000320193.json")
    assert support.sec_is_down()
    with pytest.raises(HttpError) as caught:
        transport.get("https://data.sec.gov/submissions/CIK0000320193.json")
    assert caught.value.reason == "source_unavailable"
    assert calls["n"] == 1


def test_open_market_buys_ignore_sales_grants_and_derivatives():
    buys = edgar.open_market_buys(FORM4_XML)
    assert len(buys) == 1
    assert buys[0]["code"] == "P"
    assert buys[0]["shares"] == "1000"


def test_form4_vote_uses_only_a_recent_open_market_buy(monkeypatch):
    _quiet_limiter()
    today = date(2026, 10, 15)
    bodies = {
        "https://www.sec.gov/files/company_tickers.json": json.dumps(
            {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        ),
        "https://data.sec.gov/submissions/CIK0000320193.json": json.dumps(
            {
                "filings": {
                    "recent": {
                        "form": ["4", "8-K", "4"],
                        "filingDate": ["2026-10-01", "2026-10-01", "2026-01-01"],
                        "accessionNumber": ["0000320193-26-000010", "0000320193-26-000011", "0000320193-26-000001"],
                        "primaryDocument": ["xslF345X05/own.xml", "skip.htm", "old.xml"],
                    }
                }
            }
        ),
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/xslF345X05/own.xml": FORM4_XML.decode("utf-8"),
    }

    def getter(url, headers=None, timeout=None):
        if url not in bodies:
            return _Response(404, b"missing")
        return _Response(200, bodies[url])

    monkeypatch.setattr(edgar, "SecTransport", lambda email: SecTransport(email, getter=getter))
    monkeypatch.setenv("PLUTO_SEC_CONTACT_EMAIL", "ops@example.com")
    vote = edgar.form4_signal("AAPL", today=today)
    assert vote["present"] is True
    assert vote["direction"] == "bullish"
    assert vote["magnitude"] == 3
    again = edgar.form4_signal("AAPL", today=today)
    assert again["direction"] == "bullish"


def test_13f_compares_the_two_latest_quarters_and_ignores_other_issuers(monkeypatch):
    _quiet_limiter()
    current = "https://www.sec.gov/Archives/edgar/data/1067983/000106798326000001/infotable.xml"
    prior = "https://www.sec.gov/Archives/edgar/data/1067983/000106798326000002/infotable.xml"
    bodies = {
        "https://www.sec.gov/files/company_tickers.json": json.dumps(
            {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}
        ),
        current: INFO_TABLE.format(shares=1500),
        prior: INFO_TABLE.format(shares=1000),
    }

    def getter(url, headers=None, timeout=None):
        if url.startswith("https://efts.sec.gov/LATEST/search-index"):
            assert "Apple" in url
            return _Response(
                200,
                json.dumps(
                    {
                        "hits": {
                            "hits": [
                                {"_source": {"ciks": ["0001067983"], "period_ending": "2026-06-30", "adsh": "0001067983-26-000001"}},
                                {"_source": {"ciks": ["0001067983"], "period_ending": "2026-03-31", "adsh": "0001067983-26-000002"}},
                            ]
                        }
                    }
                ),
            )
        if url.endswith("index.json"):
            return _Response(200, json.dumps({"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "infotable.xml"}]}}))
        if url not in bodies:
            return _Response(404, b"missing")
        return _Response(200, bodies[url])

    monkeypatch.setattr(edgar, "SecTransport", lambda email: SecTransport(email, getter=getter))
    monkeypatch.setenv("PLUTO_SEC_CONTACT_EMAIL", "ops@example.com")
    vote = edgar.holdings_signal("AAPL", today=date(2026, 10, 15))
    assert vote["direction"] == "bullish"
    assert vote["magnitude"] == 3
    assert edgar.shares_matching(INFO_TABLE.format(shares=1500).encode(), "Apple Inc.") == 1500
    quiet = edgar.compare_holdings(1000, 1000)
    assert quiet["direction"] == "neutral"
    down = edgar.compare_holdings(900, 1000)
    assert down["direction"] == "bearish"


def test_unusual_volume_and_price_use_the_trailing_baseline():
    closes = [100.0] * 21
    volumes = [1000.0] * 21
    closes[-1] = 106.0
    volumes[-1] = 4000.0
    frame = pd.DataFrame({"Close": closes, "Volume": volumes})
    vote = unusual.from_bars(frame)
    assert vote["present"] is True
    assert vote["direction"] == "bullish"
    assert vote["magnitude"] == 3

    quiet = unusual.from_bars(pd.DataFrame({"Close": [100.0] * 21, "Volume": [1000.0] * 21}))
    assert quiet["direction"] == "neutral"
    assert quiet["magnitude"] == 0
    assert unusual.from_bars(pd.DataFrame({"Close": [100.0] * 5, "Volume": [1.0] * 5}))["reason"] == "insufficient_history"


def test_unusual_volume_failure_is_a_missing_vote(monkeypatch):
    monkeypatch.setattr(unusual, "cached", lambda key, ttl, producer: (_ for _ in ()).throw(RuntimeError("alpaca down")))
    import integrations.alpaca_data as alpaca_data

    monkeypatch.setattr(alpaca_data, "is_configured", lambda: True)
    vote = unusual.signal("AAPL")
    assert vote["present"] is False
    assert vote["reason"] == "source_unavailable"


def test_mention_velocity_is_disabled_without_a_token_and_pluggable_when_present(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("X_API_BEARER_TOKEN", raising=False)
    mentions.set_provider(None)

    def getter(*args, **kwargs):
        raise AssertionError("X was called without a token")

    monkeypatch.setattr("requests.get", getter)
    off = mentions.signal("AAPL")
    assert off["reason"] == "disabled"
    assert off["present"] is False

    class Fake:
        def enabled(self):
            return True

        def hourly_counts(self, ticker):
            assert ticker == "NVDA"
            return [2, 2, 2, 2, 2, 2, 30]

    mentions.set_provider(Fake())
    spike = mentions.signal("NVDA")
    assert spike["direction"] == "attention"
    assert spike["magnitude"] == 2
    quiet = mentions.from_hourly_counts([4, 4, 4, 5])
    assert quiet["direction"] == "neutral"


def test_x_provider_sends_the_bearer_token_and_does_not_put_it_on_the_vote():
    seen = {}

    def getter(url, params=None, headers=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["params"] = params
        return _Response(200, json.dumps({"data": [{"tweet_count": 1}, {"tweet_count": 20}]}))

    provider = mentions.XRecentCountProvider("super-secret-token", getter=getter)
    counts = provider.hourly_counts("AAPL")
    assert counts == [1, 20]
    assert seen["url"] == mentions.COUNTS_URL
    assert seen["headers"]["Authorization"] == "Bearer super-secret-token"
    assert "super-secret-token" not in json.dumps(mentions.from_hourly_counts(counts))
    assert "cashtag" not in seen["params"]["query"]
    assert seen["params"]["query"].startswith("$AAPL")


def test_votes_adjust_confidence_but_cannot_qualify_a_failing_score(monkeypatch):
    bullish = {"present": True, "direction": "bullish", "magnitude": 3, "detail": "buy", "reason": None}
    bearish = {"present": True, "direction": "bearish", "magnitude": 3, "detail": "down", "reason": None}
    attention = {"present": True, "direction": "attention", "magnitude": 2, "detail": "talk", "reason": None}

    def use(**signals):
        monkeypatch.setattr(edgar, "form4_signal", lambda ticker: {**signals["form4"], "signal": "form4_insider_buy"})
        monkeypatch.setattr(edgar, "holdings_signal", lambda ticker: {**signals["holdings"], "signal": "form13f_holdings"})
        monkeypatch.setattr(unusual, "signal", lambda ticker: {**signals["unusual"], "signal": "unusual_activity"})
        monkeypatch.setattr(mentions, "signal", lambda ticker: {**signals["mentions"], "signal": "x_mention_velocity"})

    use(form4=bullish, holdings=bullish, unusual=bullish, mentions=attention)
    blocked = market_intel.score("AAPL", recommendation="CALL", base_confidence=50, threshold=55)
    assert blocked["adjusted_confidence"] < 55
    assert blocked["recommendation"] == "CALL"
    assert blocked["places_orders"] is False
    assert blocked["adjustment"] >= 0

    nudged = market_intel.score("AAPL", recommendation="CALL", base_confidence=70, threshold=55)
    assert nudged["adjusted_confidence"] == 78
    assert nudged["base_confidence"] == 70

    use(form4=bullish, holdings=bearish, unusual=bearish, mentions={"present": False, "direction": "neutral", "magnitude": 0, "detail": "", "reason": "disabled"})
    lowered = market_intel.score("AAPL", recommendation="CALL", base_confidence=70, threshold=55)
    assert lowered["adjusted_confidence"] < 70
    put_side = market_intel.score("AAPL", recommendation="PUT", base_confidence=70, threshold=55)
    assert put_side["adjusted_confidence"] > 70
    waiting = market_intel.score("AAPL", recommendation="WAIT", base_confidence=88, threshold=55)
    assert waiting["adjusted_confidence"] == 88


def test_one_dead_source_leaves_the_other_votes_in_place(monkeypatch):
    def boom(ticker):
        raise RuntimeError("edgar down")

    monkeypatch.setattr(edgar, "form4_signal", boom)
    monkeypatch.setattr(
        edgar,
        "holdings_signal",
        lambda ticker: {"signal": "form13f_holdings", "present": True, "direction": "bullish", "magnitude": 3, "detail": "up", "reason": None},
    )
    monkeypatch.setattr(unusual, "signal", lambda ticker: {"signal": "unusual_activity", "present": False, "reason": "source_unavailable", "direction": "neutral", "magnitude": 0})
    monkeypatch.setattr(mentions, "signal", lambda ticker: {"signal": "x_mention_velocity", "present": False, "reason": "disabled", "direction": "neutral", "magnitude": 0})
    result = market_intel.score("MSFT", recommendation="CALL", base_confidence=70, threshold=55)
    by_signal = {vote["signal"]: vote for vote in result["votes"]}
    assert by_signal["form4_insider_buy"]["present"] is False
    assert by_signal["form4_insider_buy"]["reason"] == "source_unavailable"
    assert by_signal["form13f_holdings"]["points"] == 3
    assert by_signal["x_mention_velocity"]["reason"] == "disabled"
    assert result["adjusted_confidence"] == 73
    assert len(result["votes"]) == 4


def test_signal_hit_rates_reuse_the_ledger_grade(user_id):
    suggestion_ledger.append(
        {
            "user_id": user_id,
            "ticker": "NVDA",
            "recommendation": "CALL",
            "confidence": 78,
            "decision": "skipped",
            "trading_day": "2024-01-02",
            "entry": 100,
            "stop": 90,
            "target": 120,
            "quantity": 1,
            "grade_on": "2024-01-02",
            "market_intel": {
                "base_confidence": 70,
                "adjusted_confidence": 78,
                "adjustment": 8,
                "places_orders": False,
                "votes": [
                    {"signal": "form4_insider_buy", "present": True, "points": 3, "direction": "bullish", "reason": None},
                    {"signal": "form13f_holdings", "present": True, "points": -3, "direction": "bearish", "reason": None},
                    {"signal": "unusual_activity", "present": False, "points": 0, "reason": "source_unavailable"},
                    {"signal": "x_mention_velocity", "present": False, "points": 0, "reason": "disabled"},
                ],
            },
        }
    )
    bars = [
        {"date": "2024-01-02", "open": 100, "high": 101, "low": 99, "close": 100},
        {"date": "2024-01-03", "open": 100, "high": 130, "low": 99, "close": 125},
        {"date": "2024-01-04", "open": 125, "high": 126, "low": 124, "close": 125},
        {"date": "2024-01-05", "open": 125, "high": 126, "low": 124, "close": 125},
        {"date": "2024-01-08", "open": 125, "high": 126, "low": 124, "close": 125},
        {"date": "2024-01-09", "open": 125, "high": 126, "low": 124, "close": 125},
    ]
    graded = suggestion_ledger.grade_due(lambda ticker: bars, now=suggestion_ledger._now().replace(year=2024, month=1, day=10))
    assert graded[0]["market_intel"]["votes"][0]["hit"] is True
    assert graded[0]["market_intel"]["votes"][1]["hit"] is False
    report = market_intel.summary(user_id)
    assert report["paper_only"] is True
    assert report["places_orders"] is False
    assert report["grade_weekdays"] == 5
    by_signal = {item["signal"]: item for item in report["signals"]}
    assert by_signal["form4_insider_buy"]["graded"] == 1
    assert by_signal["form4_insider_buy"]["hit_rate"] == 1.0
    assert by_signal["form13f_holdings"]["hits"] == 0
    assert by_signal["form13f_holdings"]["hit_rate"] == 0.0
    assert by_signal["unusual_activity"]["missing"] == 1
    assert by_signal["unusual_activity"]["graded"] == 0
    assert by_signal["x_mention_velocity"]["disabled"] == 1


def test_the_scan_logs_intel_beside_the_suggestion(user_id):
    import test_research_log_scan_integration as scan_setup

    intel = {
        "base_confidence": 20,
        "adjusted_confidence": 20,
        "adjustment": 0,
        "places_orders": False,
        "votes": [{"signal": "form4_insider_buy", "present": False, "points": 0, "reason": "sec_contact_email_unconfigured"}],
    }
    scan_setup._run_scan_and_capture(
        user_id,
        [{
            "ticker": "AAPL",
            "recommendation": "CALL",
            "confidence": 20,
            "ideal_entry": 100.0,
            "stop": 50.0,
            "target": 110.0,
            "strategy": "Trend",
            "market_context": {"current_price": 100},
            "market_intel": intel,
        }],
    )
    rows = [row for row in suggestion_ledger.list_rows(user_id) if row.get("ticker") == "AAPL"]
    assert rows
    assert rows[-1]["market_intel"]["votes"][0]["signal"] == "form4_insider_buy"
    assert rows[-1]["confidence"] == 20


def test_a_below_floor_setup_never_asks_for_a_vote_and_a_passing_one_keeps_the_nudge(user_id):
    add_stock(user_id, {"ticker": "AAPL"})
    calls = []

    def _score(ticker, **kwargs):
        calls.append((ticker, kwargs))
        base = kwargs["base_confidence"]
        return {
            "base_confidence": base,
            "adjusted_confidence": base + 3,
            "adjustment": 3,
            "threshold": kwargs["threshold"],
            "recommendation": kwargs["recommendation"],
            "places_orders": False,
            "votes": [],
        }

    strategy = {"strategy_confidence": 40, "recommendation": "CALL", "best_strategy": "Trend", "why_this_strategy_fits": "weak"}
    chart = {"breakout_level": 110.0, "breakdown_level": 90.0, "major_support_levels": [90.0], "major_resistance_levels": [110.0]}
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(market_intel, "score", _score)
        patcher.setattr(pluto_app, "get_market_data", lambda **kw: ([], [], ""))
        patcher.setattr(pluto_app, "build_extended_hours_intelligence", lambda ticker: {})
        patcher.setattr(pluto_app, "get_strategy_data_for_ticker", lambda ticker, **kw: strategy)
        patcher.setattr(pluto_app, "get_chart_levels_for_ticker", lambda ticker, **kw: chart)
        patcher.setattr(pluto_app, "get_options_data_for_ticker", lambda ticker, **kw: {})
        patcher.setattr(pluto_app, "_current_user_id", lambda: user_id)
        context = pluto_app._build_page_context(include_options=False)
    assert context["upcoming_opportunities"] == []
    assert calls == []

    strategy = {**strategy, "strategy_confidence": 80}
    calls.clear()
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(market_intel, "score", _score)
        patcher.setattr(pluto_app, "get_market_data", lambda **kw: ([], [], ""))
        patcher.setattr(pluto_app, "build_extended_hours_intelligence", lambda ticker: {})
        patcher.setattr(pluto_app, "get_strategy_data_for_ticker", lambda ticker, **kw: strategy)
        patcher.setattr(pluto_app, "get_chart_levels_for_ticker", lambda ticker, **kw: chart)
        patcher.setattr(pluto_app, "get_options_data_for_ticker", lambda ticker, **kw: {})
        patcher.setattr(pluto_app, "_current_user_id", lambda: user_id)
        context = pluto_app._build_page_context(include_options=False)
    opportunity = context["upcoming_opportunities"][0]
    assert opportunity["confidence"] == 83
    assert opportunity["recommendation"] == "CALL"
    assert opportunity["market_intel"]["places_orders"] is False
    assert calls and calls[0][1]["base_confidence"] == 80


def test_summary_endpoint_and_ledger_page_are_readable(user_id):
    registered = auth.register_user(f"intel-{user_id}", "TestPassword123!")
    auth.approve_user(registered["id"])
    suggestion_ledger.append(
        {
            "user_id": registered["id"],
            "ticker": "AAPL",
            "confidence": 70,
            "recommendation": "CALL",
            "decision": "skipped",
            "market_intel": {
                "adjustment": 3,
                "votes": [{"signal": "unusual_activity", "present": True, "points": 3, "reason": None}],
            },
        }
    )
    with pluto_app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = registered["id"]
        response = client.get("/api/market-intel/summary")
        page = client.get("/suggestion-ledger")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["places_orders"] is False
    assert payload["data"]["paper_only"] is True
    signals = {item["signal"]: item for item in payload["data"]["signals"]}
    assert signals["unusual_activity"]["fires"] == 1
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Market intel votes" in html
    assert "SEC Form 4 insider open-market buys" in html
    assert "+3" in html


def test_market_intel_code_does_not_submit_orders():
    banned = ("place_order", "submit_order", "_submit_and_protect_entry", "create_order")
    root = Path(market_intel.__file__).resolve().parent
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, f"{path.name} mentions {word}"
    assert scoring.PLACES_ORDERS is False
    assert pluto_app.OVERNIGHT_MIN_CONFIDENCE == 55
