"""
Unit Tests for ThaiWater Ingestion & Backfill (Member 1)
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.thaiwater_client import ThaiWaterClient
from src.storage import LocalStorageDriver
from src.ingest_thaiwater import create_envelope, ingest_waterlevel, ingest_rain, lambda_handler
from src.backfill import BackfillManager


class TestThaiWaterIngestion(unittest.TestCase):

    def setUp(self):
        self.client = ThaiWaterClient()
        self.temp_dir = tempfile.mkdtemp()
        self.storage = LocalStorageDriver(base_dir=Path(self.temp_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_station_metadata_loaded(self):
        """Verifies that 8 target canal stations with min_bank exist."""
        water_stations = self.client.get_water_stations()
        self.assertEqual(len(water_stations), 8, "Expected 8 target water level stations in Bangkok")
        
        for st in water_stations:
            self.assertIn("station_id", st)
            self.assertIn("min_bank_msl", st)
            self.assertIn("latitude", st)
            self.assertIn("longitude", st)
            self.assertGreater(st["min_bank_msl"], 0.0)

        # Check special outlier note for Phasi Charoen
        psc = next(s for s in water_stations if s["station_id"] == "BKK008")
        self.assertIn("คลองภาษีเจริญ", psc["station_name_th"])

    def test_create_envelope(self):
        """Verifies envelope format conforms to Schema Contract."""
        sample_records = [{"station_id": "BKK001", "waterlevel_msl": 1.95}]
        env = create_envelope("waterlevel", "test_endpoint", sample_records)

        self.assertEqual(env["envelope_version"], "1.0")
        self.assertEqual(env["data_type"], "waterlevel")
        self.assertEqual(env["record_count"], 1)
        self.assertIn("batch_id", env)
        self.assertIn("ingested_at_bkk", env)
        self.assertEqual(env["data"], sample_records)

    def test_ingest_waterlevel_saves_to_raw_and_incoming(self):
        """Tests that waterlevel ingestion writes valid JSON to incoming/ and raw/."""
        res = ingest_waterlevel(self.client, self.storage, use_mock=True)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["record_count"], 8)
        self.assertTrue(self.storage.exists(res["incoming_key"]), "File must exist in incoming/thaiwater/dt=.../hour=.../")
        self.assertTrue(self.storage.exists(res["partition_key"]), "File must exist in raw/thaiwater/waterlevel/...")

        # Verify saved content
        saved_data = self.storage.load_json(res["partition_key"])
        self.assertEqual(saved_data["record_count"], 8)
        self.assertEqual(len(saved_data["data"]), 8)
        first_rec = saved_data["data"][0]
        self.assertIn("diff_wl_bank_m", first_rec)

    def test_ingest_rain_saves_to_raw_and_incoming(self):
        """Tests that rainfall ingestion writes valid JSON to incoming/ and raw/."""
        res = ingest_rain(self.client, self.storage, use_mock=True)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertGreater(res["record_count"], 0)
        self.assertTrue(self.storage.exists(res["incoming_key"]), "File must exist in incoming/thaiwater/dt=.../hour=.../")
        self.assertTrue(self.storage.exists(res["partition_key"]), "File must exist in raw/thaiwater/rain/...")

    def test_lambda_handler_dispatcher(self):
        """Tests Lambda handler with different trigger types."""
        # Test waterlevel event
        event_wl = {"type": "waterlevel", "mock": True}
        res_wl = lambda_handler(event_wl)
        self.assertEqual(res_wl["statusCode"], 200)
        self.assertEqual(len(res_wl["results"]), 1)
        self.assertEqual(res_wl["results"][0]["data_type"], "waterlevel")

        # Test rain event
        event_rn = {"type": "rain", "mock": True}
        res_rn = lambda_handler(event_rn)
        self.assertEqual(res_rn["statusCode"], 200)
        self.assertEqual(len(res_rn["results"]), 1)
        self.assertEqual(res_rn["results"][0]["data_type"], "rain")

        # Test all event
        event_all = {"type": "all", "mock": True}
        res_all = lambda_handler(event_all)
        self.assertEqual(res_all["statusCode"], 200)
        self.assertEqual(len(res_all["results"]), 2)

    def test_backfill_idempotency_success_criteria_1(self):
        """
        Validates Success Criteria 1:
        Running backfill repeatedly must be idempotent and not duplicate data.
        """
        manager = BackfillManager(backend="local")
        # Override storage to temporary directory
        manager.storage = self.storage
        manager.checkpoint_path = Path(self.temp_dir) / "checkpoint.json"
        manager.checkpoints = {"completed_tasks": {}}

        # First backfill execution
        res1 = manager.backfill_waterlevel_monthly("BKK001", year=2026, month=9, force=False, use_mock=True)
        self.assertEqual(res1["status"], "SUCCESS")
        file_path = res1["destination"]

        # Read row count
        with open(file_path, "r", encoding="utf-8") as f:
            data1 = json.load(f)
        count_first_run = len(data1["data"])

        # Second backfill execution without force (Should be SKIPPED)
        res2 = manager.backfill_waterlevel_monthly("BKK001", year=2026, month=9, force=False, use_mock=True)
        self.assertEqual(res2["status"], "SKIPPED")

        # Third backfill execution with force (Should OVERWRITE idempotently without bloating rows)
        res3 = manager.backfill_waterlevel_monthly("BKK001", year=2026, month=9, force=True, use_mock=True)
        self.assertEqual(res3["status"], "SUCCESS")

        with open(file_path, "r", encoding="utf-8") as f:
            data3 = json.load(f)
        count_third_run = len(data3["data"])

        self.assertEqual(count_first_run, count_third_run, "Row count must remain identical upon re-run (Idempotent)")


if __name__ == "__main__":
    unittest.main()
