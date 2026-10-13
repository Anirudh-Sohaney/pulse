from pathlib import Path
import runpy

import numpy as np
import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "frontend" / "public" / "browser_forecast.py"


def test_shadow_estimates_are_excluded_and_observed_values_respect_availability():
    forecast = runpy.run_path(str(SCRIPT))["_signal_series"]
    dates = pd.date_range("2026-09-01", "2026-11-01", freq="D")
    rows = [
        {
            "id": "generated",
            "signal_origin": "model_news_output",
            "source_kind": "article_text_ridge_shadow_v1",
            "source_timestamp": "2026-09-15T12:00:00+00:00",
            "observation_date": "2026-09-30",
            "forecast_horizon": "month_to_date_month_end_nowcast",
            "value": 3.0,
            "usable": False,
        },
        {
            "id": "generated",
            "signal_origin": "model_news_output",
            "source_kind": "article_text_ridge_shadow_v1",
            "source_timestamp": "2026-10-09T12:00:00+00:00",
            "observation_date": "2026-10-31",
            "forecast_horizon": "month_to_date_month_end_nowcast",
            "value": 7.0,
            "usable": False,
        },
        {
            "id": "observed",
            "signal_origin": "model_external_state_feature",
            "source_kind": "observed_api",
            "source_timestamp": "2026-10-01T12:00:00+00:00",
            "observation_date": "2026-09-30",
            "forecast_horizon": None,
            "value": 2.0,
            "usable": True,
        },
        {
            "id": "observed",
            "signal_origin": "model_external_state_feature",
            "source_kind": "observed_api",
            "source_timestamp": "2026-10-09T12:00:00+00:00",
            "observation_date": "2026-10-31",
            "forecast_horizon": None,
            "value": 4.0,
            "usable": True,
        },
    ]
    aligned = forecast(rows, dates)
    assert "generated" not in aligned
    assert np.isnan(aligned["observed"][29])
    assert aligned["observed"][30] == 2.0
    assert aligned["observed"][59] == 2.0
    assert aligned["observed"][60] == 4.0
