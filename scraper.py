"""Track advertised floor-plan prices for Veris Residential properties."""

from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from g import update_google_sheet
from report import generate_report

APARTMENTS = {
    "Building A": "https://verisresidential.com/west-new-york-nj-apartments/the-capstone-at-port-imperial/",
}

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
PRICE_PATTERN = re.compile(r"\$[\d,]+(?:\.\d{2})?")
NEW_CARD_PATTERN = re.compile(
    r"Floor Plan\s*(?P<floorplan>.*?)\s*Bedrooms\s*(?P<bedrooms>.*?)"
    r"\s*Price\s*From\s*(?P<price>\$[\d,]+(?:\.\d{2})?)"
    r"\s*Available\s*(?P<move_in>.*?)(?:\s*(?:View Details|Contact)|$)",
    re.DOTALL,
)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact_text(value: str) -> str:
    return " ".join(value.split())


def price_to_cents(price: str) -> int:
    """Convert a displayed dollar amount into cents for reliable comparisons."""
    digits = price.replace("$", "").replace(",", "")
    return int(round(float(digits) * 100))


def parse_legacy_card(card: Any, apartment: str, timestamp: str) -> dict[str, str] | None:
    floorplan_node = card.query_selector(".prop-detail-floorplan-name")
    if not floorplan_node:
        return None

    texts = [compact_text(node.inner_text()) for node in card.query_selector_all(":scope > div")]
    price = next((text for text in texts if PRICE_PATTERN.fullmatch(text)), None)
    if not price:
        return None

    floorplan = compact_text(floorplan_node.inner_text())
    sqft = next((text for text in texts if re.fullmatch(r"[\d,]+", text)), "")
    move_in = next(
        (
            text
            for text in texts
            if text and text not in {floorplan, sqft, price} and not PRICE_PATTERN.search(text)
        ),
        "",
    )
    return {
        "timestamp": timestamp,
        "apartment": apartment,
        "floorplan": floorplan,
        "sqft": sqft,
        "move_in": move_in,
        "price": price,
    }


def parse_new_card_text(text: str, apartment: str, timestamp: str) -> dict[str, str] | None:
    match = NEW_CARD_PATTERN.search(compact_text(text))
    if not match:
        return None

    return {
        "timestamp": timestamp,
        "apartment": apartment,
        "floorplan": compact_text(match.group("floorplan")),
        "sqft": "",
        "move_in": compact_text(match.group("move_in")),
        "price": match.group("price"),
    }


def parse_new_card(card: Any, apartment: str, timestamp: str) -> dict[str, str] | None:
    mobile_summary = card.query_selector(".md\\:hidden")
    if not mobile_summary:
        return None
    return parse_new_card_text(mobile_summary.inner_text(), apartment, timestamp)


def deduplicate_floorplans(units: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    """Prefer the legacy card because it includes square footage when both cards exist."""
    by_floorplan: dict[str, dict[str, str]] = {}
    for unit in units:
        key = unit["floorplan"].casefold()
        existing = by_floorplan.get(key)
        if not existing or (not existing["sqft"] and unit["sqft"]):
            by_floorplan[key] = unit
    return sorted(by_floorplan.values(), key=lambda unit: unit["floorplan"])


def expand_availability(page: Page) -> None:
    """Click the listing control and wait for its explicit expanded state."""
    availability = page.locator("#prop_availability")
    view_more = availability.get_by_text("View More", exact=True)
    if view_more.count() != 1 or not view_more.is_visible():
        return

    view_more.scroll_into_view_if_needed()
    view_more.click()
    availability.get_by_text("View Less", exact=True).wait_for(state="visible", timeout=10_000)


def scrape_unit_details(
    page: Page,
    apartment: str,
    timestamp: str,
    floorplans: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    """Open each floor plan and capture its advertised individual residences."""
    details: list[dict[str, str]] = []
    for floorplan in floorplans:
        control = page.locator(
            f'#prop_availability .prop-detail-floorplan-name[data-floorplan-id]'
        ).filter(has_text=floorplan["floorplan"])
        if control.count() != 1:
            print(f"[WARN] Could not locate details control for {floorplan['floorplan']}.")
            continue

        floorplan_id = control.get_attribute("data-floorplan-id")
        if not floorplan_id:
            continue
        try:
            control.scroll_into_view_if_needed()
            control.click()
            unit_controls = page.locator(
                ".property_units_table_content "
                f'.display-contact-form.inner-form[data-floorplan-id="{floorplan_id}"]'
            )
            unit_controls.first.wait_for(state="visible", timeout=10_000)
        except PlaywrightTimeoutError:
            print(f"[WARN] Timed out while loading unit details for {floorplan['floorplan']}.")
            continue
        for unit_control in unit_controls.all():
            unit_id = unit_control.get_attribute("data-unit-title")
            rent = unit_control.get_attribute("data-rent")
            move_in = unit_control.get_attribute("data-movein")
            if not unit_id or not rent:
                continue
            details.append(
                {
                    "timestamp": timestamp,
                    "apartment": apartment,
                    "floorplan": floorplan["floorplan"],
                    "floorplan_id": floorplan_id,
                    "unit_id": unit_id,
                    "sqft": floorplan["sqft"],
                    "move_in": move_in or "",
                    "price": f"${rent}",
                }
            )
    return details


def scrape_apartment(
    apartment: str, url: str, page: Page
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    print(f"[INFO] Loading {apartment}...")
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.locator("#prop_availability").wait_for(state="visible", timeout=30_000)
    page.locator("#prop_availability .omg-results-card-body").first.wait_for(
        state="visible", timeout=30_000
    )
    page.wait_for_timeout(1_000)
    expand_availability(page)

    timestamp = utc_timestamp()
    units: list[dict[str, str]] = []
    for card in page.query_selector_all("#prop_availability .omg-results-card-body"):
        unit = parse_legacy_card(card, apartment, timestamp) or parse_new_card(
            card, apartment, timestamp
        )
        if unit:
            units.append(unit)

    units = deduplicate_floorplans(units)
    unit_details = scrape_unit_details(page, apartment, timestamp, units)
    print(
        f"[INFO] Found {len(units)} floor plans and {len(unit_details)} individual units "
        f"for {apartment}."
    )
    return units, unit_details


def scrape_all(headless: bool = True) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1440, "height": 1200})
        units: list[dict[str, str]] = []
        unit_details: list[dict[str, str]] = []
        for apartment, url in APARTMENTS.items():
            apartment_units, apartment_unit_details = scrape_apartment(apartment, url, page)
            units.extend(apartment_units)
            unit_details.extend(apartment_unit_details)
        browser.close()
    return units, unit_details


def load_latest_prices(filename: Path) -> dict[tuple[str, str], dict[str, str]]:
    if not filename.exists():
        return {}

    latest: dict[tuple[str, str], dict[str, str]] = {}
    with filename.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            key = (row["apartment"], row["floorplan"])
            latest[key] = row
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
                changes.append(
                    f"{unit['apartment']} {unit['floorplan']}: newly available at "
                    f"{unit['price']}"
                )
            continue
        if old["price"] == unit["price"]:
            continue
        difference = price_to_cents(unit["price"]) - price_to_cents(old["price"])
        direction = "up" if difference > 0 else "down"
        changes.append(
            f"{unit['apartment']} {unit['floorplan']}: {old['price']} -> "
            f"{unit['price']} ({direction} ${abs(difference) / 100:,.0f})"
        )
    return changes


def save_rows_csv(
    rows: Iterable[dict[str, str]], filename: Path, fieldnames: list[str]
) -> None:
    rows = list(rows)
    filename.parent.mkdir(parents=True, exist_ok=True)
    write_header = not filename.exists() or filename.stat().st_size == 0
    with filename.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    print(f"[INFO] Saved {len(rows)} snapshots to {filename}.")


def save_units_csv(units: Iterable[dict[str, str]], filename: Path = CSV_FILE) -> None:
    save_rows_csv(units, filename, CSV_FIELDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="Show the browser while scraping.")
    parser.add_argument("--dry-run", action="store_true", help="Print results without saving them.")
    parser.add_argument("--no-sheets", action="store_true", help="Skip the optional Google Sheets export.")
    parser.add_argument("--report-only", action="store_true", help="Rebuild the local report without scraping.")
    args = parser.parse_args()

    if args.report_only:
        generate_report(CSV_FILE, UNIT_CSV_FILE, REPORT_FILE)
        print(f"[INFO] Updated local report at {REPORT_FILE}.")
        return

    previous = load_latest_prices(CSV_FILE)
    units, unit_details = scrape_all(headless=not args.headed)
    if not units:
        raise RuntimeError("No floor plans were found; the listing page may have changed.")

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

    save_units_csv(units)
    save_rows_csv(unit_details, UNIT_CSV_FILE, UNIT_CSV_FIELDS)
    generate_report(CSV_FILE, UNIT_CSV_FILE, REPORT_FILE)
    print(f"[INFO] Updated local report at {REPORT_FILE}.")
    if not args.no_sheets:
        update_google_sheet(units)


if __name__ == "__main__":
    main()
