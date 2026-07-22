import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from privacy_audit import PUBLIC_ALLOWLIST, audit_paths
from report import freshness_status, market_recommendations, trait_constraint_status
from scripts.prepare_pages import prepare


class FreshnessTest(unittest.TestCase):
    def test_fresh_stale_and_no_complete_snapshot(self):
        times = {"Building A": ["2026-07-20T12:00:00+00:00"]}
        fresh = freshness_status(times, datetime(2026, 7, 21, tzinfo=timezone.utc))
        stale = freshness_status(times, datetime(2026, 7, 23, tzinfo=timezone.utc))
        missing = freshness_status({}, datetime(2026, 7, 21, tzinfo=timezone.utc))
        self.assertFalse(fresh["stale"])
        self.assertEqual(fresh["health"], "Healthy")
        self.assertTrue(stale["stale"])
        self.assertEqual(missing["health"], "No successful snapshot")
        self.assertEqual(missing["complete_days"], 0)

    def test_market_only_wins_market_categories_but_traits_are_not_evaluated(self):
        candidates = [
            {"floorplan_id": "cheap", "price": "$3,000", "sqft": "600", "move_in": "Immediate"},
            {"floorplan_id": "value", "price": "$3,200", "sqft": "800", "move_in": "Aug 1"},
        ]
        result = market_recommendations(candidates)
        self.assertEqual(result["best_budget"]["candidate"], "cheap")
        self.assertEqual(result["best_value"]["candidate"], "value")
        self.assertEqual(trait_constraint_status(False)["status"], "Unknown / not evaluated")


class PrivacyBoundaryTest(unittest.TestCase):
    def test_audit_detects_raw_unit_page_id_and_private_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public = root / "report.html"
            public.write_text("unit CAP-1234 page_id=98765", encoding="utf-8")
            private = root / "private" / "portfolio_report.html"
            private.parent.mkdir()
            private.write_text("private", encoding="utf-8")
            problems = audit_paths([public, private], root)
        self.assertTrue(any("raw unit" in problem for problem in problems))
        self.assertTrue(any("page ID" in problem for problem in problems))
        self.assertTrue(any("private file" in problem for problem in problems))

    def test_pages_builder_copies_only_allowlist(self):
        self.assertEqual(PUBLIC_ALLOWLIST, (Path("data/report.html"),))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "data/report.html").write_text("public", encoding="utf-8")
            (root / "private").mkdir()
            (root / "private/portfolio_report.html").write_text("private", encoding="utf-8")
            destination = root / "site"
            prepare(root, destination)
            self.assertEqual([path.name for path in destination.iterdir()], ["index.html"])

    def test_workflow_orders_tests_and_audits_before_mutating_steps(self):
        workflow = Path(".github/workflows/scraper.yml").read_text(encoding="utf-8")
        self.assertLess(workflow.index("Run complete test suite"), workflow.index("Run scraper"))
        self.assertLess(workflow.index("Audit generated public outputs"),
                        workflow.index("Commit updated price history"))
        self.assertLess(workflow.index("Audit exact GitHub Pages artifact"),
                        workflow.index("Upload GitHub Pages artifact"))


if __name__ == "__main__":
    unittest.main()
