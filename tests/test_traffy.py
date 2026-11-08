"""Behavioral checks for the Traffy Checkpoint; all source rows here are fixtures."""

import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.backfill_traffy import backfill
from src.ingest_traffy import TraffyIngestor, lambda_handler, normalize_report
from src.storage import LocalStorageDriver
from src.traffy_client import TraffyAPIError, TraffyClient
from src.traffy_filter import classify_flood


def report(ticket="fixture-1", timestamp="2026-09-24 05:30:00+00", description="น้ำท่วมถนน"):
    return {"ticket_id": ticket, "timestamp": timestamp, "description": description,
            "coords": ["100.5", "13.7"], "problem_type_abdul": [""],
            "type": None, "last_activity": "2026-09-25 06:00:00+00"}


class FakeClient:
    api_url = "https://fixture.invalid/search"
    is_mock = True

    def __init__(self, rows=None, fail_after_page=False):
        self.rows = rows if rows is not None else [report()]
        self.calls = []
        self.fail_after_page = fail_after_page

    def iter_pages(self, start, end, remaining_ms=None, allow_total_change=False):
        self.calls.append((start, end))
        yield {"index": 0, "params": {"start": start, "end": end, "offset": 0},
               "response": {"status": True, "total": len(self.rows), "results": self.rows}}
        if self.fail_after_page:
            raise TraffyAPIError("fixture page two failed")
        yield {"index": 1, "params": {"start": start, "end": end, "offset": len(self.rows)},
               "response": {"status": True, "total": None, "results": []},
               "source_exhausted": True}


class FilterTests(unittest.TestCase):
    def test_positive_descriptions_and_missing_category(self):
        for text in ("ตอนนี้น้ำท่วมถนน", "ฝนตกทีไรน้ำท่วมทุกครั้ง", "น้ำเข้าบ้าน", "น้ำรอระบาย"):
            with self.subTest(text=text):
                self.assertIs(classify_flood(text)["is_flood_report"], True)

    def test_preventive_and_negated_mentions_are_not_floods(self):
        for text in ("ขุดลอกคลองเพื่อป้องกันน้ำท่วม", "ยังไม่มีน้ำท่วมขัง", "ไม่มีน้ำขังแล้ว",
                     "ถนนนี้น้ำไม่ท่วม", "เกรงว่าจะน้ำท่วม", "อาจเกิดน้ำท่วม"):
            with self.subTest(text=text):
                self.assertIs(classify_flood(text)["is_flood_report"], False)

    def test_multiple_mentions_do_not_let_a_negation_hide_actual_flooding(self):
        result = classify_flood("ขุดลอกคลองเพื่อป้องกันน้ำท่วม แต่วันนี้น้ำขังสูงหน้าบ้าน")
        self.assertIs(result["is_flood_report"], True)
        self.assertEqual(len(result["flood_suppressed_terms"]), 1)

    def test_category_is_not_a_substitute_for_an_actual_description(self):
        result = classify_flood("ขอขุดลอกท่อ", ["น้ำท่วม"])
        self.assertIsNone(result["is_flood_report"])
        self.assertTrue(result["needs_review"])

    def test_historical_and_leak_mentions_require_review(self):
        for text in ("เมื่อปีที่แล้วมีน้ำท่วม", "ท่อประปาแตกน้ำท่วมถนน"):
            self.assertIsNone(classify_flood(text)["is_flood_report"])

    def test_thai_unicode_variants_and_invisible_characters(self):
        for text in ("นํ้าท่วม", "น้ําท่วม", "น้ำ\u200bท่วม"):
            self.assertIs(classify_flood(text)["is_flood_report"], True)

    def test_non_flood_and_empty_text(self):
        for text in (None, "ไฟถนนดับ", "ท่ออุดตัน", "ขยะริมคลอง"):
            self.assertIs(classify_flood(text)["is_flood_report"], False)


class ClientTests(unittest.TestCase):
    def client(self, payloads, page_size=2):
        client = TraffyClient(page_size=page_size, sleep=lambda _: None)
        self.params = []
        iterator = iter(payloads)
        def request(params):
            self.params.append(params.copy())
            return next(iterator)
        client._request_json = request
        return client

    def test_short_page_uses_total_and_actual_offset(self):
        client = self.client([
            {"total": 3, "results": [report("1")]},
            {"total": 3, "results": [report("2"), report("3")]},
            {"total": None, "results": []},
        ])
        self.assertEqual(len(list(client.iter_pages("2026-09-23", "2026-09-24"))), 3)
        self.assertEqual([x["offset"] for x in self.params], [0, 1, 3])

    def test_repeated_page_and_changed_total_are_failures(self):
        page = {"total": 4, "results": [report("1"), report("2")]}
        for payloads in ([page, page], [page, {"total": 5, "results": [report("3")]}]):
            with self.assertRaises(TraffyAPIError):
                list(self.client(payloads).iter_pages("2026-09-23", "2026-09-24"))

    def test_missing_rows_and_schema_changes_are_failures(self):
        for page in ({"total": 1, "results": []}, {"total": "1", "results": []},
                     {"total": 1, "results": [None]}, {"total": 0, "results": [report()]}):
            with self.assertRaises(TraffyAPIError):
                list(self.client([page]).iter_pages("2026-09-23", "2026-09-24"))

    def test_zero_reports_is_a_valid_complete_page(self):
        pages = list(self.client([{"total": 0, "results": []}]).iter_pages("2026-09-24", "2026-09-24"))
        self.assertEqual(len(pages), 1)

    def test_retry_transient_errors_but_not_a_403(self):
        sleeps = []
        replies = iter([HTTPError("url", 429, "slow down", {"Retry-After": "1"}, None),
                        io.BytesIO(b'{"status": true, "results": [], "total": 0}')])
        def opener(*args, **kwargs):
            item = next(replies)
            if isinstance(item, Exception):
                raise item
            return item
        client = TraffyClient(opener=opener, sleep=sleeps.append)
        self.assertEqual(client._request_json({})["total"], 0)
        self.assertEqual(sleeps, [1])
        client.opener = lambda *a, **k: (_ for _ in ()).throw(HTTPError("url", 403, "denied", {}, None))
        with self.assertRaisesRegex(TraffyAPIError, "403"):
            client._request_json({})
        self.assertEqual(sleeps, [1])

    def test_network_errors_exhaust_retry_budget(self):
        opener = lambda *a, **k: (_ for _ in ()).throw(URLError("fixture timeout"))
        client = TraffyClient(opener=opener, retries=1, sleep=lambda _: None)
        with self.assertRaisesRegex(TraffyAPIError, "2 attempts"):
            client._request_json({})

    def test_source_failure_status_is_not_treated_as_empty_success(self):
        client = TraffyClient(opener=lambda *a, **k: io.BytesIO(b'{"status": false}'))
        with self.assertRaises(TraffyAPIError):
            client._request_json({})

    def test_lambda_budget_checked_before_request(self):
        client = self.client([])
        with self.assertRaisesRegex(TraffyAPIError, "Insufficient Lambda time"):
            list(client.iter_pages("2026-09-24", "2026-09-24", remaining_ms=lambda: 1000))
        self.assertEqual(self.params, [])


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = LocalStorageDriver(Path(self.temp.name))
        self.client = FakeClient()
        self.engine = TraffyIngestor(self.client, self.storage,
                                   now=lambda: datetime(2026, 11, 9, tzinfo=timezone.utc))

    def test_bangkok_day_half_open_boundaries(self):
        self.client.rows = [
            report("before", "2026-09-23 16:59:59.999999+00"),
            report("start", "2026-09-23 17:00:00+00"),
            report("end-minus", "2026-09-24 16:59:59.999999+00"),
            report("end", "2026-09-24 17:00:00+00"),
        ]
        result = self.engine.ingest_day("2026-09-24")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["excluded_outside_bkk_day"], 2)
        self.assertEqual(self.client.calls, [("2026-09-23", "2026-09-24")])
        saved = self.storage.load_json(result["incoming_key"])
        self.assertTrue(saved["is_mock"])
        self.assertEqual({x["ticket_id"] for x in saved["data"]}, {"start", "end-minus"})

    def test_all_report_denominator_and_uncertain_records_are_preserved(self):
        self.client.rows = [report("flood"), report("other", description="ไฟถนนดับ"),
                            report("review", description="เมื่อปีที่แล้วน้ำท่วม")]
        result = self.engine.ingest_day("2026-09-24")
        self.assertEqual((result["record_count"], result["flood_count"], result["uncertain_count"]), (3, 1, 1))
        self.assertEqual(len(self.storage.load_json(result["flood_key"])["data"]), 1)

    def test_replay_skip_and_force_do_not_duplicate_published_rows(self):
        first = self.engine.ingest_day("2026-09-24")
        second = self.engine.ingest_day("2026-09-24")
        self.assertEqual(second["status"], "SKIPPED")
        self.assertEqual(len(self.client.calls), 1)
        third = self.engine.ingest_day("2026-09-24", force=True)
        self.assertEqual(first["records_sha256"], third["records_sha256"])
        self.assertEqual(first["incoming_key"], third["incoming_key"])
        self.assertEqual(len(self.storage.list_keys("incoming/traffy")), 1)

    def test_damaged_output_invalidates_checkpoint(self):
        first = self.engine.ingest_day("2026-09-24")
        self.storage.save_json(first["incoming_key"], {"data": []})
        second = self.engine.ingest_day("2026-09-24")
        self.assertEqual(second["status"], "SUCCESS")
        self.assertEqual(second["record_count"], 1)
        self.assertEqual(len(self.client.calls), 2)

    def test_failure_keeps_raw_but_never_publishes_incomplete_partition(self):
        self.client.fail_after_page = True
        result = self.engine.ingest_day("2026-09-24")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(len(self.storage.list_keys("raw/traffy")), 1)
        self.assertEqual(self.storage.list_keys("incoming/traffy"), [])
        self.assertEqual(self.storage.load_json(result["manifest_key"])["status"], "FAILED")
        self.client.fail_after_page = False
        self.assertEqual(self.engine.ingest_day("2026-09-24")["status"], "SUCCESS")

    def test_invalid_required_record_fails_instead_of_altering_denominator(self):
        self.client.rows = [report(), report("bad", timestamp="invalid")]
        self.assertEqual(self.engine.ingest_day("2026-09-24")["status"], "FAILED")
        self.assertEqual(self.storage.list_keys("incoming/traffy"), [])

    def test_coordinates_do_not_drop_otherwise_valid_reports(self):
        self.client.rows = [{**report(), "coords": ["nan", "13.7"]}]
        result = self.engine.ingest_day("2026-09-24")
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["invalid_coordinate_count"], 1)

    def test_duplicate_ticket_ids_keep_one_latest_revision(self):
        old = report()
        new = {**report(description="ไฟถนนดับ"), "last_activity": "2026-09-26 00:00:00+00"}
        self.client.rows = [new, old]
        result = self.engine.ingest_day("2026-09-24")
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(result["flood_count"], 0)

    def test_empty_closed_day_completes_and_can_be_skipped(self):
        self.client.rows = []
        self.assertEqual(self.engine.ingest_day("2026-09-24")["record_count"], 0)
        self.assertEqual(self.engine.ingest_day("2026-09-24")["status"], "SKIPPED")

    def test_legacy_total_discrepancy_is_explicit_quality_evidence(self):
        original = self.client.iter_pages
        def overstated(*args, **kwargs):
            for page in original(*args, **kwargs):
                if page["response"]["results"]:
                    page["response"]["total"] += 40
                yield page
        self.client.iter_pages = overstated
        result = self.engine.ingest_day("2026-09-24")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["source_total_delta"], 40)
        self.assertEqual(result["quality_warnings"], ["source_total_differs_from_exposed_rows"])

    def test_open_day_requires_opt_in_and_is_never_skipped(self):
        with self.assertRaises(ValueError):
            self.engine.ingest_day("2026-11-09")
        for _ in range(2):
            self.assertEqual(self.engine.ingest_day("2026-11-09", allow_open_day=True)["status"], "SUCCESS")
        self.assertEqual(len(self.client.calls), 2)

    def test_backfill_inclusive_and_stops_on_failure(self):
        self.assertEqual(len(list(backfill("2026-09-24", "2026-09-26", self.engine))), 3)
        self.client.fail_after_page = True
        results = list(backfill("2026-09-27", "2026-09-29", self.engine))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAILED")

    def test_lambda_raises_instead_of_hiding_failure_in_http_200(self):
        with patch("src.ingest_traffy.TraffyIngestor") as factory:
            factory.return_value.ingest_day.return_value = {"status": "FAILED", "error": "fixture"}
            with self.assertRaises(RuntimeError):
                lambda_handler({"date": "2026-09-24"})

    def test_source_time_and_coordinates_match_team_join_contract(self):
        row = normalize_report(report())
        self.assertEqual(row["reported_at_bkk"], "2026-09-24T12:30:00+07:00")
        self.assertEqual(row["reported_at_utc"], "2026-09-24T05:30:00Z")
        self.assertEqual((row["longitude"], row["latitude"]), (100.5, 13.7))
        naive = normalize_report(report(timestamp="2026-09-24 05:30:00"))
        self.assertIn("naive_timestamp_assumed_utc", naive["quality_flags"])


if __name__ == "__main__":
    unittest.main()
