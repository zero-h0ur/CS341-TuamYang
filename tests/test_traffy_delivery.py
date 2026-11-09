"""Lambda-to-S3 wiring and review metric checks, with explicitly synthetic fixtures."""

import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.review_traffy import evaluate, sample, write_json
from src.ingest_traffy import lambda_handler, normalize_report, records_hash
from tests.test_traffy import FakeClient, report


class FakeS3:
    """In-memory SDK boundary, not a live AWS service."""
    def __init__(self):
        self.objects = {}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise FileNotFoundError(Key)
        return {}

    def put_object(self, Bucket, Key, Body, ContentType):
        assert ContentType.startswith("application/json")
        self.objects[Bucket, Key] = Body
        return {}

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Bucket, Key])}


class DeliveryTests(unittest.TestCase):
    def test_lambda_defaults_to_s3_and_replays_via_shared_storage(self):
        api, source = FakeS3(), FakeClient()
        fake_sdk = types.SimpleNamespace(client=lambda *args, **kwargs: api)
        with patch.dict(sys.modules, {"boto3": fake_sdk}), \
             patch.dict("os.environ", {"AWS_LAMBDA_FUNCTION_NAME": "test-ingest-traffy"}), \
             patch("src.ingest_traffy.TraffyClient", return_value=source):
            first = lambda_handler({"date": "2026-09-24"})
            object_count = len(api.objects)
            second = lambda_handler({"date": "2026-09-24"})
        self.assertEqual(first["status"], "SUCCESS")
        self.assertEqual(second["status"], "SKIPPED")
        self.assertEqual(len(api.objects), object_count)
        self.assertEqual(len(source.calls), 1)
        keys = {key for bucket, key in api.objects}
        self.assertIn(first["incoming_key"], keys)
        self.assertIn(first["flood_key"], keys)
        self.assertIn(first["manifest_key"], keys)

    def test_json_boolean_options_cannot_accidentally_force_an_overwrite(self):
        for event in ({"force": "false"}, {"allow_open_day": "false"}):
            with self.assertRaisesRegex(ValueError, "JSON boolean"):
                lambda_handler(event)

    def test_invalid_event_shapes_and_backend_are_rejected_before_io(self):
        for event in ([], "", False, {"storage_backend": "typo"}):
            with self.subTest(event=event), self.assertRaises(ValueError):
                lambda_handler(event)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        records = [normalize_report(report(str(i), description=text)) for i, text in enumerate(
            ["น้ำท่วมถนน", "ไฟถนนดับ", "ขอขุดลอกท่อ", "น้ำท่วมบ้าน"])]
        records[2]["source_categories"] = ["น้ำท่วม"]
        self.partition = self.root / "incoming/traffy/dt=2026-09-24/traffy_2026-09-24.json"
        write_json(self.partition, {"date_bkk": "2026-09-24", "is_mock": False,
                                   "data": records, "records_sha256": records_hash(records)})
        self.sample_path = self.root / "review/sample_100.json"

    def test_seeded_sample_is_repeatable_and_blind(self):
        sample(self.root, "2026-09-24", "2026-09-24", 3, 341, self.sample_path)
        first = json.loads(self.sample_path.read_text(encoding="utf-8"))
        sample(self.root, "2026-09-24", "2026-09-24", 3, 341, self.sample_path)
        second = json.loads(self.sample_path.read_text(encoding="utf-8"))
        self.assertEqual(first, second)
        self.assertEqual(len({x["ticket_id"] for x in first["reports"]}), 3)
        self.assertEqual(set(first["reports"][0]), {"ticket_id", "description"})

    def test_metrics_keep_uncertain_reference_out_of_binary_precision(self):
        sample(self.root, "2026-09-24", "2026-09-24", 4, 341, self.sample_path)
        label_path = self.root / "labels.json"
        write_json(label_path, {"reviewer_type": "ai", "reviewer_name": "fixture",
                               "reports": [
                                   {"ticket_id": str(i), "label": value, "rationale": "fixture"}
                                   for i, value in enumerate(["flood", "flood", "non_flood", "uncertain"])
                               ]})
        result = evaluate(self.root, self.sample_path, label_path, self.root / "evidence")
        self.assertEqual(result["three_class_agreement"], 0.25)
        self.assertEqual(result["flood_precision_excluding_uncertain_reference"], 1.0)
        self.assertEqual(result["flood_recall_including_abstentions_as_missed"], 0.5)
        self.assertIsNone(result["precision"])
        self.assertEqual(result["precision_counts"]["all_predicted_positives"], 2)
        self.assertEqual(result["precision_counts"]["unresolved_predicted_positives"], 1)
        self.assertEqual(result["precision_bounds"], {"lower": 0.5, "upper": 1.0})
        self.assertFalse(result["human_verified"])

    def test_blank_human_template_is_not_accepted_as_completed_review(self):
        sample(self.root, "2026-09-24", "2026-09-24", 3, 341, self.sample_path)
        with self.assertRaises(ValueError):
            evaluate(self.root, self.sample_path, self.sample_path.with_name("human_review_100.json"),
                     self.root / "evidence")

    def test_human_labels_with_pending_status_are_not_reported_as_verified(self):
        sample(self.root, "2026-09-24", "2026-09-24", 4, 341, self.sample_path)
        labels_path = self.root / "review/human_review_100.json"
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        labels["reviewer_name"] = "fixture reviewer"
        for row in labels["reports"]:
            row.update(label="non_flood", rationale="synthetic test label")
        write_json(labels_path, labels)
        with self.assertRaisesRegex(ValueError, "status=completed"):
            evaluate(self.root, self.sample_path, labels_path, self.root / "evidence")
        labels["status"] = "completed"
        write_json(labels_path, labels)
        self.assertTrue(evaluate(self.root, self.sample_path, labels_path, self.root / "evidence")["human_verified"])

    def test_new_sampling_parameters_do_not_overwrite_existing_review_work(self):
        sample(self.root, "2026-09-24", "2026-09-24", 3, 341, self.sample_path)
        before = self.sample_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "different snapshot"):
            sample(self.root, "2026-09-24", "2026-09-24", 4, 341, self.sample_path)
        self.assertEqual(self.sample_path.read_bytes(), before)

    def test_unrelated_historical_files_are_not_loaded_into_a_study_sample(self):
        unrelated = self.root / "incoming/traffy/dt=2022-06-01/traffy_2022-06-01.json"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("not the selected study snapshot", encoding="utf-8")
        result = sample(self.root, "2026-09-24", "2026-09-24", 3, 341, self.sample_path)
        self.assertEqual(result["population_count"], 4)


if __name__ == "__main__":
    unittest.main()
