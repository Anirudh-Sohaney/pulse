"""Read-only publication of the synthetic short-horizon benchmark."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


STUDY = Path(__file__).resolve().parent / "model_artifacts" / "horizon_study_v1.json"


def study(drug_name: str | None = None) -> dict[str, Any]:
    result = json.loads(STUDY.read_text(encoding="utf-8"))
    rows = result["drugs"]
    if drug_name is not None:
        rows = [row for row in rows if row["drug_name"].casefold() == drug_name.casefold()]
    return {**result, "drug_count": len({row["drug_name"] for row in rows}),
            "drugs": rows}
