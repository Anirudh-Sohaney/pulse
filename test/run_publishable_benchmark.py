"""Untouched-test benchmark for the synthetic pharmacy stress test.

This script keeps signal selection inside the training period, uses the middle
period only for diagnostics, and reports the final result on the last 20% of
dates. It does not alter data/ or model/.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from xgboost import XGBRegressor

TEST = Path(__file__).resolve().parent
ROOT = TEST.parent
sys.path.insert(0, str(TEST))
from run_xgb_experiment import make_frame, metrics  # noqa: E402
from run_xgb_signals import signal_matrix, select_top15  # noqa: E402

OUT_JSON = TEST / "publishable_benchmark_metrics.json"
OUT_MD = TEST / "publishable_benchmark_results.md"


def model() -> XGBRegressor:
    return XGBRegressor(
        n_estimators=220, max_depth=2, learning_rate=.04,
        min_child_weight=8, subsample=.85, colsample_bytree=.5,
        reg_lambda=10, objective="reg:squarederror", tree_method="hist",
        n_jobs=2, random_state=20250915,
    )


def base_columns(frame: pd.DataFrame) -> list[str]:
    historical = [c for c in frame.columns if c.startswith(("lag_", "mean_", "std_"))]
    historical = [c for c in historical if int(c.split("_")[1]) <= 56]
    return historical + ["dow", "weekofyear", "month", "sin_year", "cos_year",
                         "price_lag1", "stockout_lag1"]


def add_variant_columns(part: pd.DataFrame, top: list[str], variant: str):
    work = part.copy()
    if variant == "sales_only":
        return work, []
    if variant == "all_current":
        return work, list(work.attrs["signal_cols"])
    if variant == "top15_current":
        return work, top
    chosen = top[:5] if variant == "top5_lagged" else top
    cols = []
    for col in chosen:
        for lag in (1, 7, 14):
            name = f"{col}__lag{lag}"
            work[name] = work[col].shift(lag)
            cols.append(name)
    return work, cols


def evaluate(frame: pd.DataFrame, base: list[str], signal_cols: list[str],
             split: tuple[pd.Timestamp, pd.Timestamp], variant: str,
             top_by_drug: dict[str, list[str]], final: bool):
    start, end = split
    all_y, all_p, rows = [], [], []
    for drug, part in frame.groupby("drug_name", sort=True):
        part = part.copy()
        part.attrs["signal_cols"] = signal_cols
        work, sig_features = add_variant_columns(part, top_by_drug[drug], variant)
        cols = base + sig_features
        work = work.dropna(subset=cols + ["target_14d"])
        if final:
            train = work[work.date < start]
            test = work[work.date >= start]
        else:
            train = work[work.date < start]
            test = work[(work.date >= start) & (work.date < end)]
        if len(train) < 100 or not len(test):
            continue
        fitted = model().fit(train[cols], train.target_14d, verbose=False)
        pred = fitted.predict(test[cols])
        score = metrics(test.target_14d, pred)
        rows.append({"drug_name": drug, **score})
        all_y.extend(test.target_14d.to_numpy())
        all_p.extend(pred)
    pooled = metrics(all_y, all_p)
    per_drug = pd.DataFrame(rows)
    return {
        "pooled": pooled,
        "mean_per_drug": {k: float(per_drug[k].mean()) for k in
                          ("mae", "rmse", "wape", "smape", "r2",
                           "within_5pct", "within_10pct", "within_20pct")},
        "drugs": int(len(per_drug)),
    }


def main() -> None:
    sales = make_frame(False)
    dates = pd.DatetimeIndex(sorted(sales.date.unique()))
    signals, signal_rows = signal_matrix(dates)
    frame = sales.set_index("date").join(signals, how="left").reset_index()
    frame[signals.columns] = frame[signals.columns].fillna(0.0)
    base = base_columns(frame)
    signal_cols = list(signals.columns)

    first, last = dates.min(), dates.max()
    train_end = first + (last - first) * .60
    validation_end = first + (last - first) * .80
    top_by_drug = {}
    for drug, part in frame.groupby("drug_name", sort=True):
        usable = part.dropna(subset=base + ["target_14d"])
        train = usable[usable.date < train_end]
        top_by_drug[drug] = [x[0] for x in select_top15(train, train.target_14d, signal_cols)]

    variants = ["sales_only", "all_current", "top15_current", "top5_lagged"]
    validation = {}
    final = {}
    for variant in variants:
        validation[variant] = evaluate(frame, base, signal_cols,
                                       (train_end, validation_end), variant,
                                       top_by_drug, final=False)
        final[variant] = evaluate(frame, base, signal_cols,
                                  (validation_end, last + pd.Timedelta(days=1)), variant,
                                  top_by_drug, final=True)

    # Robustness check: remove near-static columns and exact duplicate daily
    # series. This is feature hygiene, not a change to the synthetic target.
    dynamic = signals.loc[:, signals.nunique(dropna=True) > 2]
    dynamic = dynamic.T.drop_duplicates().T.copy()
    dynamic_frame = sales.set_index("date").join(dynamic, how="left").reset_index()
    dynamic_frame[dynamic.columns] = dynamic_frame[dynamic.columns].fillna(0.0)
    dynamic_cols = list(dynamic.columns)
    dynamic_top = {}
    for drug, part in dynamic_frame.groupby("drug_name", sort=True):
        usable = part.dropna(subset=base + ["target_14d"])
        train = usable[usable.date < train_end]
        dynamic_top[drug] = [x[0] for x in select_top15(train, train.target_14d, dynamic_cols)]
    dynamic_results = {}
    for variant in ("all_current", "top5_lagged"):
        dynamic_results[variant] = evaluate(
            dynamic_frame, base, dynamic_cols,
            (validation_end, last + pd.Timedelta(days=1)), variant,
            dynamic_top, final=True,
        )

    out = {
        "date_range": [str(first.date()), str(last.date())],
        "train_end": str(train_end.date()),
        "validation_end": str(validation_end.date()),
        "final_test_start": str(validation_end.date()),
        "signal_candidate_columns": len(signal_cols),
        "source_signal_rows": int(len(signal_rows)),
        "top15_drugs": len(top_by_drug),
        "validation": validation,
        "untouched_test": final,
        "pre_registered_primary_variant": "top5_lagged",
        "dynamic_signal_columns": len(dynamic_cols),
        "dynamic_deduplicated_test": dynamic_results,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2), encoding="utf-8")
    primary = final["top5_lagged"]["pooled"]
    baseline = final["sales_only"]["pooled"]
    dynamic_primary = dynamic_results["top5_lagged"]["pooled"]
    OUT_MD.write_text(f"""# Untouched-test benchmark

This is a transparent synthetic stress test, not observed pharmacy data.
Signal selection used only the first 60% of dates. The middle 20% is a
validation period. The final 20% beginning **{validation_end.date()}** was not
used for signal selection or model choice.

The pre-registered primary signal approach was `top5_lagged`: the five
training-selected signals for each drug, delayed by 1, 7, and 14 days. The
sales-only comparison uses the same XGBoost family and the same dates.

| Metric | Primary signals | Sales-only |
|---|---:|---:|
| MAE | {primary['mae']:.3f} | {baseline['mae']:.3f} |
| RMSE | {primary['rmse']:.3f} | {baseline['rmse']:.3f} |
| WAPE | {primary['wape']:.2%} | {baseline['wape']:.2%} |
| sMAPE | {primary['smape']:.2%} | {baseline['smape']:.2%} |
| R² | {primary['r2']:.3f} | {baseline['r2']:.3f} |
| Within 20% | {primary['within_20pct']:.2%} | {baseline['within_20pct']:.2%} |
| Test origins | {primary['n']:,} | {baseline['n']:,} |

The complete validation and untouched-test results are in
`publishable_benchmark_metrics.json`. The generator, event tags, and
provenance are documented in this directory's `synthetic_guide.md` and
`benchmark_protocol.md`.

As a robustness check, near-static and exact-duplicate signal columns were
removed, leaving **{len(dynamic_cols):,}** columns. The resulting sparse model
had **{dynamic_primary['wape']:.2%} WAPE** on the same untouched test period.
""", encoding="utf-8")
    print(json.dumps({"primary": primary, "sales_only": baseline}, indent=2))


if __name__ == "__main__":
    main()
