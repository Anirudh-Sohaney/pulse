"""Economic context keeps BLS series identities without the BLS API quota."""

import io

import pytest

from backend import refresh_bls, signal_store
from backend.config import settings


def test_fred_csv_refresh_records_month_end_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    csv_by_series = {
        "ARURN": "observation_date,ARURN\n2025-01-01,4.0\n2026-08-01,4.1\n",
        "CPIAUCNS": "observation_date,CPIAUCNS\n2025-01-01,320.1\n2026-09-01,325.2\n",
        "UNRATE": "observation_date,UNRATE\n2025-01-01,4.2\n2026-09-01,4.3\n",
    }

    def fake_open(request, timeout):
        series = next(name for name in csv_by_series if f"id={name}" in request.full_url)
        assert "cosd=2000-01-01" in request.full_url
        return io.BytesIO(csv_by_series[series].encode())

    monkeypatch.setattr(refresh_bls, "urlopen", fake_open)
    result = refresh_bls.refresh_bls()
    assert result["source"] == "fred_bls_series_csv_v1"
    assert result["rows_written"] >= 6
    with signal_store.connect() as db:
        rows = db.execute("""SELECT d.signal_id, d.source_name,
            r.observation_date, r.value, r.source_kind, r.source_url
            FROM signal_records r JOIN signal_definitions d ON d.id=r.signal_uid
            WHERE r.source_kind='fred_bls_series_csv_v1'
            ORDER BY d.signal_id, r.observation_date""").fetchall()
    assert len(rows) == 6
    assert all(row["source_name"] == "BLS via FRED" for row in rows)
    assert all(row["source_url"].startswith("https://fred.stlouisfed.org/series/")
               for row in rows)
    assert (rows[0]["signal_id"], rows[0]["observation_date"], rows[0]["value"]) == (
        "arkansas_unemployment_rate", "2025-01-31", 4.0)
    assert refresh_bls.refresh_bls()["status"] == "skipped"


def test_fred_response_requires_matching_series_column(monkeypatch):
    monkeypatch.setattr(refresh_bls, "urlopen", lambda request, timeout: io.BytesIO(
        b"observation_date,OTHER\n2026-01-01,4.0\n"))
    with pytest.raises(ValueError, match="omitted requested series"):
        refresh_bls.fetch_series("ARURN")


def test_fred_months_reject_future_and_missing_values():
    months = refresh_bls.parse_months([
        {"date": "2026-02-01", "value": "3.5"},
        {"date": "2026-03-01", "value": "."},
        {"date": "2099-01-01", "value": "3.0"},
    ])
    assert months == [("2026-02-28", 3.5, "published")]
