"""
Unit Tests for ThaiWater Ingestion & Backfill Engine (Member 1)
Owner: Member 1 (PurinatWanthanathanya)
"""

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backfill import BackfillManager
from src.ingest_thaiwater import create_envelope, ingest_rain, ingest_waterlevel, lambda_handler
from src.storage import LocalStorageDriver
from src.thaiwater_client import ThaiWaterAPIError, ThaiWaterClient
from tests.mock_client import MockThaiWaterClient


class TestThaiWaterIngestion(unittest.TestCase):

    def setUp(self):
        self.mock_client = MockThaiWaterClient()
        self.temp_dir = tempfile.mkdtemp()
        self.storage = LocalStorageDriver(base_dir=Path(self.temp_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_real_water_stations_metadata(self):
        """Verifies the 8 target Bangkok canal stations with real ThaiWater API IDs."""
        real_client = ThaiWaterClient()
        stations = real_client.get_water_stations()
        self.assertEqual(len(stations), 8, "Expected 8 target Bangkok canal stations")

        target_ids = {1, 3, 4, 5, 8, 10, 2599, 852087}
        actual_ids = {s["api_station_id"] for s in stations}
        self.assertEqual(actual_ids, target_ids, "Station IDs must match real ThaiWater API telemetry IDs")

        for s in stations:
            self.assertIn("min_bank_msl", s)
            self.assertIn("latitude", s)
            self.assertIn("longitude", s)
            self.assertIsNotNone(s["latitude"])
            self.assertIsNotNone(s["longitude"])

    def test_create_envelope_schema(self):
        """Verifies envelope conforms to standard schema contract."""
        sample_records = [{"api_station_id": 1, "waterlevel_msl": 1.45}]
        env = create_envelope("waterlevel", "test_endpoint", sample_records, is_mock=False)

        self.assertEqual(env["envelope_version"], "1.0")
        self.assertEqual(env["data_type"], "waterlevel")
        self.assertFalse(env["is_mock"])
        self.assertEqual(env["record_count"], 1)
        self.assertIn("batch_id", env)
        self.assertIn("ingested_at_bkk", env)
        self.assertEqual(env["data"], sample_records)

    def test_ingest_waterlevel_saves_to_raw_and_incoming(self):
        """Tests that waterlevel ingestion saves to both incoming/ and raw/ zones."""
        res = ingest_waterlevel(self.mock_client, self.storage)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["record_count"], 8)
        self.assertTrue(self.storage.exists(res["incoming_key"]), "File must exist in incoming/thaiwater/dt=.../hour=.../")
        self.assertTrue(self.storage.exists(res["partition_key"]), "File must exist in raw/thaiwater/waterlevel/...")

        saved_data = self.storage.load_json(res["partition_key"])
        self.assertEqual(saved_data["record_count"], 8)
        self.assertEqual(len(saved_data["data"]), 8)

    def test_ingest_rain_saves_to_raw_and_incoming(self):
        """Tests that rainfall ingestion saves to both incoming/ and raw/ zones."""
        res = ingest_rain(self.mock_client, self.storage)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertGreater(res["record_count"], 0)
        self.assertTrue(self.storage.exists(res["incoming_key"]), "File must exist in incoming/thaiwater/dt=.../hour=.../")
        self.assertTrue(self.storage.exists(res["partition_key"]), "File must exist in raw/thaiwater/rain/...")

        saved_data = self.storage.load_json(res["partition_key"])
        first_record = saved_data["data"][0]
        self.assertIn("series", first_record)
        self.assertIn("rain_24h_points", first_record)
        self.assertEqual(first_record["rain_24h_points"], 24)
        self.assertEqual(len(first_record["series"]), 36)

    def test_ingest_rain_partial_failure(self):
        """Tests that partial station failures result in PARTIAL status with failed_stations listed."""
        class PartiallyFailingMock(MockThaiWaterClient):
            def fetch_24h_rain_station(self, api_station_id: int):
                if api_station_id == 2:  # fail station 2
                    raise ThaiWaterAPIError("Station 2 timeout")
                return super().fetch_24h_rain_station(api_station_id)

        failing_client = PartiallyFailingMock()
        res = ingest_rain(failing_client, self.storage)
        self.assertEqual(res["status"], "PARTIAL")
        self.assertEqual(res["failed_count"], 1)
        self.assertEqual(res["failed_stations"][0]["station_id"], 2)
        self.assertGreater(res["record_count"], 0)

    def test_backfill_ongoing_month_not_checkpointed(self):
        """Validates that the current ongoing month is not marked complete in checkpoint."""
        manager = BackfillManager(backend="local")
        manager.storage = self.storage
        manager.client = self.mock_client
        manager.checkpoint_path = Path(self.temp_dir) / "checkpoint.json"
        manager.checkpoints = {"completed_tasks": {}, "failed_tasks": {}}

        from datetime import datetime
        now = datetime.now()
        cur_y, cur_m = now.year, now.month

        res = manager.backfill_waterlevel_monthly(1, year=cur_y, month=cur_m, force=False)
        self.assertEqual(res["status"], "SUCCESS")

        task_key = f"wl_st1_{cur_y}_{cur_m:02d}"
        self.assertNotIn(task_key, manager.checkpoints["completed_tasks"],
                         "Ongoing current month must not be marked complete in checkpoint")
        self.assertFalse(manager.is_task_completed(task_key, res["destination"]),
                         "is_task_completed must return False for current ongoing month")

    def test_backfill_idempotency_success_criteria_1(self):
        """
        Validates Success Criteria 1:
        Re-running the same task must be idempotent and skip without duplicate data.
        """
        manager = BackfillManager(backend="local")
        manager.storage = self.storage
        manager.client = self.mock_client
        manager.checkpoint_path = Path(self.temp_dir) / "checkpoint.json"
        manager.checkpoints = {"completed_tasks": {}, "failed_tasks": {}}

        # Run 1: Execution
        res1 = manager.backfill_waterlevel_monthly(1, year=2026, month=9, force=False)
        self.assertEqual(res1["status"], "SUCCESS")
        file_path = res1["destination"]

        with open(file_path, "r", encoding="utf-8") as f:
            data1 = json.load(f)
        count_first_run = len(data1["data"])

        # Run 2: Re-run should skip
        res2 = manager.backfill_waterlevel_monthly(1, year=2026, month=9, force=False)
        self.assertEqual(res2["status"], "SKIPPED")

        # Run 3: Force overwrite must maintain identical row count
        res3 = manager.backfill_waterlevel_monthly(1, year=2026, month=9, force=True)
        self.assertEqual(res3["status"], "SUCCESS")

        with open(file_path, "r", encoding="utf-8") as f:
            data3 = json.load(f)
        count_third_run = len(data3["data"])

        self.assertEqual(count_first_run, count_third_run, "Row count must remain identical upon re-run (Idempotent)")

    def test_strict_fail_fast_on_api_error(self):
        """
        Validates that API errors are never swallowed:
        Failed tasks must NOT write files and must NOT be marked complete in checkpoint.
        """
        manager = BackfillManager(backend="local")
        manager.storage = self.storage
        manager.checkpoint_path = Path(self.temp_dir) / "checkpoint.json"
        manager.checkpoints = {"completed_tasks": {}, "failed_tasks": {}}

        # Mock client that raises API error
        class BrokenClient(ThaiWaterClient):
            def fetch_historical_waterlevel(self, *args, **kwargs):
                raise ThaiWaterAPIError("Connection refused by ThaiWater API")

        manager.client = BrokenClient()

        res = manager.backfill_waterlevel_monthly(1, year=2026, month=9)
        self.assertEqual(res["status"], "FAILED")
        self.assertIn("Connection refused", res["error"])

        # Checkpoint must NOT mark task as completed
        self.assertNotIn("wl_st1_2026_09", manager.checkpoints["completed_tasks"])
        # Task must be recorded under failed_tasks
        self.assertIn("wl_st1_2026_09", manager.checkpoints["failed_tasks"])

    def test_fetch_24h_rain_station_series_and_24h_points(self):
        """Verifies fetch_24h_rain_station parses series, handles nulls, and sums exactly 24 points."""
        client = ThaiWaterClient()
        # Mock _request_json to return 36 hours of data (from 2026-10-31 00:00 to 2026-11-01 11:00)
        items = []
        base = datetime(2026, 10, 31, 0, 0)
        for i in range(36):
            dt = base + timedelta(hours=i)
            # 1.0 mm per hour, except index 35 which has null
            val = None if i == 35 else "1.0"
            items.append({"rainfall_datetime": dt.strftime("%Y-%m-%d %H:%M"), "rainfall_value": val})

        client._request_json = lambda url: {"data": items}

        res = client.fetch_24h_rain_station(api_station_id=2)
        self.assertEqual(res["rain_24h_points"], 24)
        self.assertEqual(res["rain_24h_mm"], 24.0)
        self.assertEqual(len(res["series"]), 36)
        self.assertIsNone(res["series"][-1]["rain_mm"])
        self.assertEqual(res["window_start"], "2026-10-31 00:00")
        self.assertEqual(res["window_end"], "2026-11-01 11:00")

    def test_fetch_historical_waterlevel_sends_tele_waterlevel_param(self):
        """Verifies fetch_historical_waterlevel includes station_type=tele_waterlevel."""
        client = ThaiWaterClient()
        captured_url = []
        client._request_json = lambda url: (captured_url.append(url), {"data": {"graph_data": [{"datetime": "2026-09-01 00:00", "value": "1.23"}], "min_bank": 2.2}})[1]

        res = client.fetch_historical_waterlevel(api_station_id=1, start_date="2026-09-01", end_date="2026-09-30")
        self.assertEqual(res["record_count"], 1)
        self.assertIn("station_type=tele_waterlevel", captured_url[0])
        self.assertEqual(res["records"][0]["waterlevel_msl"], 1.23)
        self.assertEqual(res["records"][0]["min_bank_msl"], 2.2)

    def test_backfill_all_null_records_treated_as_empty(self):
        """Validates that a backfill response with only null values is treated as EMPTY and not saved."""
        manager = BackfillManager(backend="local")
        manager.storage = self.storage
        manager.checkpoint_path = Path(self.temp_dir) / "checkpoint.json"
        manager.checkpoints = {"completed_tasks": {}, "failed_tasks": {}}

        class AllNullClient(ThaiWaterClient):
            def fetch_historical_waterlevel(self, *args, **kwargs):
                return {
                    "source": "mock",
                    "status": "success",
                    "is_mock": True,
                    "records": [
                        {"api_station_id": 1, "datetime": "2026-09-01 00:00", "waterlevel_msl": None},
                        {"api_station_id": 1, "datetime": "2026-09-01 00:10", "waterlevel_msl": None},
                    ]
                }

        manager.client = AllNullClient()
        res = manager.backfill_waterlevel_monthly(1, year=2026, month=9)
        self.assertEqual(res["status"], "EMPTY")
        self.assertEqual(res["record_count"], 0)
        self.assertNotIn("wl_st1_2026_09", manager.checkpoints["completed_tasks"])

    def test_backfill_below_non_null_threshold_rejected(self):
        """Validates that a backfill with non-null ratio below 30% is rejected as EMPTY."""
        manager = BackfillManager(backend="local")
        manager.storage = self.storage
        manager.checkpoint_path = Path(self.temp_dir) / "checkpoint.json"
        manager.checkpoints = {"completed_tasks": {}, "failed_tasks": {}}

        class LowCoverageClient(ThaiWaterClient):
            def fetch_historical_waterlevel(self, *args, **kwargs):
                # 10 non-null out of 100 slots = 10% (< 30% threshold)
                records = []
                for i in range(100):
                    val = 1.5 if i < 10 else None
                    records.append({"api_station_id": 1, "datetime": f"2026-09-01 {i:02d}:00", "waterlevel_msl": val})
                return {
                    "source": "mock",
                    "status": "success",
                    "is_mock": True,
                    "records": records,
                }

        manager.client = LowCoverageClient()
        res = manager.backfill_waterlevel_monthly(1, year=2026, month=9)
        self.assertEqual(res["status"], "EMPTY")
        self.assertEqual(res["record_count"], 10)
        self.assertEqual(res["non_null_ratio"], 0.1)
        self.assertNotIn("wl_st1_2026_09", manager.checkpoints["completed_tasks"])

    def test_historical_waterlevel_bank_divergence_corrected(self):
        """Verifies that API returning negative ground_level or divergent min_bank falls back to reference."""
        client = ThaiWaterClient()
        # Mock API returning -0.33 (ground level) instead of 2.2
        client._request_json = lambda url: {
            "data": {
                "graph_data": [{"datetime": "2026-09-01 00:00", "value": "1.20"}],
                "min_bank": -0.33,
            }
        }
        res = client.fetch_historical_waterlevel(api_station_id=1, start_date="2026-09-01", end_date="2026-09-30")
        self.assertEqual(res["records"][0]["min_bank_msl"], 2.2, "Must fall back to reference bank 2.2 when API returns negative ground level")
        self.assertEqual(res["records"][0]["diff_wl_bank_m"], 1.0)

    def test_phasi_charoen_station_10_flagged_as_suspect_datum(self):
        """Verifies that Phasi Charoen (ID 10) is flagged as SUSPECT_DATUM_MISMATCH and unusable for freeboard."""
        client = ThaiWaterClient()
        # Mock API returning negative waterlevel -5.5m MSL
        client._request_json = lambda url: {
            "data": {
                "graph_data": [{"datetime": "2026-09-01 00:00", "value": "-5.50"}],
                "min_bank": 1.415,
            }
        }
        res = client.fetch_historical_waterlevel(api_station_id=10, start_date="2026-09-01", end_date="2026-09-30")
        rec = res["records"][0]
        self.assertEqual(rec["quality_flag"], "SUSPECT_DATUM_MISMATCH")
        self.assertFalse(rec["usable_for_freeboard"])
        self.assertGreater(rec["diff_wl_bank_m"], 5.0)

    def test_real_rain_stations_metadata(self):
        """Verifies reference/stations_rain.json has exactly 14 valid Bangkok stations with verified coordinates."""
        client = ThaiWaterClient()
        stations = client.get_rain_stations()
        self.assertEqual(len(stations), 14, "Expected 14 target Bangkok rain stations (station 4 removed due to no rain sensor)")

        # Verify station 4 (Saphan Krungthep) was removed because it only measures waterlevel
        self.assertNotIn(4, [s["api_station_id"] for s in stations])

        # Verify station 852087 (Asok) is flagged as SUSPECT_RAIN_SENSOR_OUTAGE and unusable for rainfall
        st_asok = next((s for s in stations if s["api_station_id"] == 852087), None)
        self.assertIsNotNone(st_asok)
        self.assertEqual(st_asok.get("quality_flag"), "SUSPECT_RAIN_SENSOR_OUTAGE")
        self.assertFalse(st_asok.get("usable_for_rainfall"))

        # Verify station 2 is real Saen Saep (Bang Kapi) station and not dummy rounded (13.72, 100.51)
        st2 = next((s for s in stations if s["api_station_id"] == 2), None)
        self.assertIsNotNone(st2)
        self.assertEqual(st2["tele_station_oldcode"], "BKK008")
        self.assertEqual(st2["station_name_th"], "คลองแสนแสบ บางกะปิ")
        self.assertNotEqual((st2["latitude"], st2["longitude"]), (13.72, 100.51))
        self.assertAlmostEqual(st2["latitude"], 13.76131, places=4)
        self.assertAlmostEqual(st2["longitude"], 100.61601, places=4)

        # Verify suspect non-Bangkok station IDs (20, 23, 24, 25, 26, 28, 29, 37) are removed
        suspect_out_of_bkk = {20, 23, 24, 25, 26, 28, 29, 37}
        actual_ids = {s["api_station_id"] for s in stations}
        self.assertTrue(actual_ids.isdisjoint(suspect_out_of_bkk), "Suspect out-of-province IDs must not be in Bangkok rain reference")

        for s in stations:
            self.assertEqual(s.get("province_th"), "กรุงเทพมหานคร", f"Station {s['api_station_id']} must be in Bangkok")
            self.assertIsInstance(s.get("latitude"), float)
            self.assertIsInstance(s.get("longitude"), float)
            # Bangkok bounding box check
            self.assertTrue(13.5 <= s["latitude"] <= 14.1, f"Latitude {s['latitude']} out of Bangkok bounds")
            self.assertTrue(100.3 <= s["longitude"] <= 100.95, f"Longitude {s['longitude']} out of Bangkok bounds")

    def test_persisted_data_integrity_check(self):
        """
        Validates persisted files in data/ directory:
        1. All 8 water stations are backfilled for Sep & Oct 2026.
        2. Bang Bua (st 1) has >= 95% non-null readings, bank = 2.2, max Sep 24-26 ~ 2.82m.
        3. Phasi Charoen (st 10) is flagged with SUSPECT_DATUM_MISMATCH and unusable for freeboard.
        4. Samsen (2599) and Asok (852087) are backfilled with non-empty telemetry.
        """
        data_dir = Path(__file__).resolve().parent.parent / "data" / "raw" / "thaiwater" / "waterlevel"
        sep_dir = data_dir / "dt=2026-09-01"
        if not sep_dir.exists():
            self.skipTest("Local data/ directory not yet populated with backfill data")

        target_ids = [1, 3, 4, 5, 8, 10, 2599, 852087]
        for sid in target_ids:
            fpath = sep_dir / f"waterlevel_hist_st{sid}_2026_09.json"
            self.assertTrue(fpath.exists(), f"Missing backfill file for station {sid} in Sep 2026: {fpath}")
            with open(fpath, "r", encoding="utf-8") as f:
                doc = json.load(f)
            records = doc.get("data", [])
            non_null = [r for r in records if r.get("waterlevel_msl") is not None]
            self.assertGreater(len(non_null), 0, f"Station {sid} has 0 non-null records")

            if sid == 1:
                pct = len(non_null) / len(records)
                self.assertGreaterEqual(pct, 0.95, f"Bang Bua non-null ratio ({pct:.1%}) must be >= 95%")
                self.assertEqual(records[0]["min_bank_msl"], 2.2, "Bang Bua min_bank must be 2.2m MSL")
                sep24_26 = [r["waterlevel_msl"] for r in non_null if "2026-09-24" <= r["datetime"][:10] <= "2026-09-26"]
                self.assertTrue(bool(sep24_26), "Missing readings during storm period Sep 24-26")
                max_wl = max(sep24_26)
                self.assertAlmostEqual(max_wl, 2.82, delta=0.05, msg="Bang Bua storm peak must be ~2.82m")

            if sid == 10:
                self.assertEqual(records[0]["quality_flag"], "SUSPECT_DATUM_MISMATCH")
                self.assertFalse(records[0]["usable_for_freeboard"])
                self.assertTrue(any(r["waterlevel_msl"] < -1.0 for r in non_null))

            if sid in (2599, 852087):
                self.assertGreater(len(non_null), 500, f"Station {sid} should have >= 500 non-null readings")

        # Check rain persisted files:
        rain_dir = Path(__file__).resolve().parent.parent / "data" / "raw" / "thaiwater" / "rain" / "dt=2026-09-01"
        if rain_dir.exists():
            st4_rain = rain_dir / "rain_monthly_st4_2026_09.json"
            self.assertFalse(st4_rain.exists(), "Rain station 4 must be removed as it lacks a rain sensor")

            st852087_rain = rain_dir / "rain_monthly_st852087_2026_09.json"
            if st852087_rain.exists():
                with open(st852087_rain, "r", encoding="utf-8") as f:
                    rain_doc = json.load(f)
                rain_records = rain_doc.get("data", [])
                if rain_records:
                    self.assertEqual(rain_records[0].get("quality_flag"), "SUSPECT_RAIN_SENSOR_OUTAGE")
                    self.assertFalse(rain_records[0].get("usable_for_rainfall"))


if __name__ == "__main__":
    unittest.main()
