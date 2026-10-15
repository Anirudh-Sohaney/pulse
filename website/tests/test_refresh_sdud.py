"""The live SDUD archive keeps a continuous target history across releases."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import refresh_sdud, signal_store
from backend.app import create_app
from backend.config import settings


def _catalog(years):
    return [{"title": f"State Drug Utilization Data {year}",
             "identifier": f"00000000-0000-4000-8000-{year:012d}",
             "modified": "2026-07-13T00:00:00+00:00"} for year in years]


def test_release_selection_requires_continuous_unique_years():
    releases = refresh_sdud.select_releases(_catalog([2023, 2024, 2025]),
                                             today_year=2026)
    assert [release.year for release in releases] == [2023, 2024, 2025]
    with pytest.raises(ValueError, match="continuous"):
        refresh_sdud.select_releases(_catalog([2023, 2025]), today_year=2026)
    with pytest.raises(ValueError, match="continuous"):
        refresh_sdud.select_releases(_catalog([2023, 2023]), today_year=2026)


def test_refresh_archives_unchanged_years_and_keeps_newest_period(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    releases = refresh_sdud.select_releases(_catalog([2023, 2024, 2025]),
                                             today_year=2026)
    monkeypatch.setattr(refresh_sdud, "discover_releases", lambda: releases)
    fetched = []

    def rows(release):
        fetched.append(release.year)
        return [{"state": "AR", "year": release.year, "quarter": 4,
                 "utilization_type": "FFSU", "ndc": "00000000001",
                 "product_name": f"DRUG {release.year}",
                 "suppression_used": "false", "number_of_prescriptions": "12"},
                {"state": "AR", "year": release.year, "quarter": 4,
                 "utilization_type": "MCOU", "ndc": "00000000002",
                 "product_name": f"DRUG {release.year}",
                 "suppression_used": "true", "number_of_prescriptions": None}]

    monkeypatch.setattr(refresh_sdud, "fetch_rows", rows)
    first = refresh_sdud.refresh_sdud()
    assert first["archived_years"] == [2023, 2024, 2025]
    assert first["rows_written"] == 6
    assert fetched == [2023, 2024, 2025]
    assert signal_store.latest_sdud_snapshot(limit=1)["period"] == "2025-Q4"
    archive = signal_store.sdud_archive_summary()
    assert archive["newest_period"] == "2025-Q4"
    assert [row["year"] for row in archive["releases"]] == [2025, 2024, 2023]
    assert all(row["quarters"] == [4] and row["suppressed_rows"] == 1
               for row in archive["releases"])
    response = TestClient(create_app()).get("/api/v1/signals/sources/sdud/archive")
    assert response.status_code == 200
    assert response.json()["newest_period"] == "2025-Q4"

    second = refresh_sdud.refresh_sdud()
    assert second["rows_written"] == 0
    assert fetched == [2023, 2024, 2025]

    revised = refresh_sdud.SourceRelease(2024, releases[1].dataset_id,
                                          datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(refresh_sdud, "discover_releases",
                        lambda: [releases[0], revised, releases[2]])
    third = refresh_sdud.refresh_sdud()
    assert third["rows_written"] == 0  # Same content, new catalog timestamp.
    assert fetched == [2023, 2024, 2025, 2024]
    assert signal_store.latest_sdud_snapshot(limit=1)["period"] == "2025-Q4"
    with signal_store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sdud_snapshots").fetchone()[0] == 3
