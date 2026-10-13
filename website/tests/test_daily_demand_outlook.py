from fastapi.testclient import TestClient

from backend.app import create_app
from backend.short_horizon_research import study


def test_short_horizon_study_is_synthetic_only():
    result = study()
    assert result["model_version"] == "synthetic_horizon_xgb_v1"
    assert result["target_horizons_days"] == [1, 7, 14, 30]
    assert result["drug_count"] == 30
    assert len(result["drugs"]) == 120
    assert all(len(row["selected_signals"]) == 5 for row in result["drugs"])
    assert "not live" in result["note"]


def test_old_outlook_is_retired_and_study_is_available():
    client = TestClient(create_app())
    retired = client.get("/api/v1/signals/demand/outlook")
    assert retired.status_code == 410
    response = client.get("/api/v1/signals/demand/horizon-study?drug_name=Amlodipine%205%20mg%20tablet")
    assert response.status_code == 200
    assert response.json()["drug_count"] == 1
    assert len(response.json()["drugs"]) == 4
