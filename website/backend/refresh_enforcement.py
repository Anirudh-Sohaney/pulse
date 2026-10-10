"""Capture complete FDA drug enforcement report windows and dated class counts."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .signal_store import connect, ensure_source_definition, initialize


SOURCE_NAME = "openfda_drug_enforcement"
SOURCE_URL = "https://api.fda.gov/drug/enforcement.json"
SIGNAL_ID = "fda_enforcement_report_count_30d"
CLASSES = ("Class I", "Class II", "Class III", "Not Yet Classified")
WINDOW_DAYS = 30
PAGE_LIMIT = 1000
MAX_REPORTS = 25_000
STATE_DEFINITION = (
    "FDA drug recall enforcement reports with report_date in the 30 inclusive days "
    "ending on the source update date, grouped by FDA hazard class. A count of "
    "reports is not an active-recall count, local stock status, or drug demand. "
    "Zero means a complete source window had no reports in this class.")


def _source_date(value: object) -> date:
    try:
        return datetime.strptime(str(value).strip(), "%Y%m%d").date()
    except ValueError:
        try:
            return date.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise ValueError(f"Invalid FDA enforcement source date: {value!r}") from exc


def _get_page(query: dict, *, timeout: int = 30) -> dict:
    parameters = dict(query)
    key = os.environ.get("OPENFDA_API_KEY", "").strip()
    if key:
        parameters["api_key"] = key
    request = Request(SOURCE_URL + "?" + urlencode(parameters), headers={
        "User-Agent": "PULSE-public-signal-monitor/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError("FDA enforcement response has no results list")
    return payload


def _raw_row(record: dict) -> tuple[str, str, str, str, str]:
    raw = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return (digest, str(record.get("recall_number") or ""),
            _source_date(record.get("report_date")).isoformat(),
            str(record.get("classification") or ""), raw)


def _fetch_window(start: date, end: date, updated: date) -> list[dict]:
    """Page a complete report-date range; split before openFDA's skip cap."""
    search = f"report_date:[{start:%Y%m%d} TO {end:%Y%m%d}]"
    try:
        first = _get_page({"search": search, "limit": PAGE_LIMIT, "skip": 0,
                           "sort": "report_date:asc"})
    except HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    if _source_date(first.get("meta", {}).get("last_updated")) != updated:
        raise ValueError("FDA enforcement source changed during capture")
    total = first.get("meta", {}).get("results", {}).get("total")
    if not isinstance(total, int) or total < 0 or total > MAX_REPORTS:
        raise ValueError("FDA enforcement window count is invalid or exceeds the capture limit")
    if total > 5000:
        if start >= end:
            raise ValueError("FDA enforcement single-day reports exceed the paging limit")
        midpoint = start + timedelta(days=(end - start).days // 2)
        return (_fetch_window(start, midpoint, updated) +
                _fetch_window(midpoint + timedelta(days=1), end, updated))
    rows = list(first["results"])
    for skip in range(PAGE_LIMIT, total, PAGE_LIMIT):
        page = _get_page({"search": search, "limit": PAGE_LIMIT,
                          "skip": skip, "sort": "report_date:asc"})
        if (_source_date(page.get("meta", {}).get("last_updated")) != updated
                or page.get("meta", {}).get("results", {}).get("total") != total):
            raise ValueError("FDA enforcement source changed during pagination")
        rows.extend(page["results"])
    if len(rows) != total:
        raise ValueError("FDA enforcement report window was incomplete")
    hashes = set()
    for row in rows:
        day = _source_date(row.get("report_date"))
        if not start <= day <= end:
            raise ValueError("FDA enforcement returned a report outside the query window")
        if row.get("classification") not in CLASSES:
            raise ValueError("FDA enforcement returned an unknown classification")
        digest = _raw_row(row)[0]
        if digest in hashes:
            raise ValueError("FDA enforcement window has duplicate source rows")
        hashes.add(digest)
    return rows


def fetch_enforcement() -> tuple[list[dict], str, str, str]:
    """Fetch all reports in the 30 days ending at FDA's last update date."""
    latest = _get_page({"limit": 1, "sort": "report_date:desc"})
    updated = _source_date(latest.get("meta", {}).get("last_updated"))
    if len(latest["results"]) != 1:
        raise ValueError("FDA enforcement latest-report query was incomplete")
    if _source_date(latest["results"][0].get("report_date")) > updated:
        raise ValueError("FDA enforcement report date exceeds source update date")
    end = updated
    start = end - timedelta(days=WINDOW_DAYS - 1)
    rows = _fetch_window(start, end, updated)
    if not rows:
        raise ValueError("FDA enforcement complete window has no reports")
    return rows, updated.isoformat(), start.isoformat(), end.isoformat()


def _persist_enforcement_signals(db, records: list[dict], *, window_start: str,
                                 window_end: str, source_last_updated: str,
                                 retrieved_at: str) -> tuple[int, int]:
    start = date.fromisoformat(window_start)
    end = date.fromisoformat(window_end)
    if (end - start).days != WINDOW_DAYS - 1 or end != date.fromisoformat(source_last_updated):
        raise ValueError("FDA enforcement window and source revision disagree")
    counts = Counter(row["classification"] for row in records)
    if set(counts) - set(CLASSES):
        raise ValueError("Stored FDA enforcement window contains invalid reports")
    definitions_written = 0
    rows_written = 0
    for classification in (*CLASSES, "Total"):
        label = classification.casefold().replace(" ", "_")
        entity_key = "openfda_enforcement_class:" + label
        before = db.total_changes
        uid = ensure_source_definition(
            db, signal_id=SIGNAL_ID, cadence="weekly", geography_level="national",
            geography_id="US", entity_key=entity_key, unit="reports",
            source_name=f"FDA drug enforcement reports: {classification}",
            source_url=SOURCE_URL, state_definition=STATE_DEFINITION)
        definitions_written += db.total_changes - before
        value = float(len(records) if classification == "Total" else counts[classification])
        quality = ("official_openfda_enforcement;complete_30d_report_date_window;"
                   f"source_last_updated={source_last_updated};not_active_recall_status")
        latest = db.execute("""SELECT value, data_quality FROM signal_records
            WHERE signal_uid=? AND source_kind=? AND observation_date=?
            ORDER BY source_timestamp DESC, ingested_at DESC LIMIT 1""",
            (uid, SOURCE_NAME, window_end)).fetchone()
        if latest and latest["value"] == value and latest["data_quality"] == quality:
            continue
        row_hash = hashlib.sha256(json.dumps(
            [SOURCE_NAME, uid, window_end, source_last_updated, value],
            separators=(",", ":")).encode()).hexdigest()
        db.execute("""INSERT OR IGNORE INTO signal_records
            (row_hash, signal_uid, signal_date, observation_date, value,
             source_timestamp, ingested_at, data_quality, source_kind, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (row_hash, uid, retrieved_at[:10], window_end, value, retrieved_at,
             retrieved_at, quality, SOURCE_NAME, SOURCE_URL))
        rows_written += 1
    return definitions_written, rows_written


def refresh_enforcement() -> dict:
    initialize()
    started = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        run_id = db.execute("""INSERT INTO refresh_runs(source_name, started_at, status)
            VALUES (?, ?, 'running')""", (SOURCE_NAME, started)).lastrowid
    try:
        records, updated, start, end = fetch_enforcement()
        source_rows = [_raw_row(record) for record in records]
        if len({row[0] for row in source_rows}) != len(source_rows):
            raise ValueError("FDA enforcement window has duplicate source rows")
        retrieved = datetime.now(timezone.utc).isoformat()
        with connect() as db:
            before = db.total_changes
            db.executemany("""INSERT OR IGNORE INTO enforcement_snapshot_rows
                (row_hash, recall_number, report_date, classification, payload_json)
                VALUES (?, ?, ?, ?, ?)""", source_rows)
            source_written = db.total_changes - before
            generation_id = db.execute("""INSERT INTO enforcement_snapshot_generations
                (window_start, window_end, source_last_updated, retrieved_at, row_count)
                VALUES (?, ?, ?, ?, ?)""",
                (start, end, updated, retrieved, len(records))).lastrowid
            db.executemany("""INSERT INTO enforcement_snapshot_generation_rows
                (generation_id, row_hash) VALUES (?, ?)""",
                [(generation_id, row[0]) for row in source_rows])
            definitions_written, signal_written = _persist_enforcement_signals(
                db, records, window_start=start, window_end=end,
                source_last_updated=updated, retrieved_at=retrieved)
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='success',
                rows_written=? WHERE id=?""", (retrieved, source_written + signal_written, run_id))
        return {"source": SOURCE_NAME, "source_last_updated": updated,
                "window_start": start, "window_end": end, "reports": len(records),
                "source_rows_written": source_written,
                "signal_definitions_inserted": definitions_written,
                "signal_rows_written": signal_written, "finished_at": retrieved}
    except Exception as exc:
        with connect() as db:
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='failed', error=?
                WHERE id=?""", (datetime.now(timezone.utc).isoformat(),
                                str(exc)[:500], run_id))
        raise
