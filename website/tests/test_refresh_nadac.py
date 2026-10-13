"""NADAC refresh uses the latest snapshot already in effect."""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from backend import refresh_nadac


def test_fetch_latest_snapshot_skips_future_dated_file(monkeypatch):
    today = datetime.now(timezone.utc).date()
    prior = (today - timedelta(days=6)).isoformat()
    calls = []

    def fake_fetch(url):
        params = parse_qs(urlsplit(url).query)
        calls.append(params)
        if params["conditions[0][operator]"] == ["<="]:
            return {"count": 1, "results": [{"as_of_date": prior}]}
        assert params["conditions[0][operator]"] == ["="]
        assert params["conditions[0][value]"] == [prior]
        return {"count": 1, "results": [{"as_of_date": prior, "ndc": "1"}]}

    monkeypatch.setattr(refresh_nadac, "_fetch", fake_fetch)
    as_of, rows, source_url = refresh_nadac.fetch_latest_snapshot()
    assert as_of == prior
    assert len(rows) == 1
    assert calls[0]["conditions[0][value]"] == [today.isoformat()]
    assert calls[0]["sorts[0][order]"] == ["desc"]
    assert "as_of_date" in source_url


def test_fetch_latest_snapshot_rejects_future_response(monkeypatch):
    future = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    monkeypatch.setattr(refresh_nadac, "_fetch", lambda _: {
        "count": 1, "results": [{"as_of_date": future}],
    })
    with pytest.raises(ValueError, match="future snapshot date"):
        refresh_nadac.fetch_latest_snapshot()
