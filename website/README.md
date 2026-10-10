# PULSE — Pharmacy Demand and Replenishment Demo

PULSE serves dated public-health and pharmaceutical source context alongside an
evaluated Arkansas Medicare Part D demand-state baseline. Its frontend also
supports private, browser-based pharmacy demand planning. This is a research
system, not an operational purchasing or clinical decision system.

## What the live service runs

The FastAPI service imports a fixed 1,250-ID seed catalog and exposes dated
values through `/api/v1/signals/catalog`, `/latest`, `/history`, `/freshness`,
and source-specific routes. The source worker checks FDA news and shortages,
BLS, Medicaid, CMS, FluView, NNDSS, and wastewater feeds. Source adapters and
evaluated newer-drug outputs can add stable IDs beyond the seed catalog.

`/api/v1/signals/demand/drugs` serves the separately evaluated two-year
persistence projection of **relative** Arkansas Medicare Part D claim states.
Its response includes the source year, target year, observed source claims,
method, and chronological evaluation. A state is neither a dispensing count
nor units to order.

The 20 archived news-model IDs have a local article-text Ridge shadow runner.
Its current estimates are recorded with article provenance but remain excluded
from usable latest values. The 18 archived ATC outputs are also excluded from
usable latest values. The older `model/prod_pipeline.py` is a research fit that
fails closed for live output. The saved forecast artifact in `../model/` has
`operational_ready: false`; the website worker does not load it. The original
FLAN-T5 article-to-20-signal runner and checkpoint are not in this checkout.

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
