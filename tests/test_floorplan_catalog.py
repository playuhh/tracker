import tempfile
import unittest
from pathlib import Path

from floorplan_catalog import crawl_floorplans, extract_floorplan_images, import_floorplans


HTML = """
<img src="https://media.example/Veris_ABC_Floorplan_A1.png?width=352">
<img data-src="https://media.example/Veris_ABC_Floorplan_B6(1).png?height=130">
<img src="https://media.example/Veris_ABC_Floorplan_C1.png">
"""


class FloorplanCatalogTest(unittest.TestCase):
    def test_extracts_one_and_two_bedroom_images_and_removes_resize_query(self):
        self.assertEqual(extract_floorplan_images(HTML), {
            "A1": "https://media.example/Veris_ABC_Floorplan_A1.png",
            "B6": "https://media.example/Veris_ABC_Floorplan_B6(1).png",
        })

    def test_crawl_caches_images_and_preserves_manual_reviews(self):
        responses = {
            "https://example.test/plans": HTML.encode(),
            "https://media.example/Veris_ABC_Floorplan_A1.png": b"a1-image",
            "https://media.example/Veris_ABC_Floorplan_B6(1).png": b"b6-image",
        }

        def fetch(url, _limit, _expected_type):
            return responses[url]

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            catalog = base / "floorplans.csv"
            catalog.write_text(
                "floorplan,geometry,layout_efficiency,layout_fit,review_notes,review_confidence\n"
                "A1,rectangular,efficient,preferred,regular plan,manual\n",
                encoding="utf-8",
            )
            rows = crawl_floorplans(
                "https://example.test/plans", base / "images", catalog, fetch=fetch
            )
            self.assertEqual([row["floorplan"] for row in rows], ["A1", "B6"])
            self.assertEqual(rows[0]["layout_fit"], "preferred")
            self.assertEqual(rows[1]["layout_fit"], "")
            self.assertEqual((base / "images" / "A1.png").read_bytes(), b"a1-image")

    def test_imports_previously_saved_browser_html(self):
        responses = {
            "https://media.example/Veris_ABC_Floorplan_A1.png": b"a1-image",
            "https://media.example/Veris_ABC_Floorplan_B6(1).png": b"b6-image",
        }

        def fetch(url, _limit, _expected_type):
            return responses[url]

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            rows = import_floorplans(HTML, base / "images", base / "catalog.csv", fetch)
        self.assertEqual(len(rows), 2)

    def test_review_file_overrides_preserved_values(self):
        responses = {
            "https://media.example/Veris_ABC_Floorplan_A1.png": b"a1-image",
            "https://media.example/Veris_ABC_Floorplan_B6(1).png": b"b6-image",
        }

        def fetch(url, _limit, _expected_type):
            return responses[url]

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            reviews = base / "reviews.csv"
            reviews.write_text(
                "floorplan,geometry,layout_efficiency,layout_fit,review_notes,review_confidence\n"
                "A1,rectangular,efficient,preferred,regular,manual\n"
                "B6,irregular,inefficient,penalized,narrow,manual\n",
                encoding="utf-8",
            )
            rows = import_floorplans(
                HTML, base / "images", base / "catalog.csv", fetch,
                reviews_path=reviews,
            )
        self.assertEqual(rows[0]["layout_fit"], "preferred")
        self.assertEqual(rows[1]["layout_fit"], "penalized")


if __name__ == "__main__":
    unittest.main()
