"""Daily inference for the shared, signal-driven XGBoost demand outlook."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
import xgboost as xgb

from . import signal_store


SOURCE_NAME = "daily_signal_demand_outlook_v1"
ARTIFACT_DIR = Path(__file__).resolve().parent / "model_artifacts" / "daily_signal_demand_v1"
_MANIFEST_CACHE: tuple[int, dict[str, Any], dict[str, xgb.Booster]] | None = None


def _feature_key(row: dict[str, Any]) -> str:
    identity = "|".join(str(row.get(name) or "") for name in (
        "signal_origin", "signal_id", "cadence", "geography_level", "geography_id", "entity_key"))
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", identity).strip("_")[:90]
    return f"signal__{safe}"


def _load_artifacts() -> tuple[dict[str, Any], dict[str, xgb.Booster]]:
    global _MANIFEST_CACHE
    manifest_path = ARTIFACT_DIR / "manifest.json"
    manifest_mtime = manifest_path.stat().st_mtime_ns
    if _MANIFEST_CACHE and _MANIFEST_CACHE[0] == manifest_mtime:
        return _MANIFEST_CACHE[1], _MANIFEST_CACHE[2]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "pulse_daily_signal_demand_model_v1":
        raise ValueError("Unsupported daily demand model artifact")
    boosters: dict[str, xgb.Booster] = {}
    for drug, details in manifest.get("models", {}).items():
        compressed = (ARTIFACT_DIR / details["file"]).read_bytes()
        if hashlib.sha256(compressed).hexdigest() != details.get("sha256"):
            raise ValueError(f"Model artifact checksum mismatch for {drug}")
        raw = gzip.decompress(compressed)
        booster = xgb.Booster()
        booster.load_model(bytearray(raw))
        expected = details["features"]
        if booster.feature_names != expected:
            raise ValueError(f"Feature manifest does not match model for {drug}")
        boosters[drug] = booster
    if not boosters:
        raise ValueError("Daily demand model has no drug models")
    _MANIFEST_CACHE = (manifest_mtime, manifest, boosters)
    return manifest, boosters


def _current_signal_rows(as_of: date, selected_columns: set[str]) -> dict[str, dict[str, Any]]:
    if not selected_columns:
        return {}
    with signal_store.connect() as db:
        definitions = [dict(row) for row in db.execute("""SELECT id, signal_origin,
            signal_id, cadence, geography_level, geography_id, entity_key, unit,
            source_name, state_definition FROM signal_definitions""")]
        identities_by_column: dict[str, list[dict[str, Any]]] = {}
        for row in definitions:
            identities_by_column.setdefault(_feature_key(row), []).append(row)
        matched = {name: identities_by_column[name][0] for name in selected_columns
                   if len(identities_by_column.get(name, [])) == 1}
        if not matched:
            return {}
        ids = sorted({row["id"] for row in matched.values()})
        placeholders = ",".join("?" for _ in ids)
        rows = [dict(row) for row in db.execute(f"""SELECT signal_uid, observation_date,
            value, source_timestamp, source_kind, forecast_horizon FROM signal_records
            WHERE signal_uid IN ({placeholders})
            ORDER BY source_timestamp DESC, observation_date DESC""", ids)]
    values_by_uid: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        values_by_uid.setdefault(row["signal_uid"], []).append(row)
    cutoff = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
    result: dict[str, dict[str, Any]] = {}
    for column, definition in matched.items():
        available = []
        for row in values_by_uid.get(definition["id"], []):
            try:
                timestamp = datetime.fromisoformat(row["source_timestamp"].replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if timestamp <= cutoff:
                available.append((timestamp, row))
        if available:
            result[column] = {
                "values": [{
                    "value": float(value["value"]),
                    "observation_date": value["observation_date"],
                    "source_timestamp": source_timestamp.isoformat(),
                    "signal_id": definition["signal_id"],
                    "source_name": ("PULSE article-text signal model"
                                    if value["source_kind"] == "article_text_ridge_shadow_v1"
                                    else definition["source_name"]),
                    "unit": definition["unit"],
                    "meaning": definition["state_definition"],
                    "source_kind": value["source_kind"],
                    "forecast_horizon": value["forecast_horizon"],
                } for source_timestamp, value in available],
            }
    return result


def _available_signal_value(column: str, cutoff: datetime,
                            records: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    values = records.get(column, {}).get("values", [])
    available = [value for value in values
                 if datetime.fromisoformat(value["source_timestamp"]) <= cutoff]
    # Signal series are carried forward between source updates in the training
    # frame. If a live source has no separate snapshot at this lag, use the
    # latest value known for the run rather than inventing a zero observation.
    pool = available or values
    return max(pool, key=lambda value: (value["source_timestamp"],
                                        value["observation_date"])) if pool else None


def _level(relative_change: float | None) -> str:
    if relative_change is None:
        return "uncompared"
    if relative_change >= 0.25:
        return "high"
    if relative_change >= 0.10:
        return "elevated"
    if relative_change <= -0.10:
        return "below_recent"
    return "typical"


def _predict(manifest: dict[str, Any], boosters: dict[str, xgb.Booster],
             as_of: date, records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    predictions = []
    current_day = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
    for drug, details in manifest["models"].items():
        base = dict(details["base_features"])
        day_of_year = as_of.timetuple().tm_yday
        base.update({
            "dow": float(as_of.weekday()),
            "month": float(as_of.month),
            "sin_year": float(math.sin(2 * math.pi * day_of_year / 365.25)),
            "cos_year": float(math.cos(2 * math.pi * day_of_year / 365.25)),
        })
        signal_details = []
        available_count = 0
        for column in details["selected_signals"]:
            lagged_values = {}
            latest_value = None
            for lag in (1, 7, 14):
                cutoff = current_day - timedelta(days=lag)
                value = _available_signal_value(column, cutoff, records)
                feature_name = f"{column}__lag{lag}"
                base[feature_name] = float(value["value"]) if value else 0.0
                lagged_values[str(lag)] = value
                if value and (latest_value is None or value["source_timestamp"] > latest_value["source_timestamp"]):
                    latest_value = value
            if latest_value:
                signal_details.append({**latest_value, "lag_values": lagged_values})
                available_count += 1
        ordered = details["features"]
        vector = np.asarray([[float(base.get(name, 0.0)) for name in ordered]], dtype=np.float32)
        matrix = xgb.DMatrix(vector, feature_names=ordered)
        predicted = max(0.0, float(boosters[drug].predict(matrix)[0]))
        trailing_mean = max(0.0, float(details["trailing_mean_14"]))
        baseline = trailing_mean * 14.0
        relative = ((predicted - baseline) / baseline) if baseline > 1e-9 else None
        predictions.append({
            "drug_name": drug,
            "predicted_units_14d": round(predicted, 2),
            "trailing_baseline_units_14d": round(baseline, 2),
            "relative_change": round(relative, 4) if relative is not None else None,
            "demand_level": _level(relative),
            "signal_count": available_count,
            "signals": signal_details,
        })
    predictions.sort(key=lambda row: (
        row["relative_change"] is None,
        -(row["relative_change"] if row["relative_change"] is not None else 0),
        -row["predicted_units_14d"], row["drug_name"],
    ))
    return predictions


def refresh_daily_signal_demand_outlook(as_of: date | None = None) -> dict[str, Any]:
    """Run and persist today's drug-level 14-day demand outlook."""
    signal_store.initialize()
    current_date = as_of or datetime.now(timezone.utc).date()
    started = datetime.now(timezone.utc)
    with signal_store.connect() as db:
        run_id = db.execute("""INSERT INTO refresh_runs(source_name, started_at, status)
            VALUES (?, ?, 'running')""", (SOURCE_NAME, started.isoformat())).lastrowid
    try:
        manifest, boosters = _load_artifacts()
        selected = {column for details in manifest["models"].values()
                    for column in details["selected_signals"]}
        records = _current_signal_rows(current_date, selected)
        predictions = _predict(manifest, boosters, current_date, records)
        finished = datetime.now(timezone.utc).isoformat()
        source_count = len(records)
        summary = {
            "forecast_horizon_days": 14,
            "model_version": manifest["model_version"],
            "signals_selected": len(selected),
            "signals_with_current_values": source_count,
            "training_end": max(details["training_end"] for details in manifest["models"].values()),
            "ranking": "relative to each drug's trailing 14-day sales baseline",
        }
        with signal_store.connect() as db:
            db.execute("""INSERT INTO demand_signal_outlook_runs
                (run_date, created_at, model_version, artifact_trained_at,
                 source_signal_count, drug_count, summary_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_date) DO UPDATE SET created_at=excluded.created_at,
                model_version=excluded.model_version,
                artifact_trained_at=excluded.artifact_trained_at,
                source_signal_count=excluded.source_signal_count,
                drug_count=excluded.drug_count, summary_json=excluded.summary_json""",
                (current_date.isoformat(), finished, manifest["model_version"],
                 manifest.get("trained_at"), source_count, len(predictions),
                 json.dumps(summary, sort_keys=True)))
            db.execute("DELETE FROM demand_signal_outlook WHERE run_date=?",
                       (current_date.isoformat(),))
            db.executemany("""INSERT INTO demand_signal_outlook
                (run_date, drug_name, predicted_units_14d, trailing_baseline_units_14d,
                 relative_change, demand_level, signal_count, signals_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [(current_date.isoformat(), row["drug_name"], row["predicted_units_14d"],
                  row["trailing_baseline_units_14d"], row["relative_change"],
                  row["demand_level"], row["signal_count"],
                  json.dumps(row["signals"], sort_keys=True)) for row in predictions])
            db.execute("DELETE FROM demand_signal_outlook_runs WHERE run_date < ?",
                       ((current_date - timedelta(days=400)).isoformat(),))
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='success', rows_written=?
                WHERE id=?""", (finished, len(predictions), run_id))
        return {"source": SOURCE_NAME, "status": "success", "run_date": current_date.isoformat(),
                "drugs": len(predictions), "signals_with_current_values": source_count,
                "finished_at": finished}
    except Exception as exc:
        with signal_store.connect() as db:
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='failed', error=?
                WHERE id=?""", (datetime.now(timezone.utc).isoformat(), str(exc)[:500], run_id))
        raise


def get_demand_outlook(*, run_date: date | None = None, days: int = 1) -> dict[str, Any]:
    signal_store.initialize()
    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")
    with signal_store.connect() as db:
        if run_date:
            runs = db.execute("""SELECT * FROM demand_signal_outlook_runs
                WHERE run_date=?""", (run_date.isoformat(),)).fetchall()
        else:
            runs = db.execute("""SELECT * FROM demand_signal_outlook_runs
                ORDER BY run_date DESC LIMIT ?""", (days,)).fetchall()
        results = []
        for run in runs:
            rows = db.execute("""SELECT * FROM demand_signal_outlook
                WHERE run_date=? ORDER BY relative_change DESC, predicted_units_14d DESC,
                drug_name""", (run["run_date"],)).fetchall()
            results.append({
                "run_date": run["run_date"], "created_at": run["created_at"],
                "model_version": run["model_version"],
                "artifact_trained_at": run["artifact_trained_at"],
                "source_signal_count": run["source_signal_count"],
                "drug_count": run["drug_count"], "summary": json.loads(run["summary_json"]),
                "drugs": [{**dict(row), "signals": json.loads(row["signals_json"])}
                          for row in rows],
            })
    return {"outlooks": results, "count": len(results)}
