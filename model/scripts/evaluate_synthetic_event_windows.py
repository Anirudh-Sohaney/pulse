"""Evaluate the fixed signal recipe on tagged synthetic event windows.

This is an event-slice reproduction of the 14-day XGBoost benchmark. It uses
the local frozen signal input and synthetic sales panel; event demand is partly
encoded from those same signals by the generator, so the result is a
conditional capability check rather than external validation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBRegressor


ROOT = Path(__file__).resolve().parents[2]
SALES_PATH = ROOT / "data/synthetic_pharmacy_data/arkansas_clinic_daily_pharmacy_sales.csv"
SIGNAL_PATH = ROOT / "test/test_Signals/signals_2023_2025.csv.gz"
OUTPUT_PATH = ROOT / "model/docs/synthetic_event_window_metrics.json"
TEST_START = pd.Timestamp("2025-05-26")
EVENT_TAG = re.compile(
    r"dated_supply_outbreak_response|flu_202[34]_[0-9]+|"
    r"covid_summer_2025_outpatient_pulse"
)
PROFILE = {
    "n_estimators": 220,
    "max_depth": 2,
    "learning_rate": 0.04,
    "min_child_weight": 8,
    "subsample": 0.85,
    "colsample_bytree": 0.5,
    "reg_lambda": 10,
    "objective": "reg:squarederror",
    "tree_method": "hist",
    "n_jobs": 1,
    "random_state": 20250915,
    "verbosity": 0,
}


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", str(value)).strip("_")[:90]


def make_sales_frame() -> pd.DataFrame:
    frame = pd.read_csv(SALES_PATH, parse_dates=["date"])
    frame = frame.sort_values(["drug_name", "date"]).reset_index(drop=True)
    grouped = frame.groupby("drug_name", sort=False)["units_sold"]
    frame["target_14d"] = grouped.transform(
        lambda values: values.shift(-1).rolling(14, min_periods=14).sum().shift(-13))
    for lag in (1, 2, 3, 7, 14, 21, 28, 56):
        frame[f"lag_{lag}"] = grouped.shift(lag)
    shifted = grouped.shift(1)
    for window in (3, 7, 14, 28, 56):
        shifted_group = shifted.groupby(frame["drug_name"])
        frame[f"mean_{window}"] = shifted_group.transform(
            lambda values: values.rolling(window, min_periods=window).mean())
        frame[f"std_{window}"] = shifted_group.transform(
            lambda values: values.rolling(window, min_periods=window).std())
    frame["dow"] = frame.date.dt.dayofweek
    frame["weekofyear"] = frame.date.dt.isocalendar().week.astype(int)
    frame["month"] = frame.date.dt.month
    day_of_year = frame.date.dt.dayofyear
    frame["sin_year"] = np.sin(2 * np.pi * day_of_year / 365.25)
    frame["cos_year"] = np.cos(2 * np.pi * day_of_year / 365.25)
    frame["price_lag1"] = frame.groupby("drug_name").unit_price_usd.shift(1)
    frame["stockout_lag1"] = frame.groupby("drug_name").stockout_flag.shift(1)
    return frame


def make_signal_matrix(dates: pd.DatetimeIndex) -> pd.DataFrame:
    source = pd.read_csv(SIGNAL_PATH, parse_dates=["signal_date", "period_end"],
                         low_memory=False)
    source["value"] = pd.to_numeric(source["value"], errors="coerce")
    source = source.dropna(subset=["value", "period_end"])
    source = source[source.period_end.dt.year.between(2022, 2025)].copy()
    key_parts = ["signal_origin", "signal_id", "cadence", "geography_level",
                 "geography_id", "entity_key"]
    keys = source[key_parts].fillna("").astype(str).agg("|".join, axis=1)
    source["feature_key"] = keys.map(safe_name)
    source = (source.sort_values(["feature_key", "period_end"])
              .drop_duplicates(["feature_key", "period_end"], keep="last"))
    columns = {}
    for key, group in source.groupby("feature_key", sort=True):
        series = group.set_index("period_end")["value"].sort_index()
        series = series[~series.index.duplicated(keep="last")]
        columns[key] = series.reindex(dates, method="ffill")
    matrix = pd.DataFrame(columns, index=dates).fillna(0.0)
    if matrix.shape[1] != source.signal_id.nunique():
        raise ValueError("Signal feature-key collisions prevent a one-to-one catalog matrix")
    return matrix


def select_top_five(train: pd.DataFrame, candidates: list[str]) -> list[str]:
    target = train.target_14d.to_numpy(float)
    scores = []
    for column in candidates:
        values = train[column].to_numpy(float)
        if np.std(values) <= 1e-12:
            continue
        correlation = np.corrcoef(values, target)[0, 1]
        if np.isfinite(correlation):
            scores.append((column, abs(float(correlation))))
    scores.sort(key=lambda item: (-item[1], item[0]))
    return [column for column, _ in scores[:5]]


def base_columns(frame: pd.DataFrame) -> list[str]:
    historical = [
        column for column in frame
        if column.startswith(("lag_", "mean_", "std_"))
        and int(column.split("_")[1]) <= 56
    ]
    return historical + [
        "dow", "weekofyear", "month", "sin_year", "cos_year",
        "price_lag1", "stockout_lag1",
    ]


def wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.abs(actual - np.maximum(predicted, 0)).sum()
                 / max(np.abs(actual).sum(), 1e-9))


def future_event_windows(part: pd.DataFrame) -> np.ndarray:
    event_days = np.array([
        bool(EVENT_TAG.search(str(tags)))
        for tags in part["injected_event_tags"].fillna("")
    ])
    future = np.zeros(len(part), dtype=bool)
    for offset in range(1, 15):
        future[:-offset] |= event_days[offset:]
    return future


def evaluate() -> dict:
    sales = make_sales_frame()
    dates = pd.DatetimeIndex(sorted(sales.date.unique()))
    signals = make_signal_matrix(dates)
    frame = sales.set_index("date").join(signals, how="left").reset_index()
    frame[list(signals.columns)] = frame[list(signals.columns)].fillna(0.0)
    base = base_columns(frame)
    candidates = list(signals.columns)
    purge_boundary = TEST_START - pd.Timedelta(days=14)
    paired_rows = []

    for drug, raw_part in frame.groupby("drug_name", sort=True):
        part = raw_part.sort_values("date").copy()
        usable = part.dropna(subset=base + ["target_14d"]).copy()
        train = usable[usable.date < purge_boundary]
        selected = select_top_five(train, candidates)
        work = usable.copy()
        signal_features = []
        for signal in selected:
            for lag in (1, 7, 14):
                feature = f"{signal}__lag{lag}"
                work[feature] = work[signal].shift(lag)
                signal_features.append(feature)
        work = work.dropna(subset=signal_features + ["target_14d"])
        signal_train = work[work.date < purge_boundary]
        test = work[work.date >= TEST_START].copy()
        if train.empty or signal_train.empty or test.empty:
            continue

        signal_model = XGBRegressor(**PROFILE).fit(
            signal_train[base + signal_features], signal_train.target_14d,
            verbose=False)
        # The matched sales-only fit keeps all rows; only the signal model
        # needs the 14-day lag warm-up period dropped.
        sales_model = XGBRegressor(**PROFILE).fit(
            train[base], train.target_14d, verbose=False)
        test["signal_prediction"] = signal_model.predict(test[base + signal_features])
        test["sales_only_prediction"] = sales_model.predict(test[base])
        event_by_date = dict(zip(part.date, future_event_windows(part)))
        test["event_window"] = test.date.map(event_by_date).astype(bool)
        test["drug_name"] = drug
        paired_rows.append(test[[
            "drug_name", "date", "target_14d", "signal_prediction",
            "sales_only_prediction", "event_window",
        ]])

    scored = pd.concat(paired_rows, ignore_index=True)
    groups = {}
    for name, subset in (
        ("all_test", scored),
        ("event_window", scored[scored.event_window]),
        ("other_test", scored[~scored.event_window]),
    ):
        actual = subset.target_14d.to_numpy(float)
        signal_score = wape(actual, subset.signal_prediction.to_numpy(float))
        baseline_score = wape(actual, subset.sales_only_prediction.to_numpy(float))
        groups[name] = {
            "origins": len(subset),
            "signal_wape": signal_score,
            "sales_only_wape": baseline_score,
            "relative_wape_reduction": 1 - signal_score / baseline_score,
        }
    return {
        "protocol": "purged_fixed_top5_signal_recipe_event_window_audit_v1",
        "test_start": TEST_START.date().isoformat(),
        "purge_days": 14,
        "target_horizon_days": 14,
        "selection": "top five training-period signals by absolute raw-value correlation",
        "signal_lags_days": [1, 7, 14],
        "event_window_rule": (
            "An origin is an event window if any day in its next 14-day target "
            "has dated_supply_outbreak_response, flu_2023_24, flu_2024_25, or "
            "covid_summer_2025_outpatient_pulse in injected_event_tags."
        ),
        "scope": "deterministic synthetic single-clinic panel; event response is partly encoded by construction",
        "groups": groups,
    }


def main() -> None:
    result = evaluate()
    OUTPUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
