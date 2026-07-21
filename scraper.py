"""Track advertised floor-plan and unit prices for Veris Residential properties."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from g import update_google_sheet
from report import generate_report

BUILDING_PAGE_ID = os.environ.get("RENTAL_BUILDING_PAGE_ID", "")
APARTMENTS = {"Building A": BUILDING_PAGE_ID} if BUILDING_PAGE_ID else {}
AJAX_URL = "https://verisresidential.com/wp-admin/admin-ajax.php"
USER_AGENT = "RentalMarketTracker/1.0"
REQUEST_TIMEOUT_SECONDS = 15
REQUEST_ATTEMPTS = 3

CSV_FILE = Path("data/unit_prices.csv")
CSV_FIELDS = ["timestamp", "apartment", "floorplan", "sqft", "move_in", "price"]
UNIT_CSV_FILE = Path("data/unit_snapshots.csv")
UNIT_CSV_FIELDS = [
    "timestamp",
    "apartment",
    "floorplan",
    "floorplan_id",
    "unit_id",
    "sqft",
    "move_in",
    "price",
]
FLOORPLAN_DAILY_FILE = Path("data/floorplan_daily.csv")
FLOORPLAN_DAILY_FIELDS = [
    "timestamp",
    "apartment",
    "floorplan",
    "floorplan_id",
    "sqft",
    "visible_units",
    "min_rent",
    "median_rent",
    "max_rent",
    "min_rent_per_sqft",
    "median_rent_per_sqft",
    "max_rent_per_sqft",
    "newly_visible_units",
    "price_reductions",
    "earliest_move_in",
]
SCRAPE_RUNS_FILE = Path("data/scrape_runs.csv")
SCRAPE_RUNS_FIELDS = ["timestamp", "apartment", "status", "floorplan_count", "unit_count"]
UNIT_TRAITS_FILE = Path("data/unit_traits.csv")
REPORT_FILE = Path("data/report.html")
PRICE_PATTERN = re.compile(r"^\$?[\d,]+(?:\.\d{1,2})?$")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def price_to_cents(price: str) -> int:
    """Convert a displayed dollar amount into cents for reliable comparisons."""
    digits = price.replace("$", "").replace(",", "")
    return int(round(float(digits) * 100))


def opaque_label(kind: str, source_value: str) -> str:
    """Return a stable public label without publishing an inventory identifier."""
    digest = hashlib.sha256(source_value.encode("utf-8")).hexdigest()[:8]
    return f"{kind}-{digest}"


def anonymize_snapshot_rows(
    floorplans: Iterable[dict[str, str]], unit_details: Iterable[dict[str, str]]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Remove property, layout, and listing labels before persistence or publication."""
    floorplans = [dict(row) for row in floorplans]
    unit_details = [dict(row) for row in unit_details]
    layout_map: dict[str, tuple[str, str]] = {}
    for row in floorplans:
        source_id, source_name = row["floorplan_id"], row["floorplan"]
        layout_map[source_id] = (opaque_label("layout", source_name), opaque_label("layout-id", source_id))
        row["apartment"] = "Building A"
        row["floorplan"], row["floorplan_id"] = layout_map[source_id]
    for row in unit_details:
        source_id, source_name = row["floorplan_id"], row["floorplan"]
        layout = layout_map.get(
            source_id, (opaque_label("layout", source_name), opaque_label("layout-id", source_id))
        )
        row["apartment"] = "Building A"
        row["floorplan"], row["floorplan_id"] = layout
        row["unit_id"] = opaque_label("listing", row["unit_id"])
    return floorplans, unit_details


def format_price(value: Any) -> str:
    """Return the report's dollar display format from an endpoint price value."""
    text = str(value).strip()
    if not PRICE_PATTERN.fullmatch(text):
        raise ValueError(f"Invalid advertised rent: {value!r}")
    cents = price_to_cents(text)
    return f"${cents / 100:,.0f}" if cents % 100 == 0 else f"${cents / 100:,.2f}"


def format_availability_date(value: Any, today: date | None = None) -> str:
    """Render past availability as Immediate and future dates as e.g. ``Jul 20``."""
    text = str(value).strip()
    if not text:
        raise ValueError("Unit availability date is missing")
    parsed: date | None = None
    for pattern in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, pattern).date()
            break
        except ValueError:
            pass
    if parsed is None:
        raise ValueError(f"Invalid availability date: {value!r}")
    if parsed <= (today or date.today()):
        return "Immediate"
    return f"{parsed.strftime('%b')} {parsed.day}"


def overview_payload(page_id: str) -> dict[str, Any]:
    """Build the smallest result structure the public overview endpoint accepts."""
    fields = [
        "floorplan_name",
        "omg_feeds_floorplan_id",
        "rent_formatted",
        "rent_from_price",
        "move_in_date",
        "date_formatted",
        "sqft_commas",
    ]
    return {
        "index_table": "omg_apt_idx",
        "default_order": [{"order_column": "apt_id", "order_direction": "desc"}],
        "environment": {"page_id": page_id, "custom_post_type": "property_id"},
        "facets": [],
        "group_by": "omg_feeds_floorplan_id",
        "result_structures": {
            "collector": {
                "container": {"classes": ""},
                "card": {
                    "header": {"items": []},
                    "body": {"items": [{"column": field} for field in fields]},
                    "footer": {"items": []},
                },
            }
        },
        "results_per_page": 999,
        "current_page": 0,
        "available_results": [],
        "subquery": False,
        "stored_items_ids": [],
        "query_count": 0,
    }


def post_json(form: Mapping[str, str]) -> dict[str, Any]:
    """POST an AJAX form with bounded retries and return its JSON object response."""
    encoded = urlencode(form).encode("utf-8")
    request = Request(
        AJAX_URL,
        data=encoded,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(REQUEST_ATTEMPTS):
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Inventory endpoint returned a non-object JSON response")
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt + 1 < REQUEST_ATTEMPTS:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(
        f"Inventory request failed after {REQUEST_ATTEMPTS} attempts: {last_error}"
    ) from last_error


def required_text(row: Mapping[str, Any], field: str, context: str) -> str:
    value = row.get(field)
    if value is None or not str(value).strip():
        raise RuntimeError(f"{context} is missing required field {field!r}")
    return str(value).strip()


def parse_overview_response(
    response: Mapping[str, Any], apartment: str, timestamp: str
) -> tuple[list[dict[str, str]], int]:
    """Map overview data to the established floor-plan CSV schema."""
    raw_rows = response.get("apts_result")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise RuntimeError("Overview response contains no floor plans")
    try:
        expected_count = int(str(response["apt_count"]))
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Overview response has an invalid apt_count") from error
    if expected_count <= 0:
        raise RuntimeError("Overview response reports no available units")

    floorplans: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for row in raw_rows:
        if not isinstance(row, Mapping):
            raise RuntimeError("Overview response contains an invalid floor-plan row")
        floorplan_id = required_text(row, "omg_feeds_floorplan_id", "Overview floor plan")
        if floorplan_id in seen_ids:
            raise RuntimeError(f"Overview response repeats floor-plan id {floorplan_id}")
        seen_ids.add(floorplan_id)
        move_in = required_text(row, "date_formatted", f"Overview floor plan {floorplan_id}")
        floorplans.append(
            {
                "timestamp": timestamp,
                "apartment": apartment,
                "floorplan": required_text(row, "floorplan_name", "Overview floor plan"),
                "floorplan_id": floorplan_id,
                "sqft": required_text(row, "sqft_commas", f"Overview floor plan {floorplan_id}"),
                "move_in": "Immediate" if move_in.casefold() == "now" else move_in,
                "price": format_price(
                    required_text(row, "rent_from_price", f"Overview floor plan {floorplan_id}")
                ),
            }
        )
    return floorplans, expected_count


def parse_floorplan_response(
    response: Mapping[str, Any], floorplan: Mapping[str, str], apartment: str, timestamp: str,
    today: date | None = None,
) -> list[dict[str, str]]:
    """Map a floor-plan detail response to the established unit CSV schema."""
    raw_rows = response.get("query_response")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise RuntimeError(f"Floor plan {floorplan['floorplan']} has no individual unit records")

    units: list[dict[str, str]] = []
    seen_units: set[str] = set()
    expected_id = floorplan["floorplan_id"]
    for row in raw_rows:
        if not isinstance(row, Mapping):
            raise RuntimeError(f"Floor plan {floorplan['floorplan']} has an invalid unit record")
        actual_id = required_text(row, "omg_feeds_floorplan_id", "Unit record")
        if actual_id != expected_id:
            raise RuntimeError(
                f"Unit record belongs to floor-plan id {actual_id}, expected {expected_id}"
            )
        unit_id = required_text(row, "the_title", f"Floor plan {floorplan['floorplan']}")
        if unit_id in seen_units:
            raise RuntimeError(f"Floor plan {floorplan['floorplan']} repeats unit {unit_id}")
        seen_units.add(unit_id)
        units.append(
            {
                "timestamp": timestamp,
                "apartment": apartment,
                "floorplan": floorplan["floorplan"],
                "floorplan_id": expected_id,
                "unit_id": unit_id,
                "sqft": required_text(
                    row, "omg_feeds_apartment_squarefootage", f"Unit {unit_id}"
                ),
                "move_in": format_availability_date(
                    required_text(row, "ra_date_available", f"Unit {unit_id}"), today
                ),
                "price": format_price(required_text(row, "ra_rent", f"Unit {unit_id}")),
            }
        )
    return units


PostJSON = Callable[[Mapping[str, str]], dict[str, Any]]


def scrape_apartment(
    apartment: str, page_id: str, post: PostJSON = post_json
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Collect one overview plus one public detail request per floor plan."""
    print(f"[INFO] Loading {apartment}...")
    timestamp = utc_timestamp()
    overview = post({"action": "omg_apt_search_main_query", "payload": json.dumps(overview_payload(page_id))})
    floorplans, expected_unit_count = parse_overview_response(overview, apartment, timestamp)
    unit_details: list[dict[str, str]] = []
    for floorplan in floorplans:
        response = post({"action": "floorplan_query", "id": floorplan["floorplan_id"]})
        unit_details.extend(parse_floorplan_response(response, floorplan, apartment, timestamp))
    if len(unit_details) != expected_unit_count:
        raise RuntimeError(
            f"Overview reports {expected_unit_count} available units, but detail requests returned "
            f"{len(unit_details)}"
        )
    print(
        f"[INFO] Found {len(floorplans)} floor plans and {len(unit_details)} individual units "
        f"for {apartment}."
    )
    floorplans, unit_details = anonymize_snapshot_rows(floorplans, unit_details)
    return (
        [{field: floorplan[field] for field in CSV_FIELDS} for floorplan in floorplans],
        unit_details,
    )


def scrape_all() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if not APARTMENTS:
        raise RuntimeError("Set RENTAL_BUILDING_PAGE_ID before collecting inventory")
    units: list[dict[str, str]] = []
    unit_details: list[dict[str, str]] = []
    for apartment, page_id in APARTMENTS.items():
        apartment_units, apartment_unit_details = scrape_apartment(apartment, page_id)
        units.extend(apartment_units)
        unit_details.extend(apartment_unit_details)
    return units, unit_details


def load_latest_prices(filename: Path) -> dict[tuple[str, str], dict[str, str]]:
    if not filename.exists():
        return {}
    latest: dict[tuple[str, str], dict[str, str]] = {}
    with filename.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            latest[(row["apartment"], row["floorplan"])] = row
    return latest


def read_rows_csv(filename: Path) -> list[dict[str, str]]:
    """Read append-only history, returning no rows before its first run."""
    if not filename.exists():
        return []
    with filename.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def describe_price_changes(
    previous: dict[tuple[str, str], dict[str, str]], units: Iterable[dict[str, str]]
) -> list[str]:
    changes: list[str] = []
    for unit in units:
        key = (unit["apartment"], unit["floorplan"])
        old = previous.get(key)
        if not old:
            if previous:
                changes.append(f"{unit['apartment']} {unit['floorplan']}: newly available at {unit['price']}")
            continue
        if old["price"] == unit["price"]:
            continue
        difference = price_to_cents(unit["price"]) - price_to_cents(old["price"])
        direction = "up" if difference > 0 else "down"
        changes.append(
            f"{unit['apartment']} {unit['floorplan']}: {old['price']} -> {unit['price']} "
            f"({direction} ${abs(difference) / 100:,.0f})"
        )
    return changes


def parse_sqft(value: str) -> int | None:
    """Return a positive square-footage value, if the public response supplies one."""
    try:
        sqft = int(value.replace(",", "").strip())
    except (AttributeError, ValueError):
        return None
    return sqft if sqft > 0 else None


def median_cents(values: Iterable[int]) -> int:
    """Return the integer-cent median without introducing floating-point money values."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("Cannot calculate a median from no values")
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) // 2


def format_cents(cents: int) -> str:
    return f"${cents / 100:,.0f}" if cents % 100 == 0 else f"${cents / 100:,.2f}"


def format_rent_per_sqft(cents_per_sqft: int | None) -> str:
    return "" if cents_per_sqft is None else f"${cents_per_sqft / 100:,.2f}"


def move_in_sort_key(value: str) -> tuple[int, str]:
    """Sort Immediate before dated availability, retaining unparseable values safely."""
    if value.casefold() in {"immediate", "now"}:
        return (0, "")
    for pattern in ("%b %d", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, pattern)
            return (1, parsed.strftime("%m%d"))
        except ValueError:
            pass
    return (2, value)


def latest_snapshot_rows_before(
    rows: Iterable[dict[str, str]], apartment: str, timestamp: str
) -> list[dict[str, str]]:
    """Return the latest known unit snapshot before a run for one apartment."""
    candidates = [row for row in rows if row["apartment"] == apartment and row["timestamp"] < timestamp]
    if not candidates:
        return []
    latest = max(row["timestamp"] for row in candidates)
    return [row for row in candidates if row["timestamp"] == latest]


def floorplan_daily_rows(
    current_units: Iterable[dict[str, str]], previous_units: Iterable[dict[str, str]]
) -> list[dict[str, str]]:
    """Aggregate one complete unit snapshot into renter-facing floor-plan metrics.

    ``previous_units`` is append-only unit history from before this complete
    run.  Missing units are deliberately not converted to lease events.
    """
    current_units = list(current_units)
    previous_units = list(previous_units)
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for unit in current_units:
        groups[(unit["apartment"], unit["floorplan_id"])].append(unit)

    aggregates: list[dict[str, str]] = []
    for (apartment, floorplan_id), units in sorted(groups.items()):
        timestamp = units[0]["timestamp"]
        previous_snapshot = latest_snapshot_rows_before(previous_units, apartment, timestamp)
        previous_by_identity: dict[tuple[str, str, str], dict[str, str]] = {
            (row["apartment"], row["floorplan_id"], row["unit_id"]): row
            for row in previous_snapshot
        }
        latest_observation_by_identity: dict[tuple[str, str, str], dict[str, str]] = {}
        for row in previous_units:
            identity = (row["apartment"], row["floorplan_id"], row["unit_id"])
            if row["apartment"] != apartment or row["timestamp"] >= timestamp:
                continue
            if identity not in latest_observation_by_identity or (
                row["timestamp"] > latest_observation_by_identity[identity]["timestamp"]
            ):
                latest_observation_by_identity[identity] = row
        rents = [price_to_cents(unit["price"]) for unit in units]
        rent_per_sqft = [
            (price_to_cents(unit["price"]) + sqft // 2) // sqft
            for unit in units
            if (sqft := parse_sqft(unit["sqft"])) is not None
        ]
        newly_visible = 0
        reductions = 0
        for unit in units:
            previous = previous_by_identity.get(
                (unit["apartment"], unit["floorplan_id"], unit["unit_id"])
            )
            if previous is None:
                newly_visible += 1
            prior_observation = latest_observation_by_identity.get(
                (unit["apartment"], unit["floorplan_id"], unit["unit_id"])
            )
            if prior_observation and price_to_cents(unit["price"]) < price_to_cents(prior_observation["price"]):
                reductions += 1
        aggregates.append(
            {
                "timestamp": timestamp,
                "apartment": apartment,
                "floorplan": units[0]["floorplan"],
                "floorplan_id": floorplan_id,
                "sqft": units[0]["sqft"],
                "visible_units": str(len(units)),
                "min_rent": format_cents(min(rents)),
                "median_rent": format_cents(median_cents(rents)),
                "max_rent": format_cents(max(rents)),
                "min_rent_per_sqft": format_rent_per_sqft(min(rent_per_sqft)) if rent_per_sqft else "",
                "median_rent_per_sqft": format_rent_per_sqft(median_cents(rent_per_sqft)) if rent_per_sqft else "",
                "max_rent_per_sqft": format_rent_per_sqft(max(rent_per_sqft)) if rent_per_sqft else "",
                "newly_visible_units": str(newly_visible),
                "price_reductions": str(reductions),
                "earliest_move_in": min((unit["move_in"] for unit in units), key=move_in_sort_key),
            }
        )
    return aggregates


def scrape_run_rows(
    floorplans: Iterable[dict[str, str]], unit_details: Iterable[dict[str, str]]
) -> list[dict[str, str]]:
    """Record coverage only after all floor-plan detail responses validated."""
    floorplans = list(floorplans)
    unit_details = list(unit_details)
    by_apartment: dict[str, dict[str, str | int]] = {}
    for row in floorplans:
        record = by_apartment.setdefault(
            row["apartment"],
            {"timestamp": row["timestamp"], "floorplan_count": 0, "unit_count": 0},
        )
        record["floorplan_count"] = int(record["floorplan_count"]) + 1
    for row in unit_details:
        record = by_apartment.setdefault(
            row["apartment"],
            {"timestamp": row["timestamp"], "floorplan_count": 0, "unit_count": 0},
        )
        record["unit_count"] = int(record["unit_count"]) + 1
    return [
        {
            "timestamp": str(record["timestamp"]),
            "apartment": apartment,
            "status": "complete",
            "floorplan_count": str(record["floorplan_count"]),
            "unit_count": str(record["unit_count"]),
        }
        for apartment, record in sorted(by_apartment.items())
    ]


def save_rows_csv(rows: Iterable[dict[str, str]], filename: Path, fieldnames: list[str]) -> None:
    rows = list(rows)
    filename.parent.mkdir(parents=True, exist_ok=True)
    write_header = not filename.exists() or filename.stat().st_size == 0
    with filename.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    print(f"[INFO] Saved {len(rows)} snapshots to {filename}.")


def anonymize_history_file(filename: Path, fieldnames: list[str]) -> None:
    """Pseudonymize legacy snapshots in place before the repository is published."""
    rows = read_rows_csv(filename)
    if not rows:
        return
    for row in rows:
        if "apartment" in row:
            row["apartment"] = "Building A"
        if row.get("floorplan") and not row["floorplan"].startswith("layout-"):
            row["floorplan"] = opaque_label("layout", row["floorplan"])
        if row.get("floorplan_id") and not row["floorplan_id"].startswith("layout-id-"):
            row["floorplan_id"] = opaque_label("layout-id", row["floorplan_id"])
        if row.get("unit_id") and not row["unit_id"].startswith("listing-"):
            row["unit_id"] = opaque_label("listing", row["unit_id"])
    with filename.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"[INFO] Anonymized {len(rows)} rows in {filename}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print results without saving them.")
    parser.add_argument("--no-sheets", action="store_true", help="Skip the optional Google Sheets export.")
    parser.add_argument("--report-only", action="store_true", help="Rebuild the local report without scraping.")
    parser.add_argument("--anonymize-history", action="store_true", help="Pseudonymize retained history and rebuild the report.")
    args = parser.parse_args()
    if args.anonymize_history:
        anonymize_history_file(CSV_FILE, CSV_FIELDS)
        anonymize_history_file(UNIT_CSV_FILE, UNIT_CSV_FIELDS)
        anonymize_history_file(FLOORPLAN_DAILY_FILE, FLOORPLAN_DAILY_FIELDS)
        anonymize_history_file(SCRAPE_RUNS_FILE, SCRAPE_RUNS_FIELDS)
        generate_report(CSV_FILE, UNIT_CSV_FILE, REPORT_FILE, FLOORPLAN_DAILY_FILE, SCRAPE_RUNS_FILE, UNIT_TRAITS_FILE)
        print(f"[INFO] Updated anonymized report at {REPORT_FILE}.")
        return
    if args.report_only:
        generate_report(CSV_FILE, UNIT_CSV_FILE, REPORT_FILE, FLOORPLAN_DAILY_FILE, SCRAPE_RUNS_FILE, UNIT_TRAITS_FILE)
        print(f"[INFO] Updated local report at {REPORT_FILE}.")
        return

    previous = load_latest_prices(CSV_FILE)
    units, unit_details = scrape_all()
    changes = describe_price_changes(previous, units)
    if changes:
        print("[INFO] Price changes since the previous snapshot:")
        for change in changes:
            print(f"  - {change}")
    elif previous:
        print("[INFO] No price changes since the previous snapshot.")
    else:
        print("[INFO] First snapshot saved; future runs will report price changes.")
    if args.dry_run:
        return

    previous_unit_details = read_rows_csv(UNIT_CSV_FILE)
    daily_rows = floorplan_daily_rows(unit_details, previous_unit_details)
    runs = scrape_run_rows(units, unit_details)
    save_rows_csv(units, CSV_FILE, CSV_FIELDS)
    save_rows_csv(unit_details, UNIT_CSV_FILE, UNIT_CSV_FIELDS)
    save_rows_csv(daily_rows, FLOORPLAN_DAILY_FILE, FLOORPLAN_DAILY_FIELDS)
    save_rows_csv(runs, SCRAPE_RUNS_FILE, SCRAPE_RUNS_FIELDS)
    generate_report(CSV_FILE, UNIT_CSV_FILE, REPORT_FILE, FLOORPLAN_DAILY_FILE, SCRAPE_RUNS_FILE, UNIT_TRAITS_FILE)
    print(f"[INFO] Updated local report at {REPORT_FILE}.")
    if not args.no_sheets:
        update_google_sheet(units)


if __name__ == "__main__":
    main()
