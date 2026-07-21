import csv
import tempfile
import unittest
from pathlib import Path

from report import (
    apartment_snapshot_times,
    group_unit_history,
    plan_recommendation,
    unit_history_data,
    unit_summary,
    generate_report,
)


def unit(timestamp, unit_id, price, floorplan_id="fp-a", floorplan="A1"):
    return {
        "timestamp": timestamp,
        "apartment": "Building A",
        "floorplan": floorplan,
        "floorplan_id": floorplan_id,
        "unit_id": unit_id,
        "sqft": "700",
        "move_in": "Immediate",
        "price": price,
    }


def floorplan(timestamp):
    return {
        "timestamp": timestamp,
        "apartment": "Building A",
        "floorplan": "A1",
        "floorplan_id": "fp-a",
        "sqft": "700",
        "move_in": "Immediate",
        "price": "$3,500",
    }


class UnitHistoryTest(unittest.TestCase):
    def setUp(self):
        self.timestamps = [
            "2026-07-01T00:00:00+00:00",
            "2026-07-02T00:00:00+00:00",
            "2026-07-03T00:00:00+00:00",
            "2026-07-04T00:00:00+00:00",
        ]
        # Intentionally unordered to prove grouping sorts snapshots.
        self.rows = [
            unit(self.timestamps[3], "UNIT-0704", "$3,450"),
            unit(self.timestamps[0], "UNIT-0704", "$3,500"),
            unit(self.timestamps[1], "UNIT-0704", "$3,475"),
            unit(self.timestamps[3], "UNIT-0804", "$3,800"),
        ]

    def test_grouping_uses_apartment_floorplan_id_and_unit_id(self):
        grouped = group_unit_history(self.rows)
        self.assertEqual(len(grouped), 2)
        history = grouped[("Building A", "fp-a", "UNIT-0704")]
        self.assertEqual([row["timestamp"] for row in history], self.timestamps[:2] + self.timestamps[3:])

    def test_summary_calculates_price_delta_and_one_point_history(self):
        grouped = group_unit_history(self.rows)
        summary = unit_summary(grouped[("Building A", "fp-a", "UNIT-0704")])
        self.assertEqual(summary["first_price"], 350000)
        self.assertEqual(summary["latest_price"], 345000)
        self.assertEqual(summary["change_cents"], -5000)
        self.assertAlmostEqual(summary["change_percent"], -1.4285714)

        one_point = unit_summary(grouped[("Building A", "fp-a", "UNIT-0804")])
        self.assertEqual(one_point["snapshot_count"], 1)
        self.assertEqual(one_point["change_cents"], 0)

    def test_disappearance_and_return_starts_a_new_chart_segment(self):
        grouped = group_unit_history(self.rows)
        times = apartment_snapshot_times(
            [floorplan(timestamp) for timestamp in self.timestamps], self.rows
        )
        histories = unit_history_data(grouped, times)
        cap_0704 = next(history for history in histories if history["unit_id"] == "UNIT-0704")
        self.assertEqual(
            [point["gap_before"] for point in cap_0704["points"]], [False, False, True]
        )

    def test_generated_report_is_layout_first_and_omits_listing_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            unit_file = directory / "unit_snapshots.csv"
            floorplan_file = directory / "unit_prices.csv"
            traits_file = directory / "unit_traits.csv"
            output_file = directory / "report.html"
            self.write_csv(unit_file, self.rows)
            self.write_csv(floorplan_file, [floorplan(timestamp) for timestamp in self.timestamps])
            self.write_csv(traits_file, [
                {"unit_id": "UNIT-0704", "exposure": "SE", "sunlight": "good",
                 "view": "skyline_partial", "floor_band": "mid_high",
                 "disturbance": "medium", "confidence": "layout_plus_resident"},
                {"unit_id": "UNIT-0804", "exposure": "NW", "sunlight": "low",
                 "view": "none", "floor_band": "mid_high",
                 "disturbance": "low", "confidence": "layout_plus_resident"},
            ])

            generate_report(floorplan_file, unit_file, output_file, traits_file=traits_file)
            report = output_file.read_text(encoding="utf-8")

        self.assertIn('id="plan-selector"', report)
        self.assertIn("Personalized floor-plan recommendation", report)
        self.assertIn("preferred southeast exposure", report)
        self.assertIn("Best current fit", report)
        self.assertIn('class="fit-stack"', report)
        self.assertIn('class="signal market-signal neutral"', report)
        self.assertIn("Layout 1", report)
        self.assertIn("Collecting history (3/7 days)", report)
        self.assertIn("How to use this", report)
        self.assertIn('id="scoring-methodology"', report)
        self.assertIn("Relative asking rent", report)
        self.assertIn("35 points", report)
        self.assertIn("Exposure / direction", report)
        self.assertIn("17 points", report)
        self.assertIn("Floor-plan size", report)
        self.assertIn("0 direct points", report)
        self.assertIn("Best match 82–100", report)
        self.assertIn("rentValues=p.points.flatMap", report)
        self.assertIn("tooltip=make('title'", report)
        self.assertNotIn("UNIT-0704", report)
        self.assertNotIn("UNIT-0804", report)

    def test_recommendation_prefers_sunny_southeast_over_shaded_northwest(self):
        peers = [
            unit(self.timestamps[3], "sunny", "$3,600"),
            unit(self.timestamps[3], "shaded", "$3,600"),
        ]
        traits = {
            "sunny": {"exposure": "SE", "sunlight": "good", "view": "skyline_partial",
                      "floor_band": "mid_high", "disturbance": "medium"},
            "shaded": {"exposure": "NW", "sunlight": "low", "view": "none",
                       "floor_band": "mid_high", "disturbance": "low"},
        }
        result = plan_recommendation(peers, traits)
        self.assertEqual(result["exposure"], "SE")
        self.assertGreaterEqual(result["score"], 82)
        self.assertEqual(result["preferred_count"], 1)
        self.assertEqual(result["low_sun_count"], 1)

    def test_recommendation_degrades_safely_without_verified_traits(self):
        result = plan_recommendation([unit(self.timestamps[3], "unknown", "$3,600")], {})
        self.assertIsNone(result["score"])
        self.assertEqual(result["label"], "Not rated")

    @staticmethod
    def write_csv(path, rows):
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
