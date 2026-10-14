# PULSE

PULSE serves dated public-health and pharmaceutical context, selected Arkansas Medicare Part D claim-state projections, and a private pharmacy demand planner at [pulseintelligence.tech](https://pulseintelligence.tech).

## Current model paths

| Path | Current role |
| --- | --- |
| `website/backend/` | FastAPI service and scheduled public-source refresh worker. The worker refreshes source observations, selected annual Part D claim-state projections, and an article-text shadow experiment. |
| `website/frontend/` | Public signal pages and a private browser planner that trains from an uploaded pharmacy's own sales and inventory history. |
| `model/arkansas_pharma_signal/` | Research models, evaluators, source adapters, and a multimodal architecture with an internal news encoder. Its named outputs are target forecasts and context states, not the historical 20 article-signal columns. |
| `model/prod_pipeline.py` | Archived combined research design. It consumes the historical 20-column table as an input and deliberately refuses live prediction without an operational input path. |
| `model/trained/synthetic_horizon_xgb_v1/` | Saved 1/7/14/30-day models for 30 synthetic drug series, presented as a historical study rather than live pharmacy forecasts. |

The 20 monthly article-derived columns in the older combined model were upstream features for the drug and ATC heads. The available historical Git trees contain their dated output table, but no original article-to-20 inference runner or matching weights. The current multimodal architecture can encode article text directly; it does not contain a trained head that reproduces those 20 named columns. A separate 20-column stage is unnecessary for a future direct-text design, but that design still needs a trained and chronologically validated production forecast path. See [the news integration audit](model/docs/NEWS_ONLY_INTEGRATION.md) and [model documentation](model/docs.md).

The live annual Part D surface currently selects 1,018 drug claim-state projections from public annual data. Claims are a demand proxy, not pharmacy dispensing or units to purchase. The article-text 20-column experiment remains shadow-only and is excluded from usable latest values and purchasing.

## Repository layout

```text
PULSE/
├── data/        # Public historical source data and provenance
├── model/       # Research models, evaluation, and saved study artifacts
├── test data/   # Synthetic pharmacy input examples
└── website/     # Live API, refresh worker, frontend, and deployment scripts
```

## Run locally

Use the commands in [website/README.md](website/README.md) for the API, worker, frontend, and verification. Oracle deployment and recovery procedures are in [website/deploy/oracle/README.md](website/deploy/oracle/README.md).

No public proxy in this repository observes local shelf inventory, wholesaler allocation, or a patient's clinical need. Do not treat these outputs as procurement or clinical instructions.
