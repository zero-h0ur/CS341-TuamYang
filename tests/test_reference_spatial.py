"""
Unit and integration tests for Issue #3:
[Reference + Spatial join] ward boundaries and ward <-> station matching
Owner: PhurinKaewpuangsek

Covers Seams A-D:
1. Distance and radius logic (Haversine distance, point-in-polygon, point-to-polygon boundary distance, radius filtering)
2. WKT conversion and parseability (POLYGON / MULTIPOLYGON valid closed rings in Bangkok bounding box)
3. Ward <-> water-station and rain-station mapping output shape, column alignment with PR #13 (sql/create_khwaeng_hourly.sql),
   every selected water station maps to at least one ward, and Station 852087 (usable_for_rainfall=false) is excluded from nearest rain station.
4. Generated artifacts in reference/ (khwaeng_boundaries.csv, khwaeng_station_mapping.csv) and Athena SQL spatial join (sql/spatial_join_traffy.sql).
"""

import csv
import json
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = PROJECT_ROOT / "reference"
SQL_DIR = PROJECT_ROOT / "sql"


class TestReferenceSpatialGeometryAndWkt(unittest.TestCase):
    """Seam A & B: WKT conversion, parseability, point-in-polygon, and distance/radius logic."""

    def test_geojson_to_wkt_and_parse_wkt_roundtrip(self):
        from scripts.build_reference_spatial import geojson_geometry_to_wkt, parse_wkt_polygons

        poly_geom = {
            "type": "Polygon",
            "coordinates": [
                [
                    [100.50, 13.70],
                    [100.60, 13.70],
                    [100.60, 13.80],
                    [100.50, 13.80],
                    [100.50, 13.70],
                ]
            ],
        }
        wkt = geojson_geometry_to_wkt(poly_geom)
        self.assertTrue(wkt.startswith("POLYGON (("))
        parsed = parse_wkt_polygons(wkt)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(len(parsed[0][0]), 5)
        self.assertAlmostEqual(parsed[0][0][0][0], 100.50)
        self.assertAlmostEqual(parsed[0][0][0][1], 13.70)

    def test_point_in_polygon_and_boundary_distance_km(self):
        from scripts.build_reference_spatial import (
            point_in_wkt,
            point_to_wkt_distance_km,
        )

        # Box from lon 100.50..100.60, lat 13.70..13.80
        wkt = "POLYGON ((100.5 13.7, 100.6 13.7, 100.6 13.8, 100.5 13.8, 100.5 13.7))"

        # Inside point
        self.assertTrue(point_in_wkt(13.75, 100.55, wkt))
        self.assertEqual(point_to_wkt_distance_km(13.75, 100.55, wkt), 0.0)

        # Outside point ~1.11 km north of lat 13.80 (0.01 deg lat ~ 1.11195 km)
        self.assertFalse(point_in_wkt(13.81, 100.55, wkt))
        dist_north = point_to_wkt_distance_km(13.81, 100.55, wkt)
        self.assertAlmostEqual(dist_north, 1.112, delta=0.05)

    def test_filter_wards_within_radius_parameterizable(self):
        from scripts.build_reference_spatial import select_wards_within_radius

        wards = [
            {
                "khwaeng_code": "TH100101",
                "name_th": "พระบรมมหาราชวัง",
                "name_en": "Phraborom Maharatchawang",
                "khet": "พระนคร",
                "khet_code": "TH1001",
                "khet_en": "Phra Nakhon",
                "center_lat": 13.75,
                "center_lon": 100.55,
                "area_sqkm": 1.5,
                "wkt": "POLYGON ((100.5 13.7, 100.6 13.7, 100.6 13.8, 100.5 13.8, 100.5 13.7))",
            },
            {
                "khwaeng_code": "TH100102",
                "name_th": "วังบูรพาภิรมย์",
                "name_en": "Wang Burapha Phirom",
                "khet": "พระนคร",
                "khet_code": "TH1001",
                "khet_en": "Phra Nakhon",
                "center_lat": 13.85,
                "center_lon": 100.55,
                "area_sqkm": 1.2,
                "wkt": "POLYGON ((100.5 13.805, 100.6 13.805, 100.6 13.82, 100.5 13.82, 100.5 13.805))",
            },
        ]
        stations = [
            {
                "api_station_id": 999,
                "station_name_th": "สถานีทดสอบ",
                "river_name_th": "คลองทดสอบ",
                "latitude": 13.75,
                "longitude": 100.55,
                "usable_for_freeboard": True,
                "quality_flag": "NORMAL",
            }
        ]

        # At 0.4 km radius, only TH100101 (containing the station, dist=0.0) is within radius;
        # TH100102 starts at lat 13.805 (~0.55 km from 13.80 boundary, ~6.1 km from 13.75 station)
        sel_tight = select_wards_within_radius(wards, stations, radius_km=1.0)
        self.assertEqual([w["khwaeng_code"] for w in sel_tight], ["TH100101"])

        # At 10.0 km radius, both wards are within radius of the station at 13.75
        sel_wide = select_wards_within_radius(wards, stations, radius_km=10.0)
        self.assertEqual([w["khwaeng_code"] for w in sel_wide], ["TH100101", "TH100102"])


class TestReferenceSpatialOutputsAndContracts(unittest.TestCase):
    """Seam C & D: Generated reference CSVs, station mapping invariants, PR #13 column compatibility, and Athena SQL."""

    def setUp(self):
        self.boundaries_csv = REFERENCE_DIR / "khwaeng_boundaries.csv"
        self.mapping_csv = REFERENCE_DIR / "khwaeng_station_mapping.csv"
        self.water_json = REFERENCE_DIR / "stations_water.json"
        self.rain_json = REFERENCE_DIR / "stations_rain.json"
        self.sql_file = SQL_DIR / "spatial_join_traffy.sql"

    def test_khwaeng_boundaries_csv_exists_and_all_wkts_are_parseable(self):
        from scripts.build_reference_spatial import parse_wkt_polygons

        self.assertTrue(self.boundaries_csv.is_file(), "reference/khwaeng_boundaries.csv must exist")
        with open(self.boundaries_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        # Royal Thai Survey Department (HDX COD-AB) has 169 adm3 wards in Bangkok (TH10)
        self.assertEqual(len(rows), 169)

        required_cols = {"khwaeng_code", "name_th", "name_en", "khet", "wkt"}
        self.assertTrue(required_cols.issubset(set(rows[0].keys())))

        for r in rows:
            self.assertTrue(r["khwaeng_code"].startswith("TH10"))
            self.assertTrue(len(r["name_th"].strip()) > 0)
            self.assertTrue(len(r["name_en"].strip()) > 0)
            self.assertTrue(len(r["khet"].strip()) > 0)
            polys = parse_wkt_polygons(r["wkt"])
            self.assertGreaterEqual(len(polys), 1)
            for poly in polys:
                ext_ring = poly[0]
                self.assertGreaterEqual(len(ext_ring), 4)
                # Closed ring check
                self.assertEqual(ext_ring[0], ext_ring[-1])
                for lon, lat in ext_ring:
                    self.assertTrue(100.3 <= lon <= 101.0, f"Longitude out of BKK bounds: {lon}")
                    self.assertTrue(13.4 <= lat <= 14.0, f"Latitude out of BKK bounds: {lat}")

    def test_khwaeng_station_mapping_csv_columns_and_station_coverage(self):
        self.assertTrue(self.mapping_csv.is_file(), "reference/khwaeng_station_mapping.csv must exist")
        with open(self.mapping_csv, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        self.assertEqual(len(rows), 169)

        # Must include both Issue #3 fields and PR #13 (sql/create_khwaeng_hourly.sql) expected columns
        pr13_expected_cols = {
            "khwaeng",
            "khet",
            "paired_water_station_id",
            "water_station_dist_km",
            "paired_rain_station_id",
            "rain_station_dist_km",
        }
        issue3_cols = {
            "khwaeng_code",
            "name_th",
            "name_en",
            "within_radius_km",
            "min_water_boundary_dist_km",
            "containing_water_station_id",
            "water_usable_for_freeboard",
            "rain_usable_for_rainfall",
        }
        actual_cols = set(rows[0].keys())
        self.assertTrue(pr13_expected_cols.issubset(actual_cols), f"Missing PR #13 cols: {pr13_expected_cols - actual_cols}")
        self.assertTrue(issue3_cols.issubset(actual_cols), f"Missing Issue #3 cols: {issue3_cols - actual_cols}")

        water_stations = json.loads(self.water_json.read_text(encoding="utf-8"))
        all_water_ids = {int(s["api_station_id"]) for s in water_stations}

        # Every one of the 8 water stations must map to at least one ward within the 1 km radius
        # AND as a paired_water_station_id
        paired_water_ids = {int(r["paired_water_station_id"]) for r in rows}
        self.assertEqual(paired_water_ids, all_water_ids)

        within_radius_rows = [r for r in rows if r["within_radius_km"].lower() == "true"]
        self.assertGreaterEqual(len(within_radius_rows), 8)
        within_radius_station_ids = set()
        for r in within_radius_rows:
            for st_id_str in r["within_1km_water_station_ids"].split(";"):
                if st_id_str.strip():
                    within_radius_station_ids.add(int(st_id_str.strip()))
        self.assertEqual(
            within_radius_station_ids,
            all_water_ids,
            "Every one of the 8 water stations must have at least one ward within the 1 km default radius",
        )

        # Rain Station 852087 (SUSPECT_RAIN_SENSOR_OUTAGE, usable_for_rainfall=false) must never be paired
        paired_rain_ids = {int(r["paired_rain_station_id"]) for r in rows}
        self.assertNotIn(852087, paired_rain_ids)
        for r in rows:
            self.assertEqual(r["rain_usable_for_rainfall"].lower(), "true")

    def test_athena_spatial_join_sql_exists_and_uses_st_contains(self):
        self.assertTrue(self.sql_file.is_file(), "sql/spatial_join_traffy.sql must exist")
        sql_text = self.sql_file.read_text(encoding="utf-8")
        self.assertIn("ST_Contains", sql_text)
        self.assertIn("ST_GeometryFromText", sql_text)
        self.assertIn("ST_Point", sql_text)
        self.assertIn("reference.khwaeng_boundaries", sql_text)
        self.assertIn("reference.khwaeng_station_mapping", sql_text)
        self.assertIn("trusted.traffy", sql_text)


if __name__ == "__main__":
    unittest.main()
