import sys
import unittest
from pathlib import Path


FLYCLAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYCLAW_DIR))

import flyclaw  # noqa: E402
from airport_manager import airport_manager  # noqa: E402


class AirportConstraintTests(unittest.TestCase):
    def test_compound_hongqiao_alias_resolves_to_sha(self):
        self.assertEqual(airport_manager.resolve_all("上海虹桥"), ["SHA"])

    def test_fliggy_uses_iata_for_exact_airport_queries(self):
        self.assertEqual(flyclaw._fliggy_query_value("SHA", ["SHA"]), "SHA")
        self.assertEqual(flyclaw._fliggy_query_value("虹桥", ["SHA"]), "SHA")
        self.assertEqual(flyclaw._fliggy_query_value("上海虹桥", ["SHA"]), "SHA")

    def test_fliggy_uses_city_name_for_city_queries(self):
        self.assertEqual(flyclaw._fliggy_query_value("上海", ["PVG", "SHA"]), "上海")

    def test_exact_airport_constraints_split_wrong_airport_results(self):
        constraints = flyclaw._build_route_constraints("SHA", "CGQ", ["SHA"], ["CGQ"])
        records = [
            {
                "flight_number": "MU5593",
                "origin_iata": "SHA",
                "destination_iata": "CGQ",
                "scheduled_departure": "2026-05-14T08:55",
            },
            {
                "flight_number": "MU5643",
                "origin_iata": "PVG",
                "destination_iata": "CGQ",
                "scheduled_departure": "2026-05-14T10:35",
            },
        ]

        valid, fallback = flyclaw._split_records_by_route_constraints(
            records, constraints, "Fliggy"
        )

        self.assertEqual([r["flight_number"] for r in valid], ["MU5593"])
        self.assertEqual([r["flight_number"] for r in fallback], ["MU5643"])
        self.assertIn("origin PVG", fallback[0]["_constraint_reason"])

    def test_city_level_query_allows_multiple_origin_airports(self):
        constraints = flyclaw._build_route_constraints(
            "上海", "长春", ["PVG", "SHA"], ["CGQ"]
        )
        records = [
            {"flight_number": "MU5593", "origin_iata": "SHA", "destination_iata": "CGQ"},
            {"flight_number": "MU5643", "origin_iata": "PVG", "destination_iata": "CGQ"},
        ]

        valid, fallback = flyclaw._split_records_by_route_constraints(records, constraints)

        self.assertEqual(len(valid), 2)
        self.assertEqual(fallback, [])

    def test_merge_key_keeps_same_flight_number_separate_by_airport(self):
        records = [
            {
                "flight_number": "MU1000",
                "origin_iata": "SHA",
                "destination_iata": "CGQ",
                "scheduled_departure": "2026-05-14T08:00",
                "price": 800,
                "currency": "CNY",
                "source": "fliggy_mcp",
            },
            {
                "flight_number": "MU1000",
                "origin_iata": "PVG",
                "destination_iata": "CGQ",
                "scheduled_departure": "2026-05-14T08:00",
                "price": 500,
                "currency": "CNY",
                "source": "fliggy_mcp",
            },
        ]

        merged = flyclaw._merge_records(records)

        self.assertEqual(len(merged), 2)
        self.assertEqual({r["origin_iata"] for r in merged}, {"SHA", "PVG"})


if __name__ == "__main__":
    unittest.main()
