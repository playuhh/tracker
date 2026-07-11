"""Generate a self-contained local price-trend report from tracker snapshots."""

from __future__ import annotations

import csv
import html
from collections import defaultdict
from pathlib import Path
from typing import Iterable


def read_rows(filename: Path) -> list[dict[str, str]]:
    if not filename.exists():
        return []
    with filename.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def price_to_cents(price: str) -> int:
    return int(round(float(price.replace("$", "").replace(",", "")) * 100))


def current_snapshot_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Return rows from the most recent scrape for each apartment."""
    rows = list(rows)
    latest_by_apartment: dict[str, str] = {}
    for row in rows:
        apartment = row["apartment"]
        if apartment not in latest_by_apartment or row["timestamp"] > latest_by_apartment[apartment]:
            latest_by_apartment[apartment] = row["timestamp"]
    return [row for row in rows if row["timestamp"] == latest_by_apartment[row["apartment"]]]


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
    current_floorplan_keys = {
        (row["apartment"], row["floorplan"]) for row in current_floorplans
    }
    for key in sorted(current_floorplan_keys):
        rows = history_by_floorplan[key]
        rows.sort(key=lambda row: row["timestamp"])
        prices = [price_to_cents(row["price"]) for row in rows]
        latest = rows[-1]
        change = prices[-1] - prices[0]
        change_label = "No history yet" if len(prices) == 1 else f"${change / 100:+,.0f} since first snapshot"
        floorplan_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(latest['floorplan'])}</strong></td>"
            f"<td>{html.escape(latest['sqft']) or '-'}</td>"
            f"<td>{html.escape(latest['move_in'])}</td>"
            f"<td><strong>{html.escape(latest['price'])}</strong><br><small>{change_label}</small></td>"
            f"<td>{sparkline(prices)}</td>"
            "</tr>"
        )

    current_units = sorted(
        current_snapshot_rows(unit_history),
        key=lambda row: (row["apartment"], row["floorplan"], row["unit_id"]),
    )
    unit_rows = [
        "<tr>"
        f"<td>{html.escape(row['floorplan'])}</td>"
        f"<td><strong>{html.escape(row['unit_id'])}</strong></td>"
        f"<td>{html.escape(row['move_in'])}</td>"
        f"<td><strong>{html.escape(row['price'])}</strong></td>"
        "</tr>"
        for row in current_units
    ]
    latest_timestamp = max((row["timestamp"] for row in floorplan_history), default="No snapshots yet")

    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Building Price Tracker</title>
<style>
  :root {{ --ink:#282629; --muted:#706d6d; --paper:#faf8f5; --line:#ded8cf; --accent:#9a4e10; }}
  * {{ box-sizing:border-box; }} body {{ margin:0; color:var(--ink); background:linear-gradient(125deg,#f1ede6,#fff 40%,#eee6db); font-family:Georgia,serif; }}
  main {{ max-width:1120px; margin:0 auto; padding:56px 24px 80px; }} h1 {{ margin:0; font-size:clamp(2.5rem,7vw,5rem); font-weight:400; letter-spacing:-.05em; }}
  .eyebrow, small {{ color:var(--muted); font-family:ui-monospace,monospace; font-size:.72rem; letter-spacing:.06em; text-transform:uppercase; }}
  .summary {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:32px 0 48px; }} .metric {{ background:rgba(255,255,255,.67); border:1px solid var(--line); padding:18px; }}
  .metric strong {{ display:block; margin-top:7px; font-size:1.65rem; font-weight:400; }} section {{ margin-top:42px; }} h2 {{ font-size:1.75rem; font-weight:400; margin:0 0 13px; }}
  .panel {{ background:rgba(255,255,255,.78); border:1px solid var(--line); overflow:auto; }} table {{ width:100%; border-collapse:collapse; min-width:720px; font-family:ui-monospace,monospace; font-size:.84rem; }}
  th,td {{ padding:15px 17px; border-bottom:1px solid var(--line); text-align:left; vertical-align:middle; }} th {{ color:var(--muted); font-size:.7rem; letter-spacing:.08em; text-transform:uppercase; }} tr:last-child td {{ border-bottom:0; }} svg {{ width:180px; height:48px; display:block; }}
  @media (max-width:620px) {{ main {{ padding:36px 14px; }} .summary {{ grid-template-columns:1fr; }} }}
</style></head><body><main>
  <p class="eyebrow">Anonymous building / price history</p>
  <h1>Apartment Tracker</h1>
  <p>Last floor-plan snapshot: <strong>{html.escape(latest_timestamp)}</strong></p>
  <div class="summary"><div class="metric"><span class="eyebrow">Floor plans</span><strong>{len(floorplan_rows)}</strong></div><div class="metric"><span class="eyebrow">Available units</span><strong>{len(unit_rows)}</strong></div><div class="metric"><span class="eyebrow">Snapshots</span><strong>{len(floorplan_history)}</strong></div></div>
  <section><h2>Floor Plan Trends</h2><div class="panel"><table><thead><tr><th>Floor plan</th><th>Sq. ft.</th><th>Available</th><th>Current rent</th><th>Trend</th></tr></thead><tbody>{''.join(floorplan_rows) or '<tr><td colspan="5">Run the tracker to create the first snapshot.</td></tr>'}</tbody></table></div></section>
  <section><h2>Current Unit Availability</h2><div class="panel"><table><thead><tr><th>Floor plan</th><th>Unit</th><th>Available</th><th>Rent</th></tr></thead><tbody>{''.join(unit_rows) or '<tr><td colspan="4">Unit details will appear after the next successful scrape.</td></tr>'}</tbody></table></div></section>
</main></body></html>"""
    output_file.write_text(document, encoding="utf-8")
