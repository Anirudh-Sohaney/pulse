"""Capture provisional weekly NNDSS disease counts with source revisions."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .signal_store import connect, ensure_source_definition, initialize


SOURCE_KIND = "cdc_nndss_weekly_v1"
SOURCE_URL = "https://data.cdc.gov/resource/x9gk-5huc.json"
METADATA_URL = "https://data.cdc.gov/api/views/x9gk-5huc.json"
PAGE_SIZE = 5000
MAX_PAGE_BYTES = 8_000_000
STATES = {"Arkansas": ("state", "AR"),
          "US RESIDENTS": ("national", "US"),
          "U.S. Residents": ("national", "US")}
LABELS = {
    "Campylobacteriosis": "campylobacteriosis",
    "Chlamydia trachomatis infection": "chlamydia_trachomatis_in",
    "Gonorrhea": "gonorrhea",
    "Hepatitis C, chronic, Confirmed": "hepatitis_c_chronic_conf",
    "Hepatitis C, chronic, Probable": "hepatitis_c_chronic_prob",
    "Salmonellosis (excluding Salmonella Typhi infection and Salmonella Paratyphi infection)":
        "salmonellosis_excluding_",
}
METRICS = {"m1": "current_week_cases", "m2": "previous_52_week_max_cases",
           "m3": "current_year_cumulative_cases",
           "m4": "prior_year_cumulative_cases"}
SELECT_COLUMNS = (":updated_at", "states", "year", "week", "label", "m1", "m1_flag",
                  "m2", "m2_flag", "m3", "m3_flag", "m4", "m4_flag")


def _request(query: str) -> list[dict]:
    url = SOURCE_URL + "?" + urlencode({"$query": query})
    request = Request(url, headers={"User-Agent": "PULSE-public-signal-monitor/1.0",
                                    "Accept": "application/json"})
    with urlopen(request, timeout=45) as response:
        content = response.read(MAX_PAGE_BYTES + 1)
    if len(content) > MAX_PAGE_BYTES:
        raise ValueError("NNDSS source page exceeds the complete-response limit")
    rows = json.loads(content)
    if not isinstance(rows, list):
        raise ValueError("NNDSS source returned a non-list response")
    return rows


def _dataset_revision() -> int:
    request = Request(METADATA_URL, headers={
        "User-Agent": "PULSE-public-signal-monitor/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=45) as response:
        content = response.read(1_000_001)
    if len(content) > 1_000_000:
        raise ValueError("NNDSS metadata response is unexpectedly large")
    metadata = json.loads(content)
    if metadata.get("id") != "x9gk-5huc" or not isinstance(metadata.get("rowsUpdatedAt"), int):
        raise ValueError("NNDSS dataset revision metadata is unavailable")
    return metadata["rowsUpdatedAt"]


def _where() -> str:
    quote = lambda value: "'" + value.replace("'", "''") + "'"
    return ("year>='2022' AND states IN (" + ",".join(map(quote, STATES)) +
            ") AND label IN (" + ",".join(map(quote, LABELS)) + ")")


def fetch_rows() -> tuple[list[dict], str]:
    """Fetch every selected row with count and dataset-revision checks."""
    revision = _dataset_revision()
    where = _where()
    count_rows = _request("SELECT count(*) WHERE " + where)
    if len(count_rows) != 1 or not str(count_rows[0].get("count", "")).isdigit():
        raise ValueError("NNDSS source count is unavailable")
    expected = int(count_rows[0]["count"])
    if not 0 < expected <= 100_000:
        raise ValueError("NNDSS source row count is empty or unexpectedly large")
    rows = []
    for offset in range(0, expected, PAGE_SIZE):
        query = ("SELECT " + ",".join(SELECT_COLUMNS) + " WHERE " + where +
                 f" ORDER BY year,week,states,label LIMIT {PAGE_SIZE} OFFSET {offset}")
        page = _request(query)
        if len(page) != min(PAGE_SIZE, expected - offset):
            raise ValueError("NNDSS source pagination was incomplete")
        rows.extend(page)
    if len(rows) != expected or _dataset_revision() != revision:
        raise ValueError("NNDSS source changed during capture")
    return rows, SOURCE_URL


def _week_end(year: int, week: int) -> date:
    """CDC MMWR week: Sunday start; week one contains January 4."""
    if year < 2022 or not 1 <= week <= 53:
        raise ValueError("Invalid NNDSS MMWR year or week")
    jan4 = date(year, 1, 4)
    first_sunday = jan4 - timedelta(days=(jan4.weekday() + 1) % 7)
    result = first_sunday + timedelta(weeks=week - 1, days=6)
    if result > datetime.now(timezone.utc).date() + timedelta(days=7):
        raise ValueError("NNDSS source returned a future MMWR week")
    return result


def refresh_nndss() -> dict:
    initialize()
    started = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        run_id = db.execute("""INSERT INTO refresh_runs(source_name, started_at, status)
            VALUES (?, ?, 'running')""", (SOURCE_KIND, started)).lastrowid
    try:
        source_rows, source_url = fetch_rows()
        prepared: dict[tuple[str, str, str, str], tuple[float, str, str, str, str]] = {}
        latest_period = ""
        for source in source_rows:
            state = STATES.get(str(source.get("states", "")))
            label = str(source.get("label", ""))
            if state is None or label not in LABELS:
                raise ValueError("NNDSS source returned an unexpected state or disease label")
            day = _week_end(int(source["year"]), int(source["week"])).isoformat()
            latest_period = max(latest_period, day)
            published = datetime.fromisoformat(
                str(source[":updated_at"]).replace("Z", "+00:00"))
            if published.tzinfo is None or published > datetime.now(timezone.utc):
                raise ValueError("NNDSS source update timestamp is invalid")
            timestamp = published.astimezone(timezone.utc).isoformat()
            for metric, suffix in METRICS.items():
                raw = source.get(metric)
                flag = str(source.get(metric + "_flag") or "")
                if raw is None or str(raw).strip() == "":
                    continue  # Unpublished and suppressed cells are unknown.
                value = float(raw)
                if not math.isfinite(value) or value < 0:
                    raise ValueError("NNDSS source contains an invalid numeric value or flag")
                signal_id = f"nndss_{LABELS[label]}_{suffix}"
                key = (signal_id, state[1], day, timestamp)
                quality = (f"provisional_nndss;source_metric={metric};"
                           f"source_flag={flag if flag and flag != '-' else 'none'};not_drug_demand")
                prior = prepared.get(key)
                if prior and prior[0] != value:
                    raise ValueError("NNDSS source has conflicting values at one revision")
                prepared[key] = (value, quality, label, state[0], timestamp)
        retrieved = datetime.now(timezone.utc).isoformat()
        written = 0
        with connect() as db:
            existing = {(r["signal_uid"], r["observation_date"],
                         r["source_timestamp"], r["value"])
                        for r in db.execute("""SELECT signal_uid, observation_date,
                            source_timestamp, value FROM signal_records
                            WHERE source_kind=?""", (SOURCE_KIND,))}
            for (signal_id, geography, day, timestamp), (value, quality, label, level, _) in prepared.items():
                uid = ensure_source_definition(
                    db, signal_id=signal_id, cadence="weekly", geography_level=level,
                    geography_id=geography, entity_key=label, unit="cases",
                    source_name="CDC NNDSS Weekly Data", source_url=source_url)
                if (uid, day, timestamp, value) in existing:
                    continue
                row_hash = hashlib.sha256(json.dumps(
                    [SOURCE_KIND, uid, day, timestamp, value],
                    separators=(",", ":")).encode()).hexdigest()
                db.execute("""INSERT OR IGNORE INTO signal_records
                    (row_hash, signal_uid, signal_date, observation_date, value,
                     source_timestamp, ingested_at, data_quality, source_kind, source_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row_hash, uid, retrieved[:10], day, value, timestamp, retrieved,
                     quality, SOURCE_KIND, source_url))
                written += 1
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='success',
                rows_written=? WHERE id=?""", (retrieved, written, run_id))
        return {"source": SOURCE_KIND, "status": "success", "source_rows": len(source_rows),
                "series": len({(key[0], key[1]) for key in prepared}),
                "observations_seen": len(prepared), "rows_written": written,
                "latest_period": latest_period}
    except Exception as exc:
        with connect() as db:
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='failed', error=?
                WHERE id=?""", (datetime.now(timezone.utc).isoformat(),
                                str(exc)[:500], run_id))
        raise
