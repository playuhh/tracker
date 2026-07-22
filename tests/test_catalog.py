import tempfile
import unittest
from pathlib import Path

from catalog import (
    compile_public_catalog,
    legacy_unit_id,
    migrate_history_ids,
    secure_unit_id,
    source_unit_id,
    validate_floorplan_catalog,
)


TEST_KEY = "test-key-with-at-least-thirty-two-characters"


class CatalogTest(unittest.TestCase):
    def test_keyed_identifier_is_stable_and_differs_from_legacy_hash(self):
        source = source_unit_id("9001")
        self.assertEqual(source, "CAP-9001")
        self.assertEqual(secure_unit_id(source, TEST_KEY), secure_unit_id(source, TEST_KEY))
        self.assertNotEqual(secure_unit_id(source, TEST_KEY), legacy_unit_id(source))

    def test_short_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least 32"):
            secure_unit_id("CAP-9001", "short")

    def test_public_catalog_removes_room_number_and_exact_floor(self):
        rows = [self.sample_room("9001"), self.sample_room("9002", exposure="NW",
                                                          facade="external", pool_facing="no")]
        compiled = compile_public_catalog(rows, TEST_KEY, {90: 2})
        self.assertEqual(len(compiled), 2)
        self.assertNotIn("room_number", compiled[0])
        self.assertNotIn("floor", compiled[0])
        self.assertTrue(compiled[0]["unit_id"].startswith("listing-"))
        self.assertEqual([row["unit_id"] for row in compiled], sorted(row["unit_id"] for row in compiled))

    def test_floorplan_review_is_inherited_without_publishing_source_metadata(self):
        floorplans = [{
            "floorplan": "A4", "bedrooms": "1", "geometry": "rectangular",
            "layout_efficiency": "efficient",
            "layout_fit": "preferred", "review_confidence": "manual_image_review",
            "image_url": "https://property.example/identifying-image.png",
            "review_notes": "private identifying notes",
        }]
        compiled = compile_public_catalog(
            [self.sample_room("9001")], TEST_KEY, {90: 1}, floorplans=floorplans
        )
        self.assertEqual(compiled[0]["layout_geometry"], "rectangular")
        self.assertEqual(compiled[0]["layout_efficiency"], "efficient")
        self.assertEqual(compiled[0]["layout_fit"], "preferred")
        self.assertNotIn("image_url", compiled[0])
        self.assertNotIn("review_notes", compiled[0])

    def test_missing_or_invalid_floorplan_review_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid layout fit"):
            validate_floorplan_catalog([{
                "floorplan": "A4", "bedrooms": "1", "geometry": "rectangular",
                "layout_efficiency": "efficient",
                "layout_fit": "maybe", "review_confidence": "manual",
            }])
        with self.assertRaisesRegex(ValueError, "lack reviews"):
            compile_public_catalog(
                [self.sample_room("9001")], TEST_KEY, {90: 1}, floorplans=[{
                    "floorplan": "B1", "bedrooms": "2", "geometry": "rectangular",
                    "layout_efficiency": "efficient",
                    "layout_fit": "preferred", "review_confidence": "manual",
                }]
            )

    def test_history_migration_rekeys_known_legacy_unit(self):
        catalog = [self.sample_room("9001")]
        old_id = legacy_unit_id("CAP-9001")
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "history.csv"
            history.write_text(f"unit_id,price\n{old_id},$3,700\nunknown,$4,000\n", encoding="utf-8")
            changed = migrate_history_ids(history, catalog, TEST_KEY)
            text = history.read_text(encoding="utf-8")
        self.assertEqual(changed, 1)
        self.assertIn(secure_unit_id("CAP-9001", TEST_KEY), text)
        self.assertIn("unknown", text)

    @staticmethod
    def sample_room(room_number, exposure="SE", facade="internal", pool_facing="yes"):
        return {
            "room_number": room_number,
            "floor": str(int(room_number) // 100),
            "floorplan": "A4",
            "exposure": exposure,
            "secondary_exposure": "",
            "facade": facade,
            "pool_facing": pool_facing,
            "outlook": "pool_courtyard" if facade == "internal" else "street_or_hillside",
            "sunlight": "good" if exposure == "SE" else "low",
            "view": "pool_skyline_partial" if facade == "internal" else "none",
            "floor_band": "mid_high",
            "disturbance": "medium" if facade == "internal" else "low",
            "confidence": "test",
        }


if __name__ == "__main__":
    unittest.main()
