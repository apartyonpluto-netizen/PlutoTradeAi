from __future__ import annotations

import integrations.alpaca_data as alpaca_data


def test_snapshots_are_requested_in_batches(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    monkeypatch.setenv("PLUTO_SCANNER_SNAPSHOT_GAP_SECONDS", "0")
    monkeypatch.setattr(alpaca_data, "SNAPSHOT_BATCH_SIZE", 2)
    seen = []

    class Response:
        status_code = 200

        def __init__(self, symbols):
            self.symbols = symbols

        def json(self):
            return {symbol: {"latestTrade": {"p": 1}} for symbol in self.symbols}

    def fake_get(url, headers=None, params=None, timeout=None):
        symbols = params["symbols"].split(",")
        seen.append(symbols)
        return Response(symbols)

    monkeypatch.setattr(alpaca_data.requests, "get", fake_get)
    payload = alpaca_data.get_snapshots(["aaa", "BBB", "CCC"])
    assert seen == [["AAA", "BBB"], ["CCC"]]
    assert set(payload) == {"AAA", "BBB", "CCC"}


def test_a_bad_snapshot_batch_is_split_instead_of_dropped(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    monkeypatch.setenv("PLUTO_SCANNER_SNAPSHOT_GAP_SECONDS", "0")
    monkeypatch.setattr(alpaca_data, "SNAPSHOT_BATCH_SIZE", 2)

    class Response:
        def __init__(self, status, body):
            self.status_code = status
            self._body = body
            self.text = "bad"

        def json(self):
            return self._body

    def fake_get(url, headers=None, params=None, timeout=None):
        symbols = params["symbols"].split(",")
        if len(symbols) > 1:
            return Response(400, {})
        return Response(200, {symbols[0]: {"latestTrade": {"p": 1}}})

    monkeypatch.setattr(alpaca_data.requests, "get", fake_get)
    payload = alpaca_data.get_snapshots(["AAA", "BBB"])
    assert set(payload) == {"AAA", "BBB"}
