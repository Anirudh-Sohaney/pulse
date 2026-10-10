"""Read-only end-to-end audit of a running PULSE signal API.

Run after the database audit and source refresh. This script follows catalog
pagination, checks every latest ID, replays representative history, and checks
the evaluated drug ranking. It never writes to the service.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timezone
from urllib.request import Request, urlopen


SEED_COUNT = 1250
SHADOW_KIND = "article_text_ridge_shadow_v1"
UNSUPPORTED_KINDS = {SHADOW_KIND, "combined_demand_signals",
                     "historical_catalog", "historical_news_bridge"}
WEEKLY_KINDS = {"delphi_fluview_ilinet_v5", "cdc_nndss_weekly_v1",
                "cdc_wval_reporting_site_mean_v1"}
SOURCE_KINDS = WEEKLY_KINDS | {"bls_public_api_v1", "bls_public_api_v1_derived_v2",
                               "medicaid_state_performance_api",
                               "cms_geographic_variation_api", "openfda_drug_enforcement"}


def audit(base_url: str, *, require_source_refresh: bool = False,
          require_drug_baseline: bool = False) -> dict:
    base = base_url.rstrip("/")

    def request(path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        req = Request(base + path, data=body,
                      headers={"Content-Type": "application/json"} if body else {})
        with urlopen(req, timeout=45) as response:
            if response.status != 200:
                raise AssertionError(f"{path}: HTTP {response.status}")
            return json.load(response)

    ready = request("/ready")
    if not ready.get("ready") or ready.get("seed_definitions_expected") != SEED_COUNT:
        raise AssertionError("API is not ready with the checked-in seed contract")
    seed = request("/api/v1/signals/catalog/seed")
    if seed.get("count") != SEED_COUNT:
        raise AssertionError("Seed catalog count changed")
    seed_ids = {row["id"] for row in seed["signals"]}
    if len(seed_ids) != SEED_COUNT:
        raise AssertionError("Seed catalog has duplicate IDs")

    catalog = []
    for offset in range(0, 150_000, 1500):
        page = request(f"/api/v1/signals/catalog?limit=1500&offset={offset}")
        catalog.extend(page["signals"])
        if page["count"] < 1500:
            break
    else:
        raise AssertionError("Catalog pagination exceeded its audit bound")
    by_id = {row["id"]: row for row in catalog}
    if len(by_id) != len(catalog) or not seed_ids.issubset(by_id):
        raise AssertionError("Catalog has duplicate IDs or lacks seed identities")

    usable: dict[str, dict] = {}
    latest_recorded: dict[str, dict] = {}
    missing: set[str] = set()
    ambiguous: set[str] = set()
    for offset in range(0, len(catalog), 500):
        batch = [row["id"] for row in catalog[offset:offset + 500]]
        answer = request("/api/v1/signals/latest", {"ids": batch})
        found = {row["id"] for row in answer["values"]}
        absent = set(answer["missing_ids"])
        if found & absent or found | absent != set(batch):
            raise AssertionError("Latest did not partition requested IDs")
        missing.update(absent)
        ambiguous.update(answer["ambiguous_ids"])
        for row in answer["values"]:
            if (not row["usable"] or row["source_kind"] in UNSUPPORTED_KINDS
                    or not math.isfinite(row["value"])):
                raise AssertionError(f"Published unusable value: {row['id']}")
            if row["signal_origin"] == "model_external_state_feature":
                observed = date.fromisoformat(row["observation_date"])
                if observed > datetime.now(timezone.utc).date():
                    raise AssertionError(f"Future source period: {row['id']}")
                source_time = datetime.fromisoformat(row["source_timestamp"])
                ingested = datetime.fromisoformat(row["ingested_at"])
                if source_time.tzinfo is None or source_time > ingested:
                    raise AssertionError(f"Invalid source availability time: {row['id']}")
                if row["source_kind"] in WEEKLY_KINDS and observed.weekday() != 5:
                    raise AssertionError(f"Weekly period is not Saturday: {row['id']}")
            usable[row["id"]] = row
        for row in answer["latest_recorded_values"]:
            latest_recorded[row["id"]] = row
    if len(usable) + len(missing) != len(catalog):
        raise AssertionError("Latest coverage does not match the catalog")
    if ambiguous:
        raise AssertionError(f"Current ambiguous signal IDs: {sorted(ambiguous)[:10]}")

    news_missing = [by_id[uid] for uid in missing
                    if by_id[uid]["signal_origin"] == "model_news_output"]
    atc_missing = [by_id[uid] for uid in missing
                   if by_id[uid]["signal_id"].startswith("arkansas_atc_demand_state::")]
    if (len(news_missing) != 20 or len(atc_missing) != 18
            or len(missing & seed_ids) != 38):
        raise AssertionError("The unvalidated seed-output boundary changed")
    if any(latest_recorded[row["id"]]["source_kind"] != SHADOW_KIND
           for row in news_missing):
        raise AssertionError("A news output lost its shadow provenance")

    freshness = request("/api/v1/signals/freshness")
    coverage = request("/api/v1/signals/coverage/summary")
    if (freshness["definitions"] != len(catalog)
            or freshness["news_model"]["publishable"] is not False
            or coverage["seeded_id_count"] != SEED_COUNT
            or coverage["seeded_ids_with_usable_latest_value"] +
               coverage["seeded_ids_without_usable_latest_value"] != SEED_COUNT):
        raise AssertionError("Freshness or seed coverage disagrees with latest values")
    live_kinds = {row["source_kind"] for row in usable.values()}
    if require_source_refresh and not (WEEKLY_KINDS | {
            "openfda_drug_enforcement", "bls_public_api_v1",
            "medicaid_state_performance_api", "cms_geographic_variation_api"
            }).issubset(live_kinds):
        raise AssertionError("A required public source has no usable latest values")

    history_checked = []
    for kind in sorted(live_kinds & (SOURCE_KINDS | {"cms_partd_two_year_persistence_v2"})):
        sample = next(row for row in usable.values() if row["source_kind"] == kind)
        history = request("/api/v1/signals/history", {
            "ids": [sample["id"]], "date": sample["observation_date"]})
        if not any(row["source_kind"] == kind and row["value"] == sample["value"]
                   and row["forecast_horizon"] == sample["forecast_horizon"]
                   for row in history["values"]):
            raise AssertionError(f"Latest value is absent from exact-date history: {kind}")
        history_checked.append(kind)

    ranking = request("/api/v1/signals/demand/drugs?limit=250&offset=0")
    ranked_rows = list(ranking["drugs"])
    for offset in range(250, ranking["filtered_total"], 250):
        ranked_rows.extend(request(
            f"/api/v1/signals/demand/drugs?limit=250&offset={offset}")["drugs"])
    if require_drug_baseline:
        if (ranking.get("method") != "cms_partd_two_year_persistence_v2"
                or not ranked_rows or len(ranked_rows) != ranking["filtered_total"]):
            raise AssertionError("Evaluated drug ranking is unavailable or incomplete")
        source_year = int(ranking["source_year"])
        target_year = int(ranking["target_year"])
        if target_year != source_year + 2:
            raise AssertionError("Drug ranking source and target years disagree")
        for row in ranked_rows:
            if (row["id"] not in usable
                    or row["observation_date"] != f"{source_year}-12-31"
                    or row["forecast_horizon"] != str(target_year)
                    or not 0 <= row["demand_state"] <= 4
                    or not math.isfinite(row["observed_claims"])
                    or row["observed_claims"] < 0):
                raise AssertionError(f"Invalid ranked drug row: {row['drug_name']}")
            latest = usable[row["id"]]
            if (latest["value"] != row["demand_state"] or
                    latest["forecast_horizon"] != row["forecast_horizon"]):
                raise AssertionError(f"Ranking disagrees with latest: {row['drug_name']}")

    return {"status": "passed", "catalog_ids": len(catalog), "seed_ids": len(seed_ids),
            "usable_latest_ids": len(usable), "missing_seed_news": len(news_missing),
            "missing_seed_atc": len(atc_missing), "latest_nonshadow_period":
            freshness["latest_observation_date"], "source_kinds_with_history": history_checked,
            "ranked_drugs": len(ranked_rows), "failed_sources": freshness["failed_sources"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--require-source-refresh", action="store_true")
    parser.add_argument("--require-drug-baseline", action="store_true")
    args = parser.parse_args()
    print(json.dumps(audit(args.base_url,
                           require_source_refresh=args.require_source_refresh,
                           require_drug_baseline=args.require_drug_baseline), indent=2))


if __name__ == "__main__":
    main()
