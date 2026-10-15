"""Export retained public Arkansas SDUD releases for the research evaluator.

Only the SDUD source tables are read. The output uses the official CSV column
contract accepted by ``fetch_medicaid_sdud_csv``; no account data is exported.
"""

from __future__ import annotations

import csv
import json
import sys

from .signal_store import connect, initialize


HEADER = ("State Code", "NDC", "Year", "Quarter", "Suppression Used",
          "Product FDA List Name", "No. of Prescriptions")


def export(out=sys.stdout, manifest_out=sys.stderr) -> None:
    initialize()
    with connect() as db:
        snapshots = db.execute("""SELECT * FROM sdud_snapshots s
            WHERE s.content_hash = (
                SELECT s2.content_hash FROM sdud_snapshots s2
                WHERE s2.source_year=s.source_year
                ORDER BY s2.source_modified_at DESC, s2.retrieved_at DESC
                LIMIT 1)
            ORDER BY s.source_year""").fetchall()
        if not snapshots:
            raise ValueError("no Arkansas SDUD snapshots are retained")
        years = [int(row["source_year"]) for row in snapshots]
        if years != list(range(min(years), max(years) + 1)):
            raise ValueError("retained SDUD annual releases are not continuous")
        writer = csv.writer(out)
        writer.writerow(HEADER)
        for snapshot in snapshots:
            rows = db.execute("""SELECT year, quarter, ndc, product_name,
                prescriptions, suppressed FROM sdud_rows WHERE content_hash=?
                ORDER BY year, quarter, ndc, utilization_type""",
                (snapshot["content_hash"],))
            seen = 0
            for row in rows:
                writer.writerow(("AR", row["ndc"], row["year"], row["quarter"],
                                 "true" if row["suppressed"] else "false",
                                 row["product_name"],
                                 "" if row["suppressed"] else row["prescriptions"]))
                seen += 1
            if seen != snapshot["row_count"]:
                raise ValueError(f"SDUD snapshot {snapshot['content_hash']} is incomplete")
        json.dump({"source": "Arkansas Medicaid SDUD retained releases",
                   "releases": [{"year": row["source_year"],
                                 "content_hash": row["content_hash"],
                                 "source_url": row["source_url"],
                                 "source_modified_at": row["source_modified_at"],
                                 "retrieved_at": row["retrieved_at"],
                                 "row_count": row["row_count"]}
                                for row in snapshots]}, manifest_out, indent=2)
        manifest_out.write("\n")


if __name__ == "__main__":
    export()
