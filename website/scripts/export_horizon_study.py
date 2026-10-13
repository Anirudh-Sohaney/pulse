"""Package the model's synthetic benchmark summary for website-only builds."""

from __future__ import annotations

import hashlib
import json
import gzip
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "model/trained/synthetic_horizon_xgb_v1/manifest.json"
OUTPUT = ROOT / "website/backend/model_artifacts/horizon_study_v1.json"
sys.path.insert(0, str(ROOT / "model"))
from arkansas_pharma_signal import synthetic_horizon_xgb as horizon_model  # noqa: E402


def replay_snapshot(manifest: dict, as_of: str = "2025-12-01") -> dict:
    """Package one dated, held-out synthetic prediction per drug and horizon."""
    origin = pd.Timestamp(as_of)
    if origin < pd.Timestamp(manifest["test_start"]):
        raise ValueError("Snapshot must use the held-out period")
    if (horizon_model.digest(horizon_model.SALES) != manifest["sales_sha256"]
            or horizon_model.digest(horizon_model.SIGNALS) != manifest["signals_sha256"]):
        raise ValueError("Synthetic inputs do not match the saved boosters")
    sales = horizon_model.load_sales()
    dates = pd.DatetimeIndex(sorted(sales.date.unique()))
    signals, _ = horizon_model.load_signals(dates)
    rows = []
    for drug_name, part in sales.groupby("drug_name", sort=True):
        part = part.reset_index(drop=True)
        positions = part.index[part.date.eq(origin)]
        if len(positions) != 1:
            raise ValueError(f"Missing synthetic origin for {drug_name}")
        position = int(positions[0])
        trailing_mean = float(part.loc[position - 28:position - 1, "units_sold"].mean())
        signal_rows = signals.loc[part.date].reset_index(drop=True)
        for row in (item for item in manifest["drugs"] if item["drug_name"] == drug_name):
            work = horizon_model.add_selected_signals(part, signal_rows, row["selected_signals"])
            artifact = row["artifacts"][row["chosen_on_validation"]]
            payload = (SOURCE.parent / artifact["file"]).read_bytes()
            if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
                raise ValueError(f"Saved booster checksum mismatch for {drug_name}")
            vector = work.loc[[position], artifact["feature_columns"]].to_numpy(float)
            if not np.isfinite(vector).all():
                raise ValueError(f"Missing synthetic features for {drug_name}")
            booster = xgb.Booster()
            booster.load_model(bytearray(gzip.decompress(payload)))
            predicted = max(0.0, float(booster.predict(xgb.DMatrix(vector))[0]))
            rows.append({
                "drug_name": drug_name,
                "horizon_days": row["horizon_days"],
                "target_end": (origin.date() + timedelta(days=int(row["horizon_days"]))).isoformat(),
                "predicted_units_sold": round(predicted, 2),
                "trailing_28d_daily_units": round(trailing_mean, 2),
                "model_kind": row["chosen_on_validation"],
            })
    return {"as_of_date": as_of, "scope": "historical synthetic pharmacy replay",
            "projections": rows}


def export() -> Path:
    raw = SOURCE.read_bytes()
    manifest = json.loads(raw)
    study = {
        "model_version": manifest["model_version"],
        "source_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "scope": manifest["scope"],
        "candidate_signal_count": manifest["candidate_signal_count"],
        "target_horizons_days": manifest["target_horizons_days"],
        "validation_start": manifest["validation_start"],
        "test_start": manifest["test_start"],
        "summary": manifest["summary"],
        "drug_count": len({row["drug_name"] for row in manifest["drugs"]}),
        "snapshot": replay_snapshot(manifest),
        "drugs": [{"drug_name": row["drug_name"],
                   "horizon_days": row["horizon_days"],
                   "selected_signals": row["selected_signals"],
                   "chosen_on_validation": row["chosen_on_validation"],
                   "test": row["test"]} for row in manifest["drugs"]],
        "note": ("Synthetic pharmacy benchmark only. Historical source publication "
                 "times are incomplete, so period-end-based signal availability is an "
                 "assumption. Scores are not live Arkansas pharmacy forecasts."),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(study, separators=(",", ":")) + "\n", encoding="utf-8")
    return OUTPUT


if __name__ == "__main__":
    print(export())
