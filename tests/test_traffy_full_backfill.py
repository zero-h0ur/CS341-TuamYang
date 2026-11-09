"""Offline checks for history integrity and bounded date ownership."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import time
import unittest

from scripts.run_traffy_backfill import PacedOpener, audit_day, audit_range, run_download
from src.ingest_traffy import TraffyIngestor
from src.storage import LocalStorageDriver
from tests import test_traffy as fixtures


class HistoryAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = LocalStorageDriver(self.temp.name)
        client = fixtures.FakeClient()
        # Simulate the real-data flag only inside this disposable test fixture.
        client.is_mock = False
        self.manifest = TraffyIngestor(client, self.storage, now=lambda: datetime(
            2026, 11, 9, tzinfo=timezone.utc)).ingest_day("2026-09-24")

    def test_missing_day_prevents_full_range_completion(self):
        result = audit_range(self.storage, "2026-09-24", "2026-09-25")
        self.assertFalse(result["range_complete"])
        self.assertEqual(result["verified_days"], 1)
        self.assertEqual(result["gaps"][0]["date_bkk"], "2026-09-25")

    def test_complete_delivery_requires_both_outputs_and_raw_terminal(self):
        result = audit_range(self.storage, "2026-09-24", "2026-09-24")
        self.assertTrue(result["range_complete"])
        self.assertEqual((result["record_count"], result["flood_count"]), (1, 1))
        Path(self.temp.name, self.manifest["raw_page_keys"][-1]).unlink()
        with self.assertRaisesRegex(ValueError, "raw provenance"):
            audit_day(self.storage, "2026-09-24")

    def test_changed_report_is_detected_even_if_checkpoint_says_success(self):
        key = self.manifest["incoming_key"]
        payload = self.storage.load_json(key)
        payload["data"][0]["description"] = "fixture changed after publication"
        self.storage.save_json(key, payload)
        self.assertFalse(audit_range(self.storage, "2026-09-24", "2026-09-24")["range_complete"])

    def test_wrong_batch_or_mock_flag_is_not_real_delivery_evidence(self):
        key = self.manifest["incoming_key"]
        original = self.storage.load_json(key)
        for change in ({"batch_id": "wrong"}, {"is_mock": True}, {"date_bkk": "2026-09-25"}):
            with self.subTest(change=change):
                self.storage.save_json(key, {**original, **change})
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    audit_day(self.storage, "2026-09-24")

    def test_incomplete_source_terminal_is_not_silently_accepted(self):
        key = self.manifest["raw_page_keys"][-1]
        payload = self.storage.load_json(key)
        payload["response"]["total"] = 100
        self.storage.save_json(key, payload)
        with self.assertRaisesRegex(ValueError, "terminal empty"):
            audit_day(self.storage, "2026-09-24")


class ControlledRunnerTests(unittest.TestCase):
    def test_workers_are_bounded_and_failed_dates_retry_without_overlapping(self):
        lock = threading.Lock()
        active, calls, peak = set(), {}, [0]
        def ingest(day):
            with lock:
                self.assertNotIn(day, active)
                active.add(day)
                peak[0] = max(peak[0], len(active))
                calls[day] = calls.get(day, 0) + 1
                attempt = calls[day]
            time.sleep(0.01)
            with lock:
                active.remove(day)
            return {"status": "FAILED" if day == "day-2" and attempt == 1 else "SUCCESS", "error": "fixture"}
        result = run_download(ingest, [f"day-{i}" for i in range(8)], workers=3)
        self.assertEqual(len(result["completed"]), 8)
        self.assertEqual(calls["day-2"], 2)
        self.assertFalse(result["failures"])
        self.assertLessEqual(peak[0], 3)

    def test_graceful_stop_does_not_submit_more_dates(self):
        stopping = threading.Event()
        calls = []
        def ingest(day):
            calls.append(day)
            stopping.set()
            return {"status": "SUCCESS"}
        result = run_download(ingest, ["first", "second"], workers=1, stop=stopping.is_set)
        self.assertEqual(calls, ["first"])
        self.assertTrue(result["stopped"])
        self.assertEqual(result["completed"], ["first"])

    def test_persistent_failure_stays_visible_after_bounded_retries(self):
        calls = []
        def fail(day):
            calls.append(day)
            raise OSError("fixture failure")
        result = run_download(fail, ["day"], workers=1, attempts=2)
        self.assertEqual(calls, ["day", "day"])
        self.assertEqual(result["completed"], [])
        self.assertIn("fixture failure", result["failures"]["day"])

    def test_invalid_worker_count_and_duplicate_dates_fail_before_io(self):
        for days, workers in ((["a"], 0), (["a"], 5), (["a", "a"], 2)):
            with self.subTest(days=days, workers=workers), self.assertRaises(ValueError):
                run_download(lambda _: self.fail("unexpected work"), days, workers=workers)

    def test_request_pacing_is_shared_by_every_call(self):
        now, starts = [0.0], []
        def sleep(delay):
            now[0] += delay
        paced = PacedOpener(0.5, opener=lambda *_: starts.append(now[0]),
                            clock=lambda: now[0], sleep=sleep)
        for _ in range(3):
            paced("fixture")
        self.assertEqual(starts, [0.0, 0.5, 1.0])


if __name__ == "__main__":
    unittest.main()
