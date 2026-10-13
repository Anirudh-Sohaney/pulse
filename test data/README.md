# Synthetic dashboard upload files

Upload `arkansas_clinic_daily_pharmacy_sales.csv` as **Past sales** and
`synthetic_inventory_history.csv` as **Inventory history**. Both cover the same
30 drugs and daily dates from 2023-01-01 through 2025-12-31.

`demand_inventory_snapshot.csv` is the original final-day inventory scenario
from `website/data/`. It contains only one day and cannot be uploaded as the
dashboard's inventory-history file.

The daily inventory file is **simulated**, not an observed inventory ledger.
`generate_inventory_history.py` constructs it backward from the original
2025-12-31 stock snapshot and synthetic daily sales. Whenever the reconstructed
previous-day balance would exceed the snapshot's target stock, it assumes a
delivery and sets that previous balance to the snapshot's reorder point. This
keeps the final balances identical to the original snapshot while supplying
daily training coverage. The assumed earlier balances and replenishments must
not be treated as real pharmacy activity or used for purchasing decisions.

The uploaded sales CSV ends in 2025. The dashboard should therefore label its
result as a historical scenario rather than a current order recommendation.
