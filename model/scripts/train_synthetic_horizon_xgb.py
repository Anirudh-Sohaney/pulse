"""Train and export the paired synthetic per-drug horizon comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from arkansas_pharma_signal.synthetic_horizon_xgb import OUTPUT, train


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    result = train(args.output)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
