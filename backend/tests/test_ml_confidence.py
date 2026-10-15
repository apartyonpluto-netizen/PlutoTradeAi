from __future__ import annotations

import pandas as pd

import ml_confidence


def _frame(rows: int) -> pd.DataFrame:
    index = pd.bdate_range("2022-01-03", periods=rows)
    close = pd.Series(range(100, 100 + rows), index=index, dtype=float)
    return pd.DataFrame({
        "Open": close - 1,
        "High": close + 1,
        "Low": close - 2,
        "Close": close,
        "Volume": 1000.0,
    })


def test_features_ignore_bars_after_the_signal():
    frame = _frame(40)
    before = ml_confidence.features_as_of(frame, 25)
    mutated = frame.copy()
    mutated.loc[mutated.index[26]:, "Close"] = 1.0
    mutated.loc[mutated.index[26]:, "Open"] = 1.0
    mutated.loc[mutated.index[26]:, "High"] = 1.0
    mutated.loc[mutated.index[26]:, "Low"] = 1.0
    assert ml_confidence.features_as_of(mutated, 25) == before


def test_split_stays_chronological_when_the_input_is_reversed():
    rows = [{"entry_date": f"2024-01-{day:02d}", "net_return_percent": 1} for day in range(1, 11)]
    train, test = ml_confidence.chronological_split(list(reversed(rows)))
    assert train and test
    assert max(row["entry_date"] for row in train) < min(row["entry_date"] for row in test)


def test_same_day_sample_is_not_admitted():
    rows = [
        {
            "entry_date": "2024-01-02",
            "net_return_percent": 1 if index % 2 == 0 else -1,
            "confidence": 70,
            "direction_long": 1,
            "features": {"day_change_percent": 1},
        }
        for index in range(12)
    ]
    meta = ml_confidence.train_from_rows(rows)
    assert meta["admitted"] is False
    assert "strictly newer" in meta["reason"]
