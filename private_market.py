"""Collect one approved market-only property into ignored private history."""

import argparse
import re
from pathlib import Path

from portfolio import DomainRequestGovernor, PropertyMode, load_registry
from scraper import (
    CSV_FIELDS, UNIT_CSV_FIELDS, FLOORPLAN_DAILY_FIELDS, SCRAPE_RUNS_FIELDS,
    describe_price_changes, floorplan_daily_rows, load_latest_prices,
    post_json, read_rows_csv, save_rows_csv, scrape_apartment, scrape_run_rows,
)
from report import generate_report


def collect(key: str, dry_run: bool = False) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", key):
        raise ValueError("Use an opaque alphanumeric property key")
    config = next((item for item in load_registry() if item.key == key), None)
    if config is None or not config.enabled:
        raise RuntimeError("Private market property is missing or disabled")
    if config.compliance_status != "approved_private":
        raise RuntimeError("Private collection requires completed compliance review")
    if config.mode is not PropertyMode.MARKET_ONLY or not config.capabilities.market:
        raise RuntimeError("Private market collection requires market_only capability")
    if config.adapter_version not in {"veris_wp_ajax_v1", "veris_wp_ajax_v1.1"} or config.provider != "veris":
        raise RuntimeError("Unsupported provider adapter")
    governor = DomainRequestGovernor(max_properties=1)
    floorplans, listings = scrape_apartment(
        config.public_label, config.page_id,
        post=lambda form: post_json(form, governor, config.key),
        floorplan_prefixes=config.floorplan_prefixes,
    )
    if dry_run:
        return
    root = Path("private/market") / key
    root.mkdir(parents=True, exist_ok=True)
    prices = root / "unit_prices.csv"
    units = root / "unit_snapshots.csv"
    daily = root / "floorplan_daily.csv"
    runs = root / "scrape_runs.csv"
    for change in describe_price_changes(load_latest_prices(prices), floorplans):
        print(change)
    daily_rows = floorplan_daily_rows(listings, read_rows_csv(units))
    run_rows = scrape_run_rows(floorplans, listings)
    save_rows_csv(floorplans, prices, CSV_FIELDS)
    save_rows_csv(listings, units, UNIT_CSV_FIELDS)
    save_rows_csv(daily_rows, daily, FLOORPLAN_DAILY_FIELDS)
    save_rows_csv(run_rows, runs, SCRAPE_RUNS_FIELDS)
    generate_report(prices, units, root / "report.html", daily, runs)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    collect(args.key, args.dry_run)
