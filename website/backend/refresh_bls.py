"""Refresh BLS economic series from FRED's public CSV mirror.

FRED supplies published month/value pairs without a publication timestamp.
The first retrieval time is therefore the known availability time in PULSE.
Revisions are retained as new rows; unchanged daily fetches add no rows.
"""

from __future__ import annotations

import hashlib
import csv
import io
import json
import math
import statistics
from collections import defaultdict
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .signal_store import connect, ensure_source_definition, initialize


BLS_SERIES = {
    "arkansas_unemployment_rate": "ARURN",
    "consumer_price_index_all_items": "CPIAUCNS",
    "national_unemployment_rate": "UNRATE",
}
SOURCE_KIND = "fred_bls_series_csv_v1"
DERIVED_KIND = "fred_bls_series_csv_v1_derived_v2"
INVALID_DERIVED_KIND = "bls_public_api_v1_derived"
BLS_REFRESH_INTERVAL = timedelta(days=7)


def fetch_series(series_id: str, *, timeout: int = 25,
                 start_year: int = 2000) -> list[dict]:
    if series_id not in BLS_SERIES.values():
        raise ValueError("Unexpected FRED series ID")
    url = ("https://fred.stlouisfed.org/graph/fredgraph.csv?"
           + urlencode({"id": series_id, "cosd": f"{start_year}-01-01"}))
    request = Request(url, headers={"User-Agent": "PULSE-public-signal-monitor/1.0",
                                    "Accept": "text/csv"})
    with urlopen(request, timeout=timeout) as response:
        document = response.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(document))
    if reader.fieldnames != ["observation_date", series_id]:
        raise ValueError(f"FRED response omitted requested series {series_id}")
    rows = [{"date": row["observation_date"], "value": row[series_id]}
            for row in reader]
    if not rows:
        raise ValueError(f"FRED response contains no observations for {series_id}")
    return rows


def stored_base_months(signal_name: str) -> list[tuple[str, float, str]]:
    """Return the latest stored BLS/FRED revision for each historical month."""
    with connect() as db:
        rows = db.execute("""SELECT r.observation_date, r.value, r.data_quality
            FROM signal_records r JOIN signal_definitions d ON d.id=r.signal_uid
            WHERE d.signal_id=? AND r.source_kind IN (?, 'bls_public_api_v1')
            ORDER BY r.observation_date, r.source_timestamp DESC, r.ingested_at DESC""",
            (signal_name, SOURCE_KIND)).fetchall()
    months: dict[str, tuple[str, float, str]] = {}
    for row in rows:
        day = row["observation_date"]
        if day not in months:
            quality = "preliminary" if "preliminary" in (row["data_quality"] or "") else "published"
            months[day] = (day, row["value"], quality)
    return [months[day] for day in sorted(months)]


def parse_months(rows: list[dict]) -> list[tuple[str, float, str]]:
    """Accept only valid monthly FRED observations."""
    parsed: dict[str, tuple[float, str]] = {}
    for row in rows:
        try:
            observation = date.fromisoformat(str(row.get("date") or ""))
            if observation.day != 1:
                raise ValueError("Expected a monthly FRED observation")
            value = float(str(row.get("value") or "").replace(",", ""))
            if not math.isfinite(value):
                continue
            end = date(observation.year, observation.month,
                       monthrange(observation.year, observation.month)[1])
        except (ValueError, TypeError):
            continue
        if end > datetime.now(timezone.utc).date():
            continue
        day = end.isoformat()
        if day in parsed and parsed[day][0] != value:
            raise ValueError(f"FRED returned conflicting values for {day}")
        parsed[day] = (value, "published")
    return [(day, *parsed[day]) for day in sorted(parsed)]


def derive_months(base_name: str, months: list[tuple[str, float, str]]) -> dict[str, list[tuple[str, float, str]]]:
    """Match the catalog's preceding-observation shifts and rolling formulas."""
    months = sorted(months)
    result: dict[str, list[tuple[str, float, str]]] = {base_name: months}
    for width in (1, 4, 12):
        result[f"{base_name}_lag_{width}"] = []
        result[f"{base_name}_change_{width}"] = []
    for width in (4, 12):
        result[f"{base_name}_rolling_mean_{width}"] = []
        result[f"{base_name}_rolling_std_{width}"] = []
    result[f"{base_name}_seasonal_baseline"] = []
    result[f"{base_name}_seasonal_anomaly"] = []
    prior_by_month: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for index, (day, value, quality) in enumerate(months):
        for width in (1, 4, 12):
            if index < width:
                continue
            _, prior_value, prior_quality = months[index - width]
            combined = "preliminary" if "preliminary" in (quality, prior_quality) else "published"
            result[f"{base_name}_lag_{width}"].append((day, prior_value, combined))
            result[f"{base_name}_change_{width}"].append((day, value - prior_value, combined))
        for width in (4, 12):
            if index < width:
                continue
            window = months[index - width:index]
            values = [item[1] for item in window]
            combined = "preliminary" if any(item[2] == "preliminary" for item in window) else "published"
            result[f"{base_name}_rolling_mean_{width}"].append((day, statistics.mean(values), combined))
            result[f"{base_name}_rolling_std_{width}"].append((day, statistics.pstdev(values), combined))
        prior = prior_by_month[day[5:7]]
        if prior:
            baseline = statistics.mean(item[0] for item in prior)
            combined = "preliminary" if quality == "preliminary" or any(
                item[1] == "preliminary" for item in prior) else "published"
            result[f"{base_name}_seasonal_baseline"].append((day, baseline, combined))
            result[f"{base_name}_seasonal_anomaly"].append((day, value - baseline, combined))
        prior.append((value, quality))
    return result


def refresh_bls() -> dict:
    initialize()
    now = datetime.now(timezone.utc)
    # Space explicit --once runs as well as scheduled refreshes. FRED's CSV
    # mirror needs no BLS API registration key or anonymous API quota.
    with connect() as db:
        latest_attempt = db.execute("""SELECT MAX(COALESCE(finished_at, started_at))
            AS attempted_at FROM refresh_runs WHERE source_name=?""",
            (SOURCE_KIND,)).fetchone()["attempted_at"]
    if latest_attempt:
        try:
            attempted_at = datetime.fromisoformat(latest_attempt)
            if attempted_at.tzinfo is None:
                attempted_at = attempted_at.replace(tzinfo=timezone.utc)
            if now - attempted_at < BLS_REFRESH_INTERVAL:
                return {"source": SOURCE_KIND, "status": "skipped",
                        "reason": "seven_day_refresh_cooldown",
                        "next_attempt_at": (attempted_at + BLS_REFRESH_INTERVAL).isoformat()}
        except ValueError:
            # An invalid legacy timestamp must not permanently disable refresh.
            pass
    started = now.isoformat()
    with connect() as db:
        run_id = db.execute("""INSERT INTO refresh_runs(source_name, started_at, status)
            VALUES (?, ?, 'running')""", (SOURCE_KIND, started)).lastrowid
    try:
        fetched: dict[str, list[tuple[str, float, str]]] = {}
        for signal_name, series_id in BLS_SERIES.items():
            stored = stored_base_months(signal_name)
            # The first run backfills history. Later runs request only the
            # current and previous year, preserving older recorded months.
            # This keeps downloads small after the first backfill.
            start_year = (datetime.now(timezone.utc).year - 1
                          if len(stored) >= 24 and stored[0][0] <= "2000-01-31"
                          else 2000)
            recent = parse_months(fetch_series(series_id, start_year=start_year))
            months = sorted({day: (day, value, quality) for day, value, quality
                             in (*stored, *recent)}.values())
            if not months:
                raise ValueError(f"FRED has no valid monthly values for {series_id}")
            fetched.update(derive_months(signal_name, months))
        retrieved = datetime.now(timezone.utc).isoformat()
        inserted = 0
        with connect() as db:
            # The earlier calendar-offset formula did not match catalog
            # semantics. These rows were generated by this adapter only.
            db.execute("DELETE FROM signal_records WHERE source_kind=?",
                       (INVALID_DERIVED_KIND,))
            for signal_name, months in fetched.items():
                base = next(name for name in BLS_SERIES if signal_name == name or signal_name.startswith(name + "_"))
                source_url = f"https://fred.stlouisfed.org/series/{BLS_SERIES[base]}"
                uid = ensure_source_definition(
                    db, signal_id=signal_name, cadence="monthly",
                    geography_level="state" if base == "arkansas_unemployment_rate" else "national",
                    geography_id="AR" if base == "arkansas_unemployment_rate" else "US",
                    unit={"arkansas_unemployment_rate": "percent",
                          "national_unemployment_rate": "percent",
                          "consumer_price_index_all_items": "index"}[base],
                    source_name="BLS via FRED", source_url=source_url)
                db.execute("""UPDATE signal_definitions SET source_name=?, source_url=?
                    WHERE id=?""", ("BLS via FRED", source_url, uid))
                kind = SOURCE_KIND if signal_name in BLS_SERIES else DERIVED_KIND
                db.execute("""UPDATE signal_records SET source_url=?
                    WHERE signal_uid=? AND source_kind LIKE 'bls_public_api_v1%'
                      AND source_url=''""", (source_url, uid))
                for day, value, quality in months:
                    latest = db.execute("""SELECT value, source_kind FROM signal_records
                        WHERE signal_uid=? AND observation_date=?
                        ORDER BY source_timestamp DESC, ingested_at DESC LIMIT 1""",
                        (uid, day)).fetchone()
                    if latest and math.isclose(latest["value"], value, rel_tol=1e-12, abs_tol=1e-12):
                        continue
                    row_hash = hashlib.sha256(json.dumps(
                        [uid, day, value, retrieved, kind], separators=(",", ":")
                    ).encode()).hexdigest()
                    db.execute("""INSERT INTO signal_records
                        (row_hash, signal_uid, signal_date, observation_date, value,
                         source_timestamp, ingested_at, data_quality, missingness,
                         forecast_horizon, source_kind, source_url)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?)""",
                        (row_hash, uid, day, day, value, retrieved, retrieved,
                         quality + ";publication_time_unknown", kind, source_url))
                    inserted += 1
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='success',
                rows_written=? WHERE id=?""", (retrieved, inserted, run_id))
        return {"source": SOURCE_KIND, "series": len(BLS_SERIES),
                "catalog_signals": len(fetched),
                "values_computed": sum(map(len, fetched.values())),
                "rows_written": inserted, "finished_at": retrieved}
    except Exception as exc:
        with connect() as db:
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='failed', error=?
                WHERE id=?""", (datetime.now(timezone.utc).isoformat(), str(exc)[:500], run_id))
        raise
