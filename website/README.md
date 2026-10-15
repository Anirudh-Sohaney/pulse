# PULSE — Pharmacy Demand and Replenishment Demo

PULSE serves dated public-health and pharmaceutical source context and
Arkansas Medicare Part D demand states.
Its pharmacy portal trains an individualized forecast from a pharmacy's own
sales history and selected public signal history in the browser, with separate
next-1/7/14/30-day models.
The `/signals` page shows source-linked recent news, a color-coded historical
synthetic replay for 30 drugs at 1/7/14/30 days, and searchable annual Part D
claim categories. Pharmacy-specific short-horizon forecasts remain in the
private planner.

## What the live service runs

The FastAPI service retains a fixed 1,250-ID historical seed catalog and exposes dated
values through `/api/v1/signals/catalog`, `/latest`, `/history`, `/freshness`,
and source-specific routes. The seed manifest is an archive of identities, not
the current publication list. The source worker checks FDA news and shortages,
FRED-hosted BLS series, Medicaid, CMS, FluView, NNDSS, and wastewater feeds. Source adapters and
evaluated newer-drug outputs can add stable IDs beyond the seed catalog.
The Medicaid SDUD adapter retains the official annual releases from 2023 onward
as separate, dated snapshots. It checks catalog modification times before
downloading unchanged years. Suppressed prescription counts stay unknown;
the latest-source route remains on the newest year and quarter even when an
older release is revised. This archive is source evidence for future quarterly
evaluation, not a published quarterly drug forecast.

`/api/v1/signals/demand/horizon-study` serves the held-out 1/7/14/30-day
synthetic pharmacy comparison from `../model/trained/synthetic_horizon_xgb_v1/`.
It reports scores, five selected signal identities per drug/horizon, and a
dated 30-drug synthetic projection snapshot, not a live pharmacy forecast.
The prior `/demand/outlook` route returns HTTP 410:
its exported public model used frozen synthetic sales features and could not
produce current pharmacy-specific demand. The scheduled worker no longer
generates those outlooks. After retraining the model, run
`python3 scripts/export_horizon_study.py` from this directory to package the
updated study for website-only builds.

`/api/v1/signals/demand/drugs` serves 1,018 selected two-year projections of
**relative** Arkansas Medicare Part D claim states from the current 2024
source. The stronger tier contains 729 drugs with exact state agreement in
three earlier chronological folds. Another 289 have the same relative claim
state in the two latest source years and sit at least 0.25 log units from a
category cutoff. The combined rule achieved 88.4% balanced accuracy on a later
historical fold; the added tier was less accurate on its own. The broad
unselected baseline achieved 84.2% across four folds.
The API includes source year, target year, source claims, method, and evaluation.
A state is neither a dispensing count nor units to order. Prior broad baseline
records remain in history, but no longer qualify as usable latest projections.

The 20 archived news-model IDs were intermediate inputs to the older 1,324-drug
research model. Two pairs are exact aliases, giving 18 distinct historical
series. The original article-to-20 generator is unavailable; the local
article-text Ridge runner is a separate shadow experiment trained on ten
matched months. It checks all 20 names, both alias pairs, and each output's
range against the 97-month archived history. Its estimates retain article
provenance and remain marked unusable as direct observations. The private
browser trainer excludes unusable shadow values and uses only timestamped
eligible source rows; the CMS Part D state ranking remains separate. The 18
archived ATC outputs are excluded from the current publication scope. The
older `model/prod_pipeline.py` is a separate research path. The original
FLAN-T5 article-to-20-signal runner and checkpoint remain unavailable.

## Run locally

From `website/`, install `requirements.txt` in a virtual environment, then:

```bash
python -m scripts.import_signal_catalog
python -m scripts.audit_signal_store
python -m backend.main
```

Run `python -m backend.worker` in another terminal for scheduled source checks
at 16:00 UTC, or `python -m backend.worker --once` for one immediate pass.
The worker uses a data-directory lock, so only one instance can refresh a
database at a time. Public feeds may rate-limit repeated manual runs; see
`/api/v1/signals/freshness` for the latest status of each source.

From `website/frontend/`, run `npm install` and `npm run dev`. The development
frontend defaults to `http://localhost:5173`; the API defaults to
`http://localhost:8000`.

## Verification

```bash
cd website
python -m pytest tests
python -m scripts.audit_signal_store
python -m scripts.audit_live_signal_api --require-source-refresh --require-drug-baseline
cd frontend
npm run lint
npm run build
```

After building, run the following from `website/` to exercise a clean
temporary API and built frontend without creating an account in the live
database:

```bash
python -m scripts.browser_smoke_isolated --signal-years 3 --simulate-rate-limit
```

See [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) for the dated source and
deployment audit. Older entries in that log describe earlier catalog sizes
and experiments; use the current API and seed manifest for live counts.

Do not commit private pharmacy records, patient data, API keys, passwords, or
unreviewed model artifacts. Public proxy signals do not establish local stock,
wholesaler allocation, or procurement needs.
