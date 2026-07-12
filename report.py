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
) -> None:
    """Build the floor-plan-first dashboard; raw snapshots remain authoritative."""
    floorplan_history, unit_history = read_rows(floorplan_file), read_rows(unit_file)
    daily_history = read_rows(daily_file) if daily_file else []
    runs = read_rows(runs_file) if runs_file else []
    if not daily_history:
        daily_history = fallback_daily_rows(unit_history)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    complete_times = successful_times(runs, [*floorplan_history, *unit_history])
    current_times = {apartment: times[-1] for apartment, times in complete_times.items() if times}
    current_daily = [row for row in daily_history if row["timestamp"] == current_times.get(row["apartment"])]
    current_units = [row for row in unit_history if row["timestamp"] == current_times.get(row["apartment"])]
    current_keys = {unit_identity(row) for row in current_units}

    by_plan: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in daily_history:
        by_plan[(row["apartment"], row["floorplan_id"])].append(row)
    plan_data = []
    table_rows = []
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
        if change7 is None or inventory7 is None:
            signal, signal_class = "Insufficient history", "neutral"
        elif inventory7 > 0 and (change7 < 0 or price_to_cents(latest["min_rent"]) < points[-2]["min"]):
            signal, signal_class = "Favorable / negotiate", "good"
        elif inventory7 < 0 and change7 > 0:
            signal, signal_class = "Act sooner", "bad"
        else:
            signal, signal_class = "Watch / wait", "neutral"
        low_units = [row for row in current_units if (row["apartment"], row["floorplan_id"]) == key]
        low = min(low_units, key=lambda row: price_to_cents(row["price"]), default=None)
        available_now = sum(row["move_in"].casefold() in {"immediate", "now"} for row in low_units)
        item = {"key": f"layout-{index}", "layout": f"Layout {index}", "sqft": latest["sqft"],
                "points": points, "available_now": available_now, "low_rent": low["price"] if low else None,
                "signal": signal, "history_count": len(points)}
        plan_data.append(item)
        changes = f"7d {format_cents(change7, True)} / 30d {format_cents(change30, True)}"
        inventory = f"{latest['visible_units']} <small>7d {inventory7 if inventory7 is not None else '—'} / 30d {inventory30 if inventory30 is not None else '—'}</small>"
        table_rows.append("<tr>" +
            f"<td><button class=\"plan-link\" data-plan=\"{html.escape(item['key'], quote=True)}\">{item['layout']}</button><br><small>{html.escape(latest['sqft'])} sq ft</small></td>" +
            f"<td>{inventory}</td><td><strong>{html.escape(latest['min_rent'])}</strong> / {html.escape(latest['median_rent'])} / {html.escape(latest['max_rent'])}</td>" +
            f"<td>{html.escape(latest['median_rent_per_sqft']) or '—'}</td><td>{changes}</td>" +
            f"<td>{html.escape(latest['earliest_move_in'])}</td><td><span class=\"signal {signal_class}\">{signal}</span></td></tr>")

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

    latest_timestamp = max((row["timestamp"] for row in current_daily), default="No complete snapshots yet")
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Rental Market Tracker</title>
<style>:root{{--ink:#282629;--muted:#706d6d;--paper:#faf8f5;--line:#ded8cf;--accent:#9a4e10;--good:#28724d;--bad:#a2382d}}*{{box-sizing:border-box}}body{{margin:0;color:var(--ink);background:linear-gradient(125deg,#f1ede6,#fff 40%,#eee6db);font-family:Georgia,serif}}main{{max-width:1240px;margin:auto;padding:52px 22px 80px}}h1{{margin:0;font-size:clamp(2.6rem,7vw,5rem);font-weight:400;letter-spacing:-.05em}}h2{{font-weight:400;margin:0 0 10px;font-size:1.7rem}}section{{margin-top:45px}}.eyebrow,small,.note{{color:var(--muted);font: .72rem ui-monospace,monospace;letter-spacing:.06em;text-transform:uppercase}}.note{{text-transform:none;letter-spacing:0;font-size:.82rem}}.summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:28px 0}}.metric,.panel{{background:#ffffffc9;border:1px solid var(--line)}}.metric{{padding:16px}}.metric strong{{display:block;font-size:1.45rem;font-weight:400;margin-top:6px}}.panel{{overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:1040px;font: .81rem ui-monospace,monospace}}th,td{{padding:13px 15px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}}th{{color:var(--muted);font-size:.68rem;text-transform:uppercase}}tr:last-child td{{border:0}}button{{font:inherit;color:inherit}}.plan-link{{padding:0;border:0;background:none;color:var(--accent);cursor:pointer;font-weight:bold;text-decoration:underline}}.signal{{font-size:.72rem;padding:4px 6px;border:1px solid currentColor;white-space:nowrap}}.good{{color:var(--good)}}.bad{{color:var(--bad)}}.neutral{{color:var(--muted)}}.controls{{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 12px}}label{{display:grid;gap:4px;color:var(--muted);font:.72rem ui-monospace,monospace;text-transform:uppercase}}select{{padding:9px;background:#fff;border:1px solid var(--line);min-width:230px}}.detail-grid{{display:grid;grid-template-columns:1fr 280px;gap:10px;padding:16px}}svg{{display:block;width:100%;height:235px}}.chart-label{{fill:var(--muted);font:11px ui-monospace,monospace}}.line-min{{fill:none;stroke:#c77934;stroke-width:2}}.line-med{{fill:none;stroke:var(--accent);stroke-width:3}}.line-max{{fill:none;stroke:#563e2c;stroke-width:2}}.dot{{fill:var(--accent)}}dl{{display:grid;grid-template-columns:1fr auto;gap:10px;margin:0;font:.82rem ui-monospace,monospace}}dt{{color:var(--muted)}}dd{{margin:0;text-align:right}}@media(max-width:760px){{main{{padding:32px 12px}}.summary,.detail-grid{{grid-template-columns:1fr}}select{{width:100%}}}}</style></head><body><main>
<p class="eyebrow">Anonymous building / advertised inventory</p><h1>Rental Market</h1><p class="note">Latest complete snapshot: {html.escape(latest_timestamp)}. All prices are advertised rents, not executed lease prices.</p>
<div class="summary"><div class="metric"><span class="eyebrow">Advertised units</span><strong>{latest_market['units']}</strong><small>7d {market_7_units if market_7_units is not None else '—'} / 30d {market_30_units if market_30_units is not None else '—'}</small></div><div class="metric"><span class="eyebrow">Available floor plans</span><strong>{len(plan_data)}</strong><small>current complete run</small></div><div class="metric"><span class="eyebrow">Median advertised rent</span><strong>{format_cents(latest_market['median']) if market_points else '—'}</strong><small>7d {format_cents(market_7_rent, True)} / 30d {format_cents(market_30_rent, True)}</small></div><div class="metric"><span class="eyebrow">Visible price reductions</span><strong>{latest_market['reductions']}</strong><small>vs. prior observed price</small></div></div>
<section><h2>Floor-plan value comparison</h2><p class="note">Signals are descriptive, not predictions. Window changes require enough complete daily observations.</p><div class="panel"><table><thead><tr><th>Floor plan</th><th>Inventory</th><th>Current min / median / max</th><th>Median $/sq ft</th><th>Median-rent change</th><th>Earliest move-in</th><th>Renter signal</th></tr></thead><tbody>{''.join(table_rows) or '<tr><td colspan="7">Run the collector to create a complete inventory snapshot.</td></tr>'}</tbody></table></div></section>
<section><h2>Layout detail</h2><div class="controls"><label>Layout<select id="plan-selector"></select></label></div><div class="panel"><div class="detail-grid"><div><svg id="plan-chart" viewBox="0 0 700 235" role="img" aria-label="Minimum median and maximum advertised rent"></svg><p class="note">Minimum · median · maximum advertised rent; flat trends still draw lines and markers.</p><svg id="inventory-chart" viewBox="0 0 700 120" role="img" aria-label="Advertised listing count"></svg></div><div id="plan-summary"></div></div></div></section>
<section><h2>How to use this</h2><p class="note">Choose a layout using price per square foot, its median-rent and inventory direction, and your move-in deadline. Individual listings are intentionally not published: disappearance only means no longer advertised, not leased. Floor-level comparisons are withheld until the inventory source provides a reliable floor field.</p></section>
</main><script id="plan-data" type="application/json">{json_for_script(plan_data)}</script><script>
(()=>{{const plans=JSON.parse(document.querySelector('#plan-data').textContent),money=v=>new Intl.NumberFormat('en-US',{{style:'currency',currency:'USD',maximumFractionDigits:0}}).format(v/100),date=v=>new Date(v).toLocaleDateString(undefined,{{month:'short',day:'numeric'}}),make=(n,a)=>{{const e=document.createElementNS('http://www.w3.org/2000/svg',n);Object.entries(a).forEach(([k,v])=>e.setAttribute(k,v));return e}},label=(svg,attrs,value)=>{{const node=make('text',attrs);node.textContent=value;svg.append(node)}},path=(svg,points,field,klass,h=235)=>{{if(!points.length)return;const w=700,l=52,r=12,t=12,b=30,vs=points.map(p=>p[field]),lo=Math.min(...vs),hi=Math.max(...vs),x=i=>l+i*(w-l-r)/Math.max(points.length-1,1),y=v=>hi===lo?(t+h-b)/2:h-b-(v-lo)*(h-t-b)/(hi-lo);svg.append(make('polyline',{{points:points.map((p,i)=>`${{x(i).toFixed(1)}},${{y(p[field]).toFixed(1)}}`).join(' '),'class':klass}}));points.forEach((p,i)=>svg.append(make('circle',{{cx:x(i),cy:y(p[field]),r:3,'class':'dot'}})));return {{lo,hi}}}};
const ps=document.querySelector('#plan-selector'),pc=document.querySelector('#plan-chart'),ic=document.querySelector('#inventory-chart'),pSum=document.querySelector('#plan-summary');ps.innerHTML=plans.map(p=>`<option value="${{p.key}}">${{p.layout}} · ${{p.sqft}} sq ft</option>`).join('');function renderPlan(){{const p=plans.find(x=>x.key===ps.value);if(!p)return;pc.replaceChildren();ic.replaceChildren();let scale=path(pc,p.points,'min','line-min');path(pc,p.points,'median','line-med');path(pc,p.points,'max','line-max');if(scale){{label(pc,{{x:2,y:18,'class':'chart-label'}},money(scale.hi));label(pc,{{x:2,y:205,'class':'chart-label'}},money(scale.lo));label(pc,{{x:52,y:228,'class':'chart-label'}},date(p.points[0].timestamp));label(pc,{{x:688,y:228,'class':'chart-label','text-anchor':'end'}},date(p.points.at(-1).timestamp))}}path(ic,p.points,'units','line-med',120);pSum.innerHTML=`<dl><dt>Signal</dt><dd>${{p.signal}}</dd><dt>Lowest advertised rent</dt><dd>${{p.low_rent||'—'}}</dd><dt>Available now</dt><dd>${{p.available_now}}</dd><dt>Newly visible</dt><dd>${{p.points.at(-1).new}}</dd><dt>Price reductions</dt><dd>${{p.points.at(-1).reductions}}</dd><dt>Daily observations</dt><dd>${{p.history_count}}</dd></dl>`}}ps.onchange=renderPlan;document.querySelectorAll('.plan-link').forEach(b=>b.onclick=()=>{{ps.value=b.dataset.plan;renderPlan();document.querySelector('#plan-selector').scrollIntoView({{behavior:'smooth',block:'center'}})}});renderPlan()}})();</script></body></html>'''
    output_file.write_text(document, encoding="utf-8")
