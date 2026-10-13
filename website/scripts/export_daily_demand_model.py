"""Export the trained XGBoost models into portable runtime artifacts.

The runtime worker consumes the exported JSON boosters and feature manifest.
This script is run when the benchmark model is intentionally refreshed; it
does not run during daily inference.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import joblib
import pandas as pd


def slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()


def export_model(source: Path, output: Path) -> dict:
    forecaster = joblib.load(source)
    if not forecaster.models or set(forecaster.models) != set(forecaster.selected_signals):
        raise ValueError("The trained artifact has an incomplete per-drug model set")
    output.mkdir(parents=True, exist_ok=True)
    models = {}
    for drug in sorted(forecaster.models):
        part = forecaster.history[forecaster.history["drug_name"] == drug].copy()
        part["date"] = pd.to_datetime(part["date"])
        features = forecaster._base_features(part).iloc[-1]
        feature_columns = forecaster.feature_columns_by_drug[drug]
        selected = forecaster.selected_signals[drug]
        model_name = f"{slug(drug)}.json.gz"
        raw_path = output / f".{slug(drug)}.json"
        forecaster.models[drug].get_booster().save_model(raw_path)
        with raw_path.open("rb") as input_file, (output / model_name).open("wb") as output_file:
            with gzip.GzipFile(fileobj=output_file, mode="wb", filename="", mtime=0) as compressed:
                compressed.write(input_file.read())
        raw_path.unlink()
        model_sha256 = hashlib.sha256((output / model_name).read_bytes()).hexdigest()
        recent = part.tail(56)
        weekday_means = {
            str(int(day)): float(group["units_sold"].mean())
            for day, group in recent.groupby(recent["date"].dt.dayofweek)
        }
        base_features = {
            name: float(features[name]) for name in feature_columns
            if not name.startswith("signal__")
        }
        models[drug] = {
            "file": model_name,
            "sha256": model_sha256,
            "features": feature_columns,
            "base_features": base_features,
            "selected_signals": selected,
            "training_end": part["date"].max().date().isoformat(),
            "trailing_mean_14": float(part.tail(14)["units_sold"].mean()),
            "weekday_means": weekday_means,
        }
    parameters = forecaster.models[sorted(forecaster.models)[0]].get_params()
    recipe_keys = (
        "n_estimators", "max_depth", "learning_rate", "min_child_weight",
        "subsample", "colsample_bytree", "reg_lambda", "objective",
        "tree_method", "random_state",
    )
    manifest = {
        "schema": "pulse_daily_signal_demand_model_v1",
        "model_version": "per_drug_top5_lagged_v1",
        "model_recipe": {key: parameters[key] for key in recipe_keys},
        "signal_selection": {"rule": "top_5_absolute_training_correlation",
                             "lags_days": [1, 7, 14]},
        "models": models,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    return {"models": len(models), "manifest": str(output / "manifest.json"),
            "bytes": sum(path.stat().st_size for path in output.iterdir())}


def main() -> None:
    parser = argparse.ArgumentParser()
    website_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--source", type=Path,
                        default=website_root / "data" / "demand_forecaster.joblib")
    parser.add_argument("--output", type=Path,
                        default=website_root / "backend" / "model_artifacts" / "daily_signal_demand_v1")
    args = parser.parse_args()
    result = export_model(args.source, args.output)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
