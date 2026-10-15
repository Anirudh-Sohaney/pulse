"""CMS historical revisions must update the evaluation source generation."""

import json

import pandas as pd
import pytest

from backend import refresh_cms_partd_source as cms
from backend.config import settings


@pytest.mark.parametrize("revised_claims,expected_written", [(12, 1), (10, 0)])
def test_prior_year_revision_is_checked_and_committed(
    tmp_path, monkeypatch, revised_claims, expected_written,
):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    source = tmp_path / "original.csv.gz"
    pd.DataFrame([
        {"year": 2023, "state": "arkansas", "drug_key": "Drug A", "demand_claims": 10},
        {"year": 2024, "state": "arkansas", "drug_key": "Drug A", "demand_claims": 20},
    ]).to_csv(source, index=False, compression="gzip")
    old_url = "https://data.cms.gov/old-2023.csv"
    revised_url = "https://data.cms.gov/revised-2023.csv"
    current_url = "https://data.cms.gov/current-2024.csv"
    manifest = {"years": [2023, 2024], "source_urls": [old_url, current_url],
                "latest_source_verified_url": current_url,
                "latest_source_verified_last_modified": "2026-05-01T00:00:00+00:00",
                "latest_source_last_modified": "2026-05-01T00:00:00+00:00"}
    monkeypatch.setattr(cms, "source_snapshot", lambda: (source, manifest.copy()))
    monkeypatch.setattr(cms, "discover_releases", lambda: {
        2023: revised_url, 2024: current_url})
    monkeypatch.setattr(cms, "revision_timestamp",
                        lambda url: "2026-05-01T00:00:00+00:00")
    calls = []

    def download(url, year):
        calls.append((url, year))
        return pd.DataFrame([{"year": year, "state": "arkansas",
                              "drug_key": "Drug A", "demand_claims": revised_claims}])

    monkeypatch.setattr(cms, "download_arkansas_year", download)
    result = cms.refresh_cms_partd_source()

    assert calls == [(revised_url, 2023)]
    assert result["rows_written"] == expected_written
    committed = json.loads(cms.runtime_manifest_path().read_text())
    assert committed["source_urls"] == [revised_url, current_url]
    frame = pd.read_csv(tmp_path / committed["source_file"])
    assert frame.loc[frame.year.eq(2023), "demand_claims"].iloc[0] == revised_claims
    assert frame.loc[frame.year.eq(2024), "demand_claims"].iloc[0] == 20
