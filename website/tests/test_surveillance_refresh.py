"""Source refreshes retain native periods and remain idempotent on replay."""

import hashlib
import json
from datetime import datetime, timezone

import pytest

from backend import (refresh_enforcement, refresh_fluview, refresh_nndss,
                     refresh_wastewater, signal_store)
from backend.config import settings


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    signal_store.initialize()
    return tmp_path


def test_fluview_keeps_source_week_and_publication_time(store, monkeypatch):
    def one_week(signal, geo_type, geo_value):
        return ([('2026-10-03', '2026-10-09T00:00:00+00:00', 1.25)],
                'https://example.org/fluview')

    monkeypatch.setattr(refresh_fluview, "fetch_series", one_week)
    assert refresh_fluview.refresh_fluview()["rows_written"] == 3
    assert refresh_fluview.refresh_fluview()["rows_written"] == 0
    with signal_store.connect() as db:
        rows = db.execute("""SELECT d.signal_id, d.geography_id, r.observation_date,
            r.source_timestamp FROM signal_records r JOIN signal_definitions d
            ON d.id=r.signal_uid WHERE r.source_kind=?""",
            (refresh_fluview.SOURCE_KIND,)).fetchall()
    assert len(rows) == 3
    assert {(row["signal_id"], row["geography_id"]) for row in rows} == {
        ("fluview_ili", "AR"), ("fluview_ili", "US"), ("fluview_wili", "US")}
    assert all(row["observation_date"] == "2026-10-03" and
               row["source_timestamp"] == "2026-10-09T00:00:00+00:00" for row in rows)


def test_nndss_skips_unpublished_cells_and_keeps_revision(store, monkeypatch):
    source = {"states": "Arkansas", "label": "Campylobacteriosis",
              "year": "2026", "week": "39", ":updated_at": "2026-10-07T19:38:31.143Z",
              "m1": "14", "m1_flag": "-", "m2_flag": "NC",
              "m3": "792", "m3_flag": "-", "m4": "680", "m4_flag": "-"}
    monkeypatch.setattr(refresh_nndss, "fetch_rows", lambda: ([source], "https://example.org/nndss"))
    assert refresh_nndss.refresh_nndss()["rows_written"] == 3
    assert refresh_nndss.refresh_nndss()["rows_written"] == 0
    with signal_store.connect() as db:
        rows = db.execute("""SELECT d.signal_id, r.observation_date, r.source_timestamp
            FROM signal_records r JOIN signal_definitions d ON d.id=r.signal_uid
            WHERE r.source_kind=?""", (refresh_nndss.SOURCE_KIND,)).fetchall()
    assert len(rows) == 3
    assert all("previous_52_week_max_cases" not in row["signal_id"] for row in rows)
    assert all(row["observation_date"] == "2026-10-03" and
               row["source_timestamp"] == "2026-10-07T19:38:31.143000+00:00"
               for row in rows)


def test_wastewater_mean_retains_reporting_site_count(store, monkeypatch):
    revision = int(datetime(2026, 10, 9, 15, 5, 54, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(refresh_wastewater, "_revision", lambda: revision)
    monkeypatch.setattr(refresh_wastewater, "_fetch", lambda arkansas: (
        [{"week_end": "2026-10-03", "pathogen_target": "SARS-CoV-2",
          "mean_wval": "1.92", "site_rows": "1" if arkansas else "18"}],
        "https://example.org/wval"))
    assert refresh_wastewater.refresh_wastewater()["rows_written"] == 2
    assert refresh_wastewater.refresh_wastewater()["rows_written"] == 0
    with signal_store.connect() as db:
        rows = db.execute("""SELECT d.geography_id, r.observation_date,
            r.source_timestamp, r.data_quality FROM signal_records r
            JOIN signal_definitions d ON d.id=r.signal_uid
            WHERE r.source_kind=? ORDER BY d.geography_id""",
            (refresh_wastewater.SOURCE_KIND,)).fetchall()
    assert len(rows) == 2
    assert rows[0]["geography_id"] == "AR" and "contributing_site_rows=1" in rows[0]["data_quality"]
    assert rows[1]["geography_id"] == "US" and "contributing_site_rows=18" in rows[1]["data_quality"]
    assert all(row["observation_date"] == "2026-10-03" and
               row["source_timestamp"] == "2026-10-09T15:05:54+00:00" for row in rows)


def test_enforcement_publishes_complete_window_class_counts(store, monkeypatch):
    reports = [
        {"recall_number": "D-1", "report_date": "20260929", "classification": "Class I"},
        {"recall_number": "D-2", "report_date": "20260930", "classification": "Class II"},
    ]
    monkeypatch.setattr(refresh_enforcement, "fetch_enforcement", lambda: (
        reports, "2026-09-30", "2026-09-01", "2026-09-30"))
    first = refresh_enforcement.refresh_enforcement()
    assert first["source_rows_written"] == 2
    assert first["signal_definitions_inserted"] == 5
    assert first["signal_rows_written"] == 5
    second = refresh_enforcement.refresh_enforcement()
    assert second["source_rows_written"] == 0
    assert second["signal_rows_written"] == 0
    with signal_store.connect() as db:
        rows = db.execute("""SELECT d.entity_key, d.cadence, r.value, r.observation_date
            FROM signal_records r JOIN signal_definitions d ON d.id=r.signal_uid
            WHERE r.source_kind=?""", (refresh_enforcement.SOURCE_NAME,)).fetchall()
    assert len(rows) == 5
    assert {row["entity_key"]: row["value"] for row in rows} == {
        "openfda_enforcement_class:class_i": 1,
        "openfda_enforcement_class:class_ii": 1,
        "openfda_enforcement_class:class_iii": 0,
        "openfda_enforcement_class:not_yet_classified": 0,
        "openfda_enforcement_class:total": 2}
    assert all(row["observation_date"] == "2026-09-30" for row in rows)
    assert all(row["cadence"] == "rolling_30d" for row in rows)


def test_enforcement_old_weekly_identity_migrates_to_rolling_window(store):
    source = refresh_enforcement.SOURCE_NAME
    period = "2026-09-30"
    captured = "2026-10-10T15:00:00+00:00"
    quality = "official_openfda_enforcement;source_last_updated=2026-09-30;not_active_recall_status"
    with signal_store.connect() as db:
        source_rows = [refresh_enforcement._raw_row({
            "recall_number": f"D-{index}", "report_date": "20260930",
            "classification": "Class I"}) for index in (1, 2)]
        db.executemany("""INSERT INTO enforcement_snapshot_rows
            (row_hash, recall_number, report_date, classification, payload_json)
            VALUES (?, ?, ?, ?, ?)""", source_rows)
        generation = db.execute("""INSERT INTO enforcement_snapshot_generations
            (window_start, window_end, source_last_updated, retrieved_at, row_count)
            VALUES ('2026-09-01', ?, ?, ?, 2)""",
            (period, period, captured)).lastrowid
        db.executemany("""INSERT INTO enforcement_snapshot_generation_rows
            (generation_id, row_hash) VALUES (?, ?)""",
            [(generation, row[0]) for row in source_rows])
        old_uid = signal_store.ensure_source_definition(
            db, signal_id=refresh_enforcement.SIGNAL_ID, cadence="weekly",
            geography_level="national", geography_id="US",
            entity_key="openfda_enforcement_class:total", unit="reports",
            source_name="FDA drug enforcement reports: Total",
            source_url=refresh_enforcement.SOURCE_URL)
        old_hash = hashlib.sha256(json.dumps(
            [source, old_uid, period, period, 2.0],
            separators=(",", ":")).encode()).hexdigest()
        db.execute("""INSERT INTO signal_records
            (row_hash, signal_uid, signal_date, observation_date, value,
             source_timestamp, ingested_at, data_quality, source_kind, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (old_hash, old_uid, "2026-10-10", period, 2.0, captured, captured,
             quality, source, refresh_enforcement.SOURCE_URL))
        db.execute("PRAGMA user_version=3")
    signal_store.initialize()
    signal_store.initialize()
    with signal_store.connect() as db:
        rows = db.execute("""SELECT d.id, d.cadence, r.observation_date, r.value,
            r.source_timestamp FROM signal_records r JOIN signal_definitions d
            ON d.id=r.signal_uid WHERE r.source_kind=?""", (source,)).fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] != old_uid
        assert rows[0]["cadence"] == "rolling_30d"
        assert rows[0]["observation_date"] == period
        assert rows[0]["source_timestamp"] == captured
        assert rows[0]["value"] == 2.0
        assert db.execute("SELECT COUNT(*) FROM signal_definitions WHERE id=?",
                          (old_uid,)).fetchone()[0] == 0
