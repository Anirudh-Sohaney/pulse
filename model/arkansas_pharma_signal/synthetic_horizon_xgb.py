"""Per-drug short-horizon XGBoost comparison on the synthetic clinic panel.

This is a synthetic capability benchmark. The archived signal table records
source periods, not point-in-time publication vintages, so its period-end
availability rule must not be interpreted as a live historical replay.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from xgboost import XGBRegressor


ROOT = Path(__file__).resolve().parents[2]
SALES = ROOT / "data/synthetic_pharmacy_data/arkansas_clinic_daily_pharmacy_sales.csv"
SIGNALS = ROOT / "test/test_Signals/signals_2023_2025.csv.gz"
OUTPUT = ROOT / "model/trained/synthetic_horizon_xgb_v1"
HORIZONS = (1, 7, 14, 30)
LAGS = (1, 2, 3, 7, 14, 21, 28, 56)
WINDOWS = (3, 7, 14, 28, 56)
PROFILES = {
    "square_220": dict(n_estimators=220, max_depth=2, learning_rate=.04,
                       min_child_weight=8, subsample=.85, colsample_bytree=.5,
                       reg_lambda=10, objective="reg:squarederror"),
    "square_120": dict(n_estimators=120, max_depth=2, learning_rate=.04,
                       min_child_weight=8, subsample=.85, colsample_bytree=.7,
                       reg_lambda=10, objective="reg:squarederror"),
    "poisson_120": dict(n_estimators=120, max_depth=2, learning_rate=.04,
                        min_child_weight=8, subsample=.85, colsample_bytree=.7,
                        reg_lambda=10, objective="count:poisson", max_delta_step=.7),
}
SIGNAL_LAG_SETS = {"one_day": (1,), "one_seven_fourteen": (1, 7, 14)}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def feature_key(row: pd.Series) -> str:
    parts = (row.get("signal_origin"), row.get("signal_id"), row.get("cadence"),
             row.get("geography_level"), row.get("geography_id"), row.get("entity_key"))
    return "|".join("" if pd.isna(value) else str(value) for value in parts)


def load_signals(dates: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict]:
    rows = pd.read_csv(SIGNALS, parse_dates=["period_end"], low_memory=False)
    rows["value"] = pd.to_numeric(rows["value"], errors="coerce")
    rows = rows.dropna(subset=["period_end", "value"]).copy()
    rows["feature_key"] = rows.apply(feature_key, axis=1)
    rows = rows.sort_values(["feature_key", "period_end"]).drop_duplicates(
        ["feature_key", "period_end"], keep="last")
    series = {}
    metadata = {}
    for key, part in rows.groupby("feature_key", sort=True):
        observed = part.set_index("period_end")["value"].sort_index()
        # Forecast features at origin t use the value available at t-1.
        # A period ending on t is not used until the following origin.
        series[key] = observed.reindex(dates, method="ffill").shift(1)
        first = part.iloc[0]
        metadata[key] = {field: None if pd.isna(first.get(field)) else str(first[field])
                         for field in ("signal_origin", "signal_id", "cadence",
                                       "geography_level", "geography_id", "entity_key",
                                       "source_name", "source_url")}
    matrix = pd.DataFrame(series, index=dates, dtype=float)
    return matrix.fillna(0.0), metadata


def load_sales() -> pd.DataFrame:
    frame = pd.read_csv(SALES, parse_dates=["date"])
    required = {"date", "drug_name", "units_sold", "unit_price_usd", "stockout_flag"}
    if not required.issubset(frame):
        raise ValueError(f"Synthetic sales missing {sorted(required - set(frame))}")
    frame = frame.sort_values(["drug_name", "date"]).reset_index(drop=True)
    if frame.duplicated(["drug_name", "date"]).any():
        raise ValueError("Duplicate drug/date in synthetic sales")
    if frame["units_sold"].isna().any() or frame["units_sold"].lt(0).any():
        raise ValueError("Invalid synthetic demand counts")
    grouped = frame.groupby("drug_name", sort=False)
    units = grouped["units_sold"]
    for lag in LAGS:
        frame[f"sales_lag_{lag}"] = units.shift(lag)
    shifted = units.shift(1)
    for window in WINDOWS:
        by_drug = shifted.groupby(frame["drug_name"])
        frame[f"sales_mean_{window}"] = by_drug.transform(
            lambda values: values.rolling(window, min_periods=window).mean())
        frame[f"sales_std_{window}"] = by_drug.transform(
            lambda values: values.rolling(window, min_periods=window).std())
    frame["dow"] = frame.date.dt.dayofweek
    frame["weekofyear"] = frame.date.dt.isocalendar().week.astype(int)
    frame["month"] = frame.date.dt.month
    frame["sin_year"] = np.sin(2 * np.pi * frame.date.dt.dayofyear / 365.25)
    frame["cos_year"] = np.cos(2 * np.pi * frame.date.dt.dayofyear / 365.25)
    frame["price_lag1"] = grouped.unit_price_usd.shift(1)
    frame["stockout_lag1"] = grouped.stockout_flag.shift(1)
    for horizon in HORIZONS:
        frame[f"target_{horizon}d"] = units.transform(
            lambda values: values.shift(-1).rolling(horizon, min_periods=horizon)
            .sum().shift(1 - horizon))
    return frame


def sales_columns() -> list[str]:
    return ([f"sales_lag_{lag}" for lag in LAGS]
            + [f"sales_{stat}_{window}" for window in WINDOWS
               for stat in ("mean", "std")]
            + ["dow", "weekofyear", "month", "sin_year", "cos_year",
               "price_lag1", "stockout_lag1"])


def select_five(signals: pd.DataFrame, target: np.ndarray,
                train_mask: np.ndarray) -> list[dict]:
    """Rank one-day-lagged values on training rows; remove exact aliases."""
    x = signals.to_numpy(dtype=float)[train_mask]
    y = np.asarray(target, dtype=float)[train_mask]
    x_centered = x - x.mean(axis=0)
    y_centered = y - y.mean()
    denominator = np.sqrt((x_centered * x_centered).sum(axis=0)
                          * (y_centered * y_centered).sum())
    correlation = np.divide(x_centered.T @ y_centered, denominator,
                            out=np.zeros(x.shape[1]), where=denominator > 1e-12)
    order = np.argsort(-np.abs(correlation), kind="stable")
    selected = []
    seen_vectors = set()
    names = signals.columns.to_list()
    for index in order:
        if abs(correlation[index]) <= 1e-12:
            break
        signature = hashlib.blake2b(x[:, index].tobytes(), digest_size=16).digest()
        if signature in seen_vectors:
            continue
        seen_vectors.add(signature)
        selected.append({"signal_key": names[index],
                         "train_correlation": float(correlation[index])})
        if len(selected) == 5:
            break
    if len(selected) != 5:
        raise ValueError(f"Only {len(selected)} distinct nonconstant signals available")
    return selected


def model(profile: str) -> XGBRegressor:
    return XGBRegressor(**PROFILES[profile], tree_method="hist", n_jobs=1,
                        random_state=20250915, verbosity=0)


def score(y: np.ndarray, prediction: np.ndarray) -> dict:
    y = np.asarray(y, dtype=float)
    prediction = np.maximum(np.asarray(prediction, dtype=float), 0)
    error = np.abs(y - prediction)
    return {"n": len(y), "wape": float(error.sum() / max(y.sum(), 1e-12)),
            "mae": float(error.mean()),
            "rmse": float(np.sqrt(np.square(y - prediction).mean())),
            "within_20pct": float((error / np.maximum(y, 1) <= .2).mean())}


def signal_columns(selected: list[dict], lag_set: str) -> list[str]:
    return [f"signal_{i}_lag_{lag}" for i in range(5)
            for lag in SIGNAL_LAG_SETS[lag_set]]


def add_selected_signals(frame: pd.DataFrame, signals: pd.DataFrame,
                         selected: list[dict]) -> pd.DataFrame:
    frame = frame.copy()
    for i, item in enumerate(selected):
        values = signals[item["signal_key"]]
        frame[f"signal_{i}_lag_1"] = values.to_numpy()
        frame[f"signal_{i}_lag_7"] = values.shift(6).fillna(0).to_numpy()
        frame[f"signal_{i}_lag_14"] = values.shift(13).fillna(0).to_numpy()
    return frame


def evaluate_drug(frame: pd.DataFrame, signals: pd.DataFrame,
                  horizon: int, validation_start: pd.Timestamp,
                  test_start: pd.Timestamp) -> tuple[dict, dict]:
    target_col = f"target_{horizon}d"
    base = sales_columns()
    usable = frame.dropna(subset=base + [target_col]).copy()
    signal_rows = signals.loc[usable.date]
    usable = usable.reset_index(drop=True)
    signal_rows = signal_rows.reset_index(drop=True)
    target = usable[target_col].to_numpy(float)
    train_mask = (usable.date < validation_start - pd.Timedelta(days=horizon)).to_numpy()
    validation_mask = ((usable.date >= validation_start)
                       & (usable.date < test_start - pd.Timedelta(days=horizon))).to_numpy()
    final_train_mask = (usable.date < test_start - pd.Timedelta(days=horizon)).to_numpy()
    test_mask = (usable.date >= test_start).to_numpy()
    if min(train_mask.sum(), validation_mask.sum(), test_mask.sum()) < 30:
        raise ValueError("Insufficient separated train, validation, or test origins")
    selected = select_five(signal_rows, target, train_mask)
    work = add_selected_signals(usable, signal_rows, selected)
    base_array = work[base].to_numpy(float)
    validation = []
    for profile in PROFILES:
        fitted = model(profile).fit(base_array[train_mask], target[train_mask])
        validation.append({"kind": "sales_only", "profile": profile,
                           "lag_set": None,
                           "score": score(target[validation_mask],
                                          fitted.predict(base_array[validation_mask]))})
        for lag_set in SIGNAL_LAG_SETS:
            cols = base + signal_columns(selected, lag_set)
            x = work[cols].to_numpy(float)
            fitted = model(profile).fit(x[train_mask], target[train_mask])
            validation.append({"kind": "top_five_signals", "profile": profile,
                               "lag_set": lag_set,
                               "score": score(target[validation_mask],
                                              fitted.predict(x[validation_mask]))})
    best = {kind: min((row for row in validation if row["kind"] == kind),
                      key=lambda row: (row["score"]["wape"], row["profile"],
                                       row["lag_set"] or ""))
            for kind in ("sales_only", "top_five_signals")}
    tested = {}
    models = {}
    for kind, setting in best.items():
        cols = base if kind == "sales_only" else base + signal_columns(
            selected, setting["lag_set"])
        x = work[cols].to_numpy(float)
        fitted = model(setting["profile"]).fit(x[final_train_mask],
                                                target[final_train_mask])
        prediction = fitted.predict(x[test_mask])
        tested[kind] = score(target[test_mask], prediction)
        models[kind] = (fitted, cols)
    chosen = ("top_five_signals" if best["top_five_signals"]["score"]["wape"]
              < best["sales_only"]["score"]["wape"] else "sales_only")
    row = {"drug_name": str(frame.drug_name.iloc[0]), "horizon_days": horizon,
           "selected_signals": selected, "validation": validation,
           "selected_settings": best, "test": tested,
           "chosen_on_validation": chosen,
           "train_origins": int(train_mask.sum()),
           "validation_origins": int(validation_mask.sum()),
           "final_train_origins": int(final_train_mask.sum()),
           "test_origins": int(test_mask.sum()),
           "test_target_sum": float(target[test_mask].sum())}
    return row, models


def train(output: Path = OUTPUT) -> dict:
    sales = load_sales()
    dates = pd.DatetimeIndex(sorted(sales.date.unique()))
    signals, metadata = load_signals(dates)
    validation_start = dates[int(len(dates) * .6)]
    test_start = dates[int(len(dates) * .8)]
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for drug, part in sales.groupby("drug_name", sort=True):
        part = part.sort_values("date")
        for horizon in HORIZONS:
            row, models = evaluate_drug(part, signals, horizon,
                                        validation_start, test_start)
            slug = re.sub(r"[^a-z0-9]+", "_", drug.lower()).strip("_")
            row["artifacts"] = {}
            for kind, (fitted, columns) in models.items():
                name = f"{slug}_{horizon}d_{kind}.json.gz"
                payload = gzip.compress(fitted.get_booster().save_raw(
                    raw_format="json"), mtime=0)
                (output / name).write_bytes(payload)
                row["artifacts"][kind] = {
                    "file": name, "sha256": hashlib.sha256(payload).hexdigest(),
                    "feature_columns": columns}
            rows.append(row)
            print(f"{drug}: {horizon}d signal {row['test']['top_five_signals']['wape']:.3f} "
                  f"sales {row['test']['sales_only']['wape']:.3f}", flush=True)
    summary = {}
    for horizon in HORIZONS:
        subset = [row for row in rows if row["horizon_days"] == horizon]
        summary[str(horizon)] = {
            "drugs": len(subset),
            "signal_better_drugs": sum(row["test"]["top_five_signals"]["wape"]
                                       < row["test"]["sales_only"]["wape"]
                                       for row in subset),
            "mean_drug_signal_wape": float(np.mean([
                row["test"]["top_five_signals"]["wape"] for row in subset])),
            "mean_drug_sales_wape": float(np.mean([
                row["test"]["sales_only"]["wape"] for row in subset])),
            "pooled_signal_wape": sum(row["test"]["top_five_signals"]["wape"]
                                      * row["test_target_sum"] for row in subset)
                                  / sum(row["test_target_sum"] for row in subset),
            "pooled_sales_wape": sum(row["test"]["sales_only"]["wape"]
                                     * row["test_target_sum"] for row in subset)
                                 / sum(row["test_target_sum"] for row in subset),
        }
    for horizon in HORIZONS:
        subset = [row for row in rows if row["horizon_days"] == horizon]
        total = sum(row["test_target_sum"] for row in subset)
        summary[str(horizon)]["validation_selected_pooled_wape"] = sum(
            row["test"][row["chosen_on_validation"]]["wape"]
            * row["test_target_sum"] for row in subset) / total
        summary[str(horizon)]["validation_selected_signal_drugs"] = sum(
            row["chosen_on_validation"] == "top_five_signals" for row in subset)
    manifest = {
        "model_version": "synthetic_horizon_xgb_v1",
        "scope": "single synthetic pharmacy; 30 drug series; future recorded units sold",
        "signal_availability": "archived source period end, shifted one day; publication vintages unavailable",
        "validation_start": validation_start.date().isoformat(),
        "test_start": test_start.date().isoformat(),
        "target_horizons_days": list(HORIZONS),
        "candidate_signal_count": signals.shape[1],
        "sales_sha256": digest(SALES), "signals_sha256": digest(SIGNALS),
        "signal_metadata": metadata, "profiles": PROFILES,
        "summary": summary, "drugs": rows,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def replay_saved(drug_name: str, horizon: int, as_of: str,
                 output: Path = OUTPUT) -> dict:
    """Run a saved model on a synthetic test-period origin."""
    manifest = json.loads((output / "manifest.json").read_text())
    if digest(SALES) != manifest["sales_sha256"] or digest(SIGNALS) != manifest["signals_sha256"]:
        raise ValueError("Synthetic input data no longer matches the saved model")
    origin = pd.Timestamp(as_of).normalize()
    if origin < pd.Timestamp(manifest["test_start"]):
        raise ValueError("Saved booster replay is restricted to the held-out test period")
    matches = [row for row in manifest["drugs"]
               if row["drug_name"] == drug_name and row["horizon_days"] == horizon]
    if len(matches) != 1:
        raise ValueError("Unknown drug or target horizon")
    row = matches[0]
    sales = load_sales()
    part = sales[sales.drug_name.eq(drug_name)].reset_index(drop=True)
    matches_origin = part.index[part.date.eq(origin)]
    if len(matches_origin) != 1:
        raise ValueError("No synthetic sales feature row on the requested date")
    dates = pd.DatetimeIndex(sorted(sales.date.unique()))
    signals, _ = load_signals(dates)
    signal_rows = signals.loc[part.date].reset_index(drop=True)
    work = add_selected_signals(part, signal_rows, row["selected_signals"])
    kind = row["chosen_on_validation"]
    artifact = row["artifacts"][kind]
    compressed = (output / artifact["file"]).read_bytes()
    if hashlib.sha256(compressed).hexdigest() != artifact["sha256"]:
        raise ValueError("Saved booster checksum mismatch")
    booster = xgb.Booster()
    booster.load_model(bytearray(gzip.decompress(compressed)))
    vector = work.loc[matches_origin, artifact["feature_columns"]].to_numpy(float)
    if not np.isfinite(vector).all():
        raise ValueError("Missing synthetic feature value at requested origin")
    prediction = max(0.0, float(booster.predict(xgb.DMatrix(vector))[0]))
    return {"drug_name": drug_name, "as_of_date": origin.date().isoformat(),
            "target_start": (origin + pd.Timedelta(days=1)).date().isoformat(),
            "target_end": (origin + pd.Timedelta(days=horizon)).date().isoformat(),
            "horizon_days": horizon, "predicted_units_sold": round(prediction, 2),
            "model_kind": kind, "selected_signals": row["selected_signals"] if kind == "top_five_signals" else [],
            "note": "Historical synthetic replay using assumed signal availability; not a live pharmacy forecast."}
