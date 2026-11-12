"""
Unit tests for Curated Data Mart and Analysis (Issue #2)
Owner: Member 5 (nustapark6709650334)

Self-contained tests that run cleanly in CI without requiring gitignored
citizen PII files under data/incoming/traffy/ or data/trusted/traffy/.
"""

import json
import tempfile
import unittest
from pathlib import Path

from src.curated.analyzer import CuratedAnalyzer
from src.curated.builder import CuratedBuilder, extract_khwaeng_khet, haversine_km


class TestCuratedBuilder(unittest.TestCase):
    def test_haversine_distance(self):
        # Distance between Bang Bua (13.85402, 100.58746) and Asok (13.74325, 100.562164) ~12.6 km
        dist = haversine_km(13.85402, 100.58746, 13.74325, 100.562164)
        self.assertAlmostEqual(dist, 12.61, delta=0.5)

    def test_extract_khwaeng_khet(self):
        addr1 = "แขวงหัวหมาก เขตบางกะปิ กรุงเทพมหานคร"
        kh, khet = extract_khwaeng_khet(addr1)
        self.assertEqual(kh, "หัวหมาก")
        self.assertEqual(khet, "บางกะปิ")

        addr2 = "123 ถนนสุขุมวิท"
        kh2, khet2 = extract_khwaeng_khet(addr2)
        self.assertEqual(kh2, "ไม่ระบุ")
        self.assertEqual(khet2, "ไม่ระบุ")

        kh3, khet3 = extract_khwaeng_khet(None)
        self.assertEqual(kh3, "ไม่ระบุ")

    def test_rain_station_excludes_suspect_sensor_outage(self):
        builder = CuratedBuilder()
        # Coordinates right at Asok (Station 852087: 13.74325, 100.562164)
        # Station 852087 has usable_for_rainfall=false (SUSPECT_RAIN_SENSOR_OUTAGE)
        rain_st_id, dist_km = builder.find_nearest_rain_station(13.74325, 100.562164, usable_only=True)
        self.assertNotEqual(rain_st_id, 852087)
        self.assertTrue(builder.rain_map[rain_st_id].get("usable_for_rainfall", True))
        self.assertLess(dist_km, 5.0)

    def test_waterlevel_hourly_and_datum_mismatch_isolation(self):
        builder = CuratedBuilder()
        wl_hourly = builder.load_waterlevel_hourly(["2026-09-25", "2026-09-26"])
        # Station 1 (Bang Bua) on 2026-09-26 hour 22 overflowed
        st1 = wl_hourly.get((1, "2026-09-26", 22))
        self.assertIsNotNone(st1)
        self.assertTrue(st1["usable_for_freeboard"])
        self.assertTrue(st1["is_overflow"])
        self.assertLessEqual(st1["min_freeboard_m"], 0.0)

        # Station 10 (Phasi Charoen) has SUSPECT_DATUM_MISMATCH -> usable_for_freeboard=False
        st10 = wl_hourly.get((10, "2026-09-25", 12))
        self.assertIsNotNone(st10)
        self.assertFalse(st10["usable_for_freeboard"])
        self.assertEqual(st10["water_quality_flag"], "SUSPECT_DATUM_MISMATCH")
        self.assertIsNone(st10["min_freeboard_m"])
        self.assertIsNotNone(st10["raw_freeboard_m"])
        self.assertFalse(st10["is_overflow"])

    def test_build_from_trusted_jsonl_with_latest_batch_wins(self):
        with tempfile.TemporaryDirectory(prefix="curated_test_") as tmp:
            tmp_path = Path(tmp)
            trusted_dir = tmp_path / "trusted"
            out_dir = tmp_path / "curated" / "khwaeng_hourly"

            # 1. Write two overlapping waterlevel batches in trusted/waterlevel/dt=2026-09-25/
            wl_dir = trusted_dir / "waterlevel" / "dt=2026-09-25"
            wl_dir.mkdir(parents=True, exist_ok=True)
            older_wl = {
                "api_station_id": 8,
                "waterlevel_datetime": "2026-09-25 15:00",
                "timestamp_epoch": 1790323200,
                "waterlevel_msl": 1.50,
                "min_bank_msl": 1.97,
                "diff_wl_bank_m": 0.47,
                "quality_flag": "NORMAL",
                "usable_for_freeboard": True,
                "batch_id": "tw-wa-20260925-150500-aaaa11",
                "ingested_at_utc": "2026-09-25T08:05:00Z",
            }
            newer_wl = {
                **older_wl,
                "waterlevel_msl": 2.05,
                "diff_wl_bank_m": -0.08,
                "batch_id": "tw-wa-20260925-151500-bbbb22",
                "ingested_at_utc": "2026-09-25T08:15:00Z",
            }
            (wl_dir / "b1.jsonl").write_text(json.dumps(older_wl) + "\n", encoding="utf-8")
            (wl_dir / "b2.jsonl").write_text(json.dumps(newer_wl) + "\n", encoding="utf-8")

            # 2. Write synthetic trusted/traffy/dt=2026-09-25/traffy_2026-09-25.jsonl
            tf_dir = trusted_dir / "traffy" / "dt=2026-09-25"
            tf_dir.mkdir(parents=True, exist_ok=True)
            reports = [
                {
                    "ticket_id": "T-001",
                    "address": "แขวงลาดพร้าว เขตลาดพร้าว กรุงเทพมหานคร",
                    "latitude": 13.93183,
                    "longitude": 100.63952,
                    "report_date_bkk": "2026-09-25",
                    "report_hour_bkk": 15,
                    "is_flood_report": True,
                    "flood_classification": "flood",
                },
                {
                    "ticket_id": "T-002",
                    "address": "แขวงลาดพร้าว เขตลาดพร้าว กรุงเทพมหานคร",
                    "latitude": 13.93183,
                    "longitude": 100.63952,
                    "report_date_bkk": "2026-09-25",
                    "report_hour_bkk": 15,
                    "is_flood_report": False,
                    "flood_classification": "non_flood",
                },
                {
                    "ticket_id": "T-003",
                    "address": "แขวงหลักสอง เขตบางแค กรุงเทพมหานคร",
                    "latitude": 13.69158,
                    "longitude": 100.38127,
                    "report_date_bkk": "2026-09-25",
                    "report_hour_bkk": 15,
                    "is_flood_report": True,
                    "flood_classification": "flood",
                },
            ]
            (tf_dir / "traffy_2026-09-25.jsonl").write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in reports) + "\n",
                encoding="utf-8",
            )

            builder = CuratedBuilder(
                raw_thaiwater_dir=tmp_path / "empty_raw",
                incoming_traffy_dir=tmp_path / "empty_incoming",
                trusted_dir=trusted_dir,
                output_dir=out_dir,
            )
            rows = builder.build_khwaeng_hourly(["2026-09-25"])
            self.assertEqual(len(rows), 2)

            by_kh = {r["khwaeng"]: r for r in rows}
            # Ladprao paired with Station 8 -> newer_wl wins (waterlevel 2.05, freeboard -0.08 -> CRITICAL_OVERFLOW)
            lp = by_kh["ลาดพร้าว"]
            self.assertEqual(lp["paired_water_station_id"], 8)
            self.assertEqual(lp["max_waterlevel_msl"], 2.05)
            self.assertEqual(lp["min_freeboard_m"], -0.08)
            self.assertTrue(lp["is_overflow"])
            self.assertEqual(lp["risk_tier"], "CRITICAL_OVERFLOW")
            self.assertEqual(lp["total_reports"], 2)
            self.assertEqual(lp["flood_reports"], 1)
            self.assertEqual(lp["flood_report_ratio"], 0.5)

            # Lak Song paired with Station 10 -> SUSPECT_DATUM
            ls = by_kh["หลักสอง"]
            self.assertEqual(ls["paired_water_station_id"], 10)
            self.assertFalse(ls["usable_for_freeboard"])
            self.assertEqual(ls["risk_tier"], "SUSPECT_DATUM")

            # Verify partitioned JSONL save
            builder.save_curated(rows, "jsonl")
            part_file = out_dir / "dt=2026-09-25" / "khwaeng_hourly.jsonl"
            self.assertTrue(part_file.exists())
            lines = [ln for ln in part_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), 2)

    def test_denominator_preservation_and_ratios(self):
        mock_row = {
            "dt": "2026-09-25",
            "hour": 15,
            "khwaeng": "หัวหมาก",
            "khet": "บางกะปิ",
            "paired_water_station_id": 852087,
            "water_station_name": "อโศก",
            "canal_name": "คลองแสนแสบ",
            "water_station_dist_km": 1.25,
            "max_waterlevel_msl": 2.45,
            "avg_waterlevel_msl": 2.40,
            "min_bank_msl": 2.39,
            "raw_freeboard_m": -0.06,
            "min_freeboard_m": -0.06,
            "water_quality_flag": "NORMAL",
            "usable_for_freeboard": True,
            "is_overflow": True,
            "risk_tier": "CRITICAL_OVERFLOW",
            "rainfall_daily_mm": 205.4,
            "total_reports": 100,
            "flood_reports": 80,
            "uncertain_reports": 5,
            "non_flood_reports": 15,
            "flood_report_ratio": 0.80,
        }
        analyzer = CuratedAnalyzer([mock_row])
        q2 = analyzer.analyze_q2()
        tier_res = q2["tier_metrics"]["CRITICAL_OVERFLOW"]
        self.assertEqual(tier_res["total_reports"], 100)
        self.assertEqual(tier_res["flood_reports"], 80)
        self.assertEqual(tier_res["flood_ratio_pct"], 80.0)

        near_res = q2["near_station_tier_metrics_2km"]["CRITICAL_OVERFLOW"]
        self.assertEqual(near_res["khwaeng_hours"], 1)
        self.assertEqual(near_res["flood_ratio_pct"], 80.0)
        self.assertIn("2026-09-25", q2["within_day_tier_metrics"])
        self.assertEqual(q2["water_station_distance_stats"]["rows_within_2km"], 1)

    def test_khwaeng_centers_averages_across_all_hours(self):
        with tempfile.TemporaryDirectory(prefix="curated_center_") as tmp:
            tmp_path = Path(tmp)
            trusted_dir = tmp_path / "trusted"
            tf_dir = trusted_dir / "traffy" / "dt=2026-09-25"
            tf_dir.mkdir(parents=True, exist_ok=True)
            reports = [
                {
                    "ticket_id": "T-101",
                    "address": "แขวงหัวหมาก เขตบางกะปิ กรุงเทพมหานคร",
                    "latitude": 13.70000,
                    "longitude": 100.60000,
                    "report_date_bkk": "2026-09-25",
                    "report_hour_bkk": 10,
                    "is_flood_report": True,
                    "flood_classification": "flood",
                },
                {
                    "ticket_id": "T-102",
                    "address": "แขวงหัวหมาก เขตบางกะปิ กรุงเทพมหานคร",
                    "latitude": 13.80000,
                    "longitude": 100.70000,
                    "report_date_bkk": "2026-09-25",
                    "report_hour_bkk": 11,
                    "is_flood_report": False,
                    "flood_classification": "non_flood",
                },
            ]
            (tf_dir / "traffy_2026-09-25.jsonl").write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in reports) + "\n",
                encoding="utf-8",
            )
            builder = CuratedBuilder(
                raw_thaiwater_dir=tmp_path / "empty_raw",
                incoming_traffy_dir=tmp_path / "empty_incoming",
                trusted_dir=trusted_dir,
                output_dir=tmp_path / "out",
            )
            rows = builder.build_khwaeng_hourly(["2026-09-25"])
            self.assertEqual(len(rows), 2)
            # Both hours should use the pooled average across hours 10 and 11: (13.75, 100.65)
            self.assertAlmostEqual(rows[0]["center_latitude"], 13.75, places=4)
            self.assertAlmostEqual(rows[0]["center_longitude"], 100.65, places=4)
            self.assertAlmostEqual(rows[1]["center_latitude"], 13.75, places=4)
            self.assertAlmostEqual(rows[1]["center_longitude"], 100.65, places=4)

    def test_data_quality_report_metrics(self):
        mock_rows = [
            {
                "dt": "2026-09-24",
                "hour": 0,
                "khwaeng": "หลักสอง",
                "khet": "บางแค",
                "paired_water_station_id": 10,
                "water_station_name": "คลองภาษีเจริญ เพชรเกษม69",
                "canal_name": "คลองภาษีเจริญ",
                "max_waterlevel_msl": -6.149,
                "avg_waterlevel_msl": -6.149,
                "min_bank_msl": 1.42,
                "raw_freeboard_m": 7.569,
                "min_freeboard_m": None,
                "water_quality_flag": "SUSPECT_DATUM_MISMATCH",
                "usable_for_freeboard": False,
                "is_overflow": False,
                "risk_tier": "SUSPECT_DATUM",
                "rainfall_daily_mm": None,
                "total_reports": 10,
                "flood_reports": 2,
                "uncertain_reports": 0,
                "non_flood_reports": 8,
                "flood_report_ratio": 0.2,
            }
        ]
        analyzer = CuratedAnalyzer(mock_rows)
        dq = analyzer.generate_data_quality_report()
        self.assertEqual(dq["sensor_outliers_audit"]["detected_outlier_count"], 1)
        self.assertEqual(dq["missing_data_audit"]["null_rain_percentage"], 100.0)
        self.assertEqual(dq["spatial_join_coverage"]["khwaeng_extraction_match_pct"], 100.0)
        self.assertEqual(dq["spatial_join_coverage"]["distinct_khwaeng_khet_pairs"], 1)
        self.assertEqual(dq["spatial_join_coverage"]["official_bkk_khwaeng_count"], 180)
        self.assertEqual(dq["spatial_join_coverage"]["official_bkk_khet_count"], 50)


if __name__ == "__main__":
    unittest.main()
