"""Track advertised floor-plan and unit prices for Veris Residential properties."""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from g import update_google_sheet
from report import generate_report

APARTMENTS = {"Building A": "private-page-id"}
AJAX_URL = "https://verisresidential.com/wp-admin/admin-ajax.php"
USER_AGENT = "BuildingPriceTracker/1.0 (+https://github.com/account/tracker)"
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
REPORT_FILE = Path("data/report.html")
PRICE_PATTERN = re.compile(r"^\$?[\d,]+(?:\.\d{1,2})?$")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def price_to_cents(price: str) -> int:
    """Convert a displayed dollar amount into cents for reliable comparisons."""
    digits = price.replace("$", "").replace(",", "")
    return int(round(float(digits) * 100))


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
    return (
        [{field: floorplan[field] for field in CSV_FIELDS} for floorplan in floorplans],
        unit_details,
    )


def scrape_all() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print results without saving them.")
    parser.add_argument("--no-sheets", action="store_true", help="Skip the optional Google Sheets export.")
    parser.add_argument("--report-only", action="store_true", help="Rebuild the local report without scraping.")
    args = parser.parse_args()
    if args.report_only:
        generate_report(CSV_FILE, UNIT_CSV_FILE, REPORT_FILE)
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

    save_rows_csv(units, CSV_FILE, CSV_FIELDS)
    save_rows_csv(unit_details, UNIT_CSV_FILE, UNIT_CSV_FIELDS)
    generate_report(CSV_FILE, UNIT_CSV_FILE, REPORT_FILE)
    print(f"[INFO] Updated local report at {REPORT_FILE}.")
    if not args.no_sheets:
        update_google_sheet(units)


if __name__ == "__main__":
    main()
