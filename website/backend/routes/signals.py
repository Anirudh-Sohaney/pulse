"""Public, versioned access to observed and model-derived signal values."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .. import signal_store
from .. import short_horizon_research

router = APIRouter()
SignalId = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class LatestQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[SignalId] = Field(min_length=1, max_length=1500)


class SignalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[SignalId] = Field(min_length=1, max_length=100)
    observation_date: date | None = Field(default=None, alias="date")
    start_date: date | None = None
    end_date: date | None = None
    include_revisions: bool = False


def _validate_dates(query: SignalQuery) -> tuple[date | None, date | None]:
    if query.observation_date and (query.start_date or query.end_date):
        raise HTTPException(422, detail="Use date or start_date/end_date, not both")
    start = query.observation_date or query.start_date
    end = query.observation_date or query.end_date
    if start and end and start > end:
        raise HTTPException(422, detail="start_date must not exceed end_date")
    return start, end


@router.get("/catalog")
def catalog(search: str = Query("", max_length=120),
            origin: str = Query("", max_length=64),
            limit: int = Query(1500, ge=1, le=1500),
            offset: int = Query(0, ge=0)):
    """List stable signal IDs, identity, units, source, and last observation."""
    normalized_search = search.strip()
    normalized_origin = origin.strip()
    rows = signal_store.list_signals(search=normalized_search, origin=normalized_origin,
                                     limit=limit, offset=offset)
    total = signal_store.count_signals(search=normalized_search, origin=normalized_origin)
    return {"signals": rows, "count": len(rows), "filtered_total": total,
            "offset": offset,
            "note": "State values are ordinal categories, not medication units or purchase quantities."}


@router.get("/catalog/seed")
def seed_catalog():
    """Return exactly the 1,250 stable definitions in the checked-in seed manifest."""
    rows = signal_store.seed_definitions()
    return {"signals": rows, "count": len(rows),
            "source": "checked_in_seed_manifest",
            "note": "Definitions only; retrieve dated observations with /latest or /history."}


@router.post("/latest")
def latest(query: LatestQuery):
    """Return usable values and the absolute latest recorded row for each ID."""
    rows = signal_store.values(query.ids, latest_only=True)
    recorded = signal_store.values(query.ids, latest_only=True, include_unusable=True)
    found = {row["id"] for row in rows}
    missing_ids = [uid for uid in query.ids if uid not in found]
    archived = [row for row in recorded if row["id"] in missing_ids and not row["usable"]]
    conflicting_ids = {row["id"] for row in recorded
                       if row["unusable_reason"] == "conflicting_latest_revision"}
    coverage = ({row["id"]: row for row in signal_store.gap_report()["signals"]}
                if missing_ids else {})
    missing_details = [{"id": uid,
                        "reason": ("conflicting_latest_revision" if uid in conflicting_ids else
                                   coverage[uid]["gap_status"] if uid in coverage else "unknown_id"),
                        "last_recorded_period": (coverage[uid]["latest_observation_date"]
                                     if uid in coverage else None)}
                       for uid in missing_ids]
    return {"values": rows, "latest_recorded_values": recorded,
            "unusable_recorded_values": archived,
            "missing_ids": missing_ids,
            "missing_details": missing_details,
            "ambiguous_ids": sorted({row["id"] for row in [*rows, *recorded]
                                     if row["ambiguous"]})}


@router.get("/today")
def today(limit: int = Query(1500, ge=1, le=1500), offset: int = Query(0, ge=0)):
    """Return a point-in-time snapshot of the latest usable value per signal."""
    definitions = signal_store.list_signals(limit=limit, offset=offset)
    ids = [row["id"] for row in definitions]
    values = signal_store.values(ids, latest_only=True) if ids else []
    recorded = signal_store.values(ids, latest_only=True, include_unusable=True) if ids else []
    return {"as_of": datetime.now(timezone.utc).isoformat(),
            "snapshot_date": date.today().isoformat(),
            "values": values, "count": len(values),
            "latest_recorded_values": recorded,
            "unusable_recorded_values": [row for row in recorded if not row["usable"]],
            "recorded_count": len(recorded), "catalog_count": signal_store.count_signals(),
            "offset": offset,
            "note": "values contains the latest usable stored values. latest_recorded_values also includes recorded shadow and archived rows, identified by usable=false. Values are not necessarily observations published today; each row retains its source observation date and source timestamp, and missing values are not imputed."}


@router.get("/demand/outlook")
def demand_outlook(run_date: date | None = Query(None, alias="date"),
                   days: int = Query(1, ge=1, le=90)):
    """Retired: public synthetic forecasts cannot use frozen private-sales features."""
    raise HTTPException(410, detail=(
        "The public daily outlook was retired because its pharmacy-sales inputs were "
        "frozen synthetic history. Use /demand/horizon-study for benchmark results "
        "or train on current private sales in the browser workspace."))


@router.get("/demand/horizon-study")
def demand_horizon_study(drug_name: str | None = None):
    """Return the local synthetic 1/7/14/30-day benchmark, not live forecasts."""
    if drug_name is not None and len(drug_name) > 120:
        raise HTTPException(422, detail="drug_name is too long")
    return short_horizon_research.study(drug_name)


@router.post("/history")
def history(query: SignalQuery):
    """Return exact recorded observations on one date or in a date range."""
    start, end = _validate_dates(query)
    try:
        rows = signal_store.values(query.ids, start=start, end=end,
                                   include_revisions=query.include_revisions)
    except signal_store.HistoryTooLarge as exc:
        raise HTTPException(413, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return {"values": rows, "count": len(rows), "start_date": start,
            "end_date": end, "note": "Dates without a recorded observation are absent, not zero. Multiple conflicting values for one date are marked ambiguous."}


@router.get("/freshness")
def freshness():
    """Expose data coverage so callers can detect stale or missing feeds."""
    return signal_store.freshness()


@router.get("/coverage")
def coverage():
    return signal_store.gap_report()


@router.get("/coverage/summary")
def coverage_summary():
    """Return usable-value coverage for currently selected seeded outputs."""
    return signal_store.seed_coverage_summary()


@router.get("/demand/drugs")
def demand_drugs(search: str = Query("", max_length=120), limit: int = Query(100, ge=1, le=250),
                 offset: int = Query(0, ge=0)):
    return signal_store.demand_drugs(search=search.strip(), limit=limit, offset=offset)


@router.get("/news/recent")
def recent_news(days: int = Query(3, ge=1, le=7)):
    rows = signal_store.recent_news(days=days)
    source_checks = signal_store.news_source_checks()
    return {"articles": rows, "count": len(rows), "window_days": days,
            "source_checks": source_checks,
            "note": "Keyword relevance is context only; news is not model attribution or proof of demand."}


@router.get("/recent")
def recent_public_signals(days: int = Query(3, ge=1, le=7),
                          limit: int = Query(12, ge=1, le=20)):
    rows = signal_store.recent_public_signals(days=days, limit=limit)
    return {"signals": rows, "count": len(rows), "window_days": days,
            "note": "These are recently captured source observations and transparent source-derived surveillance measures with unresolved same-revision conflicts omitted. Their observation periods may be older than the recording date, and a recent capture does not prove a recent upstream release. They are context, not drug-demand forecasts. Wastewater values are unweighted reporting-site means, not official geographic medians; inspect their contributing-site counts."}


@router.get("/sources/shortages/recent")
def recent_shortages(days: int = Query(3, ge=1, le=7)):
    return signal_store.recent_shortage_changes(days=days)


@router.get("/sources/nadac/latest")
def latest_nadac():
    return signal_store.latest_nadac_snapshot()


@router.get("/sources/sdud/latest")
def latest_sdud(search: str = Query("", max_length=120), limit: int = Query(100, ge=1, le=250),
                offset: int = Query(0, ge=0)):
    """Observed Arkansas Medicaid prescriptions; suppressed cells remain unknown."""
    return signal_store.latest_sdud_snapshot(search=search.strip(), limit=limit, offset=offset)
