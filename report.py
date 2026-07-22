"""Generate a self-contained renter-facing market report from CSV snapshots."""

from __future__ import annotations

import csv
import html
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Iterable


UnitKey = tuple[str, str, str]

EXPOSURE_POINTS = {"SE": 17, "SW": 11, "NE": 8, "NW": 1, "unknown": 7}
SUNLIGHT_POINTS = {"good": 10, "mixed": 6, "low": 0, "unknown": 4}
FACADE_POINTS = {"internal": 12, "external": 0, "unknown": 5}
VIEW_POINTS = {
    "skyline_open": 9,
    "pool_skyline_partial": 9,
    "skyline_partial": 7,
    "pool_courtyard": 7,
    "courtyard": 5,
    "open": 4,
    "none": 1,
    "unknown": 3,
}
FLOOR_BAND_POINTS = {"low": 0, "mid": 3, "mid_high": 7, "upper": 9, "unknown": 4}
DISTURBANCE_POINTS = {"low": 8, "medium": 6, "high": 2, "unknown": 5}
LAYOUT_PENALTIES = {"preferred": 0, "acceptable": 4, "penalized": 15, "unknown": 0}
STALE_THRESHOLD_HOURS = 36


def read_rows(filename: Path) -> list[dict[str, str]]:
    if not filename.exists():
        return []
    with filename.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def price_to_cents(price: str) -> int:
    return int(round(float(price.replace("$", "").replace(",", "")) * 100))


def format_cents(cents: int | None, include_sign: bool = False) -> str:
    if cents is None:
        return "—"
    sign = "+" if include_sign and cents > 0 else "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.0f}"


def int_value(value: str, default: int = 0) -> int:
    try:
        return int(value.replace(",", ""))
    except (AttributeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def recommendation_label(score: int) -> tuple[str, str]:
    if score >= 82:
        return "Best match", "good"
    if score >= 70:
        return "Strong", "good"
    if score >= 58:
        return "Consider", "neutral"
    return "Low fit", "bad"


def unit_recommendation(
    unit: dict[str, str], peers: list[dict[str, str]], traits: dict[str, dict[str, str]],
) -> dict[str, object] | None:
    """Score one current listing without exposing it in the public report.

    Price contributes 35 points. Orientation, observed solar access, facade
    context, view, verified floor band, and disturbance/privacy contribute 65 points.
    Floor-plan efficiency can deduct up to 15 points without excluding a home.
    """
    trait = traits.get(unit["unit_id"])
    if not trait:
        return None
    peer_prices = [price_to_cents(row["price"]) for row in peers]
    baseline = int(median(peer_prices))
    rent = price_to_cents(unit["price"])
    percent_above = (rent - baseline) / baseline * 100 if baseline else 0
    price_points = round(clamp(28 - percent_above * 1.4, 14, 35))
    exposure = trait.get("exposure", "unknown")
    sunlight = trait.get("sunlight", "unknown")
    facade = trait.get("facade", "unknown")
    view = trait.get("view", "unknown")
    floor_band = trait.get("floor_band", "unknown")
    disturbance = trait.get("disturbance", "unknown")
    layout_fit = trait.get("layout_fit", "unknown")
    raw_confidence = trait.get("confidence", "unknown").casefold()
    confidence = (
        "verified" if "resident" in raw_confidence or raw_confidence == "verified"
        else "modeled" if raw_confidence not in {"", "unknown", "incomplete"}
        else "incomplete" if raw_confidence == "incomplete" else "unknown"
    )
    fit_points = (
        EXPOSURE_POINTS.get(exposure, EXPOSURE_POINTS["unknown"])
        + SUNLIGHT_POINTS.get(sunlight, SUNLIGHT_POINTS["unknown"])
        + FACADE_POINTS.get(facade, FACADE_POINTS["unknown"])
        + VIEW_POINTS.get(view, VIEW_POINTS["unknown"])
        + FLOOR_BAND_POINTS.get(floor_band, FLOOR_BAND_POINTS["unknown"])
        + DISTURBANCE_POINTS.get(disturbance, DISTURBANCE_POINTS["unknown"])
    )
    raw_score = int(price_points + fit_points)
    layout_penalty = LAYOUT_PENALTIES.get(layout_fit, 0)
    adjusted_score = max(0, raw_score - layout_penalty)
    if floor_band == "low":
        score, label, css_class = min(adjusted_score, 57), "Below floor minimum", "bad"
        floor_status = "below_minimum"
    elif floor_band == "mid":
        score = min(adjusted_score, 81)
        label, css_class = recommendation_label(score)
        floor_status = "acceptable"
    else:
        score = adjusted_score
        label, css_class = recommendation_label(score)
        floor_status = "preferred" if floor_band in {"mid_high", "upper"} else "unknown"
    if layout_fit in {"preferred", "acceptable", "penalized"}:
        layout_status = layout_fit
    else:
        layout_status = "unknown"
    reasons = []
    if layout_fit == "penalized":
        reasons.append("substantial circulation or unusable-space penalty")
    elif layout_fit == "preferred":
        reasons.append("regular, furniture-friendly floor plan")
    elif layout_fit == "acceptable":
        reasons.append("usable floor-plan shape")
    if exposure == "SE":
        reasons.append("preferred southeast exposure")
    elif exposure == "NW":
        reasons.append("northwest exposure")
    if floor_band == "low":
        reasons.append("below 5th-floor minimum")
    elif floor_band == "mid":
        reasons.append("5th–6th floor; below preferred 7+")
    elif floor_band in {"mid_high", "upper"}:
        reasons.append("floor 7+ preferred range")
    if sunlight == "good":
        reasons.append("good direct-light potential")
    elif sunlight == "low":
        reasons.append("mountain shade / little direct sun")
    if facade == "internal":
        reasons.append("preferred pool-facing interior")
    if view == "skyline_open":
        reasons.append("open skyline view")
    elif view in {"skyline_partial", "pool_skyline_partial"}:
        reasons.append("partial skyline view")
    elif view in {"courtyard", "pool_courtyard"}:
        reasons.append("pool/courtyard view")
    if disturbance == "high":
        reasons.append("amenity/noise exposure")
    if price_points >= 32:
        reasons.append("priced below layout peers")
    return {
        "score": score,
        "label": label,
        "class": css_class,
        "price_points": price_points,
        "fit_points": fit_points,
        "raw_score": raw_score,
        "layout_penalty": layout_penalty,
        "exposure": exposure,
        "facade": facade,
        "sunlight": sunlight,
        "floor_status": floor_status,
        "layout_status": layout_status,
        "reasons": reasons,
        "confidence": confidence,
    }


def plan_recommendation(
    units: list[dict[str, str]], traits: dict[str, dict[str, str]],
) -> dict[str, object]:
    """Aggregate private listing traits into a layout-first recommendation."""
    scored = [result for unit in units if (result := unit_recommendation(unit, units, traits))]
    if not scored:
        return {"score": None, "median_score": None, "label": "Not rated", "class": "neutral",
                "rated_count": 0, "unit_count": len(units), "preferred_count": 0,
                "pool_facing_count": 0, "eligible_floor_count": 0,
                "preferred_floor_count": 0, "low_sun_count": 0,
                "non_penalized_layout_count": 0,
                "confidence": "unknown", "trait_status": "not_evaluated",
                "reasons": ["Personal fit unavailable — no verified property traits"]}
    eligible = [result for result in scored if result["floor_status"] != "below_minimum"]
    best = max(eligible or scored, key=lambda result: int(result["score"]))
    scores = [int(result["score"]) for result in scored]
    return {
        **best,
        "median_score": int(median(scores)),
        "rated_count": len(scored),
        "unit_count": len(units),
        "preferred_count": sum(result["exposure"] == "SE" for result in scored),
        "pool_facing_count": sum(result["facade"] == "internal" for result in scored),
        "eligible_floor_count": sum(result["floor_status"] != "below_minimum" for result in scored),
        "preferred_floor_count": sum(result["floor_status"] == "preferred" for result in scored),
        "low_sun_count": sum(result["sunlight"] == "low" for result in scored),
        "non_penalized_layout_count": sum(
            result["layout_status"] != "penalized" for result in scored
        ),
        "trait_status": "evaluated",
    }


def market_recommendations(candidates: Iterable[dict[str, str]]) -> dict[str, dict[str, object]]:
    """Select market-only winners without consulting or imputing property traits."""
    rows = list(candidates)
    if not rows:
        return {}
    def identity(row: dict[str, str]) -> str:
        return row.get("floorplan_id") or row.get("floorplan") or "unknown"
    budget = min(rows, key=lambda row: price_to_cents(row["price"]))
    with_sqft = [row for row in rows if int_value(row.get("sqft", "")) > 0]
    value = min(
        with_sqft,
        key=lambda row: price_to_cents(row["price"]) / int_value(row["sqft"]),
    ) if with_sqft else budget
    timing = min(rows, key=lambda row: move_in_sort_key(row.get("move_in", "")))
    return {
        "best_budget": {"candidate": identity(budget), "confidence": "verified"},
        "best_value": {"candidate": identity(value),
                       "confidence": "verified" if with_sqft else "incomplete"},
        "best_timing": {"candidate": identity(timing), "confidence": "verified"},
    }


def trait_constraint_status(has_verified_traits: bool) -> dict[str, str]:
    return ({"status": "evaluated", "confidence": "verified"}
            if has_verified_traits else
            {"status": "Unknown / not evaluated", "confidence": "unknown"})


def move_in_sort_key(value: str) -> tuple[int, str]:
    if value.casefold() in {"immediate", "now"}:
        return (0, "")
    for pattern in ("%b %d", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return (1, datetime.strptime(value, pattern).strftime("%m%d"))
        except ValueError:
            pass
    return (2, value)


def unit_identity(row: dict[str, str]) -> UnitKey:
    return (row["apartment"], row["floorplan_id"], row["unit_id"])


def current_snapshot_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Return the most recent observation per apartment (legacy-history fallback)."""
    rows = list(rows)
    latest_by_apartment: dict[str, str] = {}
    for row in rows:
        apartment = row["apartment"]
        if row["timestamp"] > latest_by_apartment.get(apartment, ""):
            latest_by_apartment[apartment] = row["timestamp"]
    return [row for row in rows if row["timestamp"] == latest_by_apartment[row["apartment"]]]


def group_unit_history(rows: Iterable[dict[str, str]]) -> dict[UnitKey, list[dict[str, str]]]:
    grouped: dict[UnitKey, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[unit_identity(row)].append(row)
    for snapshots in grouped.values():
        snapshots.sort(key=lambda row: row["timestamp"])
    return dict(grouped)


def apartment_snapshot_times(
    floorplan_rows: Iterable[dict[str, str]], unit_rows: Iterable[dict[str, str]]
) -> dict[str, list[str]]:
    timestamps: dict[str, set[str]] = defaultdict(set)
    for row in [*floorplan_rows, *unit_rows]:
        timestamps[row["apartment"]].add(row["timestamp"])
    return {apartment: sorted(values) for apartment, values in timestamps.items()}


def unit_summary(rows: list[dict[str, str]]) -> dict[str, int | str | None]:
    if not rows:
        return {"first_timestamp": None, "latest_timestamp": None, "first_price": None,
                "latest_price": None, "change_cents": None, "change_percent": None,
                "snapshot_count": 0}
    first_price, latest_price = price_to_cents(rows[0]["price"]), price_to_cents(rows[-1]["price"])
    change = latest_price - first_price
    return {"first_timestamp": rows[0]["timestamp"], "latest_timestamp": rows[-1]["timestamp"],
            "first_price": first_price, "latest_price": latest_price, "change_cents": change,
            "change_percent": change / first_price * 100 if first_price else None,
            "snapshot_count": len(rows)}


def unit_history_data(
    grouped_rows: dict[UnitKey, list[dict[str, str]]],
    timestamps_by_apartment: dict[str, list[str]],
    current_keys: set[UnitKey] | None = None,
) -> list[dict[str, object]]:
    """Create JSON-safe data and preserve gaps between advertised periods."""
    histories: list[dict[str, object]] = []
    for key, rows in sorted(grouped_rows.items()):
        apartment, floorplan_id, unit_id = key
        timestamps = timestamps_by_apartment.get(apartment, [])
        previous_timestamp: str | None = None
        points = []
        for row in rows:
            points.append({"timestamp": row["timestamp"], "price": price_to_cents(row["price"]),
                           "gap_before": bool(previous_timestamp and any(
                               previous_timestamp < known < row["timestamp"] for known in timestamps))})
            previous_timestamp = row["timestamp"]
        histories.append({"key": "\u001f".join(key), "apartment": apartment,
                          "floorplan": rows[-1]["floorplan"], "floorplan_id": floorplan_id,
                          "unit_id": unit_id, "move_in": rows[-1]["move_in"], "points": points,
                          "currently_advertised": key in current_keys if current_keys is not None else True,
                          **unit_summary(rows)})
    return histories


def sparkline(prices: list[int]) -> str:
    """Small server-rendered trend, including a visible flat line and marker."""
    if not prices:
        return ""
    width, height, padding = 180, 48, 5
    low, high = min(prices), max(prices)
    points = []
    for index, price in enumerate(prices):
        x = padding if len(prices) == 1 else padding + index * (width - 2 * padding) / (len(prices) - 1)
        y = height / 2 if high == low else height - padding - (price - low) * (height - 2 * padding) / (high - low)
        points.append(f"{x:.1f},{y:.1f}")
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="rent trend">'
            f'<polyline points="{" ".join(points)}" fill="none" stroke="#9a4e10" stroke-width="2.5"/>'
            f'<circle cx="{points[-1].split(",")[0]}" cy="{points[-1].split(",")[1]}" r="3" fill="#9a4e10"/></svg>')


def json_for_script(value: object) -> str:
    return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")


def parsed_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def freshness_status(
    complete_times: dict[str, list[str]], now: datetime | None = None,
    stale_threshold_hours: int = STALE_THRESHOLD_HOURS,
) -> dict[str, object]:
    """Summarize freshness using complete runs only."""
    timestamps = [timestamp for values in complete_times.values() for timestamp in values]
    if not timestamps:
        return {"latest": None, "age_hours": None, "stale": True,
                "complete_days": 0, "health": "No successful snapshot"}
    latest = max(parsed_timestamp(timestamp) for timestamp in timestamps)
    observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age_hours = max(0.0, (observed_now - latest).total_seconds() / 3600)
    stale = age_hours > stale_threshold_hours
    return {"latest": latest.isoformat(timespec="seconds"), "age_hours": age_hours,
            "stale": stale,
            "complete_days": len({parsed_timestamp(value).date() for value in timestamps}),
            "health": "Stale" if stale else "Healthy"}


def window_change(rows: list[dict[str, object]], field: str, days: int) -> int | None:
    """Change only when coverage reaches the named daily-history window."""
    if len(rows) < days:
        return None
    latest = rows[-1]
    cutoff = parsed_timestamp(str(latest["timestamp"])) - timedelta(days=days)
    eligible = [row for row in rows if parsed_timestamp(str(row["timestamp"])) <= cutoff]
    if not eligible:
        return None
    return int(latest[field]) - int(eligible[-1][field])


def successful_times(runs: list[dict[str, str]], fallback_rows: Iterable[dict[str, str]]) -> dict[str, list[str]]:
    known: dict[str, set[str]] = defaultdict(set)
    for row in runs:
        if row.get("status") == "complete":
            known[row["apartment"]].add(row["timestamp"])
    if known:
        return {apartment: sorted(times) for apartment, times in known.items()}
    # Pre-coverage histories were only written after a successful collector run.
    for row in fallback_rows:
        known[row["apartment"]].add(row["timestamp"])
    return {apartment: sorted(times) for apartment, times in known.items()}


def fallback_daily_rows(unit_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Render old repositories gracefully until the next collector creates aggregates."""
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in unit_rows:
        grouped[(row["timestamp"], row["apartment"], row["floorplan_id"])].append(row)
    result = []
    for (timestamp, apartment, floorplan_id), rows in grouped.items():
        rents = sorted(price_to_cents(row["price"]) for row in rows)
        psf = [(price_to_cents(row["price"]) + int_value(row["sqft"]) // 2) // int_value(row["sqft"]) for row in rows if int_value(row["sqft"]) > 0]
        result.append({"timestamp": timestamp, "apartment": apartment, "floorplan": rows[0]["floorplan"],
                       "floorplan_id": floorplan_id, "sqft": rows[0]["sqft"], "visible_units": str(len(rows)),
                       "min_rent": format_cents(rents[0]), "median_rent": format_cents(int(median(rents))),
                       "max_rent": format_cents(rents[-1]),
                       "min_rent_per_sqft": f"${min(psf) / 100:.2f}" if psf else "",
                       "median_rent_per_sqft": f"${int(median(psf)) / 100:.2f}" if psf else "",
                       "max_rent_per_sqft": f"${max(psf) / 100:.2f}" if psf else "",
                       "newly_visible_units": "0", "price_reductions": "0",
                       "earliest_move_in": min((row["move_in"] for row in rows), key=move_in_sort_key)})
    return result


def generate_report(
    floorplan_file: Path, unit_file: Path, output_file: Path,
    daily_file: Path | None = None, runs_file: Path | None = None,
    traits_file: Path | None = None, now: datetime | None = None,
) -> None:
    """Build the floor-plan-first dashboard; raw snapshots remain authoritative."""
    floorplan_history, unit_history = read_rows(floorplan_file), read_rows(unit_file)
    daily_history = read_rows(daily_file) if daily_file else []
    runs = read_rows(runs_file) if runs_file else []
    traits = {row["unit_id"]: row for row in read_rows(traits_file)} if traits_file else {}
    if not daily_history:
        daily_history = fallback_daily_rows(unit_history)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    complete_times = successful_times(runs, [*floorplan_history, *unit_history])
    freshness = freshness_status(complete_times, now)
    current_times = {apartment: times[-1] for apartment, times in complete_times.items() if times}
    current_daily = [row for row in daily_history if row["timestamp"] == current_times.get(row["apartment"])]
    current_units = [row for row in unit_history if row["timestamp"] == current_times.get(row["apartment"])]
    current_keys = {unit_identity(row) for row in current_units}

    by_plan: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in daily_history:
        by_plan[(row["apartment"], row["floorplan_id"])].append(row)
    plan_data = []
    recommendation_cards = []
    plan_keys = sorted(
        {(row["apartment"], row["floorplan_id"]) for row in current_daily},
        key=lambda key: (int_value(by_plan[key][-1]["sqft"]), key),
    )
    for index, key in enumerate(plan_keys, start=1):
        history = sorted(by_plan[key], key=lambda row: row["timestamp"])
        latest = history[-1]
        points = [{"timestamp": row["timestamp"], "min": price_to_cents(row["min_rent"]),
                   "median": price_to_cents(row["median_rent"]), "max": price_to_cents(row["max_rent"]),
                   "units": int_value(row["visible_units"]), "psf": row["median_rent_per_sqft"],
                   "new": int_value(row["newly_visible_units"]), "reductions": int_value(row["price_reductions"])} for row in history]
        change7 = window_change(points, "median", 7)
        change30 = window_change(points, "median", 30)
        inventory7 = window_change(points, "units", 7)
        inventory30 = window_change(points, "units", 30)
        coverage_days = len({parsed_timestamp(point["timestamp"]).date() for point in points})
        if change7 is None or inventory7 is None:
            signal, signal_class = f"Collecting history ({coverage_days}/7 days)", "neutral"
        elif inventory7 > 0 and (change7 < 0 or price_to_cents(latest["min_rent"]) < points[-2]["min"]):
            signal, signal_class = "Favorable / negotiate", "good"
        elif inventory7 < 0 and change7 > 0:
            signal, signal_class = "Act sooner", "bad"
        else:
            signal, signal_class = "Watch / wait", "neutral"
        low_units = [row for row in current_units if (row["apartment"], row["floorplan_id"]) == key]
        low = min(low_units, key=lambda row: price_to_cents(row["price"]), default=None)
        available_now = sum(row["move_in"].casefold() in {"immediate", "now"} for row in low_units)
        recommendation = plan_recommendation(low_units, traits)
        item = {"key": f"layout-{index}", "layout": f"Layout {index}", "sqft": latest["sqft"],
                "points": points, "available_now": available_now, "low_rent": low["price"] if low else None,
                "signal": signal, "history_count": len(points), "coverage_days": coverage_days,
                "recommendation": recommendation}
        plan_data.append(item)
        changes = f"7d {format_cents(change7, True)} / 30d {format_cents(change30, True)}"
        inventory = f"{latest['visible_units']} <small>7d {inventory7 if inventory7 is not None else '—'} / 30d {inventory30 if inventory30 is not None else '—'}</small>"
        score = recommendation["score"]
        score_display = "—" if score is None else f"{score}/100"
        reasons = "; ".join(str(reason) for reason in recommendation["reasons"][:3])
        fit = (f'<div class="fit-stack"><strong class="score">{score_display}</strong>'
               f'<span class="signal {recommendation["class"]}">{recommendation["label"]}</span>'
               f'<small>Confidence: {recommendation["confidence"]}</small>'
               f'<small>{recommendation["rated_count"]}/{recommendation["unit_count"]} current homes rated</small></div>')
        recommendation_cards.append(
            f'<article class="plan-card"><header><div><button class="plan-link" '
            f'data-plan="{html.escape(item["key"], quote=True)}">{item["layout"]}</button>'
            f'<small>{html.escape(latest["sqft"])} sq ft</small></div>{fit}</header>'
            f'<p class="why">{html.escape(reasons)}</p><dl class="card-stats">'
            f'<dt>Inventory</dt><dd>{inventory}</dd>'
            f'<dt>Min / median / max</dt><dd><strong>{html.escape(latest["min_rent"])}</strong> / '
            f'{html.escape(latest["median_rent"])} / {html.escape(latest["max_rent"])}</dd>'
            f'<dt>Median $/sq ft</dt><dd>{html.escape(latest["median_rent_per_sqft"]) or "—"}</dd>'
            f'<dt>Median-rent change</dt><dd>{changes}</dd>'
            f'<dt>Earliest move-in</dt><dd>{html.escape(latest["earliest_move_in"])}</dd>'
            f'<dt>Market signal</dt><dd><span class="signal market-signal {signal_class}">{signal}</span></dd>'
            '</dl></article>'
        )

    market_points = []
    for apartment, timestamps in complete_times.items():
        for timestamp in timestamps:
            rows = [row for row in unit_history if row["apartment"] == apartment and row["timestamp"] == timestamp]
            if rows:
                market_points.append({"timestamp": timestamp, "units": len(rows),
                                      "median": int(median([price_to_cents(row["price"]) for row in rows])),
                                      "reductions": sum(point["reductions"] for point in [p for plan in plan_data for p in plan["points"] if p["timestamp"] == timestamp])})
    market_points.sort(key=lambda row: row["timestamp"])
    latest_market = market_points[-1] if market_points else {"units": 0, "median": 0, "reductions": 0}
    market_7_units, market_30_units = window_change(market_points, "units", 7), window_change(market_points, "units", 30)
    market_7_rent, market_30_rent = window_change(market_points, "median", 7), window_change(market_points, "median", 30)

    latest_timestamp = str(freshness["latest"] or "No complete snapshots yet")
    rated_plans = [plan for plan in plan_data if plan["recommendation"]["score"] is not None]
    best_plan = max(rated_plans, key=lambda plan: int(plan["recommendation"]["score"])) if rated_plans else None
    best_fit = (f'{best_plan["layout"]} · {best_plan["recommendation"]["score"]}/100'
                if best_plan else "Personal fit unavailable")
    if freshness["stale"] and best_plan:
        best_fit += " · stale data"
    age_display = "—" if freshness["age_hours"] is None else f'{float(freshness["age_hours"]):.1f} hours'
    stale_warning = (
        '<aside class="stale-warning"><strong>Stale-data warning:</strong> The report remains readable, '
        'but current availability and recommendations may be out of date.</aside>'
        if freshness["stale"] else ""
    )
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Rental Market Tracker</title>
<style>:root{{--ink:#282629;--muted:#706d6d;--paper:#faf8f5;--line:#ded8cf;--accent:#9a4e10;--good:#28724d;--bad:#a2382d}}*{{box-sizing:border-box}}body{{margin:0;color:var(--ink);background:linear-gradient(125deg,#f1ede6,#fff 40%,#eee6db);font-family:Georgia,serif}}main{{max-width:1380px;margin:auto;padding:52px 22px 80px}}h1{{margin:0;font-size:clamp(2.6rem,7vw,5rem);font-weight:400;letter-spacing:-.05em}}h2{{font-weight:400;margin:0 0 10px;font-size:1.7rem}}h3{{margin:0;font-size:1rem;font-weight:400}}section{{margin-top:45px}}.eyebrow,small,.note{{color:var(--muted);font:.72rem ui-monospace,monospace;letter-spacing:.06em;text-transform:uppercase}}.note{{text-transform:none;letter-spacing:0;font-size:.82rem}}.summary{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:28px 0}}.metric,.panel,.method-card,.plan-card{{background:#ffffffc9;border:1px solid var(--line)}}.metric{{padding:16px}}.metric strong{{display:block;font-size:1.45rem;font-weight:400;margin-top:6px}}.panel{{overflow:hidden}}button{{font:inherit;color:inherit}}.plan-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:14px}}.plan-card{{padding:18px;min-width:0}}.plan-card header{{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}}.plan-card header>div:first-child{{display:grid;gap:8px}}.plan-link{{padding:0;border:0;background:none;color:var(--accent);cursor:pointer;font-weight:bold;text-decoration:underline;font-size:1rem;text-align:left}}.fit-stack{{display:grid;justify-items:end;gap:6px;min-width:132px}}.score{{display:block;font-size:1.2rem}}.fit-stack small{{line-height:1.35;text-align:right}}.why{{min-height:2.6em;margin:16px 0;color:var(--ink);font:.82rem/1.45 ui-monospace,monospace}}.card-stats{{border-top:1px solid var(--line);padding-top:13px}}.signal{{display:inline-block;font-size:.72rem;padding:4px 6px;border:1px solid currentColor;white-space:nowrap}}.market-signal{{max-width:190px;white-space:normal;line-height:1.25}}.good{{color:var(--good)}}.bad{{color:var(--bad)}}.neutral{{color:var(--muted)}}.controls{{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 12px}}label{{display:grid;gap:4px;color:var(--muted);font:.72rem ui-monospace,monospace;text-transform:uppercase}}select{{padding:10px 38px 10px 12px;background:#fff;border:1px solid var(--line);min-width:230px}}.detail-grid{{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,360px);gap:28px;padding:24px}}.chart-column{{min-width:0}}.chart-block+ .chart-block{{border-top:1px solid var(--line);margin-top:18px;padding-top:18px}}.chart-heading{{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:0 0 6px}}.chart-title{{font:600 .76rem ui-monospace,monospace;letter-spacing:.05em;text-transform:uppercase}}.legend{{display:flex;gap:12px;flex-wrap:wrap;color:var(--muted);font:.68rem ui-monospace,monospace}}.legend span{{display:flex;align-items:center;gap:5px}}.legend i{{width:15px;height:2px;background:currentColor}}.legend .min{{color:#c77934}}.legend .med{{color:var(--accent)}}.legend .max{{color:#563e2c}}#plan-chart,#inventory-chart{{display:block;width:100%;height:auto;overflow:hidden}}.chart-label{{fill:var(--muted);font:11px ui-monospace,monospace}}.chart-grid{{stroke:var(--line);stroke-width:1}}.line-min{{fill:none;stroke:#c77934;stroke-width:2}}.line-med{{fill:none;stroke:var(--accent);stroke-width:3}}.line-max{{fill:none;stroke:#563e2c;stroke-width:2}}.dot{{fill:var(--accent)}}.hover-target{{fill:transparent;pointer-events:all}}.chart-tooltip{{pointer-events:none}}.chart-tooltip rect{{fill:var(--ink);opacity:.94}}.chart-tooltip text{{fill:#fff;font:11px ui-monospace,monospace}}#plan-summary{{align-self:start;background:var(--paper);border:1px solid var(--line);border-top:3px solid var(--accent);padding:18px}}#plan-summary dl{{grid-template-columns:minmax(116px,1fr) minmax(118px,1.25fr);gap:0 14px}}#plan-summary dt,#plan-summary dd{{padding:9px 0;border-bottom:1px solid var(--line);line-height:1.35}}#plan-summary dt:last-of-type,#plan-summary dd:last-of-type{{border-bottom:0}}dl{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;margin:0;font:.82rem ui-monospace,monospace}}dt{{color:var(--muted)}}dd{{margin:0;text-align:right;max-width:230px;overflow-wrap:anywhere}}.method-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:14px}}.method-card{{padding:16px}}.method-card .weight{{display:block;margin:8px 0;font:1.35rem ui-monospace,monospace}}.method-card p{{margin:0;color:var(--muted);font:.78rem/1.5 ui-monospace,monospace}}.method-wide{{grid-column:span 2}}.method-foot{{margin-top:12px;padding:16px;border-left:3px solid var(--accent);background:#ffffff8f}}@media(max-width:900px){{main{{padding:32px 12px}}.summary,.detail-grid,.method-grid,.plan-grid{{grid-template-columns:1fr}}.detail-grid{{gap:20px;padding:18px}}.method-wide{{grid-column:auto}}select{{width:100%}}}}@media(max-width:520px){{.plan-card header{{display:grid}}.fit-stack{{justify-items:start;min-width:0}}.fit-stack small{{text-align:left}}.detail-grid{{padding:12px}}.chart-heading{{align-items:flex-start;flex-direction:column;gap:7px}}#plan-summary dl,dl{{grid-template-columns:1fr}}#plan-summary dd,dd{{text-align:left;max-width:none;margin-bottom:6px}}#plan-summary dt{{border-bottom:0;padding-bottom:0}}}}</style></head><body><main>
<style>.line-min{{stroke-dasharray:2 5}}.line-max{{stroke-dasharray:8 5}}</style>
<p class="eyebrow">Anonymous building / advertised inventory</p><h1>Rental Market</h1>{stale_warning}<p class="note">Latest complete snapshot: {html.escape(latest_timestamp)} · Snapshot age: {age_display} · Complete-history day count: {freshness['complete_days']} · Collector health: {freshness['health']}. All prices are advertised rents, not executed lease prices.</p>
<div class="summary"><div class="metric"><span class="eyebrow">Advertised units</span><strong>{latest_market['units']}</strong><small>7d {market_7_units if market_7_units is not None else '—'} / 30d {market_30_units if market_30_units is not None else '—'}</small></div><div class="metric"><span class="eyebrow">Available floor plans</span><strong>{len(plan_data)}</strong><small>current complete run</small></div><div class="metric"><span class="eyebrow">Best current fit</span><strong>{best_fit}</strong><small>best rated home, layout-level display</small></div><div class="metric"><span class="eyebrow">Median advertised rent</span><strong>{format_cents(latest_market['median']) if market_points else '—'}</strong><small>7d {format_cents(market_7_rent, True)} / 30d {format_cents(market_30_rent, True)}</small></div><div class="metric"><span class="eyebrow">Visible price reductions</span><strong>{latest_market['reductions']}</strong><small>vs. prior observed price</small></div></div>
<section><h2>Personalized floor-plan recommendation</h2><p class="note">Recommendation uses the best currently advertised home in each layout: 35 points for relative asking rent and 65 for verified exposure, direct sunlight, pool-facing versus exterior facade, view, floor band, and disturbance/privacy. Irregular geometry alone does not disqualify a home. Layouts with substantial circulation or unusable space receive up to a 15-point efficiency penalty but can still be offset by price and residence traits. Floor 5 is the minimum; floor 7+ is preferred. Southeast and the interior pool-facing facade are preferred; northwest homes with mountain shade receive a strong penalty. Individual listings remain private.</p><div class="plan-grid">{''.join(recommendation_cards) or '<article class="plan-card">Run the collector to create a complete inventory snapshot.</article>'}</div></section>
<section><h2>Layout detail</h2><div class="controls"><label>Layout<select id="plan-selector"></select></label></div><div class="panel"><div class="detail-grid"><div class="chart-column"><div class="chart-block"><div class="chart-heading"><span class="chart-title">Advertised rent</span><span class="legend"><span class="min"><i></i>Minimum</span><span class="med"><i></i>Median</span><span class="max"><i></i>Maximum</span></span></div><svg id="plan-chart" viewBox="0 0 700 235" role="img" aria-label="Minimum median and maximum advertised rent"></svg></div></div><aside id="plan-summary" aria-label="Selected layout summary"></aside></div></div></section>
<section><h2>How to use this</h2><p class="note">Start with Personal fit, then compare the rent range and market signal. An unusual outline is not automatically bad; the layout penalty reflects how much circulation or hard-to-use area it appears to create. A layout can have both strong and weak exposures; homes below floor 5 are excluded when an eligible alternative exists, and floors 5–6 remain below the preferred floor-7 threshold. Individual listings are intentionally not published. Disappearance only means no longer advertised, not leased.</p></section>
<section id="scoring-methodology"><h2>Scoring methodology</h2><p class="note">The 100-point personal-fit score is intentionally transparent. It reflects the stated preference for floor 5 minimum / floor 7+ preferred, southeast light, an interior pool-facing home, and the observed northwest mountain shade. Skyline labels require floor 7+ evidence; direction alone never creates a Manhattan-view claim. Every advertised room is joined to the verified PDF catalog before scoring.</p><div class="method-grid">
<article class="method-card method-wide"><h3>Relative asking rent</h3><strong class="weight">35 points</strong><p>Compared only with other current homes in the same floor plan. Formula: round(clamp(28 − 1.4 × percent above the layout median, 14, 35)). At the median: 28; about 5% below: 35; 10% above: 14. This prevents a larger layout from winning simply because it is larger.</p></article>
<article class="method-card"><h3>Exposure / direction</h3><strong class="weight">17 points</strong><p>SE {EXPOSURE_POINTS['SE']} · SW {EXPOSURE_POINTS['SW']} · NE {EXPOSURE_POINTS['NE']} · NW {EXPOSURE_POINTS['NW']} · unknown {EXPOSURE_POINTS['unknown']}.</p></article>
<article class="method-card"><h3>Direct sunlight</h3><strong class="weight">10 points</strong><p>Good {SUNLIGHT_POINTS['good']} · mixed {SUNLIGHT_POINTS['mixed']} · low / mountain shade {SUNLIGHT_POINTS['low']} · unknown {SUNLIGHT_POINTS['unknown']}.</p></article>
<article class="method-card"><h3>Pool-facing preference</h3><strong class="weight">12 points</strong><p>Interior / pool-facing {FACADE_POINTS['internal']} · exterior {FACADE_POINTS['external']} · unknown {FACADE_POINTS['unknown']}. This is an explicit personal preference, not a universal market premium.</p></article>
<article class="method-card"><h3>View</h3><strong class="weight">9 points</strong><p>Open skyline {VIEW_POINTS['skyline_open']} · pool + partial skyline {VIEW_POINTS['pool_skyline_partial']} · partial skyline {VIEW_POINTS['skyline_partial']} · pool/courtyard {VIEW_POINTS['pool_courtyard']} · open {VIEW_POINTS['open']} · none {VIEW_POINTS['none']} · unknown {VIEW_POINTS['unknown']}.</p></article>
<article class="method-card"><h3>Floor requirement</h3><strong class="weight">9 points + gate</strong><p>Upper / floors 10–11: {FLOOR_BAND_POINTS['upper']} · preferred / floors 7–9: {FLOOR_BAND_POINTS['mid_high']} · acceptable / floors 5–6: {FLOOR_BAND_POINTS['mid']} · below minimum / floors 2–4: {FLOOR_BAND_POINTS['low']}. Below-floor-5 homes are excluded whenever a layout has an eligible alternative; floors 5–6 cannot receive Best match.</p></article>
<article class="method-card"><h3>Disturbance / privacy</h3><strong class="weight">8 points</strong><p>Low disturbance {DISTURBANCE_POINTS['low']} · medium {DISTURBANCE_POINTS['medium']} · high / amenity-noise exposure {DISTURBANCE_POINTS['high']} · unknown {DISTURBANCE_POINTS['unknown']}. Pool activity remains a separate risk instead of canceling the pool-facing preference.</p></article>
<article class="method-card"><h3>Floor-plan size</h3><strong class="weight">0 direct points</strong><p>Square footage and bedroom layout are shown for comparison but do not directly add points. Their cost is already handled through within-layout rent comparison.</p></article>
<article class="method-card"><h3>Layout efficiency</h3><strong class="weight">0 to −15 points</strong><p>Shape alone has no penalty. Usable but mildly awkward plans deduct 4 points; layouts with substantial circulation, narrow transition zones, or hard-to-use area deduct 15. This is a preference penalty, not an exclusion.</p></article></div>
<p class="note method-foot"><strong>Labels:</strong> Best match 82–100 · Strong 70–81 · Consider 58–69 · Low fit below 58. Floor rules override these labels: floors 2–4 are below minimum, and floors 5–6 are capped at Strong. Layout-efficiency deductions can be offset by rent or stronger residence traits. A home with no verified traits is not rated.</p></section>
</main><script id="plan-data" type="application/json">{json_for_script(plan_data)}</script><script>
(()=>{{const plans=JSON.parse(document.querySelector('#plan-data').textContent),money=v=>new Intl.NumberFormat('en-US',{{style:'currency',currency:'USD',maximumFractionDigits:0}}).format(v/100),date=v=>new Date(v).toLocaleDateString(undefined,{{month:'short',day:'numeric'}}),make=(n,a)=>{{const e=document.createElementNS('http://www.w3.org/2000/svg',n);Object.entries(a).forEach(([k,v])=>e.setAttribute(k,v));return e}},label=(svg,attrs,value)=>{{const node=make('text',attrs);node.textContent=value;svg.append(node)}},path=(svg,points,field,klass,h=235,range=null)=>{{if(!points.length)return;const w=700,l=52,r=12,t=12,b=30,vs=points.map(p=>p[field]),lo=range?range.lo:Math.min(...vs),hi=range?range.hi:Math.max(...vs),x=i=>l+i*(w-l-r)/Math.max(points.length-1,1),y=v=>hi===lo?(t+h-b)/2:h-b-(v-lo)*(h-t-b)/(hi-lo);svg.append(make('polyline',{{points:points.map((p,i)=>`${{x(i).toFixed(1)}},${{y(p[field]).toFixed(1)}}`).join(' '),'class':klass}}));points.forEach((p,i)=>svg.append(make('circle',{{cx:x(i),cy:y(p[field]),r:4,'class':'dot'}})));return {{lo,hi}}}},tooltips=(svg,points,fields,h)=>{{const w=700,l=52,r=12,plot=w-l-r,cell=plot/Math.max(points.length-1,1),boxW=176,boxH=fields.length===1?48:86;points.forEach((p,i)=>{{const x=l+i*cell,target=make('rect',{{x:Math.max(l,x-cell/2),y:0,width:Math.max(cell,16),height:h,'class':'hover-target',tabindex:0,'aria-label':`${{date(p.timestamp)}}. ${{fields.map(([field,name])=>`${{name}}: ${{field==='units'?p[field]:money(p[field])}}`).join('. ')}}`}}),show=()=>{{svg.querySelector('.chart-tooltip')?.remove();const tx=Math.min(Math.max(x-boxW/2,4),w-boxW-4),ty=8,g=make('g',{{'class':'chart-tooltip'}});g.append(make('rect',{{x:tx,y:ty,width:boxW,height:boxH,rx:4}}));label(g,{{x:tx+12,y:ty+18}},date(p.timestamp));fields.forEach(([field,name],j)=>label(g,{{x:tx+12,y:ty+37+j*15}},`${{name}}: ${{field==='units'?p[field]:money(p[field])}}`));svg.append(g)}},hide=()=>svg.querySelector('.chart-tooltip')?.remove();target.addEventListener('pointerenter',show);target.addEventListener('pointerleave',hide);target.addEventListener('focus',show);target.addEventListener('blur',hide);svg.append(target)}})}};
const ps=document.querySelector('#plan-selector'),pc=document.querySelector('#plan-chart'),pSum=document.querySelector('#plan-summary');ps.innerHTML=plans.map(p=>`<option value="${{p.key}}">${{p.layout}} · ${{p.sqft}} sq ft</option>`).join('');function renderPlan(){{const p=plans.find(x=>x.key===ps.value);if(!p)return;pc.replaceChildren();const rentValues=p.points.flatMap(point=>[point.min,point.median,point.max]),scale={{lo:Math.min(...rentValues),hi:Math.max(...rentValues)}};[48,205].forEach(y=>pc.append(make('line',{{x1:52,y1:y,x2:688,y2:y,'class':'chart-grid'}})));path(pc,p.points,'min','line-min',235,scale);path(pc,p.points,'median','line-med',235,scale);path(pc,p.points,'max','line-max',235,scale);label(pc,{{x:2,y:18,'class':'chart-label'}},money(scale.hi));label(pc,{{x:2,y:205,'class':'chart-label'}},money(scale.lo));label(pc,{{x:52,y:228,'class':'chart-label'}},date(p.points[0].timestamp));label(pc,{{x:688,y:228,'class':'chart-label','text-anchor':'end'}},date(p.points.at(-1).timestamp));tooltips(pc,p.points,[['min','Minimum'],['median','Median'],['max','Maximum']],235);const r=p.recommendation,reason=r.reasons.slice(0,3).join('; ');pSum.innerHTML=`<dl><dt>Personal fit</dt><dd>${{r.score===null?'—':r.score+'/100 · '+r.label}}</dd><dt>Why</dt><dd>${{reason}}</dd><dt>Rated current homes</dt><dd>${{r.rated_count}}/${{r.unit_count}}</dd><dt>Floor 5+ eligible</dt><dd>${{r.eligible_floor_count}}</dd><dt>Floor 7+ preferred</dt><dd>${{r.preferred_floor_count}}</dd><dt>Preferred southeast</dt><dd>${{r.preferred_count}}</dd><dt>Pool-facing interior</dt><dd>${{r.pool_facing_count}}</dd><dt>Low-sun northwest</dt><dd>${{r.low_sun_count}}</dd><dt>Market signal</dt><dd>${{p.signal}}</dd><dt>Lowest advertised rent</dt><dd>${{p.low_rent||'—'}}</dd><dt>Available now</dt><dd>${{p.available_now}}</dd><dt>Newly visible</dt><dd>${{p.points.at(-1).new}}</dd><dt>Price reductions</dt><dd>${{p.points.at(-1).reductions}}</dd><dt>Complete snapshot days</dt><dd>${{p.coverage_days}}</dd></dl>`}}ps.onchange=renderPlan;document.querySelectorAll('.plan-link').forEach(b=>b.onclick=()=>{{ps.value=b.dataset.plan;renderPlan();document.querySelector('#plan-selector').scrollIntoView({{behavior:'smooth',block:'center'}})}});renderPlan()}})();</script></body></html>'''
    output_file.write_text(document, encoding="utf-8")
