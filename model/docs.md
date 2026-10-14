# Model documentation index

The active local short-horizon research model is
`arkansas_pharma_signal/synthetic_horizon_xgb.py`. Its command-line runner is
`scripts/train_synthetic_horizon_xgb.py`; trained per-drug XGBoost boosters and
the full score/feature manifest are under
`trained/synthetic_horizon_xgb_v1/`. It evaluates next-1/7/14/30-day recorded
units sold for 30 synthetic drug series, using five selected historical signal
series per drug and comparing against matched sales-only models. See
[`README.md`](README.md) and [`comparison.md`](comparison.md) for results and
limitations.

The public website currently publishes selected annual Arkansas Medicare Part
D claims-state projections through a separate baseline. Those are claims
proxies, not daily pharmacy sales forecasts. The article-to-20-signal model
remains an unvalidated shadow experiment, and its outputs are not proven
inputs to a live drug forecast. `prod_pipeline.py` fails closed for forecast
output when its required operational inputs cannot be verified.

The current multimodal research architecture encodes articles internally and
could support a direct text-to-forecast path without reconstructing the old
20-column intermediary. Its internal news state is not the 20 named historical
outputs; no trained and qualified direct-text production forecast is packaged
here. See [`docs/NEWS_ONLY_INTEGRATION.md`](docs/NEWS_ONLY_INTEGRATION.md).

Project targets, source availability, and past research are documented in
[`docs/PROJECT_GOAL.md`](docs/PROJECT_GOAL.md),
[`docs/TARGET_AVAILABILITY.md`](docs/TARGET_AVAILABILITY.md), and
[`docs/REBUILD_PROGRESS.md`](docs/REBUILD_PROGRESS.md).
