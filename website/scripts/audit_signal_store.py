"""Read-only release audit of catalog identity and signal-record datestamps."""

from __future__ import annotations

import json
import hashlib
import math
import sqlite3
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from backend.config import settings
from backend.signal_store import (IDENTITY_FIELDS, SEED_DEFINITION_COUNT,
                                  SEED_HISTORICAL_RECORD_COUNT, catalog_rows,
                                  news_version_hash, signal_uid)
from scripts.import_signal_catalog import DEFAULT_HISTORY, DEFAULT_HISTORY_SHA256


UNKNOWN_SOURCE_TIME_KINDS = {"historical_catalog", "historical_news_bridge"}
WEEKLY_SOURCE_KINDS = {"delphi_fluview_ilinet_v5", "cdc_nndss_weekly_v1",
                       "cdc_wval_reporting_site_mean_v1"}
MONTHLY_SOURCE_KINDS = {"bls_public_api_v1", "bls_public_api_v1_derived_v2",
                        "fred_bls_series_csv_v1", "fred_bls_series_csv_v1_derived_v2",
                        "medicaid_state_performance_api"}
ANNUAL_SOURCE_KINDS = {"cms_geographic_variation_api"}
LIVE_OBSERVATION_KINDS = WEEKLY_SOURCE_KINDS | {
    "bls_public_api_v1", "bls_public_api_v1_derived_v2",
    "fred_bls_series_csv_v1", "fred_bls_series_csv_v1_derived_v2",
    "medicaid_state_performance_api", "cms_geographic_variation_api",
    "openfda_drug_enforcement"}
DEFAULT_DEFINITIONS = (Path(__file__).resolve().parents[1] / "catalog" /
                       "signal_definitions.json")


def _date(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except (TypeError, ValueError):
        return False


def _aware_time(value: str) -> bool:
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except (TypeError, ValueError):
        return False


def audit(path: Path | None = None, *, expected_definitions: int = SEED_DEFINITION_COUNT,
          check_bundled_history: bool = True) -> dict:
    database = path or Path(settings.DATA_PATH) / "signals.sqlite3"
    if not database.is_file():
        raise ValueError(f"Signal database is missing: {database}")
    problems: list[str] = []
    source_counts: Counter[str] = Counter()
    unknown_source_times = 0
    news_versions = 0
    expected_history = {}
    seed_payload = json.loads(DEFAULT_DEFINITIONS.read_text(encoding="utf-8"))
    seed_rows = seed_payload.get("definitions")
    seed_ids = {row.get("id") for row in seed_rows or [] if isinstance(row, dict)}
    if (seed_payload.get("schema") != "pulse_signal_definitions_v1"
            or seed_payload.get("definition_count") != expected_definitions
            or len(seed_ids) != expected_definitions):
        raise ValueError("Signal seed definition manifest count or schema mismatch")
    if check_bundled_history:
        if hashlib.sha256(DEFAULT_HISTORY.read_bytes()).hexdigest() != DEFAULT_HISTORY_SHA256:
            raise ValueError("Bundled historical catalog checksum mismatch")
        _, source_rows, rejected = catalog_rows(DEFAULT_HISTORY, ingested_at="source-audit")
        if rejected:
            raise ValueError(f"Bundled historical catalog has {rejected} invalid rows")
        expected_history = {
            row[0]: (row[1], row[2], row[3], row[4], row[5],
                     row[7], row[8], row[9], row[10], row[11])
            for row in source_rows if row[1] in seed_ids}
        if len(expected_history) != SEED_HISTORICAL_RECORD_COUNT:
            raise ValueError("Bundled historical catalog has an unexpected unique row count")
    verified_history: set[str] = set()
    truncated = False
    today = datetime.now(timezone.utc).date()
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            problems.append("SQLite integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            problems.append("SQLite foreign key check failed")
        definition_rows = list(db.execute("SELECT * FROM signal_definitions"))
        definition_by_id = {row["id"]: row for row in definition_rows}
        definitions = len(definition_rows)
        if definitions < expected_definitions:
            problems.append(
                f"Expected at least {expected_definitions} signal definitions, found {definitions}")
        for definition in definition_rows:
            identity = {field: definition[field] for field in IDENTITY_FIELDS}
            if definition["id"] != signal_uid(identity):
                problems.append(f"{definition['id']}: signal ID does not match its identity")
                if len(problems) >= 20:
                    break
        present_definition_ids = {row["id"] for row in definition_rows}
        missing_seed_ids = seed_ids - present_definition_ids
        if missing_seed_ids:
            problems.append(f"{len(missing_seed_ids)} seeded signal definitions are missing")
        records = 0
        for row in db.execute("""SELECT row_hash, signal_uid, source_kind, signal_date,
                observation_date, source_timestamp, ingested_at, value,
                data_quality, missingness, forecast_horizon, source_url
                FROM signal_records"""):
            records += 1
            source_counts[row["source_kind"]] += 1
            if row["source_kind"] == "combined_demand_signals":
                problems.append(f"{row['row_hash']}: unsupported combined-model output remains")
            if (row["source_kind"] == "openfda_drug_enforcement"
                    and definition_by_id[row["signal_uid"]]["cadence"] != "rolling_30d"):
                problems.append(f"{row['row_hash']}: FDA 30-day window has incorrect cadence")
            for column in ("signal_date", "observation_date"):
                if not _date(row[column]):
                    problems.append(f"{row['row_hash']}: invalid {column}")
            if not _aware_time(row["ingested_at"]):
                problems.append(f"{row['row_hash']}: invalid ingested_at")
            if row["source_timestamp"]:
                if not _aware_time(row["source_timestamp"]):
                    problems.append(f"{row['row_hash']}: invalid source_timestamp")
                elif (_aware_time(row["ingested_at"]) and
                      datetime.fromisoformat(row["source_timestamp"]) >
                      datetime.fromisoformat(row["ingested_at"])):
                    problems.append(f"{row['row_hash']}: source timestamp follows capture")
            elif row["source_kind"] in UNKNOWN_SOURCE_TIME_KINDS:
                unknown_source_times += 1
            else:
                problems.append(f"{row['row_hash']}: missing live source_timestamp")
            if not isinstance(row["value"], (int, float)) or not math.isfinite(row["value"]):
                problems.append(f"{row['row_hash']}: nonfinite value")
            if row["source_kind"] in LIVE_OBSERVATION_KINDS and _date(row["observation_date"]):
                observed = date.fromisoformat(row["observation_date"])
                if observed > today:
                    problems.append(f"{row['row_hash']}: future live observation period")
                if row["source_kind"] in WEEKLY_SOURCE_KINDS and observed.weekday() != 5:
                    problems.append(f"{row['row_hash']}: weekly source period is not Saturday")
                if (row["source_kind"] in MONTHLY_SOURCE_KINDS and
                        observed.day != monthrange(observed.year, observed.month)[1]):
                    problems.append(f"{row['row_hash']}: monthly source period is not month end")
                if (row["source_kind"] in ANNUAL_SOURCE_KINDS and
                        (observed.month, observed.day) != (12, 31)):
                    problems.append(f"{row['row_hash']}: annual source period is not year end")
                if (row["source_kind"] == "cdc_wval_reporting_site_mean_v1"
                        and (not row["data_quality"] or
                             "contributing_site_rows=" not in row["data_quality"])):
                    problems.append(f"{row['row_hash']}: wastewater site count is missing")
                if (row["source_kind"] == "openfda_drug_enforcement"
                        and (not row["data_quality"] or
                             f"source_last_updated={row['observation_date']}" not in
                             row["data_quality"])):
                    problems.append(f"{row['row_hash']}: enforcement source window is not dated")
            if row["row_hash"] in expected_history:
                actual = tuple(row[column] for column in (
                    "signal_uid", "signal_date", "observation_date", "value",
                    "source_timestamp", "data_quality", "missingness",
                    "forecast_horizon", "source_kind", "source_url"))
                if actual != expected_history[row["row_hash"]]:
                    problems.append(f"{row['row_hash']}: bundled historical row changed")
                verified_history.add(row["row_hash"])
            if len(problems) >= 20:
                truncated = True
                break
        missing_history = set(expected_history) - verified_history
        if missing_history and not truncated:
            problems.append(f"{len(missing_history)} bundled historical rows are missing")
        if not truncated and db.execute("""SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='news_article_versions'""").fetchone():
            for row in db.execute("""SELECT version_hash, url_hash, source_name,
                    title, url, source, source_timestamp, timestamp_kind,
                    first_observed_at, relevance_score, matched_terms, summary_text
                    FROM news_article_versions"""):
                news_versions += 1
                if (not _aware_time(row["source_timestamp"])
                        or not _aware_time(row["first_observed_at"])):
                    problems.append(f"{row['version_hash']}: invalid news source or capture time")
                elif (datetime.fromisoformat(row["source_timestamp"]) >
                      datetime.fromisoformat(row["first_observed_at"])):
                    problems.append(f"{row['version_hash']}: article publication follows capture")
                if row["timestamp_kind"] not in {"gdelt_first_seen", "rss_pub_date"}:
                    problems.append(f"{row['version_hash']}: invalid news timestamp kind")
                if hashlib.sha256(row["url"].encode("utf-8")).hexdigest() != row["url_hash"]:
                    problems.append(f"{row['version_hash']}: news URL hash changed")
                if news_version_hash(row) != row["version_hash"]:
                    problems.append(f"{row['version_hash']}: news evidence content changed")
                if len(problems) >= 20:
                    break
        if not truncated:
            missing_model_evidence = db.execute("""SELECT COUNT(*) FROM signal_records r
                LEFT JOIN news_model_record_provenance p ON p.row_hash=r.row_hash
                WHERE r.source_kind='article_text_ridge_shadow_v1'
                  AND p.row_hash IS NULL""").fetchone()[0]
            if missing_model_evidence:
                problems.append(f"{missing_model_evidence} shadow rows lack model provenance")
            mismatched_model_inputs = db.execute("""SELECT COUNT(*)
                FROM news_model_record_provenance p
                WHERE p.input_count!=(SELECT COUNT(*) FROM news_model_record_inputs i
                                      WHERE i.row_hash=p.row_hash)""").fetchone()[0]
            if mismatched_model_inputs:
                problems.append(f"{mismatched_model_inputs} shadow rows lack article inputs")
    if problems:
        raise ValueError("Signal store audit failed: " + "; ".join(problems))
    return {"status": "passed", "definitions": definitions, "records": records,
            "verified_bundled_historical_records": len(verified_history),
            "unknown_historical_source_timestamps": unknown_source_times,
            "news_article_versions": news_versions,
            "records_by_source_kind": dict(sorted(source_counts.items()))}


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
