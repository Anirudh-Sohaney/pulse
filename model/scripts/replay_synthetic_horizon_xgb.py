"""Replay a saved short-horizon booster on a synthetic held-out date."""

from __future__ import annotations

import argparse
import json

from arkansas_pharma_signal.synthetic_horizon_xgb import replay_saved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("drug_name")
    parser.add_argument("horizon_days", type=int, choices=(1, 7, 14, 30))
    parser.add_argument("as_of", help="YYYY-MM-DD in the saved test period")
    args = parser.parse_args()
    print(json.dumps(replay_saved(args.drug_name, args.horizon_days, args.as_of), indent=2))


if __name__ == "__main__":
    main()
