import json
import unittest
from datetime import date
from pathlib import Path

from scraper import (
    describe_price_changes,
    format_availability_date,
    parse_floorplan_response,
    parse_overview_response,
    price_to_cents,
    scrape_apartment,
)
from report import sparkline

FIXTURES = Path(__file__).parent / "fixtures"
TIMESTAMP = "2026-07-10T00:00:00+00:00"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ScraperHelpersTest(unittest.TestCase):
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
                    "floorplan": "S3",
                    "floorplan_id": "3553544",
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
        self.assertEqual(units[0]["unit_id"], "UNIT-0704")
        self.assertEqual(units[0]["price"], "$3,757")
        self.assertEqual(units[0]["move_in"], "Immediate")
        self.assertEqual(units[1]["unit_id"], "UNIT-0804")
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

    def test_count_mismatch_fails_loudly(self):
        overview = load_fixture("overview_response.json")
        detail = load_fixture("floorplan_s3_response.json")

        def post(_: dict[str, str]) -> dict:
            return overview if _["action"] == "omg_apt_search_main_query" else detail

        overview["apt_count"] = "3"
        with self.assertRaisesRegex(RuntimeError, "Overview reports 3 available units"):
            scrape_apartment("Building A", "private-page-id", post)

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
