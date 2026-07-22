"""Versioned parser adapter for the currently supported Veris response shapes."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping

from portfolio import NormalizedFloorPlan, NormalizedListing


ADAPTER_VERSION = "veris_wp_ajax_v1"
VARIANT_VERSION = "veris_wp_ajax_v1.1"
PRICE_PATTERN = re.compile(r"^\$?[\d,]+(?:\.\d{1,2})?$")


def _text(row: Mapping[str, Any], field: str, context: str) -> str:
    value = row.get(field)
    if value is None or not str(value).strip():
        raise RuntimeError(f"{context} is missing required field {field!r}")
    return str(value).strip()


def _price(value: Any) -> str:
    text = str(value).strip()
    if not PRICE_PATTERN.fullmatch(text):
        raise ValueError(f"Invalid advertised rent: {value!r}")
    cents = int(round(float(text.replace("$", "").replace(",", "")) * 100))
    return f"${cents / 100:,.0f}" if cents % 100 == 0 else f"${cents / 100:,.2f}"


def _availability(value: Any, today: date | None = None) -> str:
    text = str(value).strip()
    parsed = None
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
    fields = ["floorplan_name", "omg_feeds_floorplan_id", "rent_formatted",
              "rent_from_price", "move_in_date", "date_formatted", "sqft_commas"]
    return {
        "index_table": "omg_apt_idx",
        "default_order": [{"order_column": "apt_id", "order_direction": "desc"}],
        "environment": {"page_id": page_id, "custom_post_type": "property_id"},
        "facets": [], "group_by": "omg_feeds_floorplan_id",
        "result_structures": {"collector": {"container": {"classes": ""}, "card": {
            "header": {"items": []}, "body": {"items": [{"column": field} for field in fields]},
            "footer": {"items": []}}}},
        "results_per_page": 999, "current_page": 0, "available_results": [],
        "subquery": False, "stored_items_ids": [], "query_count": 0,
    }


def parse_overview_response(response: Mapping[str, Any], apartment: str,
                            timestamp: str) -> tuple[list[dict[str, str]], int]:
    variant = response.get("schema_version") == VARIANT_VERSION
    raw_rows = response.get("results" if variant else "apts_result")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise RuntimeError("Overview response contains no floor plans")
    try:
        expected_count = int(str(response["total_count" if variant else "apt_count"]))
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Overview response has an invalid apt_count") from error
    if expected_count <= 0:
        raise RuntimeError("Overview response reports no available units")
    normalized: list[NormalizedFloorPlan] = []
    seen: set[str] = set()
    for row in raw_rows:
        if not isinstance(row, Mapping):
            raise RuntimeError("Overview response contains an invalid floor-plan row")
        source_id = _text(row, "omg_feeds_floorplan_id", "Overview floor plan")
        if source_id in seen:
            raise RuntimeError(f"Overview response repeats floor-plan id {source_id}")
        seen.add(source_id)
        move_in = _text(row, "date_formatted", f"Overview floor plan {source_id}")
        normalized.append(NormalizedFloorPlan(
            property_key=apartment, source_id=source_id,
            name=_text(row, "floorplan_name", "Overview floor plan"),
            sqft=_text(row, "sqft_commas", f"Overview floor plan {source_id}"),
            move_in="Immediate" if move_in.casefold() == "now" else move_in,
            price=_price(_text(row, "rent_from_price", f"Overview floor plan {source_id}")),
        ))
    return [{"timestamp": timestamp, "apartment": item.property_key,
             "floorplan": item.name, "floorplan_id": item.source_id,
             "sqft": item.sqft, "move_in": item.move_in, "price": item.price}
            for item in normalized], expected_count


def parse_floorplan_response(response: Mapping[str, Any], floorplan: Mapping[str, str],
                             apartment: str, timestamp: str,
                             today: date | None = None) -> list[dict[str, str]]:
    variant = response.get("schema_version") == VARIANT_VERSION
    raw_rows = response.get("units" if variant else "query_response")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise RuntimeError(f"Floor plan {floorplan['floorplan']} has no individual unit records")
    normalized: list[NormalizedListing] = []
    seen: set[str] = set()
    expected_id = floorplan["floorplan_id"]
    for row in raw_rows:
        if not isinstance(row, Mapping):
            raise RuntimeError(f"Floor plan {floorplan['floorplan']} has an invalid unit record")
        actual_id = _text(row, "omg_feeds_floorplan_id", "Unit record")
        if actual_id != expected_id:
            raise RuntimeError(f"Unit record belongs to floor-plan id {actual_id}, expected {expected_id}")
        unit_id = _text(row, "the_title", f"Floor plan {floorplan['floorplan']}")
        if unit_id in seen:
            raise RuntimeError(f"Floor plan {floorplan['floorplan']} repeats unit {unit_id}")
        seen.add(unit_id)
        normalized.append(NormalizedListing(
            property_key=apartment, floorplan_id=expected_id, source_unit_id=unit_id,
            sqft=_text(row, "omg_feeds_apartment_squarefootage", f"Unit {unit_id}"),
            move_in=_availability(_text(row, "ra_date_available", f"Unit {unit_id}"), today),
            price=_price(_text(row, "ra_rent", f"Unit {unit_id}")),
        ))
    return [{"timestamp": timestamp, "apartment": item.property_key,
             "floorplan": floorplan["floorplan"], "floorplan_id": item.floorplan_id,
             "unit_id": item.source_unit_id, "sqft": item.sqft,
             "move_in": item.move_in, "price": item.price} for item in normalized]
