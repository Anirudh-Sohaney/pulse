"""Evaluate dated news features against the full Arkansas CMS Part D panel.

This reproduces the historical annual proxy evaluation from the currently
available demand panel and archived 20-column signal outputs. It does not
validate the missing article-to-signal generator or measure pharmacy sales.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from arkansas_pharma_signal.regional_demand_state import (
    FEATURES,
    _metrics,
    _state,
    build_state_demand_view,
)
from arkansas_pharma_signal.regression import LogisticRidge


def _predict(train: pd.DataFrame, target: pd.DataFrame, columns: list[str],
             thresholds: tuple[float, ...], alpha: float) -> np.ndarray:
    states = _state(train["target_demand_claims"].to_numpy(), *thresholds)
    probabilities = []
    for state in range(5):
        model = LogisticRidge(alpha=alpha).fit(
            train[columns].fillna(0).to_numpy(float),
            (states == state).astype(float),
            columns,
        )
        probabilities.append(model.predict_proba(
            target[columns].fillna(0).to_numpy(float)))
    return np.column_stack(probabilities).argmax(axis=1)


def evaluate(demand_path: Path, news_path: Path) -> tuple[dict, pd.DataFrame]:
    demand = build_state_demand_view(pd.read_csv(demand_path))
    news = pd.read_csv(news_path)
    news["date"] = pd.to_datetime(news["date"], errors="raise")
    news["year"] = news["date"].dt.year
    news_columns = [column for column in news.columns if column not in ("date", "year")]
    news = news.groupby("year", as_index=False)[news_columns].sum()
    news[news_columns] = np.log1p(news[news_columns].clip(lower=0))
    view = demand.merge(news, on="year", how="inner")
    columns = [*FEATURES, *news_columns]

    all_actual, all_history_fixed, all_history_tuned, all_news = [], [], [], []
    folds = []
    for test_year in (2021, 2022, 2023):
        train = view[view.year < test_year - 1]
        validation = view[view.year == test_year - 1]
        test = view[view.year == test_year]
        if train.empty or validation.empty or test.empty:
            continue
        thresholds = tuple(np.quantile(
            train["target_demand_claims"], np.arange(1, 5) / 5))
        validation_actual = _state(validation["target_demand_claims"], *thresholds)
        alpha_scores = {}
        history_alpha_scores = {}
        for alpha in (0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0):
            prediction = _predict(train, validation, columns, thresholds, alpha)
            alpha_scores[alpha] = _metrics(validation_actual, prediction, 5)[
                "balanced_accuracy"]
            history_prediction = _predict(
                train, validation, list(FEATURES), thresholds, alpha)
            history_alpha_scores[alpha] = _metrics(
                validation_actual, history_prediction, 5)["balanced_accuracy"]
        alpha = max(alpha_scores, key=alpha_scores.get)
        history_alpha = max(history_alpha_scores, key=history_alpha_scores.get)
        fit = pd.concat([train, validation])
        actual = _state(test["target_demand_claims"], *thresholds)
        history_fixed = _predict(fit, test, list(FEATURES), thresholds, 10.0)
        history_tuned = _predict(
            fit, test, list(FEATURES), thresholds, history_alpha)
        with_news = _predict(fit, test, columns, thresholds, alpha)
        all_actual.append(actual)
        all_history_fixed.append(history_fixed)
        all_history_tuned.append(history_tuned)
        all_news.append(with_news)
        folds.append({
            "test_feature_year": test_year,
            "target_year": test_year + 1,
            "train_feature_years": [int(train.year.min()), int(train.year.max())],
            "validation_feature_year": test_year - 1,
            "test_rows": int(len(test)),
            "selected_alpha": float(alpha),
            "history_only_fixed_alpha_10": _metrics(actual, history_fixed, 5),
            "history_only_validation_tuned_alpha": float(history_alpha),
            "history_only_validation_tuned": _metrics(actual, history_tuned, 5),
            "history_plus_news": _metrics(actual, with_news, 5),
        })
    if not folds:
        raise RuntimeError("no eligible chronological folds")
    actual = np.concatenate(all_actual)
    history_fixed = np.concatenate(all_history_fixed)
    history_tuned = np.concatenate(all_history_tuned)
    with_news = np.concatenate(all_news)
    history_metrics = _metrics(actual, history_fixed, 5)
    history_tuned_metrics = _metrics(actual, history_tuned, 5)
    news_metrics = _metrics(actual, with_news, 5)
    incremental_news_value = bool(
        news_metrics["accuracy"] > history_tuned_metrics["accuracy"]
        and news_metrics["balanced_accuracy"] > history_tuned_metrics["balanced_accuracy"])
    result = {
        "protocol": "rolling_origin_full_cms_arkansas_state_drug_news_v1",
        "source": "CMS Medicare Part D Arkansas state-by-generic-drug panel",
        "news_source": "dated archived 20-column NLP signal surface",
        "news_transform": "annual sums followed by log1p; no target columns",
        "feature_boundary": "feature year t predicts demand target year t+1",
        "test_feature_years": "2021-2023",
        "drug_count": int(view["drug_key"].nunique()),
        "test_rows": int(len(actual)),
        "news_signal_count": len(news_columns),
        "fold_count": len(folds),
        "overall_history_only": history_metrics,
        "overall_history_only_validation_tuned": history_tuned_metrics,
        "overall_history_plus_news": news_metrics,
        "incremental_news_value_vs_matched_baseline": incremental_news_value,
        "folds": folds,
        "promotion_candidate": bool(
            len(folds) >= 3
            and news_metrics["accuracy"] >= 0.80
            and news_metrics["balanced_accuracy"] >= 0.80
            and incremental_news_value),
        "scope": "annual CMS Part D demand proxy by Arkansas generic drug; not dispensing or inventory truth",
    }

    current = pd.read_csv(demand_path)
    current = current[current["state"].astype(str).str.lower().eq("arkansas")].copy()
    current["year"] = pd.to_numeric(current["year"], errors="raise").astype(int)
    current["drug_key"] = current["drug_key"].astype(str)
    current["demand_claims"] = pd.to_numeric(current["demand_claims"], errors="raise")
    current = (current.groupby(["year", "drug_key"], as_index=False)["demand_claims"]
               .sum().sort_values(["drug_key", "year"]))
    grouped = current.groupby("drug_key", sort=False)["demand_claims"]
    current["lag1_demand"] = grouped.shift(1)
    current["lag2_demand"] = grouped.shift(2)
    current["rolling3_demand"] = grouped.transform(
        lambda values: values.shift(1).rolling(3, min_periods=1).mean())
    latest_year = int(current["year"].max())
    latest = current[current["year"].eq(latest_year)].copy()
    latest = latest.merge(news, on="year", how="inner")
    thresholds = tuple(np.quantile(
        view["target_demand_claims"], np.arange(1, 5) / 5))
    forecast_state = _predict(view, latest, columns, thresholds, 0.1)
    forecast = latest[["drug_key", "year", *news_columns]].copy()
    forecast["forecast_year"] = latest_year + 1
    forecast["predicted_demand_state"] = forecast_state
    forecast["state_definition"] = "training quantile_1..quantile_5"
    forecast["model"] = "logistic_one_vs_rest_with_20_dated_nlp_signals"
    forecast["source"] = "CMS Medicare Part D Arkansas state-by-generic-drug panel"
    result.update({
        "forecast_rows": int(len(forecast)),
        "forecast_feature_year": latest_year,
        "forecast_target_year": latest_year + 1,
    })
    return result, forecast


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demand", type=Path, default=Path(
        "data/targeted_additions/cms_partd_geography_drug/data/"
        "arkansas_partd_geography_drug_by_year.csv.gz"))
    parser.add_argument("--news", type=Path, default=Path(
        "model/artifacts/news/news_only_catalog_features.csv.gz"))
    parser.add_argument("--output", type=Path, default=Path(
        "model/artifacts/evaluation/cms_partd_full_news_metrics.json"))
    parser.add_argument("--forecast-output", type=Path, default=Path(
        "model/artifacts/forecasts/cms_partd_full_news_next_year.csv.gz"))
    args = parser.parse_args()

    result, forecast = evaluate(args.demand, args.news)
    args.forecast_output.parent.mkdir(parents=True, exist_ok=True)
    forecast.to_csv(args.forecast_output, index=False, compression="gzip")
    result["forecast_output"] = str(args.forecast_output)
    result["forecast_output"] = str(args.forecast_output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "drug_count", "test_rows", "fold_count", "overall_history_only",
        "overall_history_only_validation_tuned",
        "overall_history_plus_news")}, indent=2))


if __name__ == "__main__":
    main()
