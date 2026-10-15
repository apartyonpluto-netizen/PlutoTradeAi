"""Form 4 insider buys and 13F quarter-over-quarter holding changes.

Both reads use the free EDGAR hosts. A missing contact email, a timeout,
or a bad document becomes a missing vote. Sales, grants, and option
exercises are not insider-buy votes: only transaction code P with
acquired code A counts.
"""

from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

_ticker_lock = threading.Lock()

from market_intel.support import HttpError, SecTransport, cached, contact_email, missing, observation

FORM4_SIGNAL = "form4_insider_buy"
HOLDINGS_SIGNAL = "form13f_holdings"
FORM4_LOOKBACK_DAYS = 30
FORM4_MAX_FILINGS = 3
HOLDINGS_MAX_PER_QUARTER = 2
HOLDINGS_CHANGE = 0.05
MAX_DOCUMENT_BYTES = 2_000_000
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_NAME_STOP = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED",
    "PLC", "LLC", "LP", "THE", "GROUP", "HOLDINGS", "HOLDING", "CLASS", "COM", "COMMON",
}


def form4_signal(ticker: str, *, transport: Optional[SecTransport] = None, today: Optional[date] = None) -> Dict[str, Any]:
    symbol = _symbol(ticker)
    if not symbol:
        return missing(FORM4_SIGNAL, "invalid_ticker")
    email = contact_email()
    if transport is None and not email:
        return missing(FORM4_SIGNAL, "sec_contact_email_unconfigured", "SEC contact email is not configured")
    day = today or date.today()
    client = transport or SecTransport(email)

    use_cache = transport is None and today is None

    def produce() -> Dict[str, Any]:
        return _form4(symbol, client, day, use_cache=use_cache)

    if not use_cache:
        try:
            return _vote(FORM4_SIGNAL, produce())
        except Exception:
            return missing(FORM4_SIGNAL, "source_unavailable")
    try:
        return _vote(FORM4_SIGNAL, cached(f"form4:v1:{symbol}:{day.isoformat()}", 2 * 60 * 60, produce))
    except Exception:
        return missing(FORM4_SIGNAL, "source_unavailable")


def holdings_signal(ticker: str, *, transport: Optional[SecTransport] = None, today: Optional[date] = None) -> Dict[str, Any]:
    symbol = _symbol(ticker)
    if not symbol:
        return missing(HOLDINGS_SIGNAL, "invalid_ticker")
    email = contact_email()
    if transport is None and not email:
        return missing(HOLDINGS_SIGNAL, "sec_contact_email_unconfigured", "SEC contact email is not configured")
    day = today or date.today()
    client = transport or SecTransport(email)

    use_cache = transport is None and today is None

    def produce() -> Dict[str, Any]:
        return _holdings(symbol, client, day, use_cache=use_cache)

    if not use_cache:
        try:
            return _vote(HOLDINGS_SIGNAL, produce())
        except Exception:
            return missing(HOLDINGS_SIGNAL, "source_unavailable")
    try:
        return _vote(HOLDINGS_SIGNAL, cached(f"13f:v1:{symbol}:{day.isoformat()}", 12 * 60 * 60, produce))
    except Exception:
        return missing(HOLDINGS_SIGNAL, "source_unavailable")


def open_market_buys(body: bytes) -> List[Dict[str, Any]]:
    """Transaction code P and acquired code A. Grants and sales are ignored."""
    root = _xml(body)
    if root is None:
        return []
    buys = []
    for node in root.iter():
        if _local(node.tag) != "nonDerivativeTransaction":
            continue
        code = _value(node, "transactionCode").upper()
        acquired = _value(node, "transactionAcquiredDisposedCode").upper()
        if code == "P" and acquired == "A":
            buys.append(
                {
                    "code": code,
                    "acquired": acquired,
                    "shares": _value(node, "transactionShares"),
                    "date": _value(node, "transactionDate"),
                }
            )
    return buys


def shares_matching(body: bytes, company_title: str) -> Optional[int]:
    """Sum share counts on information-table rows for this issuer. None if unreadable."""
    root = _xml(body)
    if root is None:
        return None
    total = 0
    matched = False
    for node in root.iter():
        if _local(node.tag) != "infoTable":
            continue
        issuer = _value(node, "nameOfIssuer")
        if not names_match(company_title, issuer):
            continue
        kind = _value(node, "sshPrnamtType").upper()
        if kind and kind != "SH":
            continue
        shares = _int(_value(node, "sshPrnamt"))
        if shares is None:
            continue
        matched = True
        total += shares
    if not matched:
        return 0
    return total


def names_match(company_title: str, issuer: str) -> bool:
    wanted = _tokens(company_title)
    have = _tokens(issuer)
    if not wanted or not have:
        return False
    if len(wanted) == 1:
        return have == wanted
    return all(token in have for token in wanted)


def compare_holdings(current: int, prior: int) -> Dict[str, Any]:
    """Quarter-over-quarter change. Both sides must have matched shares."""
    if current <= 0 or prior <= 0:
        return observation(
            HOLDINGS_SIGNAL,
            direction="neutral",
            magnitude=0,
            detail="not enough matched 13F shares to compare the last two quarters",
        )
    ratio = (current - prior) / prior
    if ratio >= HOLDINGS_CHANGE:
        direction = "bullish"
        magnitude = 3
    elif ratio <= -HOLDINGS_CHANGE:
        direction = "bearish"
        magnitude = 3
    else:
        direction = "neutral"
        magnitude = 0
    return observation(
        HOLDINGS_SIGNAL,
        direction=direction,
        magnitude=magnitude,
        detail=f"sampled 13F shares moved {ratio:+.1%} quarter over quarter",
    )


def _form4(symbol: str, transport: SecTransport, day: date, *, use_cache: bool = True) -> Dict[str, Any]:
    record = _ticker_record(symbol, transport, use_cache=use_cache)
    if not record:
        return missing(FORM4_SIGNAL, "unknown_ticker", f"no EDGAR company for {symbol}")
    submissions = transport.get_json(f"https://data.sec.gov/submissions/CIK{record['cik']}.json")
    recent = ((submissions.get("filings") or {}).get("recent") or {}) if isinstance(submissions, dict) else {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    cutoff = (day - timedelta(days=FORM4_LOOKBACK_DAYS)).isoformat()
    buys: List[Dict[str, Any]] = []
    checked = 0
    for form, filed, accession, document in zip(forms, dates, accessions, documents):
        if str(form).upper() != "4" or str(filed) < cutoff:
            continue
        if checked >= FORM4_MAX_FILINGS:
            break
        checked += 1
        url = _archive_url(record["cik"], str(accession), str(document))
        try:
            body = transport.get(url)
        except HttpError as exc:
            if exc.reason == "source_unavailable":
                raise
            continue
        buys.extend(open_market_buys(body))
    if buys:
        return observation(
            FORM4_SIGNAL,
            direction="bullish",
            magnitude=3,
            detail=f"{len(buys)} open-market insider buy{'s' if len(buys) != 1 else ''} filed since {cutoff}",
        )
    return observation(
        FORM4_SIGNAL,
        direction="neutral",
        magnitude=0,
        detail=f"no open-market insider buys filed since {cutoff}",
    )


def _holdings(symbol: str, transport: SecTransport, day: date, *, use_cache: bool = True) -> Dict[str, Any]:
    record = _ticker_record(symbol, transport, use_cache=use_cache)
    if not record or not record.get("title"):
        return missing(HOLDINGS_SIGNAL, "unknown_ticker", f"no EDGAR company for {symbol}")
    start = day - timedelta(days=280)
    query = urlencode(
        {
            "q": f"\"{record['title']}\"",
            "forms": "13F-HR",
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": day.isoformat(),
        }
    )
    payload = transport.get_json(f"https://efts.sec.gov/LATEST/search-index?{query}")
    hits = ((payload.get("hits") or {}).get("hits") or []) if isinstance(payload, dict) else []
    by_period: Dict[str, List[Dict[str, Any]]] = {}
    for hit in hits:
        source = hit.get("_source") if isinstance(hit, dict) else None
        if not isinstance(source, dict):
            continue
        period = str(source.get("period_ending") or "")[:10]
        accession = str(source.get("adsh") or "")
        ciks = source.get("ciks") or []
        if not period or not accession or not ciks:
            continue
        by_period.setdefault(period, []).append({"cik": str(ciks[0]), "accession": accession})
    periods = sorted(by_period, reverse=True)[:2]
    if len(periods) < 2:
        return observation(
            HOLDINGS_SIGNAL,
            direction="neutral",
            magnitude=0,
            detail="fewer than two 13F quarters were available to compare",
        )
    totals = []
    for period in periods:
        total = 0
        saw_table = False
        for filing in by_period[period][:HOLDINGS_MAX_PER_QUARTER]:
            shares = _filing_shares(transport, filing["cik"], filing["accession"], record["title"])
            if shares is None:
                continue
            saw_table = True
            total += shares
        if not saw_table:
            return observation(
                HOLDINGS_SIGNAL,
                direction="neutral",
                magnitude=0,
                detail="13F information tables did not include a readable share count",
            )
        totals.append(total)
    result = compare_holdings(totals[0], totals[1])
    result["detail"] = f"{periods[0]} vs {periods[1]}: {result['detail']}"
    return result


def _filing_shares(transport: SecTransport, cik: str, accession: str, title: str) -> Optional[int]:
    index_url = _archive_url(cik, accession, "index.json")
    try:
        payload = transport.get_json(index_url)
    except HttpError as exc:
        if exc.reason == "source_unavailable":
            raise
        return None
    raw_items = ((payload.get("directory") or {}).get("item") or []) if isinstance(payload, dict) else []
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    names = [str(item["name"]) for item in raw_items if isinstance(item, dict) and item.get("name")]
    chosen = _infotable_name(names)
    if not chosen:
        return None
    try:
        body = transport.get(_archive_url(cik, accession, chosen))
    except HttpError as exc:
        if exc.reason == "source_unavailable":
            raise
        return None
    return shares_matching(body, title)


def _infotable_name(names: List[str]) -> Optional[str]:
    lowered = [(name, name.lower()) for name in names]
    for name, low in lowered:
        if "infotable" in low and low.endswith(".xml"):
            return name
    for name, low in lowered:
        if low.endswith(".xml") and "primary" not in low:
            return name
    return None


def _ticker_record(symbol: str, transport: SecTransport, *, use_cache: bool) -> Optional[Dict[str, str]]:
    def produce() -> Dict[str, Dict[str, str]]:
        payload = transport.get_json(TICKER_MAP_URL)
        return _parse_ticker_map(payload)

    with _ticker_lock:
        try:
            mapping = cached("edgar:company-tickers:v1", 24 * 60 * 60, produce) if use_cache else produce()
        except HttpError:
            raise
        except Exception as exc:
            raise HttpError("source_unavailable") from exc
    if not isinstance(mapping, dict) or mapping.get("_failed"):
        return None
    row = mapping.get(symbol)
    return row if isinstance(row, dict) else None


def _vote(signal: str, value: Any) -> Dict[str, Any]:
    if isinstance(value, dict) and value.get("signal") and not value.get("_failed"):
        return value
    return missing(signal, "source_unavailable")


def _parse_ticker_map(payload: Any) -> Dict[str, Dict[str, str]]:
    rows = payload.values() if isinstance(payload, dict) else payload if isinstance(payload, list) else []
    mapping: Dict[str, Dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper()
        cik = str(row.get("cik_str") or "").strip()
        if not ticker or not cik.isdigit():
            continue
        mapping[ticker] = {"cik": cik.zfill(10), "title": str(row.get("title") or "").strip()}
    return mapping


def _archive_url(cik: str, accession: str, name: str) -> str:
    cik_path = str(int(cik))
    folder = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_path}/{folder}/{quote(name, safe='/')}"


def _xml(body: bytes) -> Optional[ET.Element]:
    if not body or len(body) > MAX_DOCUMENT_BYTES:
        return None
    try:
        return ET.fromstring(body)
    except ET.ParseError:
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _value(parent: ET.Element, name: str) -> str:
    for node in parent.iter():
        if _local(node.tag) != name:
            continue
        if node.text and node.text.strip():
            return node.text.strip()
        for child in node:
            if _local(child.tag) == "value" and child.text and child.text.strip():
                return child.text.strip()
    return ""


def _tokens(name: str) -> List[str]:
    cleaned = "".join(ch if ch.isalnum() else " " for ch in name.upper())
    return [part for part in cleaned.split() if part not in _NAME_STOP and len(part) > 1]


def _int(value: str) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _symbol(ticker: str) -> str:
    symbol = str(ticker or "").strip().upper()
    if not symbol or len(symbol) > 10:
        return ""
    if not all(ch.isalnum() or ch in ".-" for ch in symbol):
        return ""
    return symbol
