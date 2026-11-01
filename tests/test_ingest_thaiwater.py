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
        from datetime import timedelta
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


if __name__ == "__main__":
    unittest.main()
