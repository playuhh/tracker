import json
import unittest
from datetime import date
from pathlib import Path

from scraper import (
    describe_price_changes,
    floorplan_daily_rows,
    format_availability_date,
    median_cents,
    parse_floorplan_response,
    parse_overview_response,
    price_to_cents,
    scrape_run_rows,
    scrape_apartment,
    validate_unit_catalog_coverage,
)
from catalog import secure_unit_id
from report import sparkline

FIXTURES = Path(__file__).parent / "fixtures"
TIMESTAMP = "2026-07-10T00:00:00+00:00"
TEST_KEY = "test-key-with-at-least-thirty-two-characters"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ScraperHelpersTest(unittest.TestCase):
    def test_median_cents_handles_odd_and_even_prices_without_float_money(self):
        self.assertEqual(median_cents([100, 300, 200]), 200)
        self.assertEqual(median_cents([100, 200]), 150)

    def test_floorplan_daily_aggregates_inventory_new_units_reductions_and_missing_sqft(self):
        previous = [
            {"timestamp": "2026-07-10T00:00:00+00:00", "apartment": "Building A", "floorplan": "A1", "floorplan_id": "a1", "unit_id": "101", "sqft": "700", "move_in": "Immediate", "price": "$3,500"},
            {"timestamp": "2026-07-10T00:00:00+00:00", "apartment": "Building A", "floorplan": "A1", "floorplan_id": "a1", "unit_id": "102", "sqft": "0", "move_in": "Jul 20", "price": "$3,800"},
        ]
        current = [
            {**previous[0], "timestamp": "2026-07-11T00:00:00+00:00", "price": "$3,450"},
            {**previous[1], "timestamp": "2026-07-11T00:00:00+00:00"},
            {**previous[0], "timestamp": "2026-07-11T00:00:00+00:00", "unit_id": "103", "price": "$3,600"},
        ]
        row = floorplan_daily_rows(current, previous)[0]
        self.assertEqual(row["visible_units"], "3")
        self.assertEqual(row["min_rent"], "$3,450")
        self.assertEqual(row["median_rent"], "$3,600")
        self.assertEqual(row["newly_visible_units"], "1")
        self.assertEqual(row["price_reductions"], "1")
        self.assertEqual(row["min_rent_per_sqft"], "$4.93")

    def test_complete_run_record_is_created_only_from_validated_snapshot_data(self):
        floorplans = [{"timestamp": TIMESTAMP, "apartment": "Building A", "floorplan": "A1"}]
        details = [{"timestamp": TIMESTAMP, "apartment": "Building A", "unit_id": "101"}]
        self.assertEqual(
            scrape_run_rows(floorplans, details),
            [{"timestamp": TIMESTAMP, "apartment": "Building A", "status": "complete",
              "floorplan_count": "1", "unit_count": "1"}],
        )

    def test_price_to_cents(self):
        self.assertEqual(price_to_cents("$3,757"), 375700)
        self.assertEqual(price_to_cents("$3,757.50"), 375750)

    def test_parse_overview_response_maps_existing_csv_schema(self):
        floorplans, count = parse_overview_response(
            load_fixture("overview_response.json"), "Building A", TIMESTAMP
        )
        self.assertEqual(count, 2)
        self.assertEqual(
            floorplans,
            [
                {
                    "timestamp": TIMESTAMP,
                    "apartment": "Building A",
                    "floorplan": "Example Studio",
                    "floorplan_id": "fixture-floorplan",
                    "sqft": "618",
                    "move_in": "Immediate",
                    "price": "$3,757",
                }
            ],
        )

    def test_parse_floorplan_response_maps_individual_units(self):
        floorplans, _ = parse_overview_response(
            load_fixture("overview_response.json"), "Building A", TIMESTAMP
        )
        units = parse_floorplan_response(
            load_fixture("floorplan_s3_response.json"),
            floorplans[0],
            "Building A",
            TIMESTAMP,
            today=date(2026, 7, 12),
        )
        self.assertEqual(units[0]["unit_id"], "source-listing-alpha")
        self.assertEqual(units[0]["price"], "$3,757")
        self.assertEqual(units[0]["move_in"], "Immediate")
        self.assertEqual(units[1]["unit_id"], "source-listing-beta")
        self.assertEqual(units[1]["price"], "$3,925")
        self.assertEqual(units[1]["move_in"], "Jul 20")

    def test_format_availability_date(self):
        self.assertEqual(
            format_availability_date("07/12/2026", today=date(2026, 7, 12)), "Immediate"
        )
        self.assertEqual(
            format_availability_date("2026-07-20", today=date(2026, 7, 12)), "Jul 20"
        )

    def test_empty_overview_response_fails_loudly(self):
        with self.assertRaisesRegex(RuntimeError, "no floor plans"):
            parse_overview_response({"apts_result": [], "apt_count": "0"}, "Building A", TIMESTAMP)

    def test_empty_floorplan_response_fails_loudly(self):
        floorplans, _ = parse_overview_response(
            load_fixture("overview_response.json"), "Building A", TIMESTAMP
        )
        with self.assertRaisesRegex(RuntimeError, "no individual unit records"):
            parse_floorplan_response({}, floorplans[0], "Building A", TIMESTAMP)

    def test_supported_adapter_variant_uses_explicit_schema_version(self):
        overview = load_fixture("overview_response.json")
        variant = {
            "schema_version": "veris_wp_ajax_v1.1",
            "results": overview["apts_result"],
            "total_count": overview["apt_count"],
        }
        floorplans, count = parse_overview_response(variant, "Building B", TIMESTAMP)
        detail = load_fixture("floorplan_s3_response.json")
        units = parse_floorplan_response(
            {"schema_version": "veris_wp_ajax_v1.1", "units": detail["query_response"]},
            floorplans[0], "Building B", TIMESTAMP, today=date(2026, 7, 12),
        )
        self.assertEqual(count, 2)
        self.assertEqual(len(units), 2)

    def test_malformed_overview_and_missing_required_field_fail(self):
        with self.assertRaisesRegex(RuntimeError, "no floor plans"):
            parse_overview_response({"apts_result": "bad", "apt_count": 1},
                                    "Building A", TIMESTAMP)
        overview = load_fixture("overview_response.json")
        del overview["apts_result"][0]["sqft_commas"]
        with self.assertRaisesRegex(RuntimeError, "sqft_commas"):
            parse_overview_response(overview, "Building A", TIMESTAMP)

    def test_count_mismatch_fails_loudly(self):
        overview = load_fixture("overview_response.json")
        detail = load_fixture("floorplan_s3_response.json")

        def post(_: dict[str, str]) -> dict:
            return overview if _["action"] == "omg_apt_search_main_query" else detail

        overview["apt_count"] = "3"
        with self.assertRaisesRegex(RuntimeError, "Overview reports 3 available units"):
            scrape_apartment("Building A", "private-page-id", post)

    def test_completed_scrape_pseudonymizes_layout_and_listing_identifiers(self):
        overview = load_fixture("overview_response.json")
        detail = load_fixture("floorplan_s3_response.json")

        def post(request: dict[str, str]) -> dict:
            return overview if request["action"] == "omg_apt_search_main_query" else detail

        floorplans, units = scrape_apartment(
            "Building A", "private-page-id", post, unit_hash_key=TEST_KEY
        )
        self.assertTrue(floorplans[0]["floorplan"].startswith("layout-"))
        self.assertTrue(units[0]["floorplan_id"].startswith("layout-id-"))
        self.assertTrue(units[0]["unit_id"].startswith("listing-"))
        self.assertNotIn("source-listing-alpha", units[0].values())

    def test_live_inventory_must_match_verified_catalog(self):
        live = [
            {"unit_id": "CAP-9001", "floorplan": "S3"},
            {"unit_id": "CAP-9002", "floorplan": "S3"},
        ]
        catalog = [
            {"unit_id": secure_unit_id(row["unit_id"], TEST_KEY), "floorplan": "S3"}
            for row in live
        ]
        validate_unit_catalog_coverage(live, catalog, TEST_KEY, expected_catalog_size=2)
        catalog[1]["floorplan"] = "A1"
        with self.assertRaisesRegex(RuntimeError, "1 floor-plan mismatches"):
            validate_unit_catalog_coverage(live, catalog, TEST_KEY, expected_catalog_size=2)

    def test_describe_price_changes(self):
        previous = {
            ("Building A", "A1"): {
                "apartment": "Building A",
                "floorplan": "A1",
                "price": "$3,759",
            }
        }
        current = [
            {
                "timestamp": TIMESTAMP,
                "apartment": "Building A",
                "floorplan": "A1",
                "sqft": "767",
                "move_in": "Immediate",
                "price": "$3,859",
            }
        ]
        self.assertEqual(
            describe_price_changes(previous, current),
            ["Building A A1: $3,759 -> $3,859 (up $100)"],
        )

    def test_describe_price_changes_reports_new_floorplan_after_first_snapshot(self):
        previous = {
            ("Building A", "A1"): {
                "apartment": "Building A",
                "floorplan": "A1",
                "price": "$3,759",
            }
        }
        current = [
            {
                "timestamp": TIMESTAMP,
                "apartment": "Building A",
                "floorplan": "B2",
                "sqft": "1,080",
                "move_in": "Immediate",
                "price": "$5,167",
            }
        ]
        self.assertEqual(
            describe_price_changes(previous, current),
            ["Building A B2: newly available at $5,167"],
        )

    def test_sparkline_renders_a_single_price(self):
        self.assertIn("<svg", sparkline([375700]))
        self.assertIn("polyline", sparkline([375700]))


if __name__ == "__main__":
    unittest.main()
