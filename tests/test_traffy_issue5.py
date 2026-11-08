"""Issue #5 behavior checks. Every source row here is a synthetic fixture."""

import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest.mock import Mock, call, patch

from src.ingest_traffy import TraffyIngestor, ingest_hourly, lambda_handler
from src.backfill_traffy import backfill
from src.storage import LocalStorageDriver
from src.traffy_client import TraffyAPIError
from scripts.review_traffy import flood_precision
from tests import test_traffy as fixture_tests
from tests.test_traffy import report


class HourlyTests(unittest.TestCase):
    def test_normal_poll_refreshes_today_in_bangkok_even_before_midnight_utc(self):
        engine = Mock()
        engine.ingest_day.return_value = {"status": "SUCCESS"}
        result = ingest_hourly(engine, now=datetime(2026, 11, 8, 21, 30, tzinfo=timezone.utc))
        engine.ingest_day.assert_called_once_with(date(2026, 11, 9), force=True,
                                                  allow_open_day=True, remaining_ms=None)
        self.assertEqual(result["status"], "SUCCESS")

    def test_midnight_poll_catches_yesterdays_last_hour_before_today(self):
        engine = Mock()
        engine.ingest_day.return_value = {"status": "SUCCESS"}
        ingest_hourly(engine, now=datetime(2026, 11, 8, 17, 5, tzinfo=timezone.utc))
        self.assertEqual(engine.ingest_day.call_args_list, [
            call(date(2026, 11, 8), force=True, allow_open_day=True, remaining_ms=None),
            call(date(2026, 11, 9), force=True, allow_open_day=True, remaining_ms=None),
        ])

    def test_midnight_failure_is_not_masked_by_successful_today(self):
        engine = Mock()
        engine.ingest_day.return_value = {"status": "FAILED", "error": "fixture"}
        result = ingest_hourly(engine, now=datetime(2026, 11, 8, 17, 5, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(engine.ingest_day.call_count, 1)

    def test_hourly_lambda_dispatch_and_invalid_mode(self):
        with patch("src.ingest_traffy.TraffyIngestor"), patch("src.ingest_traffy.ingest_hourly") as poll:
            poll.return_value = {"status": "SUCCESS", "mode": "hourly", "results": []}
            self.assertEqual(lambda_handler({"mode": "hourly"})["mode"], "hourly")
            poll.return_value = {"status": "FAILED", "results": [{"status": "FAILED", "error": "fixture"}]}
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                lambda_handler({"mode": "hourly"})
        for event in ({"mode": "typo"}, {"mode": "hourly", "date": "2026-09-24"}):
            with self.assertRaises(ValueError):
                lambda_handler(event)

    def test_current_day_pagination_allows_changing_total_but_closed_day_does_not(self):
        pages = [{"total": 1, "results": [report("1")]},
                 {"total": 2, "results": [report("2")]},
                 {"total": None, "results": []}]
        factory = fixture_tests.ClientTests()
        client = factory.client(pages)
        self.assertEqual(len(list(client.iter_pages("2026-11-09", "2026-11-09", allow_total_change=True))), 3)
        with self.assertRaises(TraffyAPIError):
            list(factory.client(pages).iter_pages("2026-11-08", "2026-11-08"))

    def test_live_cutoff_excludes_future_rows_and_exposes_provisional_warnings(self):
        class LiveClient:
            api_url = "https://fixture.invalid/search"
            is_mock = True

            def iter_pages(self, start, end, remaining_ms=None, allow_total_change=False):
                if not allow_total_change:
                    raise AssertionError("Live date must opt into provisional pagination")
                yield {"index": 0, "params": {}, "response": {
                    "total": 2, "results": [report("valid", "2026-11-09 04:00:00+00")]}}
                yield {"index": 1, "params": {}, "response": {
                    "total": 3, "results": [report("future", "2026-11-09 06:00:00+00")]}}
                yield {"index": 2, "params": {}, "response": {
                    "total": None, "results": []}, "source_exhausted": True}

        with tempfile.TemporaryDirectory() as directory:
            storage = LocalStorageDriver(directory)
            engine = TraffyIngestor(LiveClient(), storage,
                                   now=lambda: datetime(2026, 11, 9, 5, tzinfo=timezone.utc))
            result = engine.ingest_day("2026-11-09", allow_open_day=True)
            self.assertEqual(result["status"], "SUCCESS")
            self.assertTrue(result["is_provisional"])
            self.assertEqual(result["record_count"], 1)
            self.assertEqual(result["window_end_utc_exclusive"], "2026-11-09T05:00:00Z")
            self.assertEqual(result["source_totals_observed"], [2, 3])
            self.assertIn("source_total_changed_during_provisional_poll", result["quality_warnings"])
            saved = storage.load_json(result["incoming_key"])
            self.assertTrue(saved["is_provisional"])
            self.assertEqual(saved["data"][0]["ticket_id"], "valid")

    def test_midnight_closed_day_query_can_include_a_live_adjacent_utc_day(self):
        class MidnightClient:
            api_url = "https://fixture.invalid/search"
            is_mock = True

            def iter_pages(self, start, end, remaining_ms=None, allow_total_change=False):
                if not allow_total_change:
                    raise AssertionError("Padded UTC day is still live before 07:00 Bangkok")
                yield {"index": 0, "params": {}, "response": {
                    "total": 1, "results": [report("yesterday", "2026-11-08 16:30:00+00")]}}
                yield {"index": 1, "params": {}, "response": {
                    "total": 2, "results": [report("today", "2026-11-08 17:01:00+00")]}}
                yield {"index": 2, "params": {}, "response": {
                    "total": None, "results": []}, "source_exhausted": True}

        with tempfile.TemporaryDirectory() as directory:
            engine = TraffyIngestor(MidnightClient(), LocalStorageDriver(directory),
                                   now=lambda: datetime(2026, 11, 8, 17, 5, tzinfo=timezone.utc))
            result = engine.ingest_day("2026-11-08")
            self.assertEqual(result["status"], "SUCCESS")
            self.assertFalse(result["is_provisional"])
            self.assertEqual(result["record_count"], 1)
            self.assertIn("source_total_changed_in_adjacent_live_utc_day", result["quality_warnings"])


class HistoricalBackfillTests(unittest.TestCase):
    def test_a_limited_run_resumes_past_completed_dates_on_the_next_run(self):
        completed = set()
        engine = Mock()
        def ingest(day, force=False):
            status = "SUCCESS" if force or day not in completed else "SKIPPED"
            completed.add(day)
            return {"status": status, "date_bkk": day.isoformat()}
        engine.ingest_day.side_effect = ingest
        first = list(backfill("2022-06-01", "2022-06-05", engine, max_days=2))
        self.assertEqual([r["date_bkk"] for r in first], ["2022-06-01", "2022-06-02"])
        self.assertTrue(first[-1]["backfill_progress"]["budget_exhausted"])
        self.assertEqual(first[-1]["backfill_progress"]["next_date"], "2022-06-03")
        second = list(backfill("2022-06-01", "2022-06-05", engine, max_days=2))
        self.assertEqual([r["status"] for r in second], ["SKIPPED", "SKIPPED", "SUCCESS", "SUCCESS"])
        self.assertEqual(second[-1]["backfill_progress"]["next_date"], "2022-06-05")
        third = list(backfill("2022-06-01", "2022-06-05", engine, max_days=2))
        self.assertTrue(third[-1]["backfill_progress"]["range_complete"])
        self.assertIsNone(third[-1]["backfill_progress"]["next_date"])

    def test_failed_date_remains_the_resume_point(self):
        engine = Mock()
        engine.ingest_day.return_value = {"status": "FAILED", "error": "fixture"}
        results = list(backfill("2022-06-01", "2022-06-05", engine, max_days=2))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["backfill_progress"]["next_date"], "2022-06-01")
        self.assertFalse(results[0]["backfill_progress"]["range_complete"])

    def test_invalid_budget_or_reversed_range_is_rejected_before_fetching(self):
        engine = Mock()
        for budget in (0, -1, True):
            with self.assertRaises(ValueError):
                list(backfill("2022-06-01", "2022-06-05", engine, max_days=budget))
        with self.assertRaises(ValueError):
            list(backfill("2022-06-05", "2022-06-01", engine))
        engine.ingest_day.assert_not_called()


class PrecisionTests(unittest.TestCase):
    def matrix(self, tp, fp, unresolved):
        return {"flood": {"flood": tp}, "non_flood": {"flood": fp},
                "uncertain": {"flood": unresolved}}

    def test_resolved_precision_uses_true_and_false_positives(self):
        result = flood_precision(self.matrix(8, 2, 0))
        self.assertEqual(result["precision"], 0.8)
        self.assertEqual(result["precision_status"], "scored")
        self.assertEqual(result["precision_counts"]["all_predicted_positives"], 10)

    def test_unresolved_positives_remain_in_the_denominator_of_bounds(self):
        result = flood_precision(self.matrix(8, 2, 2))
        self.assertIsNone(result["precision"])
        self.assertAlmostEqual(result["precision_bounds"]["lower"], 8 / 12)
        self.assertAlmostEqual(result["precision_bounds"]["upper"], 10 / 12)
        self.assertAlmostEqual(result["resolved_positive_coverage"], 10 / 12)

    def test_no_positive_predictions_does_not_become_zero_or_perfect_precision(self):
        result = flood_precision(self.matrix(0, 0, 0))
        self.assertIsNone(result["precision"])
        self.assertEqual(result["precision_status"], "no_predicted_positives")
        self.assertEqual(result["precision_bounds"], {"lower": None, "upper": None})


if __name__ == "__main__":
    unittest.main()
