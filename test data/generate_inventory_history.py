"""Build a clearly synthetic inventory ledger for the dashboard example.

The source contains only a final stock snapshot. Earlier daily balances are
constructed backward from that snapshot and the synthetic sales series, with
assumed replenishments whenever a prior balance would exceed target stock.
They are demonstration inputs, not observed inventory transactions.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path


HERE = Path(__file__).resolve().parent
SALES = HERE / "arkansas_clinic_daily_pharmacy_sales.csv"
SNAPSHOT = HERE / "demand_inventory_snapshot.csv"
OUTPUT = HERE / "synthetic_inventory_history.csv"


def read_sales() -> dict[str, list[tuple[date, int]]]:
    rows: dict[str, list[tuple[date, int]]] = defaultdict(list)
    with SALES.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            units = int(row["units_sold"])
            if units < 0:
                raise ValueError("Sales units must be nonnegative")
            rows[row["drug_name"]].append((date.fromisoformat(row["date"]), units))
    for drug, history in rows.items():
        history.sort()
        if len(history) != len(set(day for day, _ in history)):
            raise ValueError(f"Duplicate sales dates for {drug}")
        if any(later[0] - earlier[0] != timedelta(days=1)
               for earlier, later in zip(history, history[1:])):
            raise ValueError(f"Sales dates are not consecutive for {drug}")
    return rows


def read_snapshot() -> dict[str, dict[str, str]]:
    with SNAPSHOT.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    snapshot = {row["drug_name"]: row for row in rows}
    if len(snapshot) != len(rows):
        raise ValueError("Duplicate drugs in inventory snapshot")
    return snapshot


def main() -> None:
    sales = read_sales()
    snapshot = read_snapshot()
    if set(sales) != set(snapshot):
        raise ValueError("Sales and inventory drugs differ")

    history_rows: list[dict[str, str | int]] = []
    for drug in sorted(sales):
        daily_sales = sales[drug]
        final = snapshot[drug]
        if daily_sales[-1][0] != date.fromisoformat(final["as_of_date"]):
            raise ValueError(f"Last sales and inventory dates differ for {drug}")
        balance = int(final["on_hand_units"])
        reorder = int(final["reorder_point_units"])
        target = int(final["target_stock_units"])
        if min(balance, reorder, target) < 0 or reorder > target:
            raise ValueError(f"Invalid stock levels for {drug}")

        for day, sold in reversed(daily_sales):
            history_rows.append({
                "date": day.isoformat(),
                "drug_name": drug,
                "on_hand_units": balance,
                "on_order_units": 0,
            })
            previous_balance_without_delivery = balance + sold
            balance = (reorder if previous_balance_without_delivery > target
                       else previous_balance_without_delivery)

    history_rows.sort(key=lambda row: (row["date"], row["drug_name"]))
    with OUTPUT.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "date", "drug_name", "on_hand_units", "on_order_units",
        ], lineterminator="\n")
        writer.writeheader()
        writer.writerows(history_rows)
    print(f"Wrote {len(history_rows):,} synthetic daily inventory rows to {OUTPUT}")


if __name__ == "__main__":
    main()
