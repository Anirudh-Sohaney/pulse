"""Worker activity is visible without conflating it with source publication dates."""

from datetime import datetime, timedelta, timezone

from backend import signal_store
from backend.config import settings
from scripts import operational_status


def test_freshness_exposes_worker_liveness_and_source_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    signal_store.initialize()
    empty = signal_store.freshness()
    assert empty["worker_recent"] is False
    assert empty["source_checks"] == []
    assert empty["failed_sources"] == []

    signal_store.record_worker_heartbeat()
    now = datetime.now(timezone.utc).isoformat()
    with signal_store.connect() as db:
        db.execute("""INSERT INTO refresh_runs
            (source_name, started_at, finished_at, status)
            VALUES ('source_a', ?, ?, 'success')""", (now, now))
    fresh = signal_store.freshness()
    assert fresh["worker_recent"] is True
    assert fresh["worker_last_check_at"]
    assert fresh["source_checks"][0]["last_status"] == "success"
    assert fresh["source_checks"][0]["last_success_at"] == now
    assert fresh["failed_sources"] == []

    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with signal_store.connect() as db:
        db.execute("UPDATE service_heartbeats SET checked_at=?", (old,))
        db.execute("""INSERT INTO refresh_runs
            (source_name, started_at, finished_at, status)
            VALUES ('source_a', ?, ?, 'failed')""", (now, now))
    stale = signal_store.freshness()
    assert stale["worker_recent"] is False
    assert stale["source_checks"][0]["last_status"] == "failed"
    assert stale["source_checks"][0]["last_success_at"] == now
    assert stale["failed_sources"] == ["source_a"]


def test_optional_bls_failures_never_affect_displayed_signal_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    signal_store.initialize()
    now = datetime.now(timezone.utc).isoformat()
    with signal_store.connect() as db:
        for source_name in ("bls_public_api_v1", "bls_public_api_v1_derived_v2",
                            "fred_bls_series_csv_v1"):
            db.execute("""INSERT INTO refresh_runs
                (source_name, started_at, finished_at, status, error)
                VALUES (?, ?, ?, 'failed', 'quota exceeded')""",
                       (source_name, now, now))
    result = signal_store.freshness()
    assert {row["source_name"] for row in result["source_checks"]} == {
        "bls_public_api_v1", "bls_public_api_v1_derived_v2",
        "fred_bls_series_csv_v1"}
    assert result["failed_sources"] == []


def test_weekly_bls_source_does_not_fail_daily_source_health(monkeypatch):
    monkeypatch.setattr(operational_status, "REFRESH_TASKS", [
        (lambda: {}, ("fred_bls_series_csv_v1",)),
    ])
    monkeypatch.setattr(operational_status, "freshness", lambda: {
        "source_checks": [{"source_name": "fred_bls_series_csv_v1",
                           "last_attempt_at": "2026-10-01T00:00:00+00:00"}],
        "failed_sources": [],
    })
    status = operational_status.sources_status(datetime.now(timezone.utc))
    assert status["healthy"] is True
    assert status["missing_sources"] == []
    assert status["stale_sources"] == []


def test_source_native_definition_is_stable_outside_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_PATH", str(tmp_path))
    signal_store.initialize()
    with signal_store.connect() as db:
        first = signal_store.ensure_source_definition(
            db, signal_id="adult_medicaid_enrollment", cadence="monthly",
            geography_level="state", geography_id="AR", unit="persons",
            source_name="Medicaid source", source_url="https://example.org/source")
        second = signal_store.ensure_source_definition(
            db, signal_id="adult_medicaid_enrollment", cadence="monthly",
            geography_level="state", geography_id="AR", unit="persons",
            source_name="Medicaid source", source_url="https://example.org/source")
        row = db.execute("SELECT * FROM signal_definitions WHERE id=?", (first,)).fetchone()
    assert first == second == signal_store.signal_uid(dict(row))
    assert row["unit"] == "persons"
    assert row["source_url"] == "https://example.org/source"
    assert first not in {row["id"] for row in signal_store.seed_definitions()}
