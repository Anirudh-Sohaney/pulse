"""Per-user XGBoost training executed by Pyodide inside a dedicated web worker.

Input CSV bytes never leave the browser. Public signal rows are accepted only
when their recorded source timestamp preceded the feature date. Historical
rows with unknown source availability are excluded from training.
"""

import io
import json
import math
from datetime import timedelta

import numpy as np
import pandas as pd
from xgboost import XGBRegressor


HORIZONS = (1, 7, 14, 30)


def _calendar_dates(values, label, *, optional=False):
    """Accept one unambiguous calendar-day format for private CSV inputs."""
    raw = values.astype("string").str.strip()
    present = raw.notna() & raw.ne("")
    valid_format = raw.str.fullmatch(r"\d{4}-\d{2}-\d{2}").fillna(False)
    if ((present & ~valid_format) | (~present if not optional else False)).any():
        raise ValueError(f"{label} must use YYYY-MM-DD calendar dates")
    parsed = pd.to_datetime(raw.where(present), format="%Y-%m-%d", errors="coerce")
    if (present & parsed.isna()).any():
        raise ValueError(f"{label} contains an invalid calendar date")
    return parsed


def _read_sales(csv_text, *, today=None):
    frame = pd.read_csv(io.StringIO(csv_text))
    required = {"date", "drug_name", "units_sold"}
    if not required.issubset(frame):
        raise ValueError("Sales CSV requires date, drug_name, units_sold")
    frame["date"] = _calendar_dates(frame["date"], "Sales date")
    if frame["drug_name"].isna().any():
        raise ValueError("Sales drug names must be present")
    frame["drug_name"] = frame["drug_name"].astype(str).str.strip()
    frame["units_sold"] = pd.to_numeric(frame["units_sold"], errors="coerce")
    if frame[["date", "drug_name", "units_sold"]].isna().any().any():
        raise ValueError("Sales dates, names, and quantities must be valid")
    current_day = pd.Timestamp(today).normalize() if today else pd.Timestamp.now().normalize()
    if frame["date"].max() > current_day:
        raise ValueError("Sales CSV contains a date after today")
    if frame["drug_name"].eq("").any() or frame["units_sold"].lt(0).any() or not np.isfinite(frame["units_sold"]).all():
        raise ValueError("Sales names must be present and quantities nonnegative")
    if "unit_price_usd" in frame:
        frame["unit_price_usd"] = pd.to_numeric(frame["unit_price_usd"], errors="coerce")
        if frame["unit_price_usd"].isna().any() or not np.isfinite(frame["unit_price_usd"]).all() or frame["unit_price_usd"].lt(0).any():
            raise ValueError("Sales unit_price_usd must be a finite nonnegative number in every row")
    if "stockout_flag" in frame:
        frame["stockout_flag"] = pd.to_numeric(frame["stockout_flag"], errors="coerce")
        if frame["stockout_flag"].isna().any() or not frame["stockout_flag"].isin([0, 1]).all():
            raise ValueError("Sales stockout_flag must be 0 or 1 in every row")
    if frame.duplicated(["drug_name", "date"]).any():
        raise ValueError("Sales CSV has duplicate drug/date rows")
    frame = frame.sort_values(["drug_name", "date"])
    for drug, part in frame.groupby("drug_name"):
        if len(part) < 180 or len(pd.date_range(part.date.min(), part.date.max())) != len(part):
            raise ValueError(f"{drug} needs at least 180 consecutive daily rows")
    return frame


def _read_inventory(csv_text, sales, *, today=None):
    frame = pd.read_csv(io.StringIO(csv_text))
    if not {"drug_name", "on_hand_units"}.issubset(frame):
        raise ValueError("Inventory CSV requires drug_name and on_hand_units")
    date_col = "as_of_date" if "as_of_date" in frame else "date" if "date" in frame else None
    if date_col is None:
        raise ValueError("Inventory CSV requires date or as_of_date")
    frame["date"] = _calendar_dates(frame[date_col], "Inventory date")
    if frame["drug_name"].isna().any():
        raise ValueError("Inventory drug names must be present")
    frame["drug_name"] = frame["drug_name"].astype(str).str.strip()
    frame["on_hand_units"] = pd.to_numeric(frame["on_hand_units"], errors="coerce")
    frame["on_order_units"] = pd.to_numeric(frame["on_order_units"], errors="coerce") if "on_order_units" in frame else 0.0
    if "expected_arrival_date" in frame:
        frame["expected_arrival_date"] = _calendar_dates(
            frame["expected_arrival_date"], "Expected arrival date", optional=True)
    else:
        frame["expected_arrival_date"] = pd.NaT
    if frame[["date", "drug_name", "on_hand_units", "on_order_units"]].isna().any().any():
        raise ValueError("Inventory dates and quantities must be valid")
    current_day = pd.Timestamp(today).normalize() if today else pd.Timestamp.now().normalize()
    if frame["date"].max() > current_day:
        raise ValueError("Inventory CSV contains a date after today")
    if frame["drug_name"].eq("").any() or not np.isfinite(frame[["on_hand_units", "on_order_units"]]).all().all():
        raise ValueError("Inventory names and quantities must be finite")
    if frame[["on_hand_units", "on_order_units"]].lt(0).any().any():
        raise ValueError("Inventory quantities must be nonnegative")
    if frame.duplicated(["drug_name", "date"]).any():
        raise ValueError("Inventory CSV has duplicate drug/date rows")
    latest = frame.sort_values("date").groupby("drug_name", as_index=False).tail(1)
    if set(latest.drug_name) != set(sales.drug_name):
        raise ValueError("Inventory and sales must contain the same drugs")
    sales_end = sales.groupby("drug_name").date.max()
    for row in latest.itertuples():
        if row.date != sales_end[row.drug_name]:
            raise ValueError(
                f"{row.drug_name} latest inventory date must match its last sales date "
                "so the plan starts from the same stock snapshot")
    for drug, sales_part in sales.groupby("drug_name"):
        inventory_part = frame[frame.drug_name.eq(drug)]
        aligned = pd.merge_asof(
            sales_part[["date"]].sort_values("date"),
            inventory_part[["date", "on_hand_units"]].sort_values("date"),
            on="date", direction="backward", tolerance=pd.Timedelta(days=1))
        if aligned.on_hand_units.notna().sum() < 180:
            raise ValueError(
                f"{drug} needs inventory snapshots covering at least 180 sales days "
                "(same day or previous day) to train with inventory history")
    return frame, latest


def _features(part, horizon=14):
    work = part.copy().sort_values("date").reset_index(drop=True)
    sales = work.units_sold.astype(float)
    for lag in (1, 2, 3, 7, 14, 21, 28, 56):
        work[f"lag_{lag}"] = sales.shift(lag)
    for window in (3, 7, 14, 28, 56):
        work[f"mean_{window}"] = sales.shift(1).rolling(window).mean()
        work[f"std_{window}"] = sales.shift(1).rolling(window).std()
    work["dow"] = work.date.dt.dayofweek
    work["month"] = work.date.dt.month
    work["weekofyear"] = work.date.dt.isocalendar().week.astype(int)
    day = work.date.dt.dayofyear
    work["sin_year"] = np.sin(2 * np.pi * day / 365.25)
    work["cos_year"] = np.cos(2 * np.pi * day / 365.25)
    price = work["unit_price_usd"] if "unit_price_usd" in work else pd.Series(0.0, index=work.index)
    stockout = work["stockout_flag"] if "stockout_flag" in work else pd.Series(0.0, index=work.index)
    work["price_lag1"] = price.shift(1)
    work["stockout_lag1"] = stockout.shift(1)
    work["inventory_lag1"] = work.on_hand_units.shift(1)
    work["target"] = sales.shift(-1).rolling(horizon, min_periods=horizon).sum().shift(1 - horizon)
    # Sales during a stockout are censored observations of demand. Exclude
    # every target whose following horizon includes a marked stockout.
    work["target_stockout_days"] = stockout.shift(-1).rolling(horizon, min_periods=horizon).sum().shift(1 - horizon)
    columns = [name for name in work if name.startswith(("lag_", "mean_", "std_"))]
    columns += ["dow", "month", "weekofyear", "sin_year", "cos_year", "price_lag1", "stockout_lag1"]
    columns.append("inventory_lag1")
    return work, columns


def _new_model():
    return XGBRegressor(n_estimators=220, max_depth=2, learning_rate=0.04,
                        min_child_weight=8, subsample=0.85, colsample_bytree=0.5,
                        reg_lambda=10, objective="reg:squarederror", tree_method="hist",
                        n_jobs=1, random_state=20250915)


def _signal_series(rows, dates):
    """Use the latest period and revision known on each feature day."""
    by_id = {}
    for row in rows:
        if (row.get("ambiguous") or row.get("usable") is False or
                row.get("source_kind") == "article_text_ridge_shadow_v1" or
                not row.get("source_timestamp")):
            continue
        source_time = pd.to_datetime(row["source_timestamp"], errors="coerce", utc=True)
        period_end = pd.to_datetime(row["observation_date"], errors="coerce", utc=True)
        if pd.isna(source_time) or pd.isna(period_end):
            continue
        try:
            value = float(row["value"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        source_time = source_time.tz_convert(None)
        period_end = period_end.tz_convert(None).normalize()
        available = max(source_time.normalize(), period_end)
        if row.get("forecast_horizon") and period_end > source_time.normalize():
            # A live model estimate targets a future observation period but is
            # available to a forecast as soon as its source timestamp exists.
            available = source_time.normalize()
        by_id.setdefault(row["id"], []).append((available, period_end, source_time, value))
    output = {}
    for uid, events in by_id.items():
        events.sort()
        latest_by_period = {}
        position = 0
        values = []
        for day in dates:
            while position < len(events) and events[position][0] <= day:
                _, period, source_time, value = events[position]
                previous = latest_by_period.get(period)
                if previous is None or source_time >= previous[0]:
                    latest_by_period[period] = (source_time, value)
                position += 1
            values.append(latest_by_period[max(latest_by_period)][1]
                          if latest_by_period else np.nan)
        aligned = pd.Series(values)
        if aligned.notna().sum() >= 30 and aligned.nunique() > 1:
            output[uid] = aligned.to_numpy()
    return output


def _plan_quantities(direct, weights, trailing, latest, dates):
    """Credit inbound stock only after its dated arrival, never immediately."""
    weights = np.asarray(weights, dtype=float)
    anchors = {0: 0.0}
    for horizon in HORIZONS:
        anchors[horizon] = max(anchors[max(anchors)], float(direct[horizon]), 0.0)
    daily = np.zeros(14)
    for start, end in ((0, 1), (1, 7), (7, 14)):
        segment = np.maximum(weights[start:end], 0)
        if segment.sum() <= 0:
            segment = np.ones(end - start)
        daily[start:end] = (anchors[end] - anchors[start]) * segment / segment.sum()
    cumulative = np.cumsum(daily)
    on_hand = float(latest.on_hand_units)
    on_order = float(latest.on_order_units)
    arrival = latest.expected_arrival_date
    overdue = bool(on_order > 0 and pd.notna(arrival) and arrival.date() < latest.date.date())
    inbound = np.asarray([on_order if pd.notna(arrival) and not overdue and day.date() > arrival.date() else 0.0
                          for day in dates])
    deficits = np.maximum(cumulative - on_hand - inbound, 0)
    minimums = [max(0, math.ceil(float(deficits[:horizon].max()) - 1e-9))
                for horizon in (1, 7, 14)]
    safety = math.ceil(1.65 * float(trailing.std(ddof=0)) * math.sqrt(7))
    target_stock = math.ceil(max(anchors[14], float(trailing.mean()) * 14) + safety)
    recommended = max(minimums[-1], math.ceil(target_stock - on_hand - inbound[-1]), 0)
    depleted = np.flatnonzero(deficits > 1e-9)
    return {"on_hand_units": on_hand, "on_order_units": on_order,
            "expected_arrival_date": arrival.date().isoformat() if pd.notna(arrival) else None,
            "incoming_eta_overdue": overdue,
            "incoming_counted_by_day_14": bool(inbound[-1] > 0),
            "forecast_1d_units": round(anchors[1], 2),
            "forecast_7d_units": round(anchors[7], 2),
            "forecast_14d_units": round(anchors[14], 2),
            "forecast_30d_units": round(anchors[30], 2),
            "days_until_stockout": int(depleted[0] + 1) if len(depleted) else None,
            "minimum_buy_1d_units": minimums[0],
            "minimum_buy_7d_units": minimums[1],
            "minimum_buy_14d_units": minimums[2],
            "recommended_order_units": recommended,
            "inventory_status": "reorder_now" if minimums[1] > 0 else "reorder_soon" if recommended > 0 else "covered"}


def _fit_horizon(part, horizon, signals):
    work, base_columns = _features(part, horizon)
    usable = work.dropna(subset=base_columns + ["target"])
    usable = usable[usable.target_stockout_days.eq(0)].copy()
    if len(usable) < 60:
        raise ValueError(f"{part.drug_name.iloc[0]} has too few uncensored {horizon}-day targets")
    cutoff = usable.date.iloc[int(len(usable) * .8)]
    train_mask = usable.date < cutoff - timedelta(days=horizon)
    holdout_mask = usable.date >= cutoff
    if train_mask.sum() < 30 or holdout_mask.sum() < 10:
        raise ValueError(f"{part.drug_name.iloc[0]} needs more history for a purged {horizon}-day validation split")
    scored = []
    for uid, values in signals.items():
        lagged = pd.Series(values).shift(1).fillna(0)
        x = lagged.loc[usable.index].to_numpy(float)
        if np.std(x[train_mask.to_numpy()]) <= 1e-12:
            continue
        correlation = np.corrcoef(x[train_mask.to_numpy()], usable.loc[train_mask, "target"])[0, 1]
        if np.isfinite(correlation):
            scored.append((abs(float(correlation)), uid, values, x[train_mask.to_numpy()].tobytes()))
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = []
    vectors = set()
    for _, uid, values, vector in scored:
        if vector in vectors:
            continue
        vectors.add(vector)
        selected.append((uid, values))
        if len(selected) == 5:
            break
    columns = base_columns.copy()
    if len(selected) == 5:
        for index, (_, values) in enumerate(selected):
            for lag in (1, 7, 14):
                name = f"signal_{index}_lag_{lag}"
                work[name] = pd.Series(values).shift(lag).fillna(0)
                columns.append(name)
    else:
        selected = []
    usable = work.loc[usable.index]
    train_rows = usable.loc[train_mask]
    holdout = usable.loc[holdout_mask]
    actual = holdout.target.to_numpy(float)
    denominator = max(actual.sum(), 1e-9)
    sales_model = _new_model().fit(train_rows[base_columns], train_rows.target)
    sales_wape = float(np.abs(actual - np.maximum(sales_model.predict(holdout[base_columns]), 0)).sum() / denominator)
    chosen_columns = base_columns
    chosen_wape = sales_wape
    if selected:
        signal_model = _new_model().fit(train_rows[columns], train_rows.target)
        signal_wape = float(np.abs(actual - np.maximum(signal_model.predict(holdout[columns]), 0)).sum() / denominator)
        if signal_wape + 1e-6 < sales_wape:
            chosen_columns = columns
            chosen_wape = signal_wape
        else:
            selected = []
    final = _new_model().fit(usable[chosen_columns], usable.target)
    forecast = max(0.0, float(final.predict(work.iloc[[-1]][chosen_columns])[0]))
    return forecast, {"wape": chosen_wape, "private_only_wape": sales_wape,
                      "signals_used": [uid for uid, _ in selected]}


def train(payload_json):
    payload = json.loads(payload_json)
    today = payload.get("local_today")
    sales = _read_sales(payload["sales_csv"], today=today)
    inventory_history, inventory = _read_inventory(payload["inventory_csv"], sales, today=today)
    signal_rows = payload.get("signal_rows") or []
    signal_years = int(payload.get("signal_years") or 0)
    if signal_years not in (0, 1, 2, 3):
        raise ValueError("Choose zero, one, two, or three years of signal history")
    results = []
    plans = []
    for drug, part in sales.groupby("drug_name", sort=True):
        part = part.copy()
        inventory_part = inventory_history[inventory_history.drug_name.eq(drug)]
        part = pd.merge_asof(part.sort_values("date"),
                             inventory_part[["date", "on_hand_units"]].sort_values("date"),
                             on="date", direction="backward", tolerance=pd.Timedelta(days=1))
        signals = {}
        if signal_years:
            earliest = part.date.max() - pd.DateOffset(years=signal_years)
            eligible = [row for row in signal_rows if pd.to_datetime(row["observation_date"]) >= earliest]
            signals = _signal_series(eligible, part.date)
        direct = {}
        by_horizon = {}
        for horizon in HORIZONS:
            direct[horizon], by_horizon[str(horizon)] = _fit_horizon(part, horizon, signals)
        recent = part.tail(56)
        weekday_means = recent.groupby(recent.date.dt.dayofweek).units_sold.mean()
        dates = [part.date.max() + timedelta(days=i) for i in range(1, 15)]
        weights = np.asarray([max(0.0, float(weekday_means.get(day.dayofweek, 0))) for day in dates])
        latest = inventory[inventory.drug_name.eq(drug)].iloc[0]
        trailing = recent.tail(28).units_sold.astype(float)
        plans.append({"drug_name": drug, "as_of_date": latest.date.date().isoformat(),
                      **_plan_quantities(direct, weights, trailing, latest, dates)})
        results.append({"drug_name": drug, "by_horizon": by_horizon,
                        "wape": by_horizon["14"]["wape"],
                        "private_only_wape": by_horizon["14"]["private_only_wape"],
                        "signals_used": by_horizon["14"]["signals_used"]})
    plans.sort(key=lambda row: (-row["recommended_order_units"], row["drug_name"]))
    return json.dumps({"items": plans, "per_drug": results,
                       "history_start": sales.date.min().date().isoformat(),
                       "history_end": sales.date.max().date().isoformat(),
                       "signal_years": signal_years, "model": "browser_xgboost_direct_multihorizon_v7",
                       "note": "Private files stayed in this browser. Separate 1-, 7-, 14-, and 30-day unit totals were trained. Targets containing marked stockouts were excluded. Public signals with unknown or later retrieval timestamps were excluded, and exactly five distinct eligible signals were considered per drug and horizon. Signals were used only when they improved purged holdout error. Inconsistent cumulative horizons were raised to the preceding horizon for planning; daily amounts within the longer windows are allocations. Incoming stock was credited only after its dated arrival."})


if "payload" in globals():
    result = train(payload)
