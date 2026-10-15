# Model rebuild progress

**Last updated:** 2026-10-15
**Status:** Multi-horizon synthetic XGBoost artifacts are saved under `model/trained/synthetic_horizon_xgb_v1/`. Article-text 20-signal inference remains a quarantined shadow path; real-world production validation is outstanding.

## Current result

The new trainer evaluates direct next-1/7/14/30-day recorded-unit totals for
all 30 synthetic drugs. Each drug/horizon uses five training-selected distinct
signals and compares matched sales-only XGBoost. On the untouched final
period, pooled signal-versus-sales-only WAPE is 42.34%/43.57% for 1 day,
29.69%/28.66% for 7 days, 30.66%/30.02% for 14 days, and 30.72%/32.70% for
30 days. Validation-selected use of signals yields 42.30%, 29.54%, 30.66%,
and 28.85% respectively. The one-day and 30-day signal arms improve pooled
WAPE; the seven-day and fourteen-day arms do not. The signal archive does not
preserve publication vintages, so this period-end-based historical replay is
not a point-in-time operational validation. Full protocol and scores are in
[`../comparison.md`](../comparison.md).

The earlier 14-day result below is retained as a separate fixed-recipe study.

The earlier local demand forecaster predicted 14-day unit totals for 30 synthetic drug series from sales history and dated catalog signals. The catalog has 1,312 signal columns. Its fixed historical top-five recipe was rebuilt with a 14-day purge between training labels and the test period.

| Evaluation | Signal model | Matched sales-only | Result |
|---|---:|---:|---|
| Fixed historical recipe, purged final period | 29.27% pooled WAPE | 31.96% pooled WAPE | 8.43% relative WAPE reduction; 18 of 30 drugs improved |
| 90-grid validation winner, purged final period | 32.87% pooled WAPE | 29.85% pooled WAPE | 10.14% relative WAPE regression |
| Archived 20-output temporal model, final 20 months | 6.3771 mean MAE | 8.9300 persistence MAE | Lower error on the archived monthly values |
| Article text to archived 20 outputs, two-month holdout | 11.91% pooled WAPE | 87.07% persistence WAPE | Exploratory only; only 10 months matched labels |

The purged historical recipe closely reproduces the earlier synthetic result of 29.40% versus 32.35%. Its comparison uses the repository's existing final date window and synthetic data; it is not evidence on observed pharmacy sales.

## Work completed

- On 2026-10-15, exported the retained public Arkansas SDUD releases (2023–2026)
  from the live source store through `website/backend/export_sdud_model_csv.py`.
  The export contains only source rows and records each release hash and source
  timestamp in a separate manifest; it contains no account data. Corrected the
  model's CSV adapter to use the historical lowercase product-name identity and
  to replace, rather than sum, a revised drug-quarter. The resulting panel has
  56,053 drug-quarter rows, including 1,072 drug names shared with 2012–2022
  history and 867 drugs spanning the 2022-Q4/2023-Q1 boundary. The 2023–2026
  release hashes start `01a6f7684387`, `909f177ee058`, `0181c5f6fa40`, and
  `62677ced06c4` respectively.
- The strict quarterly evaluation on that merged panel selected a pointwise
  model with 9.16% test WAPE; the best model by test score reached 8.58%, but
  that diagnostic result did not determine selection. The strongest naive
  WAPE was 11.06%. In the
  one-year rolling evaluation, all nine folds beat their strongest naive
  baseline, five cleared the 10% improvement gate, and mean improvement was
  15.8%. Mean selected three-state accuracy was 67.6% (40.2% balanced), below
  the project's 75% promotion goal. The 2025 test fold selected a model with
  5.23% WAPE against 7.34% for its strongest naive baseline. These scores
  weaken under the unrestricted future-window protocol: eight of nine folds
  beat their strongest naive baseline, only three clear 10%, and mean
  improvement is 9.2%, below that protocol's 10% gate. Both rolling reports
  are saved locally as `quarterly_rolling_1y_live_target_metrics.json` and
  `quarterly_rolling_live_target_metrics.json` under `model/artifacts/evaluation/`.
  These remain research evidence: source vintages before the 2026 archive capture
  were not retained, suppressed counts remain unknown, and the latest source
  period is 2026-Q1, so a next-quarter forecast would target the already-past
  2026-Q2. No quarterly forecast was promoted.

- On 2026-10-15, the Oracle source worker retained official Arkansas Medicaid
  SDUD releases for 2023-Q1 through 2025-Q4 and 2026-Q1, adding 272,904
  NDC/utilization rows beyond the previously stored 2026 snapshot. The public
  archive route reports each release's quarters, suppression, source timestamp,
  retrieval timestamp, and hash. The annual files have 90,367, 90,533, and
  92,004 rows for 2023, 2024, and 2025 respectively; 2026-Q1 has 22,703.
  Suppressed rows remain unknown. This closes the historical source gap after
  the model's 2022 panel, but the newest observed quarter is still 2026-Q1:
  its one-quarter-ahead target is 2026-Q2, already past as of this review.
  No current quarterly demand forecast was promoted from this backfill.

- On 2026-10-11, added and ran `arkansas_pharma_signal/synthetic_horizon_xgb.py` over 30 drugs and four direct future-unit windows. Saved 240 checksum-pinned XGBoost boosters, one manifest with five selected signal identities and scores for each drug/horizon, a command-line trainer, and a held-out synthetic replay command. Verified 120 drug/horizon rows, five distinct signals in each, loadability/checksums of every booster, and replay inference for both sales-only and signal-selected models.

- On 2026-10-09, corrected the separate annual city/drug model's blend calibration in `arkansas_pharma_signal/cli.py`. The old training command used a full-data ridge to select its last-year validation weight, leaking those labels. The new command freezes identity categories before the validation feature year and selects the weight from a ridge fit on prior years only. On the local 544,070-row 2013–2024 panel, the corrected 2023 feature-year validation chose ridge weight 0.00 at WAPE 0.14846; the old 0.70 weight and 0.14609 score were contaminated. The regenerated artifact still fails the operational input gate for 56 source variables. This calibration repair does not validate source release timing or make the annual model live-ready.

- Inspected current model code, local archived outputs, synthetic benchmark procedures, and reachable Git history. The history shows downstream code consuming the 20 monthly outputs, but no recovered upstream FLAN-T5 inference runner or matching checkpoint.
- Found 97 archived monthly rows for the 20 outputs (2018-01 through 2026-01). Trained 20 temporal XGBoost models on their own output lags and calendar features. These preserve the signal IDs and output schema; they do not reproduce article understanding.
- Found a separate GDELT article corpus with 354 records, 292 containing full text, but only 10 distinct publication months matching the archived labels. Trained a small TF-IDF/Ridge text experiment and recorded its metrics without promoting it.
- Exported that locally saved TF-IDF/Ridge model into a checksum-pinned JSON artifact and added a NumPy-only backend loader. A parity replay over all ten matched text months reproduced the saved sklearn outputs to a maximum absolute error of `3.38e-14` after the same nonnegative clipping.
- Added a daily shadow refresh for the latest closed UTC month. It uses only captured article titles and summaries, preserves the 20 catalog IDs, records model estimates as unvalidated/unusable observations, and leaves the original FLAN-T5 path distinct. The local source database currently has only five article versions from 2026-10-05 through 2026-10-08 and no captured text for the previous closed month, so it cannot yet generate a current monthly estimate.
- Trained and compared 90 configurations across 30 synthetic drug series. The grid varies signal groups/counts and lags, correlation-ranking alignment, tree depth, estimator count, learning rate, regularization, column sampling, and squared-error, Poisson, Tweedie, and pseudo-Huber objectives.
- Kept the validation-selected grid winner separate from the fixed historical recipe. Both model sets have saved weights, feature manifests, and a selectable local inference path.
- Verified Python compilation, report regeneration, and local inference for both demand recipes. Each inference run emitted 30 forecasts. `git diff --check` passed at the last code review.

> **Historical status:** The `model/rebuilt_demand/` source directory listed
> below was removed in commit `77c9427`. These links describe artifacts from
> that earlier workspace and are not executable in the current checkout.
> Current reproducible paths include `model/scripts/train_synthetic_horizon_xgb.py`,
> `data/synthetic_pharmacy_data/evaluate_current_benchmark.py`,
> `model/scripts/evaluate_synthetic_event_windows.py`, and
> `model/scripts/evaluate_cms_partd_news.py`.

## Historical artifacts

- Training and report generation: [`../rebuilt_demand/train.py`](../rebuilt_demand/train.py)
- Local demand inference: [`../rebuilt_demand/predict.py`](../rebuilt_demand/predict.py)
- Article-text experiment: [`../rebuilt_demand/train_news_text.py`](../rebuilt_demand/train_news_text.py)
- Human-readable experiment comparison: [`../comparison.md`](../comparison.md)
- Folder guide and run commands: [`../README.md`](../README.md)
- Historical news-source findings: [`NEWS_ONLY_INTEGRATION.md`](NEWS_ONLY_INTEGRATION.md)

## Remaining gaps

1. The exact historical FLAN-T5 article-to-20-signal runner/checkpoint remains unavailable. The local TF-IDF/Ridge replacement is wired only as a shadow and does not claim to reproduce it.
2. Ten matched article months are not enough to evaluate a reliable text-to-signal model. More dated, legally usable article text and signal labels are needed; the current feed summary input also differs from the archived full-text training corpus.
3. Demand accuracy has only been evaluated on the deterministic single-site synthetic panel. Evaluation against observed pharmacy sales, inventory, and stockout outcomes is required before operational use.
4. The 1,312 catalog includes derived/model outputs; the synthetic study measures their association with generated demand and does not establish independent real-world predictive value.

## Update rule

After each substantive model or data change, add the date, changed artifact, evaluation protocol, measured result, and any newly resolved or remaining limitation here. Keep `comparison.md` as the detailed per-configuration scorecard and this file as the running project log.
