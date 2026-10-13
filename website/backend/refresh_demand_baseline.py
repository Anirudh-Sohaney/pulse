"""Publish an evaluated two-year Arkansas Part D claims-state projection.

This is an annual public claims proxy. It does not estimate pharmacy orders,
inventory, or current dispensing, and it does not rewrite legacy signal rows.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .refresh_cms_partd_source import source_snapshot
from .signal_store import connect, initialize, signal_uid


SOURCE_KIND = "cms_partd_two_year_tiered_v4"
MIN_FOLDS = 4
MIN_BALANCED_ACCURACY = 0.75
MIN_SELECTED_HOLDOUT_BALANCED_ACCURACY = 0.85
MIN_CUTOFF_LOG_DISTANCE = 0.25
MAX_PUBLIC_DRUG_OUTPUTS = 1200
CMS_SIGNAL_SOURCE_NAME = "PULSE production CMS Part D demand output"
CMS_SIGNAL_SOURCE_URL = (
    "https://data.cms.gov/provider-summary-by-type-of-service/"
    "medicare-part-d-prescribers/medicare-part-d-prescribers-by-geography-and-drug"
)


def load_claims(path: Path) -> tuple[pd.DataFrame, str]:
    if not path.is_file():
        raise FileNotFoundError(f"CMS Part D source missing: {path}")
    raw = path.read_bytes()
    if raw.startswith(b"version https://git-lfs.github.com/spec/v1"):
        raise ValueError("CMS Part D source is a Git LFS pointer; run git lfs pull")
    digest = hashlib.sha256(raw).hexdigest()
    frame = pd.read_csv(path, compression="gzip")
    required = {"year", "state", "drug_key", "demand_claims"}
    if not required.issubset(frame):
        raise ValueError(f"CMS Part D source missing columns: {sorted(required - set(frame))}")
    frame = frame[list(required)].copy()
    frame["year"] = pd.to_numeric(frame["year"], errors="raise").astype(int)
    frame["demand_claims"] = pd.to_numeric(frame["demand_claims"], errors="raise")
    if (frame["state"].astype(str).str.lower() != "arkansas").any():
        raise ValueError("CMS Part D source contains non-Arkansas rows")
    if frame["drug_key"].isna().any() or frame["drug_key"].astype(str).str.strip().eq("").any():
        raise ValueError("CMS Part D source contains an empty drug key")
    if (frame["demand_claims"] < 0).any() or not np.isfinite(frame["demand_claims"]).all():
        raise ValueError("CMS Part D source contains invalid claims")
    if frame.duplicated(["year", "drug_key"]).any():
        raise ValueError("CMS Part D source has duplicate drug/year rows")
    return frame.sort_values(["year", "drug_key"]).reset_index(drop=True), digest


def paired_years(frame: pd.DataFrame) -> pd.DataFrame:
    current = frame[["year", "drug_key", "demand_claims"]].rename(
        columns={"demand_claims": "current_claims"})
    target = frame[["year", "drug_key", "demand_claims"]].rename(
        columns={"demand_claims": "target_claims"}).copy()
    target["year"] -= 2
    return current.merge(target, on=["year", "drug_key"], validate="one_to_one")


def evaluate(frame: pd.DataFrame) -> dict:
    pairs = paired_years(frame)
    latest_feature_year = int(pairs["year"].max())
    folds = []
    for test_year in range(latest_feature_year - 3, latest_feature_year + 1):
        # At the October forecast date in target year t+2, annual claims
        # through feature year t are available. A validation pair from t-1
        # would need target-year t+1 claims, released too late for that date.
        validation_year = test_year - 2
        training = pairs[pairs["year"] < validation_year]
        validation = pairs[pairs["year"] == validation_year]
        test = pairs[pairs["year"] == test_year]
        if training["year"].nunique() < 4 or validation.empty or test.empty:
            continue
        thresholds = np.quantile(training["target_claims"], [.2, .4, .6, .8])
        actual = np.digitize(test["target_claims"], thresholds)
        predicted = np.digitize(test["current_claims"], thresholds)
        if set(actual.tolist()) != set(range(5)):
            raise ValueError(f"Two-year test fold {test_year} lacks a demand category")
        recalls = [float(np.mean(predicted[actual == state] == state))
                   for state in range(5) if np.any(actual == state)]
        folds.append({"feature_year": test_year, "target_year": test_year + 2,
                      "train_feature_year_max": test_year - 3,
                      "validation_feature_year": validation_year,
                      "test_rows": int(len(test)),
                      "accuracy": float(np.mean(predicted == actual)),
                      "balanced_accuracy": float(np.mean(recalls)),
                      "thresholds": [float(value) for value in thresholds]})
    if len(folds) < MIN_FOLDS:
        raise ValueError(f"Two-year baseline has only {len(folds)} eligible folds")
    if any(item["balanced_accuracy"] < MIN_BALANCED_ACCURACY for item in folds):
        raise ValueError("Two-year baseline failed its chronological accuracy floor")
    return {"protocol": "rolling_origin_two_year_partd_claim_state_release_safe_v2",
            "scope": "Arkansas Medicare Part D annual generic-drug claims proxy",
            "method": "two_year_state_persistence",
            "fold_count": len(folds), "test_rows": sum(item["test_rows"] for item in folds),
            "mean_accuracy": float(np.mean([item["accuracy"] for item in folds])),
            "mean_balanced_accuracy": float(np.mean(
                [item["balanced_accuracy"] for item in folds])),
            "folds": folds}


def _selection_tier(claims: float, thresholds: list[float] | np.ndarray,
                    prior_claims: float | None, historical_matches: list[bool],
                    required_matches: int) -> str | None:
    if len(historical_matches) == required_matches and all(historical_matches):
        return "high_history"
    if prior_claims is None:
        return None
    state = int(np.digitize(claims, thresholds))
    if state != int(np.digitize(prior_claims, thresholds)):
        return None
    distance = min(abs(math.log1p(claims) - math.log1p(boundary))
                   for boundary in thresholds)
    return "stable_far_from_cutoff" if distance >= MIN_CUTOFF_LOG_DISTANCE else None


def _selected_fold(frame: pd.DataFrame, pairs: pd.DataFrame,
                   selected_fold: dict, prior_folds: list[dict]) -> dict:
    year = selected_fold["feature_year"]
    thresholds = selected_fold["thresholds"]
    prior_claims = frame.loc[frame["year"] == year - 1].set_index("drug_key")["demand_claims"]
    prior_results: dict[str, list[bool]] = {}
    for fold in prior_folds:
        for row in pairs[pairs["year"] == fold["feature_year"]].itertuples():
            prior_results.setdefault(row.drug_key, []).append(
                int(np.digitize(row.target_claims, fold["thresholds"])) ==
                int(np.digitize(row.current_claims, fold["thresholds"])))
    rows = []
    for row in pairs[pairs["year"] == year].itertuples():
        tier = _selection_tier(
            row.current_claims, thresholds,
            float(prior_claims[row.drug_key]) if row.drug_key in prior_claims else None,
            prior_results.get(row.drug_key, []), len(prior_folds))
        if tier:
            rows.append((tier, int(np.digitize(row.target_claims, thresholds)),
                         int(np.digitize(row.current_claims, thresholds))))
    if not rows:
        raise ValueError(f"No selected drugs in chronological fold {year}")
    actual = np.array([row[1] for row in rows])
    predicted = np.array([row[2] for row in rows])
    if set(actual.tolist()) != set(range(5)):
        raise ValueError(f"Selected fold {year} lacks a demand category")
    return {"feature_year": year, "rows": len(rows),
            "accuracy": float(np.mean(actual == predicted)),
            "balanced_accuracy": float(np.mean([
                np.mean(predicted[actual == state] == state) for state in range(5)])),
            "tiers": {tier: {"rows": sum(item[0] == tier for item in rows),
                             "accuracy": float(np.mean([
                                 item[1] == item[2] for item in rows if item[0] == tier]))}
                      for tier in ("high_history", "stable_far_from_cutoff")}}


def consistent_drugs(frame: pd.DataFrame, metrics: dict,
                     publication_thresholds: np.ndarray) -> tuple[dict[str, str], dict]:
    """Publish strong history or stable claims far from a state boundary."""
    folds = metrics["folds"]
    if len(folds) != 4:
        raise ValueError("Drug consistency selection requires four chronological folds")
    pairs = paired_years(frame)
    validation = _selected_fold(frame, pairs, folds[2], folds[:2])
    holdout = _selected_fold(frame, pairs, folds[3], folds[:3])
    if holdout["balanced_accuracy"] < MIN_SELECTED_HOLDOUT_BALANCED_ACCURACY:
        raise ValueError("Selected drugs failed the later chronological fold")
    history: dict[str, list[bool]] = {}
    for fold in folds[:3]:
        for row in pairs[pairs["year"] == fold["feature_year"]].itertuples():
            history.setdefault(row.drug_key, []).append(
                int(np.digitize(row.target_claims, fold["thresholds"])) ==
                int(np.digitize(row.current_claims, fold["thresholds"])))
    source_year = int(frame["year"].max())
    prior_claims = frame.loc[frame["year"] == source_year - 1].set_index("drug_key")["demand_claims"]
    selected: dict[str, str] = {}
    for row in frame[frame["year"] == source_year].itertuples():
        tier = _selection_tier(
            row.demand_claims, publication_thresholds,
            float(prior_claims[row.drug_key]) if row.drug_key in prior_claims else None,
            history.get(row.drug_key, []), 3)
        if tier:
            selected[row.drug_key] = tier
    if not selected or len(selected) > MAX_PUBLIC_DRUG_OUTPUTS:
        raise ValueError("Tiered selection produced no drugs or exceeded the public output cap")
    return selected, {"selection_feature_years": [fold["feature_year"] for fold in folds[:3]],
                      "cutoff_log_distance": MIN_CUTOFF_LOG_DISTANCE,
                      "selected_drugs": len(selected),
                      "selection_tiers": {tier: sum(value == tier for value in selected.values())
                                          for tier in ("high_history", "stable_far_from_cutoff")},
                      "validation": validation, "holdout": holdout,
                      "holdout_accuracy": holdout["accuracy"],
                      "holdout_balanced_accuracy": holdout["balanced_accuracy"],
                      "holdout_rows": holdout["rows"]}


def ensure_drug_signal_definitions(db, drug_keys: list[str]) -> int:
    """Give each evaluated drug output a stable catalog ID before recording it."""
    definitions = []
    for drug_key in sorted(set(drug_keys)):
        identity = {
            "signal_origin": "derived_demand_output",
            "signal_id": f"cms_part_d_demand_state::{drug_key}",
            "cadence": "annual",
            "geography_level": "state",
            "geography_id": "AR",
            "entity_key": drug_key,
        }
        definitions.append((
            signal_uid(identity),
            identity["signal_origin"], identity["signal_id"], identity["cadence"],
            identity["geography_level"], identity["geography_id"], identity["entity_key"],
            "five_state_demand", CMS_SIGNAL_SOURCE_NAME, CMS_SIGNAL_SOURCE_URL,
            "0=Lowest to 4=Highest", "predicted_demand_state",
        ))
    before = db.total_changes
    db.executemany("""INSERT OR IGNORE INTO signal_definitions
        (id, signal_origin, signal_id, cadence, geography_level, geography_id,
         entity_key, unit, source_name, source_url, state_definition, model_output_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", definitions)
    return db.total_changes - before


def refresh_demand_baseline() -> dict:
    initialize()
    started = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        run_id = db.execute("""INSERT INTO refresh_runs(source_name, started_at, status)
            VALUES (?, ?, 'running')""", (SOURCE_KIND, started)).lastrowid
    try:
        path, manifest = source_snapshot()
        frame, source_sha = load_claims(path)
        source_year = int(frame["year"].max())
        target_year = source_year + 2
        if target_year < datetime.now(timezone.utc).year:
            raise ValueError(f"CMS Part D source ends in {source_year}; two-year target {target_year} is stale")
        metrics = evaluate(frame)
        pairs = paired_years(frame)
        thresholds = np.quantile(pairs["target_claims"], [.2, .4, .6, .8])
        qualified_keys, selection = consistent_drugs(frame, metrics, thresholds)
        metrics["publication_selection"] = selection
        metrics["publication_thresholds_claims"] = [float(value) for value in thresholds]
        current = frame[frame["year"] == source_year].copy()
        current = current[current["drug_key"].isin(qualified_keys)].copy()
        if current.empty:
            raise ValueError("CMS Part D source has no latest-year drugs")
        current["demand_state"] = np.digitize(current["demand_claims"], thresholds)
        current["selection_tier"] = current["drug_key"].map(qualified_keys)
        years = manifest.get("years") or []
        urls = manifest.get("source_urls") or []
        actual_years = sorted(int(year) for year in frame["year"].unique())
        if years != actual_years or len(urls) != len(years):
            raise ValueError("CMS Part D source and manifest years disagree")
        source_url = urls[years.index(source_year)]
        run_hash = hashlib.sha256(json.dumps(
            [SOURCE_KIND, source_sha, source_year, target_year],
            separators=(",", ":")).encode()).hexdigest()
        finished = datetime.now(timezone.utc).isoformat()
        with connect() as db:
            existing = db.execute("SELECT metrics_json FROM demand_forecast_runs WHERE run_hash=?",
                                  (run_hash,)).fetchone()
            inserted = 0
            if not existing:
                db.execute("""INSERT INTO demand_forecast_runs
                    (run_hash, source_sha256, source_year, target_year, created_at,
                     method, source_url, metrics_json, row_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_hash, source_sha, source_year, target_year, finished,
                     SOURCE_KIND, source_url, json.dumps(metrics), len(current)))
                db.executemany("""INSERT INTO demand_forecasts
                    (run_hash, drug_key, demand_state, observed_claims, selection_tier)
                    VALUES (?, ?, ?, ?, ?)""",
                    [(run_hash, row.drug_key, int(row.demand_state), float(row.demand_claims),
                      row.selection_tier)
                     for row in current.itertuples()])
                inserted = len(current)
            elif (json.loads(existing["metrics_json"]).get("publication_thresholds_claims")
                  != metrics["publication_thresholds_claims"]):
                # Backfill exact category boundaries without changing predictions
                # or the original publication timestamp.
                db.execute("""UPDATE demand_forecast_runs SET metrics_json=?
                    WHERE run_hash=?""", (json.dumps(metrics), run_hash))
            definitions_inserted = ensure_drug_signal_definitions(
                db, current["drug_key"].astype(str).tolist())
            definitions = {row["entity_key"]: row["id"] for row in db.execute("""
                SELECT id, entity_key FROM signal_definitions
                WHERE signal_origin='derived_demand_output'
                  AND signal_id LIKE 'cms_part_d_demand_state::%'""")}
            signal_rows = 0
            for row in current.itertuples():
                uid = definitions.get(row.drug_key)
                if uid is None:
                    continue
                row_hash = hashlib.sha256(json.dumps(
                    [uid, run_hash, target_year], separators=(",", ":")
                ).encode()).hexdigest()
                before = db.total_changes
                db.execute("""INSERT OR IGNORE INTO signal_records
                    (row_hash, signal_uid, signal_date, observation_date, value,
                     source_timestamp, ingested_at, data_quality, missingness,
                     forecast_horizon, source_kind, source_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)""",
                    (row_hash, uid, finished[:10], f"{source_year}-12-31",
                     int(row.demand_state), finished, finished,
                     f"evaluated_two_year_claim_state_projection;selection_tier={row.selection_tier};not_dispensing",
                     str(target_year), SOURCE_KIND, source_url))
                signal_rows += db.total_changes - before
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='success',
                rows_written=? WHERE id=?""",
                       (finished, inserted + definitions_inserted + signal_rows, run_id))
        return {"source": SOURCE_KIND, "source_year": source_year,
                "target_year": target_year, "drugs": len(current),
                "mean_balanced_accuracy": metrics["mean_balanced_accuracy"],
                "rows_written": inserted, "definitions_written": definitions_inserted,
                "catalog_rows_written": signal_rows,
                "finished_at": finished}
    except Exception as exc:
        with connect() as db:
            db.execute("""UPDATE refresh_runs SET finished_at=?, status='failed', error=?
                WHERE id=?""", (datetime.now(timezone.utc).isoformat(), str(exc)[:500], run_id))
        raise
