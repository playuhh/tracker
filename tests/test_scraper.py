import unittest

from scraper import (
    deduplicate_floorplans,
    describe_price_changes,
    parse_new_card_text,
    price_to_cents,
)
from report import sparkline


class ScraperHelpersTest(unittest.TestCase):
    def test_price_to_cents(self):
        self.assertEqual(price_to_cents("$3,757"), 375700)
        self.assertEqual(price_to_cents("$3,757.50"), 375750)

    def test_deduplicate_prefers_record_with_square_footage(self):
        units = [
            {
                "timestamp": "2026-07-10T00:00:00+00:00",
                "apartment": "Building A",
                "floorplan": "A1",
                "sqft": "",
                "move_in": "Now",
                "price": "$3,759",
            },
            {
                "timestamp": "2026-07-10T00:00:00+00:00",
                "apartment": "Building A",
                "floorplan": "A1",
                "sqft": "767",
                "move_in": "Now",
                "price": "$3,759",
            },
        ]
        result = deduplicate_floorplans(units)
        self.assertEqual(result, [units[1]])

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
                "timestamp": "2026-07-10T00:00:00+00:00",
                "apartment": "Building A",
                "floorplan": "A1",
                "sqft": "767",
                "move_in": "Now",
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
                "timestamp": "2026-07-10T00:00:00+00:00",
                "apartment": "Building A",
                "floorplan": "B2",
                "sqft": "1,080",
                "move_in": "Now",
                "price": "$5,167",
            }
        ]
        self.assertEqual(
            describe_price_changes(previous, current),
            ["Building A B2: newly available at $5,167"],
        )

    def test_parse_current_capstone_new_card_format(self):
        unit = parse_new_card_text(
            "Floor PlanS3BedroomsStudio / 1 BathPriceFrom $3,757AvailableNow",
            "Building A",
            "2026-07-10T00:00:00+00:00",
        )
        self.assertEqual(
            unit,
            {
                "timestamp": "2026-07-10T00:00:00+00:00",
                "apartment": "Building A",
                "floorplan": "S3",
                "sqft": "",
                "move_in": "Now",
                "price": "$3,757",
            },
        )

    def test_sparkline_renders_a_single_price(self):
        self.assertIn("<svg", sparkline([375700]))
        self.assertIn("polyline", sparkline([375700]))


if __name__ == "__main__":
    unittest.main()
