"""Capture weekly CDC wastewater reporting-site means with site counts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .signal_store import connect, ensure_source_definition, initialize


SOURCE_KIND = "cdc_wval_reporting_site_mean_v1"
SOURCE_URL = "https://data.cdc.gov/resource/atcp-73re.json"
METADATA_URL = "https://data.cdc.gov/api/views/atcp-73re.json"
PATHOGENS = {"SARS-CoV-2": "covid", "Influenza A virus": "flu", "RSV": "rsv"}
MAX_GROUPS = 5000
MAX_RESPONSE_BYTES = 3_000_000


def _url(arkansas: bool) -> str:
    quoted = ",".join("'" + name.replace("'", "''") + "'" for name in PATHOGENS)
    where = f"pathogen_target IN ({quoted})"
    if arkansas:
        where += " AND state_territory='Arkansas'"
    query = ("SELECT week_end,pathogen_target,avg(site_wval) AS mean_wval,"
             "count(site_wval) AS site_rows WHERE " + where +
             " GROUP BY week_end,pathogen_target ORDER BY week_end,pathogen_target"
             f" LIMIT {MAX_GROUPS}")
    return SOURCE_URL + "?" + urlencode({"$query": query})


def _revision() -> int:
    request = Request(METADATA_URL, headers={
        "User-Agent": "PULSE-public-signal-monitor/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=45) as response:
        content = response.read(1_000_001)
    if len(content) > 1_000_000:
        raise ValueError("CDC WVAL metadata response is unexpectedly large")
    metadata = json.loads(content)
    if metadata.get("id") != "atcp-73re" or not isinstance(metadata.get("rowsUpdatedAt"), int):
        raise ValueError("CDC WVAL dataset revision is unavailable")
    return metadata["rowsUpdatedAt"]


def _fetch(arkansas: bool, *, timeout: int = 60) -> tuple[list[dict], str]:
    url = _url(arkansas)
    request = Request(url, headers={"User-Agent": "PULSE-public-signal-monitor/1.0",
                                    "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        content = response.read(MAX_RESPONSE_BYTES + 1)
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("CDC WVAL aggregate response exceeds its size limit")
    rows = json.loads(content)
    if not isinstance(rows, list) or not rows or len(rows) >= MAX_GROUPS:
        raise ValueError("CDC WVAL aggregate response is empty or truncated")
    return rows, url


def refresh_wastewater() -> dict:
    initialize()
    started = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        run_id = db.execute("""INSERT INTO refresh_runs(source_name, started_at, status)
            VALUES (?, ?, 'running')""", (SOURCE_KIND, started)).lastrowid
    try:
        first_revision = _revision()
        fetched = [("AR", *_fetch(True)), ("US", *_fetch(False))]
        if _revision() != first_revision:
            raise ValueError("CDC WVAL dataset changed during aggregate capture")
        revision = datetime.fromtimestamp(first_revision, timezone.utc).isoformat()
        prepared: dict[tuple[str, str, str], tuple[float, int, str]] = {}
        latest_period = ""
        for geography, rows, url in fetched:
            for row in rows:
                pathogen = PATHOGENS.get(str(row.get("pathogen_target", "")))
                if pathogen is None:
                    raise ValueError("CDC WVAL returned an unexpected pathogen")
                day = date.fromisoformat(str(row["week_end"]))
                value = float(row["mean_wval"])
                count = int(row["site_rows"])
                if (day.weekday() != 5 or day > datetime.now(timezone.utc).date()
                        or not math.isfinite(value) or value < 0 or count < 1):
                    raise ValueError("CDC WVAL returned an invalid weekly aggregate")
                latest_period = max(latest_period, day.isoformat())
                key = (geography, pathogen, day.isoformat())
                if key in prepared:
                    raise ValueError("CDC WVAL returned duplicate weekly aggregates")
                prepared[key] = (value, count, url)
        retrieved = datetime.now(timezone.utc).isoformat()
        written = 0
        with connect() as db:
            for (geography, pathogen, day), (value, count, url) in prepared.items():
                uid = ensure_source_definition(
                    db, signal_id=f"cdc_wval_site_mean_{pathogen}", cadence="weekly",
                    geography_level="state" if geography == "AR" else "national",
                    geography_id=geography, entity_key="reporting_site_unweighted_mean",
                    unit="mean_site_wval",
                    source_name="CDC WVAL reporting-site mean (not official median)",
                    source_url=url)
                latest = db.execute("""SELECT value, data_quality FROM signal_records
                    WHERE signal_uid=? AND source_kind=? AND observation_date=?
                    ORDER BY source_timestamp DESC, ingested_at DESC LIMIT 1""",
                    (uid, SOURCE_KIND, day)).fetchone()
                quality = ("derived_unweighted_reporting_site_mean;"
                           f"contributing_site_rows={count};"
                           "not_official_state_or_national_median;not_drug_demand")
                if latest and latest["value"] == value and latest["data_quality"] == quality:
                    continue
                row_hash = hashlib.sha256(json.dumps(
                    [SOURCE_KIND, uid, day, revision, value, count],
                    separators=(",", ":")).encode()).hexdigest()
                db.execute("""INSERT OR IGNORE INTO signal_records
                    (row_hash, signal_uid, signal_date, observation_date, value,
                     source_timestamp, ingested_at, data_quality, source_kind, source_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row_hash, uid, retrieved[:10], day, value, revision, retrieved,
                     quality, SOURCE_KIND, url))
                written += 1
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='success',
                rows_written=? WHERE id=?""", (retrieved, written, run_id))
        return {"source": SOURCE_KIND, "status": "success",
                "series": len({(geo, pathogen) for geo, pathogen, _ in prepared}),
                "aggregate_rows_seen": len(prepared), "rows_written": written,
                "latest_period": latest_period, "dataset_revision": revision}
    except Exception as exc:
        with connect() as db:
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='failed', error=?
                WHERE id=?""", (datetime.now(timezone.utc).isoformat(),
                                str(exc)[:500], run_id))
        raise
