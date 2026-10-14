"""Contract tests with synthetic evidence, never claimed as real performance."""

import json
from pathlib import Path

import pytest

from arkansas_pharma_signal.metric_audit import audit_metric_library
from arkansas_pharma_signal.universal_forecast import TARGET_METADATA


STATES = {str(i): 10 for i in range(5)}


def _write(root: Path, name: str, payload: dict) -> None:
    (root / name).write_text(json.dumps(payload), encoding="utf-8")


def _candidate(audit: dict, name: str) -> dict:
    return next(row for row in audit["candidates"] if row["metric"] == name)


def _event(precision: float = 0.9) -> dict:
    return {"event_applicable": True, "predicted_event_count": 12,
            "true_positive_precision": precision}


def _pressure(root: Path) -> None:
    _write(root, "shortage_pressure_rolling_metrics.json", {
        "test_rows": 120, "fold_count": 3, "mean_model_accuracy": 0.8,
        "mean_model_balanced_accuracy": 0.7,
        "per_drug_count_with_minimum_rows": 120,
        "per_drug_count_at_or_above_75_percent": 110,
        "state_counts_in_scored_rows": STATES, "event_metrics": _event(),
    })


def _nadac(root: Path) -> None:
    folds = [{"test_rows": 30, "test_ndcs": 100,
              "model": {"under_5_percent_error": 0.8, "wape": 0.1},
              "persistence": {"wape": 0.05},
              "model_beats_persistence_wape": False} for _ in range(3)]
    _write(root, "nadac_historical_reconstruction_rolling_metrics.json", {
        "test_ndcs": 100, "rolling": {"fold_count": 3, "folds": folds},
    })


def test_missing_evaluation_evidence_fails_closed(tmp_path):
    audit = audit_metric_library(tmp_path)
    assert audit["qualified_metric_count"] == 0
    assert all(row["status"] == "rejected" for row in audit["candidates"])
    assert not any(audit["part_one_gates"].values())
    assert not any(gate["passed"] for gate in audit["coverage_gates"].values())


def test_binary_shortage_rejected_and_supported_five_state_promoted(tmp_path):
    _pressure(tmp_path)
    audit = audit_metric_library(tmp_path)
    binary = _candidate(audit, "fda_shortage_continuation")
    assert binary["status"] == "rejected"
    assert "binary-only target is prohibited; use at least 5 states" in binary["reasons"]
    pressure = _candidate(audit, "ndc_monthly_shortage_pressure_state")
    assert pressure["status"] == "qualified_proxy"
    assert pressure["target_type"] == "five_state"
    assert pressure["event_definition"] == {"kind": "state", "states": [3, 4]}
    assert pressure["drug_count"] == 120
    assert pressure["drug_count_at_or_above_75_percent"] == 110
    assert audit["part_one_gates"]["per_drug_100_plus_75_percent"] is True


def test_arcos_state_requires_its_own_evidence(tmp_path):
    _write(tmp_path, "arcos_distribution_state_metrics.json", {
        "test_rows": 75, "fold_count": 3, "mean_model_accuracy": 0.72,
        "mean_model_balanced_accuracy": 0.68, "drug_count": 39,
        "zip3_count": 85, "state_counts_in_scored_rows": STATES,
        "event_metrics": _event(),
    })
    audit = audit_metric_library(tmp_path)
    numeric = _candidate(audit, "arcos_zip3_drug_next_quarter_distribution")
    assert numeric["status"] == "rejected"
    assert "held_out_rows<25" in numeric["reasons"]
    state = _candidate(audit, "arcos_zip3_drug_next_quarter_distribution_state")
    assert state["status"] == "qualified_proxy"
    assert state["reasons"] == []
    assert state["held_out_rows"] == 75
    assert state["rolling_folds"] == 3


def test_proxy_accuracy_does_not_imply_incremental_utility(tmp_path):
    _pressure(tmp_path)
    _write(tmp_path, "weekly_fluview_ar_proxy_metrics.json", {
        "five_state": {"test_rows": 75, "fold_count": 3,
                       "mean_model_accuracy": 0.7,
                       "mean_model_balanced_accuracy": 0.5,
                       "state_counts_in_scored_rows": STATES,
                       "model_balanced_improvement_vs_persistence": -0.1},
    })
    audit = audit_metric_library(tmp_path)
    flu = _candidate(audit, "arkansas_weekly_fluview_respiratory_pressure_state")
    pressure = _candidate(audit, "ndc_monthly_shortage_pressure_state")
    assert flu["status"] == "rejected"
    assert "balanced_accuracy<65%" in flu["reasons"]
    assert flu["baseline_skill"]["status"] == "persistence_dominated"
    assert flu["baseline_skill"]["incremental_utility_status"] == "not_demonstrated"
    assert pressure["baseline_skill"]["incremental_utility_status"] == "mixed_cross_signal_test"
    assert pressure["baseline_skill"]["all_folds_beating_persistence"] is False


def test_imbalanced_hospital_state_targets_rejected(tmp_path):
    _write(tmp_path, "hospital_respiratory_state_metrics.json", {"results": {
        "covid": {"test_rows": 75, "fold_count": 3, "mean_model_accuracy": 0.7,
                  "mean_model_balanced_accuracy": 0.5,
                  "state_counts_in_scored_rows": STATES},
        "influenza": {"test_rows": 75, "fold_count": 3,
                      "mean_model_accuracy": 0.7,
                      "mean_model_balanced_accuracy": 0.7,
                      "state_counts_in_scored_rows": {str(i): 10 for i in range(4)}},
    }})
    audit = audit_metric_library(tmp_path)
    covid = _candidate(audit, "arkansas_weekly_hospital_covid_admission_pressure_state")
    influenza = _candidate(audit, "arkansas_weekly_hospital_influenza_admission_pressure_state")
    assert covid["status"] == "rejected"
    assert "balanced_accuracy<65%" in covid["reasons"]
    assert influenza["status"] == "rejected"
    assert "fewer than 5 target states are observed in scored rows" in influenza["reasons"]


def test_reconstructed_nadac_records_persistence_limitation(tmp_path):
    _nadac(tmp_path)
    nadac = _candidate(audit_metric_library(tmp_path), "nadac_next_observed_price")
    assert nadac["rolling_folds"] == 3
    assert nadac["held_out_rows"] == 90
    assert nadac["status"] == "qualified_proxy"
    assert nadac["accuracy_within_5pct_or_exact_state"] == pytest.approx(0.8)
    assert nadac["baseline_skill"]["status"] == "persistence_dominated"
    assert nadac["baseline_skill"]["all_folds_beating_persistence"] is False


def test_all_candidates_have_provenance_fields(tmp_path):
    audit = audit_metric_library(tmp_path)
    required = {"source_url", "source_license_access", "target_semantics",
                "feature_timestamp_boundary", "geography_scope",
                "supplier_resolution", "missingness_censoring"}
    for candidate in audit["candidates"]:
        assert required <= candidate.keys(), candidate["metric"]
        assert candidate["source_license_access"]
        assert candidate["target_semantics"]
        assert candidate["feature_timestamp_boundary"]


def test_qualified_targets_remain_proxy_only(tmp_path):
    _pressure(tmp_path)
    audit = audit_metric_library(tmp_path)
    assert audit["target_validity"]["passed"] is True
    assert audit["target_validity"]["qualified_targets_are_proxy_only"] is True
    assert audit["target_validity"]["qualified_direct_inventory_claims"] == []
    assert audit["qualified_metric_count"] == 1
    for candidate in audit["candidates"]:
        assert candidate["target_is_direct_pharmacy_observation"] is False
        assert candidate["target_claim_scope"] == "proxy_only_not_pharmacy_inventory"


def test_regional_wastewater_level_and_change_rejected_on_event_gate(tmp_path):
    payload = {"test_rows": 195, "fold_count": 3,
               "mean_model_accuracy": 0.7,
               "mean_model_balanced_accuracy": 0.5,
               "region_count": 5, "state_counts_in_scored_rows": STATES,
               "event_metrics": _event(precision=0.6)}
    _write(tmp_path, "regional_wastewater_five_state_metrics.json", {
        "results": {"Influenza A virus": {"level": payload, "change": payload}}})
    audit = audit_metric_library(tmp_path)
    for representation in ("level", "change"):
        candidate = _candidate(
            audit, f"arkansas_region_weekly_wastewater_influenza_{representation}_pressure_state")
        assert candidate["status"] == "rejected"
        assert "event true-positive route not met" in candidate["reasons"]
        assert candidate["held_out_rows"] == 195


def test_statewide_wastewater_audits_all_three_pathogens(tmp_path):
    names = {row["metric"] for row in audit_metric_library(tmp_path)["candidates"]}
    assert {"arkansas_weekly_wastewater_influenza_state",
            "arkansas_weekly_wastewater_rsv_state",
            "arkansas_weekly_wastewater_sars_cov_2_state"} <= names


def test_supported_proxies_cover_geography_drug_supplier_and_disease(tmp_path):
    for filename, payload in (
        ("recall_numeric_rolling_metrics.json", {"ndc_count": 120}),
        ("supplier_recall_numeric_rolling_metrics.json",
         {"ndc_count": 120, "supplier_count": 4}),
        ("shortage_supplier_count_numeric_rolling_metrics.json", {"ndc_count": 120}),
    ):
        _write(tmp_path, filename, {
            "test_rows": 100, "fold_count": 3,
            "mean_model_within_5_percent_error": 0.8,
            "per_drug_count_at_or_above_75_percent": 110,
            "event_metrics": _event(), **payload,
        })
    _write(tmp_path, "regional_demand_five_state_metrics.json", {
        "test_rows": 100, "fold_count": 3, "mean_model_accuracy": 0.8,
        "mean_model_balanced_accuracy": 0.7, "drug_count": 120,
        "region_count": 5,
        "per_region_accuracy": {f"region-{i}": 0.8 for i in range(5)},
        "state_counts_in_scored_rows": STATES, "event_metrics": _event(),
    })
    _write(tmp_path, "weekly_fluview_ar_proxy_metrics.json", {
        "national_five_state": {
            "test_rows": 100, "fold_count": 3, "mean_model_accuracy": 0.8,
            "mean_model_balanced_accuracy": 0.7,
            "state_counts_in_scored_rows": STATES, "event_metrics": _event(),
        },
    })
    _write(tmp_path, "county_demand_five_state_metrics.json", {
        "test_rows": 100, "fold_count": 3, "mean_model_accuracy": 0.8,
        "mean_model_balanced_accuracy": 0.5, "drug_count": 120,
        "county_count": 5, "county_count_at_or_above_75_percent": 5,
        "state_counts_in_scored_rows": STATES, "event_metrics": _event(),
    })
    _write(tmp_path, "nssp_respiratory_five_state_metrics.json", {
        "results": {"influenza": {
            "test_rows": 100, "fold_count": 3, "mean_model_accuracy": 0.7,
            "mean_model_balanced_accuracy": 0.5,
            "state_counts_in_scored_rows": STATES, "event_metrics": _event(),
        }},
    })
    audit = audit_metric_library(tmp_path)
    expected = {"ndc_monthly_recall_severity",
                "supplier_ndc_monthly_recall_severity",
                "ndc_monthly_shortage_supplier_count",
                "arkansas_region_annual_demand_five_state",
                "national_weekly_fluview_respiratory_pressure_state",
                "arkansas_county_annual_demand_five_state",
                "arkansas_weekly_nssp_influenza_ed_pressure_state"}
    qualified = {row["metric"] for row in audit["candidates"]
                 if row["status"] == "qualified_proxy"}
    assert qualified == expected
    assert all(audit["coverage_gates"][scope]["passed"] for scope in
               ("geography", "drug", "supplier", "disease_symptom"))
    assert audit["part_one_gates"]["per_arkansas_region_75_percent"] is True
    assert audit["part_one_gates"]["weekly_or_monthly_75_percent"] is True


def test_qualified_event_metrics_are_machine_readable(tmp_path):
    _pressure(tmp_path)
    _nadac(tmp_path)
    audit = audit_metric_library(tmp_path)
    assert audit["qualified_metric_count"] == 2
    for candidate in audit["candidates"]:
        if candidate["status"] != "qualified_proxy":
            continue
        if candidate["event_definition"]["kind"] == "not_applicable":
            assert candidate["event_metrics"] is None
            continue
        metrics = candidate["event_metrics"]
        assert metrics and metrics["event_applicable"] is True
        assert metrics["predicted_event_count"] > 0
        assert metrics["true_positive_precision"] >= 0.0


def test_event_definitions_match_operational_target_metadata(tmp_path):
    _pressure(tmp_path)
    _nadac(tmp_path)
    _write(tmp_path, "recall_numeric_rolling_metrics.json", {
        "test_rows": 100, "fold_count": 3,
        "mean_model_within_5_percent_error": 0.8, "ndc_count": 120,
        "event_metrics": _event(),
    })
    audit = audit_metric_library(tmp_path)
    qualified = [row for row in audit["candidates"]
                 if row["status"] == "qualified_proxy"]
    assert len(qualified) == 3
    for candidate in qualified:
        definition = candidate["event_definition"]
        metadata_definition = TARGET_METADATA[candidate["metric"]]["event"]
        assert definition["kind"] == metadata_definition["kind"]
        if definition["kind"] == "state":
            assert definition["states"] == metadata_definition["event_states"]
        elif definition["kind"] == "numeric":
            assert definition["threshold"] == metadata_definition["event_threshold"]
        else:
            assert definition["kind"] == "not_applicable"
