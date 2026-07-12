"""Generate a self-contained local price-trend report from tracker snapshots."""

from __future__ import annotations

import csv
import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable


UnitKey = tuple[str, str, str]


def read_rows(filename: Path) -> list[dict[str, str]]:
    if not filename.exists():
        return []
    with filename.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def price_to_cents(price: str) -> int:
    return int(round(float(price.replace("$", "").replace(",", "")) * 100))


def format_cents(cents: int, include_sign: bool = False) -> str:
    sign = "+" if include_sign and cents > 0 else "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.0f}"


def unit_identity(row: dict[str, str]) -> UnitKey:
    """Return the durable identity for an advertised unit."""
    return (row["apartment"], row["floorplan_id"], row["unit_id"])


def current_snapshot_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Return rows from the most recent scrape for each apartment."""
    rows = list(rows)
    latest_by_apartment: dict[str, str] = {}
    for row in rows:
        apartment = row["apartment"]
        if apartment not in latest_by_apartment or row["timestamp"] > latest_by_apartment[apartment]:
            latest_by_apartment[apartment] = row["timestamp"]
    return [row for row in rows if row["timestamp"] == latest_by_apartment[row["apartment"]]]


def group_unit_history(rows: Iterable[dict[str, str]]) -> dict[UnitKey, list[dict[str, str]]]:
    """Group unit snapshots by identity, ordered by observation time."""
    grouped: dict[UnitKey, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[unit_identity(row)].append(row)
    for snapshots in grouped.values():
        snapshots.sort(key=lambda row: row["timestamp"])
    return dict(grouped)


def apartment_snapshot_times(
    floorplan_rows: Iterable[dict[str, str]], unit_rows: Iterable[dict[str, str]]
) -> dict[str, list[str]]:
    """Return known collector timestamps by apartment.

    Floor-plan rows are included because a scrape can succeed even when there
    are no advertised units.  Those timestamps let the report distinguish a
    genuine disappearance/reappearance from two adjacent observations.
    """
    timestamps: dict[str, set[str]] = defaultdict(set)
    for row in [*floorplan_rows, *unit_rows]:
        timestamps[row["apartment"]].add(row["timestamp"])
    return {apartment: sorted(values) for apartment, values in timestamps.items()}


def unit_summary(rows: list[dict[str, str]]) -> dict[str, int | str | None]:
    """Calculate the first/latest price information for one sorted unit history."""
    if not rows:
        return {
            "first_timestamp": None,
            "latest_timestamp": None,
            "first_price": None,
            "latest_price": None,
            "change_cents": None,
            "change_percent": None,
            "snapshot_count": 0,
        }
    first_price = price_to_cents(rows[0]["price"])
    latest_price = price_to_cents(rows[-1]["price"])
    change_cents = latest_price - first_price
    return {
        "first_timestamp": rows[0]["timestamp"],
        "latest_timestamp": rows[-1]["timestamp"],
        "first_price": first_price,
        "latest_price": latest_price,
        "change_cents": change_cents,
        "change_percent": (change_cents / first_price * 100) if first_price else None,
        "snapshot_count": len(rows),
    }


def unit_history_data(
    grouped_rows: dict[UnitKey, list[dict[str, str]]],
    timestamps_by_apartment: dict[str, list[str]],
) -> list[dict[str, object]]:
    """Create compact, JSON-safe data for the selected-unit dashboard.

    ``gap_before`` marks an observation that follows at least one known scrape
    during which the unit was absent.  The browser uses it to start a new SVG
    line segment instead of joining the two advertised periods.
    """
    histories: list[dict[str, object]] = []
    for key, rows in sorted(grouped_rows.items()):
        apartment, floorplan_id, unit_id = key
        timestamps = timestamps_by_apartment.get(apartment, [])
        points = []
        previous_timestamp: str | None = None
        for row in rows:
            gap_before = bool(
                previous_timestamp
                and any(previous_timestamp < timestamp < row["timestamp"] for timestamp in timestamps)
            )
            points.append(
                {
                    "timestamp": row["timestamp"],
                    "price": price_to_cents(row["price"]),
                    "gap_before": gap_before,
                }
            )
            previous_timestamp = row["timestamp"]
        summary = unit_summary(rows)
        histories.append(
            {
                "key": "\u001f".join(key),
                "apartment": apartment,
                "floorplan": rows[-1]["floorplan"],
                "floorplan_id": floorplan_id,
                "unit_id": unit_id,
                "points": points,
                **summary,
            }
        )
    return histories


def sparkline(prices: list[int]) -> str:
    width, height, padding = 180, 48, 5
    if not prices:
        return ""
    low, high = min(prices), max(prices)
    span = max(high - low, 1)
    points = []
    for index, price in enumerate(prices):
        x = padding if len(prices) == 1 else padding + index * (width - 2 * padding) / (len(prices) - 1)
        y = height / 2 if high == low else height - padding - (price - low) * (height - 2 * padding) / span
        points.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="price trend">'
        f'<polyline points="{" ".join(points)}" fill="none" stroke="#9a4e10" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{points[-1].split(",")[0]}" cy="{points[-1].split(",")[1]}" r="3" fill="#9a4e10"/>'
        "</svg>"
    )


def json_for_script(value: object) -> str:
    """Serialize report data without allowing a snapshot field to end a script tag."""
    return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")


def generate_report(
    floorplan_file: Path,
    unit_file: Path,
    output_file: Path,
) -> None:
    floorplan_history = read_rows(floorplan_file)
    unit_history = read_rows(unit_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    history_by_floorplan: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in floorplan_history:
        history_by_floorplan[(row["apartment"], row["floorplan"])].append(row)

    floorplan_rows = []
    current_floorplans = current_snapshot_rows(floorplan_history)
    current_floorplan_keys = {(row["apartment"], row["floorplan"]) for row in current_floorplans}
    for key in sorted(current_floorplan_keys):
        rows = sorted(history_by_floorplan[key], key=lambda row: row["timestamp"])
        prices = [price_to_cents(row["price"]) for row in rows]
        latest = rows[-1]
        change = prices[-1] - prices[0]
        change_label = "No history yet" if len(prices) == 1 else f"{format_cents(change, include_sign=True)} since first snapshot"
        floorplan_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(latest['floorplan'])}</strong></td>"
            f"<td>{html.escape(latest['sqft']) or '-'}</td>"
            f"<td>{html.escape(latest['move_in'])}</td>"
            f"<td><strong>{html.escape(latest['price'])}</strong><br><small>{change_label}</small></td>"
            f"<td>{sparkline(prices)}</td>"
            "</tr>"
        )

    grouped_units = group_unit_history(unit_history)
    unit_histories = unit_history_data(
        grouped_units, apartment_snapshot_times(floorplan_history, unit_history)
    )
    summary_by_key = {history["key"]: history for history in unit_histories}
    current_units = sorted(
        current_snapshot_rows(unit_history),
        key=lambda row: (row["apartment"], row["floorplan"], row["unit_id"]),
    )
    unit_rows = []
    for row in current_units:
        summary = summary_by_key["\u001f".join(unit_identity(row))]
        change = summary["change_cents"]
        change_label = "No history yet" if summary["snapshot_count"] == 1 else f"{format_cents(int(change), include_sign=True)}"
        unit_rows.append(
            "<tr>"
            f"<td>{html.escape(row['floorplan'])}</td>"
            f"<td><strong>{html.escape(row['unit_id'])}</strong></td>"
            f"<td>{html.escape(row['move_in'])}</td>"
            f"<td data-sort=\"{price_to_cents(row['price'])}\"><strong>{html.escape(row['price'])}</strong></td>"
            f"<td data-sort=\"{int(change or 0)}\">{change_label}</td>"
            "</tr>"
        )
    latest_timestamp = max((row["timestamp"] for row in floorplan_history), default="No snapshots yet")
    history_json = json_for_script(unit_histories)

    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Building Price Tracker</title>
<style>
  :root {{ --ink:#282629; --muted:#706d6d; --paper:#faf8f5; --line:#ded8cf; --accent:#9a4e10; --good:#28724d; --bad:#a2382d; }}
  * {{ box-sizing:border-box; }} body {{ margin:0; color:var(--ink); background:linear-gradient(125deg,#f1ede6,#fff 40%,#eee6db); font-family:Georgia,serif; }}
  main {{ max-width:1120px; margin:0 auto; padding:56px 24px 80px; }} h1 {{ margin:0; font-size:clamp(2.5rem,7vw,5rem); font-weight:400; letter-spacing:-.05em; }}
  .eyebrow, small {{ color:var(--muted); font-family:ui-monospace,monospace; font-size:.72rem; letter-spacing:.06em; text-transform:uppercase; }}
  .summary {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:32px 0 48px; }} .metric {{ background:rgba(255,255,255,.67); border:1px solid var(--line); padding:18px; }}
  .metric strong {{ display:block; margin-top:7px; font-size:1.65rem; font-weight:400; }} section {{ margin-top:42px; }} h2 {{ font-size:1.75rem; font-weight:400; margin:0 0 13px; }}
  .panel {{ background:rgba(255,255,255,.78); border:1px solid var(--line); overflow:auto; }} table {{ width:100%; border-collapse:collapse; min-width:720px; font-family:ui-monospace,monospace; font-size:.84rem; }}
  th,td {{ padding:15px 17px; border-bottom:1px solid var(--line); text-align:left; vertical-align:middle; }} th {{ color:var(--muted); font-size:.7rem; letter-spacing:.08em; text-transform:uppercase; }} th button {{ appearance:none; border:0; background:transparent; color:inherit; cursor:pointer; font:inherit; letter-spacing:inherit; padding:0; text-transform:inherit; }} tr:last-child td {{ border-bottom:0; }} svg {{ width:180px; height:48px; display:block; }}
  .controls {{ display:flex; flex-wrap:wrap; gap:12px; margin:0 0 13px; }} label {{ display:grid; gap:5px; color:var(--muted); font-family:ui-monospace,monospace; font-size:.72rem; letter-spacing:.06em; text-transform:uppercase; }} select {{ min-width:220px; border:1px solid var(--line); border-radius:0; background:#fff; color:var(--ink); font:inherit; padding:10px; }}
  .unit-grid {{ display:grid; grid-template-columns:minmax(270px,.85fr) minmax(360px,1.4fr); gap:12px; }} .unit-summary {{ padding:20px; }} .unit-summary dl {{ display:grid; grid-template-columns:1fr auto; gap:11px 20px; margin:0; font-family:ui-monospace,monospace; font-size:.82rem; }} .unit-summary dt {{ color:var(--muted); }} .unit-summary dd {{ margin:0; text-align:right; }} .chart-wrap {{ min-height:250px; padding:20px; }} #unit-chart {{ width:100%; height:210px; }} .chart-label {{ fill:var(--muted); font:11px ui-monospace,monospace; }} .chart-line {{ fill:none; stroke:var(--accent); stroke-width:3; stroke-linecap:round; stroke-linejoin:round; }} .chart-dot {{ fill:var(--accent); }} .empty {{ color:var(--muted); font-family:ui-monospace,monospace; font-size:.88rem; }}
  @media (max-width:760px) {{ main {{ padding:36px 14px; }} .summary,.unit-grid {{ grid-template-columns:1fr; }} select {{ min-width:0; width:100%; }} }}
</style></head><body><main>
  <p class="eyebrow">Anonymous building / price history</p>
  <h1>Apartment Tracker</h1>
  <p>Last floor-plan snapshot: <strong>{html.escape(latest_timestamp)}</strong></p>
  <div class="summary"><div class="metric"><span class="eyebrow">Floor plans</span><strong>{len(floorplan_rows)}</strong></div><div class="metric"><span class="eyebrow">Available units</span><strong>{len(unit_rows)}</strong></div><div class="metric"><span class="eyebrow">Floor-plan snapshots</span><strong>{len(floorplan_history)}</strong></div></div>
  <section><h2>Floor Plan Trends</h2><div class="panel"><table><thead><tr><th>Floor plan</th><th>Sq. ft.</th><th>Available</th><th>Current rent</th><th>Trend</th></tr></thead><tbody>{''.join(floorplan_rows) or '<tr><td colspan="5">Run the tracker to create the first snapshot.</td></tr>'}</tbody></table></div></section>
  <section><h2>Unit Price History</h2><p class="empty" id="unit-empty" hidden>No retained unit history is available yet.</p><div id="unit-explorer"><div class="controls"><label>Floor plan<select id="floorplan-filter"></select></label><label>Unit<select id="unit-selector"></select></label></div><div class="unit-grid"><div class="panel unit-summary" id="unit-summary"></div><div class="panel chart-wrap" id="unit-chart-wrap"><svg id="unit-chart" viewBox="0 0 620 210" role="img" aria-label="Selected unit price history"></svg><p class="empty" id="chart-empty" hidden>A single observation cannot show a price trend yet.</p></div></div></div></section>
  <section><h2>Current Unit Availability</h2><div class="panel"><table id="current-units"><thead><tr><th><button data-column="0">Floor plan</button></th><th><button data-column="1">Unit</button></th><th><button data-column="2">Available</button></th><th><button data-column="3">Current rent</button></th><th><button data-column="4">Change since first</button></th></tr></thead><tbody>{''.join(unit_rows) or '<tr><td colspan="5">Unit details will appear after the next successful scrape.</td></tr>'}</tbody></table></div></section>
</main><script id="unit-history-data" type="application/json">{history_json}</script>
<script>
(() => {{
  const histories = JSON.parse(document.getElementById('unit-history-data').textContent);
  const explorer = document.getElementById('unit-explorer');
  const empty = document.getElementById('unit-empty');
  const floorplan = document.getElementById('floorplan-filter');
  const selector = document.getElementById('unit-selector');
  const summary = document.getElementById('unit-summary');
  const chart = document.getElementById('unit-chart');
  const chartEmpty = document.getElementById('chart-empty');
  const money = value => new Intl.NumberFormat('en-US', {{style:'currency', currency:'USD', maximumFractionDigits:0}}).format(value / 100);
  const date = value => new Date(value).toLocaleDateString(undefined, {{year:'numeric', month:'short', day:'numeric'}});
  const escaped = value => String(value).replace(/[&<>"']/g, char => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[char]));
  function options() {{
    const plans = [...new Set(histories.map(item => item.floorplan))].sort();
    floorplan.innerHTML = '<option value="">All floor plans</option>' + plans.map(plan => `<option value="${{escaped(plan)}}">${{escaped(plan)}}</option>`).join('');
    chooseUnits();
  }}
  function chooseUnits() {{
    const selected = histories.filter(item => !floorplan.value || item.floorplan === floorplan.value);
    selector.innerHTML = selected.map(item => `<option value="${{escaped(item.key)}}">${{escaped(item.floorplan)}} · ${{escaped(item.unit_id)}}</option>`).join('');
    render();
  }}
  function render() {{
    const item = histories.find(history => history.key === selector.value);
    if (!item) return;
    const change = item.change_cents;
    const percent = item.change_percent === null ? '—' : `${{change > 0 ? '+' : ''}}${{item.change_percent.toFixed(1)}}%`;
    summary.innerHTML = `<dl><dt>First observed</dt><dd>${{date(item.first_timestamp)}}</dd><dt>Latest observed</dt><dd>${{date(item.latest_timestamp)}}</dd><dt>First advertised rent</dt><dd>${{money(item.first_price)}}</dd><dt>Latest advertised rent</dt><dd>${{money(item.latest_price)}}</dd><dt>Change</dt><dd>${{change === 0 ? money(change) : (change > 0 ? '+' : '') + money(change)}} (${{percent}})</dd><dt>Visible snapshots</dt><dd>${{item.snapshot_count}}</dd></dl>`;
    chart.replaceChildren();
    chartEmpty.hidden = item.points.length > 1;
    if (item.points.length < 2) return;
    const width = 620, height = 210, left = 54, right = 18, top = 18, bottom = 36;
    const prices = item.points.map(point => point.price), low = Math.min(...prices), high = Math.max(...prices), span = Math.max(high-low, 1);
    const x = index => left + index * (width-left-right) / Math.max(item.points.length-1, 1);
    const y = price => high === low ? (top + height-bottom) / 2 : height-bottom - (price-low) * (height-top-bottom) / span;
    const ns = 'http://www.w3.org/2000/svg';
    const make = (name, attrs) => {{ const node = document.createElementNS(ns, name); Object.entries(attrs).forEach(([key,value]) => node.setAttribute(key,value)); return node; }};
    chart.append(make('line', {{x1:left,y1:height-bottom,x2:width-right,y2:height-bottom,stroke:'#ded8cf'}}));
    chart.append(make('text', {{x:left,y:height-12,class:'chart-label'}})).textContent = date(item.points[0].timestamp);
    chart.append(make('text', {{x:width-right,y:height-12,class:'chart-label','text-anchor':'end'}})).textContent = date(item.points.at(-1).timestamp);
    chart.append(make('text', {{x:4,y:top+8,class:'chart-label'}})).textContent = money(high);
    chart.append(make('text', {{x:4,y:height-bottom,class:'chart-label'}})).textContent = money(low);
    let segment = [];
    item.points.forEach((point, index) => {{
      if (point.gap_before && segment.length) {{ chart.append(make('polyline', {{points:segment.join(' '),class:'chart-line'}})); segment = []; }}
      segment.push(`${{x(index).toFixed(1)}},${{y(point.price).toFixed(1)}}`);
      chart.append(make('circle', {{cx:x(index),cy:y(point.price),r:4,class:'chart-dot'}}));
    }});
    if (segment.length) chart.append(make('polyline', {{points:segment.join(' '),class:'chart-line'}}));
  }}
  floorplan.addEventListener('change', chooseUnits); selector.addEventListener('change', render);
  if (!histories.length) {{ explorer.hidden = true; empty.hidden = false; }} else options();
  const table = document.querySelector('#current-units tbody'); let ascending = true;
  document.querySelectorAll('#current-units th button').forEach(button => button.addEventListener('click', () => {{
    const column = Number(button.dataset.column); const rows = [...table.rows];
    rows.sort((a,b) => {{ const av = a.cells[column].dataset.sort || a.cells[column].textContent.trim(); const bv = b.cells[column].dataset.sort || b.cells[column].textContent.trim(); return (Number(av) - Number(bv) || String(av).localeCompare(String(bv))) * (ascending ? 1 : -1); }});
    rows.forEach(row => table.append(row)); ascending = !ascending;
  }}));
}})();
</script></body></html>"""
    output_file.write_text(document, encoding="utf-8")
