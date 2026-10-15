# Arkansas Pharmaceutical Signal Model

## Short-horizon synthetic pharmacy model (2026-10-11)

`scripts/train_synthetic_horizon_xgb.py` now trains separate per-drug XGBoost
models for the next **1, 7, 14, and 30 days of recorded units sold**. Each
drug/horizon selects five distinct signal series from the 1,312-column archive
using training-only correlation, then compares a sales-plus-signals model with
a matched sales-only model. Three XGBoost profiles and two signal-lag layouts
are selected on a purged chronological validation period; the final period is
held out. The trained boosters and feature manifests are in
`trained/synthetic_horizon_xgb_v1/`.

| Future sales window | Signals pooled WAPE | Sales-only pooled WAPE | Validation-selected pooled WAPE |
|---|---:|---:|---:|
| 1 day | 42.34% | 43.57% | 42.30% |
| 7 days | 29.69% | 28.66% | 29.54% |
| 14 days | 30.66% | 30.02% | 30.66% |
| 30 days | 30.72% | 32.70% | 28.85% |

These results show feasibility on one deterministic synthetic pharmacy, with
mixed evidence for signal lift by horizon. The archive records signal period
ends but does not provide historical publication vintages; the benchmark's
one-day-after-period-end availability is an assumption, not a verified replay.
Its output is not a live Arkansas pharmacy prediction, and the 20 article
signal IDs remain unvalidated shadow estimates. See [comparison.md](comparison.md)
for protocol and interpretation.

Run from the repository root with
`PYTHONPATH=model website/.venv/bin/python model/scripts/train_synthetic_horizon_xgb.py`.
Replay a saved booster on a held-out synthetic origin with
`PYTHONPATH=model website/.venv/bin/python model/scripts/replay_synthetic_horizon_xgb.py "Acetaminophen 500 mg tablet" 1 2025-06-01`.
The replay is an artifact check, not a current pharmacy forecast.

> **Checkout note (2026-10-10):** This document retains research results for
> `rebuilt_demand/`, but that source directory is absent from this
> checkout and local HEAD. An earlier experimental version is in reachable
> local Git history; its training and export commands cannot be run from this
> checkout as written. The exported article inference artifact and
> its shadow backend runner remain under `website/`; they are not validated
> production demand outputs.

> **Current website publication (2026-10-10):** The live website ranks 1,018
> selected two-year Arkansas Medicare Part D claims-state projections in two
> evidence tiers. This is
> a separate, release-safe persistence path; it does not consume the archived
> 20 news columns or the older 1,324-drug research forecast. The 20 archived
> columns include two exact alias pairs, so they represent 18 distinct
> historical series. The newer article-text runner preserves the 20-ID contract
> as a shadow experiment and does not supply live ranking features.

This directory contains the code, tests, research notes, and generated artifacts for an Arkansas-first system that builds public-data signals and evaluates forecasts related to pharmaceutical demand, disease pressure, drug shortages, recalls, and distribution. It combines data adapters, identity and geography mapping, time-aware feature construction, baseline and research models, chronological evaluation, and guarded output contracts.

The system is designed as a contextual signal layer for pharmacy and public-health analysis. It is **not** a source of observed pharmacy stock-on-hand, wholesaler allocation, backorder, or stockout truth: the public datasets used here generally do not record those outcomes. Any “demand,” “supply risk,” “shortage pressure,” or similar value must be interpreted according to the target and evidence fields attached to it. A metric being forecastable does not establish that it improves private pharmacy inventory decisions.

This README is the entry point to the codebase. The detailed evidence, source citations, target definitions, and contracts remain in `docs/` and in the data-source manifests under `../data/`.

## What the demand-signal evidence shows

The narrow version of the remembered result is correct: adding selected dated
signals improved XGBoost accuracy on the repository's deterministic synthetic
pharmacy panel. That proves the downstream model can use signals when they
carry information. It does not prove the missing live article-to-20 generator
or validate the same lift on real pharmacy sales.

The fixed top-five-signal recipe uses each drug's previous sales and inventory
features plus the five training-selected catalog signals at 1-, 7-, and 14-day
lags. On the final 6,180 14-day forecast origins, the purged rerun scored
29.27% pooled WAPE versus 31.96% for matched sales-only XGBoost, an 8.43%
relative reduction; it improved WAPE for 18 of 30 drugs. A separate audit of
the same purged recipe found 43.65% versus 49.43% WAPE on 2,256 origins whose
target windows overlap injected outbreak/supply-response days, and 13.10%
versus 12.32% on the other 3,924 origins. Thus the synthetic lift is stronger
in its marked event windows and reverses outside them.

That pattern is partly built into the generator: it applies a next-month
demand multiplier to acute-drug categories using the same frozen
`national_supply_chain_risk` series supplied to the models. The value is
available before the response month, so the timing is not future-data leakage;
however, it makes this a conditional capability demonstration by design. Two
signal IDs are exact aliases, so the 20 named news outputs represent 18
distinct historical series. See [`comparison.md`](comparison.md), the
generator in `../data/synthetic_pharmacy_data/`, and the event breakdown in
`docs/synthetic_event_window_metrics.json`.

The earlier pre-registered benchmark reported 29.40% versus 32.35% WAPE on the
same final period but did not purge the 14-day target at the train/test
boundary. The purged fixed-recipe result above removes that boundary overlap.
The separate 90-configuration grid's validation-selected winner scored
32.87% WAPE versus 29.85% for its matched sales-only baseline, so that tuned
winner did not improve the holdout. All synthetic results are single-site
scenarios, not observed dispensing data.

There is also a real-data annual CMS Part D evaluation over three chronological
folds, covering 1,368 Arkansas generic-drug targets and 3,575 held-out
drug-years. The saved comparison initially appeared to show lift: history
with a fixed ridge penalty reached 68.50% exact / 69.25% balanced accuracy,
while history plus news selected its penalty on validation and reached 80.64%
/ 80.96%. That baseline was not tuned on the same terms. The restored evaluator
now validation-tunes both: history-only reaches 82.43% / 82.81%, above the
history-plus-news result. This does not show incremental value from news for
the annual claims proxy. It also does not test weekly dispensing or
inventory. The archived signal-generation process has unknown row-level
article provenance, and its original FLAN-T5 runner and checkpoint are
unavailable.

The original `model/rebuilt_demand/` directory was removed in commit
`77c9427`; its 90-grid training code and saved per-drug models are not in this
checkout. The comparison report preserves those results. The pre-registered
benchmark, fixed-recipe event audit, and annual CMS evaluator can currently be
rerun from this tree.

Reproduce the available comparisons from the repository root:

```bash
python data/synthetic_pharmacy_data/evaluate_current_benchmark.py
PYTHONPATH=model website/.venv/bin/python model/scripts/evaluate_synthetic_event_windows.py
PYTHONPATH=model website/.venv/bin/python model/scripts/evaluate_cms_partd_news.py
```

The current website's 1,018 selected drug projections are a separate CMS
claims-persistence path. They do not consume the 20 archived news columns or
the synthetic XGBoost signal recipe. No current application path feeds the
live article-text shadow estimates into a per-drug XGBoost forecast; the
synthetic comparison uses the frozen 1,312-column signal file. The article
backend remains a shadow estimate with ten matched months, not a validated
replacement for the missing original signal generator. The legacy
`model/prod_pipeline.py` intentionally fails closed for forecast output.

The source-native FluView observations can be summarized as a complete, point-in-time annual candidate with `python -m arkansas_pharma_signal.fluview_point_in_time --database website/data/signals.sqlite3 --year 2025 --as-of 2026-10-09T23:00:00Z` when the model package is on `PYTHONPATH`. The bridge reads only captured Arkansas ILI, U.S. ILI, and U.S. weighted ILI rows, selects the latest revision published and recorded by the cutoff, and withholds all three means unless every Saturday in the requested year is present. Its output explicitly says `model_compatible: false`: the saved model used a different issue-year feature alignment and an unpublished Arkansas weighted-ILI column. This bridge is a building block for corrected retraining, not a forecast runner.

## What the system does

At a high level, the code turns dated public observations into traceable, model-ready signals and then into evaluated, filterable forecast surfaces:

```text
public/local source files
        │
        ▼
source adapters, schema checks, and provenance
        │
        ├── drug/entity and geography resolution
        ├── structured event and news-signal extraction
        └── cadence-specific target and feature panels
        │
        ▼
point-in-time-safe joins and historical features
        │
        ├── simple persistence / seasonal baselines
        ├── regression and state models
        └── optional multimodal neural research model
        │
        ▼
chronological holdouts, rolling-origin evaluation, and ablations
        │
        ▼
qualified metric rows, feature store, forecasts, and publishability audit
```

Arkansas is the primary local evaluation setting. National, neighboring-state, and global observations may provide context, but the code should preserve their original geography and only project them when an explicit, auditable mapping permits it. Unresolved product, supplier, county, factory, API, or parent-company links are kept unresolved rather than silently inferred.

## Folder architecture

| Path | What it contains |
|---|---|
| `arkansas_pharma_signal/` | Main Python package. Source adapters, feature and panel builders, target-specific evaluators, models, output validators, and the command-line interface live here. |
| Historical `rebuilt_demand/` | Removed in commit `77c9427`; retained 90-grid scores and protocol are summarized in `comparison.md`, but the old trainer and serialized model files are absent. |
| `tests/` | Unit and contract tests for schemas, chronology, joins, model behavior, target evaluation, output validation, and CLI-related behavior. |
| `docs/` | Design and research documentation: architecture, data sources, implementation and target contracts, testing protocol, availability limitations, status, and review history. |
| `docs/REBUILD_PROGRESS.md` | Dated progress log for the local 20-output and 1,312-signal rebuild, including verified metrics and unresolved data gaps. |
| `scripts/` | Standalone audit and reproducibility helpers, including metric-library and project-status checks. These complement, but do not replace, the package CLI and pytest suite. |
| `artifacts/` | Generated local outputs, including panels, trained model metadata, forecast tables, news artifacts, evaluation reports, and run metadata. Contents depend on which commands have been run and are not all source-controlled or reproducible without the corresponding input data. |
| `prod_pipeline.py` | A separate legacy historical-fit script. Its forecast and CLI emission paths fail closed because current news-model and ATC inputs are unavailable. It is not the same pipeline as the package CLI. See [Legacy JSON output](#legacy-json-output). |
| `final_predictions.json` | Checked-in legacy example; a compact collection of categorical signal predictions, not the canonical validated forecast table. The legacy script no longer overwrites it. |
| `README.md` | This overview and operating guide. |
| `docs.md` | Short additional documentation entry point; the detailed documents are in `docs/`. |
| `pyproject.toml` | Python package metadata, required and optional dependencies, and pytest configuration. |
| `.gitignore` | Local artifact/cache exclusions. |

### Package module guide

The package is deliberately split by data boundary and responsibility. Modules are grouped below by their role; several specialized adapters have their own files and tests.

#### Execution, configuration, and data contracts

| Module(s) | Responsibility |
|---|---|
| `cli.py` | Command registry and orchestration for panel builds, training, forecasts, evaluations, signal construction, and audits. |
| `config.py` | Repository-root resolution and canonical locations for inputs and generated artifact directories. |
| `io.py` | Checked CSV/JSON reading, writing, and metadata helpers. |
| `input_contract.py` | Input coverage, disposition, and eligibility summaries used to make input scope explicit. |
| `datasets.py` | Main panel/next-period dataset preparation, feature-mode selection, and ablation feature selection. |
| `features.py`, `layers.py` | Annual feature and cross-source feature construction. |
| `entities.py` | Drug-name normalization and product/ingredient identity helpers. |
| `geography.py` | Arkansas geography crosswalks, county resolution, and mapping utilities. |
| `canonical_graph.py` | Versionable entity graph with typed relationships and edge-level provenance. |
| `event_schema.py` | Schemas and semantics for structured events. |
| `publishability.py`, `external_metric_output.py` | Fail-closed checks for whether metrics and forecast rows meet their declared contracts. |

#### Public data adapters and contextual signal families

| Module(s) | Responsibility |
|---|---|
| `input_sources.py`, `live_inputs.py` | Source definitions and explicitly refreshable public inputs, including selected CMS, FDA, and weather feeds. |
| `news_corpus.py`, `event_extraction.py`, `event_schema.py` | Load available article text and metadata, extract typed events, and retain date/evidence boundaries. |
| `news_signals.py`, `news_relevance.py`, `text_signals.py`, `expert_news_heads.py`, `learned_event_state.py` | News relevance, disease/event signal construction, text features, and associated experimental/evaluation paths. |
| `news_only_adapter.py` | Validated bridge for the archived 20-column news-only monthly output table. It has a strict schema boundary and prefixes bridged features with `news_only_`. The original FLAN-T5 inference runner is unavailable; the separate website TF-IDF/Ridge article estimate is shadow-only and unvalidated. |
| `weekly_health_proxy.py`, `nssp_respiratory.py`, `respnet.py`, `hospital_respiratory.py`, `wastewater_pressure.py`, `regional_wastewater.py`, `overdose_pressure.py` | Disease, respiratory, wastewater, hospital-utilization, and overdose context adapters/evaluators. These are health-pressure proxies, not medication inventory labels. |
| `historical_weather.py` | Historical weather loading and aggregation with observed coverage retained. |
| `shortage_pressure.py`, `supplier_shortage.py`, `recall_pressure.py`, `arkansas_exposure.py` | FDA shortage, supplier-event, recall, and Arkansas exposure signals and evaluation. |
| `arcos_evaluation.py`, `supply_chain_pressure.py`, `supplier_hierarchy.py` | Controlled-substance distribution, supply-chain context, and supplier hierarchy construction. |
| `nadac_target.py`, `demand_price`-related adapters | Drug price and acquisition-cost targets/context where supported by the local data. Price is not treated as demand or shortage truth. |
| `medicaid_demand.py`, `monthly_pharmacy_demand.py`, `quarterly.py`, `cms_partd_quarterly.py`, `county_demand_state.py`, `regional_demand_state.py`, `therapeutic_class_demand.py`, `county_outcomes.py` | Arkansas/public demand proxies at their native monthly, quarterly, annual, county, region, or therapeutic-class grains. |
| `apcd_claim_counts.py`, `historical_metric_context.py`, `metric_feature_store.py` | Claim-activity and historical metric-context experiments, plus controlled attachment of qualified metric rows to downstream features. |

#### Models, evaluation, and reporting

| Module(s) | Responsibility |
|---|---|
| `regression.py`, `training.py`, `forecast.py`, `evaluate.py` | Baseline and regression training, operational annual forecast construction, and strict/rolling evaluation. |
| `neural.py` | Smaller neural/blending components used by the existing evaluation/training path where enabled. |
| `modular_model.py` | Larger modular multimodal research architecture (described below); its implementation is not, by itself, evidence of forecast quality or production promotion. |
| `universal_forecast.py` | Broad county × drug × supplier forecast contract with evidence, grain, horizon, confidence/uncertainty, and promotion metadata. |
| `publishable_evaluation.py`, `publishable_model_evaluation.py`, `publishable_test_dataset.py`, `external_validation.py` | Evaluation datasets and checks for chronological performance, external references, and publication eligibility. |
| `metric_audit.py`, `qualified_metric_forecasts.py`, `metric_feature_store.py`, `cross_signal_utility.py`, `signal_utility.py` | Metric qualification, utility/ablation analysis, and safe conversion/attachment of qualified forecast metrics. |
| `project_status.py`, `annotation.py`, `expert_gold.py`, `event_accuracy.py` | Status, annotation, expert-gold, and event extraction evaluation/reporting. |

The source tree contains additional target-specific modules beyond the table. For the authoritative current package inventory, inspect `arkansas_pharma_signal/` and use `rg --files model/arkansas_pharma_signal` from the repository root.

## How the modeling pipeline works

### 1. Inputs and provenance

The main inputs are local datasets under `../data/`, including the combined external-state feature store, event and entity tables, and selected additions from CMS/HHS, FDA, CDC, DEA, weather, news, and other documented public sources. Exact paths vary by task; the source catalog is in [Data sources](docs/DATA_SOURCES.md), and individual source directories under `../data/` carry their own manifests and notes.

Source records can differ in date cadence, publication lag, geography, drug identity, suppression, and coverage. Adapters and contracts are intended to preserve these characteristics, not erase them. An absent observation is not automatically zero; a source release date is not automatically the event date; and a national statistic is not automatically a county or pharmacy observation.

Some source datasets or large artifacts may be stored through Git LFS. A clone with pointer files rather than payloads cannot build every target. Retrieve tracked LFS objects when applicable and check each source manifest before interpreting a missing file as a model problem.

### 2. Identity and geography resolution

Drug and supplier names are normalized and mapped where evidence supports it, using sources such as FDA NDC records, RxNorm-related mappings, and documented crosswalks. Geography resolution uses explicit county/region/city/ZIP relationships when available. The entity graph records relationship provenance and effective dates for graph-based consumers.

Mapping coverage is not assumed complete. County allocations, supplier parents, API manufacturers, factories, or product-to-establishment links must not be invented to fill gaps. Output consumers should inspect identity fields, `confidence`, `evidence_type`, and missingness/availability fields where present.

### 3. Feature and target construction

Feature builders combine source-native observations at compatible time and geography grains. Examples include prior demand, seasonal or rolling summaries, disease surveillance, news-derived event context, weather, shortage/recall counts, supplier exposure, price, and trade/supply-chain indicators. The exact feature set depends on the task and serialized model contract; there is no universal fixed set of “all signals.”

Targets are also source- and cadence-specific. Examples include annual CMS Part D claim proxies, quarterly Arkansas Medicaid SDUD prescription counts, monthly HHS Medicaid provider/NDC claim activity, shortage-supplier counts, recall severity, respiratory pressure, or ARCOS ZIP3 distribution pressure. These targets are not interchangeable, and their forecast rows retain their defined grain and meaning.

For models that use future outcomes, chronological evaluation separates feature history from the target period. Point-in-time rules and source availability matter: only information available at or before the feature origin can be used for that forecast. Some current snapshots have no historical publication vintages, so they can be useful descriptive context but cannot support an honest historical replay as if they had been known then.

### 4. Model families

The package favors measured, interpretable baselines and target-specific models over assuming that a larger model is better. Depending on the target, the code includes persistence/seasonal comparators, ridge-style regression, logistic/state models, feature blends, and specialized source-family evaluators. Training, validation, and feature selection belong inside the appropriate chronological split; a result must be compared with a meaningful baseline on the same held-out periods.

The optional modular research model in `modular_model.py` is a multimodal architecture with six code-level stages:

1. `NewsDocumentEncoder` represents timestamped news documents and handles empty-document inputs.
2. `TypedGraphReasoner` performs masked message passing over typed entities and relations.
3. `TemporalFusionReasoner` encodes causal sequences of historical demand and external state.
4. `CrossModalInteractionReasoner` exchanges information across news, graph/geography, and temporal states.
5. `DeepConnectionReasoner` applies a further typed interaction stage across those modalities.
6. `ArkansasPredictionHeads` emits numeric/quantile and target-state outputs plus driver contributions under the configured contracts.

The model and its tests establish an implementation contract; they do not establish that this architecture beats simpler models. It remains a research path unless the appropriate leakage-safe rolling-origin gates and publication criteria are met. See [Architecture](docs/ARCHITECTURE.md), [Project goal](docs/PROJECT_GOAL.md), and [Testing protocol](docs/TESTING_PROTOCOL.md) for its exact design and evaluation requirements.

### 5. Evaluation and promotion

Evaluation should use chronological holdouts and, where enough history exists, multiple rolling-origin folds. Reports may include WAPE, MAE, error/skill relative to persistence, exact or balanced state accuracy, event precision/recall, within-tolerance coverage, fold coverage, and per-drug or per-region coverage. Metric choice depends on target semantics; accuracy on an imbalanced binary label can be misleading.

The code separates several claims that are easy to conflate:

- **A source is integrated:** it can be loaded and represented as a feature.
- **A proxy is predictable:** a historical proxy target can be forecast under a specified test contract.
- **A feature adds utility:** a matched ablation improves the same held-out folds over an otherwise identical baseline.
- **A metric is qualified:** it passes the project’s defined evidence and coverage gates.
- **A pharmacy outcome improves:** it helps predict direct private outcomes such as inventory or stockouts. The public datasets do not currently establish this claim.

`audit-publishability` and the metric serializers are intentionally fail-closed. An unqualified proxy, stale/legacy state definition, malformed row, unsupported geography, or missing required evidence must not silently become a publishable forecast. Consult the generated audit report and its source metadata instead of inferring promotion from the existence of an output file.

## Main output surfaces

Generated files live under `artifacts/` (unless a command explicitly documents another path). The exact set is run-dependent; common contracts include:

| Artifact family | Typical output | Meaning |
|---|---|---|
| Main demand panel | `artifacts/panel/panel.csv` and `artifacts/metadata/panel.json` | Joined model-ready historical panel, with build metadata. |
| Trained models | `artifacts/trained/models.json` plus metadata | Serialized model parameters/contracts used by supported forecast commands. Presence alone does not imply passing forecast gates. |
| Standard forecast | `artifacts/forecasts/forecast.csv` (name is defined by the CLI implementation) and `artifacts/metadata/forecast.json` | One-year-ahead annual city/drug research rows from the guarded model, bounded by the requested row budget. Unsupported short horizons and intervals are not emitted. |
| Quarterly forecast | `artifacts/forecasts/quarterly_forecast.csv` and quarterly metadata | Arkansas Medicaid quarterly demand proxy forecast, optionally refreshed from official CMS SDUD input in memory. |
| Universal forecast | `artifacts/forecasts/universal_forecast.csv` and `artifacts/metadata/universal_forecast.json` | Filterable county × drug × supplier-oriented output, with unresolved mappings and evidence type explicit. If trained models are unavailable, the implementation may label rows as heuristics rather than trained predictions. |
| Qualified metric surface | `artifacts/forecasts/qualified_metric_forecasts.csv.gz` | Rows for metrics that passed their metric-level contract; not a blanket pharmacy inventory forecast. |
| Feature store | `artifacts/forecasts/qualified_metric_feature_store.csv.gz` with JSON metadata | Grain-preserving values/availability metadata intended for compatible downstream joins. Never assume it can be broadcast to a finer geography or pharmacy. |
| News artifacts | `artifacts/news/` | Historical corpus, Layer 1 news-state features, relevance outputs, and optional validated news-only bridge. These are intermediate features, not final forecasts. |
| Evaluation | `artifacts/evaluation/` | Point-in-time and rolling metrics, ablations, coverage reports, status reports, external-reference checks, and publishability results. |
| Other intermediate artifacts | `artifacts/entities/`, `geography/`, `events/`, `suppliers/`, `weather/`, `outcomes/`, `metadata/` | Built mappings, graph, structured events, weather panels, supplier context, county outcomes, and command/run metadata where generated. |

The local annual model was retrained on 2026-10-09 after fixing calibration leakage: the raw-scale ridge blend weight is selected using only a model fitted before the held-out feature year, with identity categories frozen before that year. The corrected 2023 feature-year validation selected a ridge weight of **0.00** and WAPE **0.14846**. The older artifact selected **0.70** and reported **0.14609** after fitting the ridge on the validation labels; that score was not a valid held-out calibration result. The corrected fit is still a research artifact: 56 trained source variables lack compatible live adapters and the source panel is partly backed by unhydrated Git LFS files. The training command records a SHA-256 digest of the complete panel, and the standard forecast command requires that exact panel plus a leakage-safe calibration declaration before proceeding. Neither safeguard promotes the artifact as live-ready.
The annual forecast writer now emits one row per target/city/drug for the next year only. With the corrected claims blend at weight zero, its claims driver list is empty and the family is labeled persistence. It does not duplicate identical predictions under each disease label, stretch annual claims into short horizons, claim a calibrated interval from a log-scale residual, or substitute a neutral shortage-risk score when no risk model exists. Missing targets and uncalibrated intervals remain absent.

### Forecast row interpretation

Forecast output is generally a table of many rows rather than a single universal prediction. A row should be read together with its target name and semantics, forecast period/horizon, observation period, geography level and identifiers, drug identity, supplier resolution, evidence/provenance, prediction method, and uncertainty/calibration/promotion fields when available. Not every surface contains every key.

In particular:

- A county × drug or supplier × drug row is only as specific as the underlying observed data and mapping evidence.
- A supplier or labeler key is not necessarily a confirmed manufacturer, parent, API source, or local distributor.
- An exposure-based or event-based risk score is not observed on-hand inventory and should not be presented as a calibrated probability unless its contract says it is calibrated.
- `confidence`, risk values, interval bounds, or state labels must be interpreted using their associated status/semantics fields. Missing uncertainty is not certainty.
- A feature store is a join interface, not proof that a source improves a downstream model.

## Install and run

Run commands from the repository root. The package metadata requires Python 3.9+ and the core dependencies `pandas`, `numpy`, and `xgboost`; scikit-learn and pytest are included in the `dev` optional extra, and PyTorch is in the `deep` extra. Individual source adapters may need additional packages or data files. The repository may use a project-local environment such as `data/.venv`; substitute the correct interpreter for your machine.

Example setup:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e "./model[dev]"
```

On Windows PowerShell, activation is typically `\.venv\Scripts\Activate.ps1`. If not installing the package, set `PYTHONPATH=model` when invoking it.

### Core workflow

```bash
# Build the main annual panel from local data.
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . build-panel

# Train supported demand/risk models and save their feature contracts.
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . train

# Run strict next-period evaluation.
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . evaluate --no-neural

# Generate the standard forecast surface.
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . forecast --max-rows 10000

# Run the final fail-closed publication checks.
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . audit-publishability
```

`--no-neural` skips the more expensive neural blend during the standard evaluator; omit it when you want that path evaluated and have the required dependencies/resources. Run `... cli --help` and `... cli <command> --help` for current flags. A successful command means its code completed; use the output metrics and status artifact to decide whether a forecast or metric qualified.

### Other useful workflows

```bash
# Strict quarterly Medicaid SDUD demand evaluation, optionally rolling-origin.
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . evaluate-quarterly --rolling

# Generate the quarterly forecast; repeat --live-sdud-url for multiple annual CMS files.
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . forecast-quarterly --max-rows 10000

# Build the entity graph, article corpus, event layer, and news signals.
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . build-graph
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . build-news-corpus
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . build-events
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . build-news-signals

# Build the county × drug × supplier-oriented forecast and apply filters.
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . forecast-universal \
  --max-rows 10000 --county-fips 05001 05003 --region central

# Build/calculate other supported surfaces and audits.
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . build-geography
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . build-supplier-hierarchy
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . validate-external-reference
```

The CLI also includes commands for weather refresh/history, county outcomes, event annotation/gold benchmarks, supplier shortage evaluation, ARCOS evaluation, and target-specific utility experiments. These are research and data-build tools; run the command’s help and consult its matching docs before interpreting its outputs.

To evaluate the retained public Arkansas SDUD releases from the Oracle source
store, export only those source rows and keep the hash manifest beside them:

```bash
ssh dev_server 'docker exec website-worker-1 python -m backend.export_sdud_model_csv' \
  > /tmp/pulse-sdud-archive.csv 2> /tmp/pulse-sdud-archive-manifest.json
PYTHONPATH=model website/.venv/bin/python -m arkansas_pharma_signal.cli \
  --root . evaluate-quarterly \
  --live-sdud-url file:///tmp/pulse-sdud-archive.csv \
  --rolling --rolling-test-window-years 1
```

This supplies current *target history* to the research evaluator; it does not
make a current forecast when the latest published quarter is already past.
The CSV adapter excludes suppressed counts and replaces any overlapping
drug-quarter in the old panel instead of adding a revised count to it.

### Separate news-only model integration

The archived 20-signal FLAN-T5-small output is a dated feature bridge, not an inference pipeline. The adapter validates and materializes it for downstream training; it does not directly forecast pharmacy availability. A separately trained TF-IDF/Ridge article-to-20 model and API shadow runner are documented in `docs/NEWS_ONLY_INTEGRATION.md`. If the historical bridge changes, rebuild the bridge and panel and retrain models so the saved feature contract matches:

The historical 20-signal table is bundled at
`website/catalog/news_only_catalog_features.csv.gz` and runs through January
2026. The original upstream `run_extract.py` and FLAN-T5 inference checkpoint
are absent. The separate local article model can produce shadow estimates,
but its small training sample and summary-text inputs do not qualify it for
published demand values. The web service imports historical dated values and
keeps the new estimates unusable until their model is evaluated for promotion.

```bash
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . build-news-only-features
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . build-panel
PYTHONPATH=model python -m arkansas_pharma_signal.cli --root . train
```

Full source and execution details are in [News-only integration](docs/NEWS_ONLY_INTEGRATION.md). This adapter is optional; the base package does not silently fabricate the missing news model output.

## Legacy JSON output

`prod_pipeline.py` is a separate, older research workflow. Its historical fit code reads the bundled monthly news-model signal table plus particular CMS/HHS and RxNorm/ATC files. The upstream news-model inference runner and model-specific checkpoint are absent. The script now exits without emitting forecasts, and `ProductionModel.predict()` raises a clear error. The checked-in `model/final_predictions.json` remains an archival example with `news_signals_20`, `arkansas_atc_signals_18`, and `cms_part_d_signals_1300` keys.

This file is **not** the same as the package CLI’s artifact set: it does not have the canonical row-level forecast contract, and its payload uses categorical demand states. The former zero-vector ATC inference path was removed because it could fabricate a live-looking output. The news loader only reads a local historical CSV; no API key can turn it into a live runner. Treat `final_predictions.json` as a legacy/demo signal bundle unless a new runner is independently implemented, evaluated, and reconciled with the current contracts. Do not cite it as proof of live inference, inventory prediction, or the latest qualified model output.

## Testing and quality checks

The test suite is organized around the package’s functional boundaries and contracts. It covers (among other areas) schemas and input eligibility, date/grain-safe joins, demand and shortage targets, news relevance and extraction, geography, supplier identity, source-specific adapters, chronological evaluation, learned model structures, metric promotion, feature-store attachment, and forecast serialization.

Run tests from the repository root:

```bash
PYTHONPATH=model python -m pytest model/tests
```

For a focused test, pass its path, for example `model/tests/test_modular_model.py`. The authoritative list is `model/tests/`. Some integration tests require local data artifacts and optional libraries. A passing unit suite verifies tested implementation behavior; it does not demonstrate forecast superiority or readiness for pharmacy deployment.

The standalone audit helpers include:

```bash
PYTHONPATH=model python model/scripts/audit_metric_library.py
PYTHONPATH=model python model/scripts/audit_project_status.py
```

Use the [Testing protocol](docs/TESTING_PROTOCOL.md) for evaluation rules, required split discipline, promotion gates, and interpretation requirements. Do not overwrite historical evaluation evidence with an in-sample fit or compare metrics from different populations, grains, or time windows as though they were matched experiments.

## Data and scientific limitations

1. **No public inventory ground truth.** The available public data do not directly reveal pharmacy shelf counts, fill denials, backorders, or wholesaler allocations.
2. **Targets are proxies.** Claims, prescriptions, drug spending, disease activity, shortage listings, recalls, and ARCOS distribution answer different questions and operate at different grains.
3. **Source time is not always event time.** Publication lag and historical-vintage availability can make a retrospective feature invalid for a simulated historical forecast. The source-specific date contract matters.
4. **Mappings are incomplete.** Drug identity, supplier hierarchy, county assignment, API/factory links, and geography coverage may be partial. Unknowns must stay visible.
5. **A signal is not automatically useful.** Incremental value must be measured with matched chronological ablations against a suitable baseline; integration alone is not uplift.
6. **Research architecture is not model validation.** A large neural model can compile, train, and pass shape tests without beating a persistence or regression baseline.
7. **Publication is separately gated.** A generated row or metric file is not necessarily qualified for external use. Check the audit status, target semantics, evaluation coverage, calibration, and provenance.

See [Target availability](docs/TARGET_AVAILABILITY.md), [Data sources](docs/DATA_SOURCES.md), and [Research basis](docs/RESEARCH_BASIS.md) for detailed evidence and source-specific limitations.

## Documentation map

| Document | Use it for |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | System layers, forecast grid, model architecture, and output concepts. |
| [Codebase guide](docs/CODEBASE_GUIDE.md) | Execution graph, package roles, operational commands, and maintenance principles. |
| [Data sources](docs/DATA_SOURCES.md) | Local source inventory, roles, source citations, and caveats. |
| [Target availability](docs/TARGET_AVAILABILITY.md) | What the sources can and cannot directly measure. |
| [Project goal](docs/PROJECT_GOAL.md) | The acceptance contract and forecast-quality gates. |
| [Implementation contract](docs/IMPLEMENTATION_CONTRACT.md) | Input/output expectations and minimum implementation requirements. |
| [Testing protocol](docs/TESTING_PROTOCOL.md) | Chronology, evaluation, and model-promotion requirements. |
| [Research basis](docs/RESEARCH_BASIS.md) | Research rationale and methodological references. |
| [Metric research matrix](docs/METRIC_RESEARCH_MATRIX.md) | Candidate metrics, evidence, and measured utility. |
| [Input variable audit](docs/INPUT_VARIABLE_AUDIT.md) | Feature availability and input-contract auditing. |
| [News-only integration](docs/NEWS_ONLY_INTEGRATION.md) | Separate FLAN-T5 feature source and adapter boundary. |
| [Review log](docs/REVIEW_LOG.md) | Historical reviews and recorded decisions; not a substitute for current artifacts. |
| [Project status](docs/PROJECT_GOAL.md), [expansion status](docs/EXPANSION_STATUS.md), and [completion notes](docs/GOAL_COMPLETION.md) | Current/recorded completion criteria and expansion tracking. Check file dates and generated audits for the newest state. |

## Safe extension rules

When adding a source, feature, target, or model:

1. Document the source, retrieval method/date, licensing/access conditions, coverage, cadence, keys, suppression behavior, and publication delay.
2. Define the target grain and semantics precisely. Never relabel a public proxy as pharmacy inventory.
3. Preserve raw or source-native values and record transformations; avoid turning unknown or suppressed observations into zero.
4. Add identity/geography crosswalks only with an explicit evidence basis. Keep unresolved joins and missingness explicit.
5. Build features using only information available at the forecast origin; test time barriers and target windows.
6. Compare against persistence or another appropriate baseline on identical chronological folds. Evaluate the feature family and model change with a matched ablation.
7. Add unit/contract tests for schema, chronology, missingness, grain, and serialization, not merely a smoke test that code executes.
8. Update the relevant source, target, architecture, and implementation documents; regenerate provenance metadata and audit artifacts as appropriate.
9. Keep the neural/research path distinct from qualified operational paths until it passes the same evidence gates.

For changes that affect output semantics or promotion, update the contracts before treating old artifacts as current. Generated artifacts may become stale when source data, feature builders, or model contracts change; their metadata and hashes should be inspected before reuse.
