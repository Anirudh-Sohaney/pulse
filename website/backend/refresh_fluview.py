"""Capture dated CDC FluView ILINet observations from Delphi's source snapshot."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from datetime import date, datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .signal_store import connect, ensure_source_definition, initialize


SOURCE_KIND = "delphi_fluview_ilinet_v5"
SOURCE_URL = "https://delphi.cmu.edu/epidata/v5/snapshot/"
SERIES = (("ili", "state", "ar", "AR"),
          ("ili", "nation", "us", "US"),
          ("wili", "nation", "us", "US"))
COLUMNS = ("signal", "report_time", "geo_type", "geo_value", "reference_time",
           "value", "fill_method", "age_group")
MAX_RESPONSE_BYTES = 12_000_000


def _url(signal: str, geo_type: str) -> str:
    return SOURCE_URL + "?" + urlencode({
        "source": "fluview_ilinet", "signal": signal, "geo_type": geo_type,
        "fill_method": "source", "columns": ",".join(COLUMNS)})


def fetch_series(signal: str, geo_type: str, geo_value: str,
                 *, timeout: int = 45) -> tuple[list[tuple[str, str, float]], str]:
    """Select the latest published revision for each source week."""
    if (signal, geo_type, geo_value) not in {(a, b, c) for a, b, c, _ in SERIES}:
        raise ValueError("Unsupported FluView geography or signal")
    url = _url(signal, geo_type)
    request = Request(url, headers={"User-Agent": "PULSE-public-signal-monitor/1.0",
                                    "Accept": "text/csv"})
    with urlopen(request, timeout=timeout) as response:
        content = response.read(MAX_RESPONSE_BYTES + 1)
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("FluView response exceeds the complete-snapshot limit")
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if not set(COLUMNS).issubset(reader.fieldnames or []):
        raise ValueError("FluView snapshot schema changed")
    latest: dict[str, tuple[str, float]] = {}
    now = datetime.now(timezone.utc)
    for row in reader:
        if (row["signal"] != signal or row["geo_type"] != geo_type
                or row["geo_value"].casefold() != geo_value
                or row["fill_method"] != "source" or row["age_group"] != "all"):
            continue
        day = date.fromisoformat(row["reference_time"])
        if day.weekday() != 5 or day > now.date():
            raise ValueError("FluView returned an invalid source week")
        published = datetime.fromisoformat(row["report_time"].replace("Z", "+00:00"))
        if published.tzinfo is None or published > now:
            raise ValueError("FluView returned an invalid report time")
        value = float(row["value"])
        if not math.isfinite(value) or value < 0:
            raise ValueError("FluView returned an invalid ILI value")
        key = day.isoformat()
        item = (published.astimezone(timezone.utc).isoformat(), value)
        previous = latest.get(key)
        if previous and previous[0] == item[0] and previous[1] != value:
            raise ValueError("FluView has conflicting values at one revision")
        if previous is None or item[0] > previous[0]:
            latest[key] = item
    if not latest:
        raise ValueError("FluView has no observations for the requested geography")
    return [(day, *latest[day]) for day in sorted(latest)], url


def refresh_fluview() -> dict:
    initialize()
    started = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        run_id = db.execute("""INSERT INTO refresh_runs(source_name, started_at, status)
            VALUES (?, ?, 'running')""", (SOURCE_KIND, started)).lastrowid
    try:
        fetched = [(signal, geography, *fetch_series(signal, geo_type, geo_value))
                   for signal, geo_type, geo_value, geography in SERIES]
        retrieved = datetime.now(timezone.utc).isoformat()
        rows_written = 0
        periods_seen = 0
        latest_period = ""
        with connect() as db:
            for signal, geography, rows, url in fetched:
                uid = ensure_source_definition(
                    db, signal_id=f"fluview_{signal}", cadence="weekly",
                    geography_level="state" if geography == "AR" else "national",
                    geography_id=geography, unit="percent_outpatient_visits",
                    source_name="Delphi FluView ILINet V5 (CDC source)", source_url=url)
                prior = {(r["observation_date"], r["source_timestamp"], r["value"])
                         for r in db.execute("""SELECT observation_date, source_timestamp, value
                             FROM signal_records WHERE signal_uid=? AND source_kind=?""",
                             (uid, SOURCE_KIND))}
                for day, published, value in rows:
                    periods_seen += 1
                    latest_period = max(latest_period, day)
                    if (day, published, value) in prior:
                        continue
                    row_hash = hashlib.sha256(json.dumps(
                        [SOURCE_KIND, uid, day, published, value],
                        separators=(",", ":")).encode()).hexdigest()
                    db.execute("""INSERT OR IGNORE INTO signal_records
                        (row_hash, signal_uid, signal_date, observation_date, value,
                         source_timestamp, ingested_at, data_quality, source_kind, source_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (row_hash, uid, retrieved[:10], day, value, published, retrieved,
                         "observed_surveillance;revisions_possible;not_drug_demand",
                         SOURCE_KIND, url))
                    rows_written += 1
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='success',
                rows_written=? WHERE id=?""", (retrieved, rows_written, run_id))
        return {"source": SOURCE_KIND, "status": "success", "rows_written": rows_written,
                "observations_seen": periods_seen, "series": len(SERIES),
                "latest_reference_time": latest_period}
    except Exception as exc:
        with connect() as db:
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='failed', error=?
                WHERE id=?""", (datetime.now(timezone.utc).isoformat(),
                                str(exc)[:500], run_id))
        raise
