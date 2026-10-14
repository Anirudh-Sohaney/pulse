# Archived monthly ATC signal claim

An earlier project report claimed that 18 Arkansas therapeutic-class demand
heads exceeded 75% exact accuracy on a three-state monthly holdout. This is a
historical claim, **not a current completion or deployment result**. The
report's cited evaluator (`model/scripts/goal_evaluate_atc_news.py`) and metric
file (`model/artifacts/evaluation/goal_atc_news_signals.json`) are absent from
this checkout. The original 20-signal FLAN-T5 inference runner and checkpoint
are also absent. Those results cannot be reproduced or promoted from the
available files.

The archived design combined 20 monthly news-model outputs with past Arkansas
Medicaid provider/NDC claim-line counts, mapped to ATC therapeutic classes.
Its three states were defined by training-period demand terciles, with a
reported 2022–2024 holdout. The old report listed these classes: C10, C08,
N06, B01, G01, A10, D06, A02, A06, V03, C02, M03, D01, L01, C03, N07,
D10, and V04. These identifiers describe the claimed research heads; they are
not 18 usable production outputs.

`model/prod_pipeline.py` retains part of the historical training design but
its `predict()` method deliberately fails closed. The checked-in
`model/final_predictions.json` is an archived example, not fresh inference.
The website's article-to-20 replacement has only ten matched training months
and remains a shadow estimate excluded from usable latest values. The live
drug surface uses a separately evaluated CMS Part D consistency selection.

To qualify monthly ATC heads for deployment, the missing evaluator must be
reconstructed with dated source inputs, frozen state thresholds, chronological
holdouts, majority and persistence baselines, and an independently observed
target. The 20 article estimates also need a validated source-to-output
runner before they can act as operational features. See
[`NEWS_ONLY_INTEGRATION.md`](NEWS_ONLY_INTEGRATION.md) and
[`PART_A_STATUS.md`](PART_A_STATUS.md) for the current evidence and limits.
