import unittest
from unittest.mock import patch

from scraper import scrape_apartment
from portfolio import validate_registry
from tests.test_portfolio import registry_entry


class ScopedInventoryTest(unittest.TestCase):
    def test_registry_scope_is_private_and_validated(self):
        config = validate_registry({"properties": [registry_entry(
            source={"page_id": "test", "floorplan_prefixes": ["TowerN", "TowerS"]}
        )]})[0]
        self.assertEqual(config.floorplan_prefixes, ("TowerN", "TowerS"))
        with self.assertRaises(ValueError):
            validate_registry({"properties": [registry_entry(
                source={"page_id": "test", "floorplan_prefixes": "TowerN"}
            )]})

    def scrape(self, prefixes, expected=3):
        plans = [{"floorplan_id": str(i), "floorplan": name, "timestamp": "t",
                  "apartment": "Building B", "sqft": "700", "move_in": "Immediate",
                  "price": "$3,000"}
                 for i, name in enumerate(("TowerN A1", "TowerS A1", "Other A1"))]
        def detail(_response, plan, *_):
            return [{**plan, "unit_id": plan["floorplan_id"]}]
        with patch("scraper.parse_overview_response", return_value=(plans, expected)), \
             patch("scraper.parse_floorplan_response", side_effect=detail):
            return scrape_apartment("Building B", "test", post=lambda _: {},
                                    unit_hash_key="x" * 32, floorplan_prefixes=prefixes)

    def test_mixed_inventory_is_scoped_after_full_count_validation(self):
        plans, listings = self.scrape(("TowerN", "TowerS"))
        self.assertEqual(len(plans), 2)
        self.assertEqual(len(listings), 2)
        self.assertTrue(all("Tower" not in row["floorplan"] for row in plans))

    def test_bad_full_count_fails_even_if_selected_inventory_matches(self):
        with self.assertRaisesRegex(RuntimeError, "Overview reports"):
            self.scrape(("TowerN",), expected=4)

    def test_empty_scope_is_not_a_complete_snapshot(self):
        with self.assertRaisesRegex(RuntimeError, "No verified floor plans"):
            self.scrape(("Missing",))

    def test_prefix_must_match_a_whole_token(self):
        with self.assertRaisesRegex(RuntimeError, "No verified floor plans"):
            self.scrape(("Tower",))


if __name__ == "__main__":
    unittest.main()
