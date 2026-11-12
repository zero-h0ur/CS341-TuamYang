"""
Unit tests for Issue #1: validation rules + Step Functions pipeline steps.
Owner: zero-h0ur

Run: python -X utf8 -m unittest tests.test_validation -v
Uses real ThaiWater batches committed under data/incoming/ (copied to a temp dir)
and a synthetic Traffy day (real Traffy payloads are not committed: personal data).
"""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.storage import LocalStorageDriver
from src.validation import pipeline
from src.validation.rules import (FAIL, PASS, WARN, records_hash, validate_envelope,
                                  validate_rain, validate_traffy, validate_waterlevel,
                                  validate_waterlevel_history)

BKK = timezone(timedelta(hours=7))
INCOMING = ROOT / "data" / "incoming" / "thaiwater" / "dt=2026-11-10" / "hour=00"
WL_FILE = INCOMING / "waterlevel_tw-wa-20261110-002522-07161d.json"
RAIN_FILE = INCOMING / "rain_tw-ra-20261110-002459-9b2cb6.json"
WATER = json.loads((ROOT / "reference" / "stations_water.json").read_text(encoding="utf-8"))
RAIN = json.loads((ROOT / "reference" / "stations_rain.json").read_text(encoding="utf-8"))


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def failed(report):
    return {c["rule"] for c in report["checks"] if c["severity"] == FAIL}


def warned(report):
    return {c["rule"] for c in report["checks"] if c["severity"] == WARN}


def fresh_waterlevel():
    """Real batch with ingestion time moved to 10 min after the newest reading (so it is fresh)."""
    env = load(WL_FILE)
    env["ingested_at_bkk"] = "2026-11-09T22:10:00+07:00"
    for r in env["data"]:
        if r["api_station_id"] == 2599:      # hourly station: keep it on the hour, fresh
            r["waterlevel_datetime"] = "2026-11-09 22:00"
        elif r["waterlevel_datetime"] != "2026-11-09 22:00":
            r["waterlevel_datetime"] = "2026-11-09 22:00"
        local = datetime.strptime(r["waterlevel_datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=BKK)
        r["timestamp_epoch"] = int(local.timestamp())
        r["timestamp_iso"] = local.isoformat()
    return env


def traffy_day(n=20, date="2026-09-26"):
    base = datetime.fromisoformat(f"{date}T00:30:00+07:00")
    data = []
    for i in range(n):
        t = base + timedelta(hours=i % 24, minutes=i)
        cls = ["flood", "non_flood", "uncertain"][i % 3]
        data.append({
            "ticket_id": f"2026-T{i:04d}", "description": "น้ำท่วมขัง" if cls == "flood" else "ไฟดับ",
            "address": "x", "longitude": 100.55 + i * 0.001, "latitude": 13.75 + i * 0.001,
            "source_categories": [], "reported_at_utc": t.astimezone(timezone.utc).isoformat(),
            "timestamp_epoch": int(t.timestamp()), "report_date_bkk": t.date().isoformat(),
            "report_hour_bkk": t.hour, "quality_flags": [], "flood_classification": cls,
            "is_flood_report": {"flood": True, "non_flood": False, "uncertain": None}[cls],
        })
    env = {
        "envelope_version": "1.0", "batch_id": f"tf-{date}-abc123", "data_type": "traffy", "is_mock": False,
        "source_endpoint": "https://publicapi.traffy.in.th/share/teamchadchart/search",
        "ingested_at_utc": "2026-11-08T21:20:00Z", "ingested_at_bkk": "2026-11-09T04:20:00+07:00",
        "date_bkk": date, "rule_version": "traffy-flood-v1.0", "is_provisional": False,
        "record_count": n, "records_sha256": records_hash(data),
        "flood_count": sum(r["is_flood_report"] is True for r in data), "data": data,
    }
    manifest = {"status": "SUCCESS", "batch_id": env["batch_id"], "date_bkk": date,
                "records_sha256": env["records_sha256"], "quality_warnings": []}
    return env, manifest


class TestWaterlevelRules(unittest.TestCase):

    def test_real_batch_has_no_failures_and_flags_stale_readings(self):
        rep = validate_waterlevel(load(WL_FILE), WATER).to_dict()
        self.assertEqual(rep["status"], WARN)
        self.assertEqual(failed(rep), set())
        self.assertIn("freshness", warned(rep))       # real file: readings 2.4-3.4 h old at 00:25

    def test_fresh_batch_passes(self):
        rep = validate_waterlevel(fresh_waterlevel(), WATER).to_dict()
        self.assertEqual(rep["status"], PASS, [c for c in rep["checks"] if not c["passed"]])

    def test_phasi_charoen_excluded_is_not_an_outlier_warning(self):
        rep = validate_waterlevel(fresh_waterlevel(), WATER).to_dict()
        self.assertNotIn("10", rep["record_flags"])   # usable_for_freeboard=false -> skipped

    def test_bank_set_to_ground_level_fails(self):
        env = fresh_waterlevel()
        env["data"][0]["min_bank_msl"] = env["data"][0]["ground_level_msl"]
        self.assertIn("bank_matches_reference", failed(validate_waterlevel(env, WATER).to_dict()))

    def test_duplicate_station_time_fails(self):
        env = fresh_waterlevel()
        env["data"].append(copy.deepcopy(env["data"][0]))
        env["record_count"] += 1
        self.assertIn("unique_station_time", failed(validate_waterlevel(env, WATER).to_dict()))

    def test_all_null_waterlevel_fails(self):
        env = fresh_waterlevel()
        for r in env["data"]:
            r["waterlevel_msl"] = None
        self.assertIn("waterlevel_non_null_ratio", failed(validate_waterlevel(env, WATER).to_dict()))

    def test_unknown_station_fails(self):
        env = fresh_waterlevel()
        env["data"][0]["api_station_id"] = 999
        self.assertIn("station_in_reference", failed(validate_waterlevel(env, WATER).to_dict()))

    def test_epoch_not_matching_bangkok_time_fails(self):
        env = fresh_waterlevel()
        env["data"][0]["timestamp_epoch"] += 7 * 3600   # the classic "added 7 hours twice" bug
        self.assertIn("epoch_matches_local_time", failed(validate_waterlevel(env, WATER).to_dict()))

    def test_future_reading_fails(self):
        env = fresh_waterlevel()
        env["ingested_at_bkk"] = "2026-11-09T21:00:00+07:00"
        self.assertIn("no_future_readings", failed(validate_waterlevel(env, WATER).to_dict()))

    def test_inconsistent_freeboard_warns(self):
        env = fresh_waterlevel()
        env["data"][1]["diff_wl_bank_m"] += 0.5
        rep = validate_waterlevel(env, WATER).to_dict()
        self.assertEqual(rep["status"], WARN)
        self.assertIn("freeboard_consistent", warned(rep))

    def test_implausible_level_warns_and_flags_station(self):
        env = fresh_waterlevel()
        r = next(x for x in env["data"] if x["usable_for_freeboard"])
        r["waterlevel_msl"] = 9.0
        r["diff_wl_bank_m"] = r["min_bank_msl"] - 9.0
        rep = validate_waterlevel(env, WATER).to_dict()
        self.assertIn("waterlevel_plausible_range", warned(rep))
        self.assertIn("WATERLEVEL_OUT_OF_RANGE", rep["record_flags"][str(r["api_station_id"])])

    def test_mock_batch_fails(self):
        env = fresh_waterlevel()
        env["is_mock"] = True
        self.assertIn("not_mock", failed(validate_waterlevel(env, WATER).to_dict()))

    def test_record_count_mismatch_fails(self):
        env = fresh_waterlevel()
        env["record_count"] = 99
        self.assertIn("record_count_matches", failed(validate_waterlevel(env, WATER).to_dict()))

    def test_missing_station_warns(self):
        env = fresh_waterlevel()
        env["data"] = env["data"][:-1]
        env["record_count"] -= 1
        self.assertIn("all_reference_stations_present", warned(validate_waterlevel(env, WATER).to_dict()))


class TestRainRules(unittest.TestCase):

    def test_real_batch_warns_but_does_not_fail(self):
        rep = validate_rain(load(RAIN_FILE), RAIN).to_dict()
        self.assertEqual(failed(rep), set())
        self.assertIn("all_usable_stations_reporting", warned(rep))   # 4 TMD stations silent

    def test_negative_rain_fails(self):
        env = load(RAIN_FILE)
        env["data"][0]["series"][0]["rain_mm"] = -1.0
        self.assertIn("rain_non_negative", failed(validate_rain(env, RAIN).to_dict()))

    def test_legacy_file_without_series_fails(self):
        env = load(RAIN_FILE)
        del env["data"][0]["series"]
        self.assertIn("has_hourly_series", failed(validate_rain(env, RAIN).to_dict()))

    def test_coverage_below_threshold_fails(self):
        env = load(RAIN_FILE)
        for r in env["data"]:
            r["rain_24h_points"] = 0
            r["rain_24h_mm"] = None
            for p in r["series"]:
                p["rain_mm"] = None
        self.assertIn("rain_station_coverage", failed(validate_rain(env, RAIN).to_dict()))

    def test_24h_sum_mismatch_warns(self):
        env = load(RAIN_FILE)
        env["data"][0]["rain_24h_mm"] = 99.0
        self.assertIn("rain_24h_matches_series", warned(validate_rain(env, RAIN).to_dict()))

    def test_duplicate_hour_fails(self):
        env = load(RAIN_FILE)
        env["data"][0]["series"].append(dict(env["data"][0]["series"][0]))
        self.assertIn("series_unique_time", failed(validate_rain(env, RAIN).to_dict()))

    def test_extreme_hourly_rain_warns(self):
        env = load(RAIN_FILE)
        p = env["data"][0]["series"][3]
        p["rain_mm"] = 300.0
        env["data"][0]["rain_24h_mm"] += 300.0
        self.assertIn("rain_plausible_max", warned(validate_rain(env, RAIN).to_dict()))


class TestTraffyRules(unittest.TestCase):

    def test_valid_day_passes(self):
        env, man = traffy_day()
        rep = validate_traffy(env, man).to_dict()
        self.assertEqual(rep["status"], PASS, [c for c in rep["checks"] if not c["passed"]])

    def test_manifest_not_success_fails(self):
        env, man = traffy_day()
        man["status"] = "FAILED"
        self.assertIn("manifest_success", failed(validate_traffy(env, man).to_dict()))

    def test_manifest_from_other_batch_fails(self):
        env, man = traffy_day()
        man["batch_id"] = "tf-other"
        self.assertIn("manifest_batch_matches", failed(validate_traffy(env, man).to_dict()))

    def test_edited_data_breaks_hash(self):
        env, man = traffy_day()
        env["data"][0]["description"] = "edited"
        self.assertIn("records_hash_valid", failed(validate_traffy(env, man).to_dict()))

    def test_duplicate_ticket_fails(self):
        env, man = traffy_day()
        env["data"][1]["ticket_id"] = env["data"][0]["ticket_id"]
        env["records_sha256"] = man["records_sha256"] = records_hash(env["data"])
        self.assertIn("unique_ticket_id", failed(validate_traffy(env, man).to_dict()))

    def test_report_from_other_day_fails(self):
        env, man = traffy_day()
        r = env["data"][0]
        t = datetime.fromtimestamp(r["timestamp_epoch"], BKK) - timedelta(days=1)
        r.update(timestamp_epoch=int(t.timestamp()), report_date_bkk=t.date().isoformat(), report_hour_bkk=t.hour)
        env["records_sha256"] = man["records_sha256"] = records_hash(env["data"])
        self.assertIn("reports_inside_day", failed(validate_traffy(env, man).to_dict()))

    def test_wrong_flood_label_fails(self):
        env, man = traffy_day()
        env["data"][0]["is_flood_report"] = False       # classification is "flood"
        env["records_sha256"] = man["records_sha256"] = records_hash(env["data"])
        self.assertIn("flood_label_consistent", failed(validate_traffy(env, man).to_dict()))

    def test_many_missing_coordinates_warn(self):
        env, man = traffy_day()
        for r in env["data"][:5]:
            r["latitude"] = r["longitude"] = None
        env["records_sha256"] = man["records_sha256"] = records_hash(env["data"])
        rep = validate_traffy(env, man).to_dict()
        self.assertEqual(rep["status"], WARN)
        self.assertIn("coordinates_in_bangkok", warned(rep))

    def test_provisional_day_warns(self):
        env, man = traffy_day()
        env["is_provisional"] = True
        self.assertIn("closed_day", warned(validate_traffy(env, man).to_dict()))


class TestHistoryAndDispatch(unittest.TestCase):

    def test_pr7_pre_fix_backfill_is_rejected_for_all_three_bugs(self):
        env = load(ROOT / "tests" / "fixtures" / "pr7_bad_waterlevel_hist_st1.json")
        rep = validate_waterlevel_history(env, WATER).to_dict()
        self.assertEqual(rep["status"], FAIL)
        self.assertTrue({"interval_grid", "waterlevel_non_null_ratio", "bank_matches_reference"} <= failed(rep))

    def test_committed_backfill_passes(self):
        path = ROOT / "data/raw/thaiwater/waterlevel/dt=2026-09-01/waterlevel_hist_st1_2026_09.json"
        rep = validate_envelope(load(path), water_stations=WATER, rain_stations=RAIN)
        self.assertEqual(rep["status"], PASS)

    def test_unknown_type_and_garbage_fail(self):
        self.assertEqual(validate_envelope({"data_type": "x"}, water_stations=WATER, rain_stations=RAIN)["status"], FAIL)
        self.assertEqual(validate_envelope({"_parse_error": "bad"}, water_stations=WATER, rain_stations=RAIN)["status"], FAIL)


class TestPipelineSteps(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.storage = LocalStorageDriver(Path(self.tmp))
        dest = Path(self.tmp) / "incoming/thaiwater/dt=2026-11-10/hour=00"
        dest.mkdir(parents=True)
        for f in INCOMING.glob("*.json"):
            shutil.copy(f, dest / f.name)
        self.wl_key = f"incoming/thaiwater/dt=2026-11-10/hour=00/{WL_FILE.name}"
        self.rain_key = f"incoming/thaiwater/dt=2026-11-10/hour=00/{RAIN_FILE.name}"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_check_parses_thaiwater_key_and_eventbridge_event(self):
        event = {"detail": {"bucket": {"name": "b"}, "object": {"key": self.wl_key}}}
        b = pipeline.check_batch(event, self.storage)
        self.assertEqual((b["dataset"], b["dt"], b["hour"], b["batch_id"]),
                         ("waterlevel", "2026-11-10", "00", "tw-wa-20261110-002522-07161d"))
        self.assertFalse(b["skip"] or b["already_processed"])

    def test_check_skips_non_batch_keys(self):
        b = pipeline.check_batch({"bucket": "b", "key": "raw/thaiwater/x.json"}, self.storage)
        self.assertTrue(b["skip"])

    def test_waterlevel_end_to_end_and_idempotent(self):
        res = pipeline.run_local(self.wl_key, base_dir=self.tmp)
        self.assertEqual(res["final_state"], "Published")
        self.assertEqual(res["validation_status"], WARN)
        self.assertEqual(res["verified_rows"], 8)
        marker = self.storage.load_json(res["marker_key"])
        self.assertEqual(marker["status"], "SUCCESS")
        self.assertIn("freshness", marker["warnings"])
        rows = (Path(self.tmp) / res["trusted_key"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 8)
        self.assertEqual(json.loads(rows[0])["batch_id"], res["batch_id"])
        again = pipeline.run_local(self.wl_key, base_dir=self.tmp)
        self.assertEqual(again["final_state"], "Skipped")
        self.assertTrue(again["already_processed"])
        forced = pipeline.run_local(self.wl_key, base_dir=self.tmp, force=True)
        self.assertEqual(forced["final_state"], "Published")

    def test_rain_series_flattened_to_hourly_rows(self):
        res = pipeline.run_local(self.rain_key, base_dir=self.tmp)
        self.assertEqual(res["final_state"], "Published")
        self.assertEqual(res["row_count"], sum(len(r["series"]) for r in load(RAIN_FILE)["data"]))

    def test_failed_batch_goes_to_quarantine_not_trusted(self):
        env = load(WL_FILE)
        for r in env["data"]:
            r["min_bank_msl"] = r["ground_level_msl"]
        self.storage.save_json(self.wl_key, env)
        res = pipeline.run_local(self.wl_key, base_dir=self.tmp)
        self.assertEqual(res["final_state"], "BatchQuarantined")
        self.assertTrue(self.storage.exists(res["quarantine_key"]))
        self.assertTrue(self.storage.exists(res["marker_key"]))
        self.assertIn("bank_matches_reference", res["failures"])
        self.assertEqual(self.storage.list_keys("trusted/"), [])
        self.assertEqual(pipeline.run_local(self.wl_key, base_dir=self.tmp)["final_state"], "Skipped")

    def test_unparseable_json_is_quarantined(self):
        bad = Path(self.tmp) / self.wl_key
        bad.write_text("{not json", encoding="utf-8")
        self.assertEqual(pipeline.run_local(self.wl_key, base_dir=self.tmp)["final_state"], "BatchQuarantined")

    def test_traffy_waits_for_manifest_then_publishes_one_file_per_day(self):
        env, man = traffy_day()
        key = "incoming/traffy/dt=2026-09-26/traffy_2026-09-26.json"
        self.storage.save_json(key, env)
        man_key = "checkpoints/traffy/dt=2026-09-26/manifest.json"
        self.storage.save_json(man_key, {**man, "status": "RUNNING"})
        with self.assertRaises(pipeline.BatchNotReady):
            pipeline.check_batch({"bucket": "b", "key": key}, self.storage)
        self.storage.save_json(man_key, man)
        res = pipeline.run_local(key, base_dir=self.tmp)
        self.assertEqual(res["final_state"], "Published")
        self.assertEqual(res["trusted_key"], "trusted/traffy/dt=2026-09-26/traffy_2026-09-26.jsonl")
        self.assertEqual(res["verified_rows"], 20)

    def test_verify_detects_truncated_trusted_file(self):
        batch = pipeline.check_batch({"bucket": "b", "key": self.wl_key}, self.storage)
        batch = pipeline.validate_batch(batch, self.storage)
        batch = pipeline.publish_batch(pipeline.transform_batch(batch, self.storage), self.storage)
        p = Path(self.tmp) / batch["trusted_key"]
        p.write_text("\n".join(p.read_text(encoding="utf-8").splitlines()[:3]) + "\n", encoding="utf-8")
        with self.assertRaises(pipeline.VerificationError):
            pipeline.verify_batch(batch, self.storage)

    def test_traffy_unicode_separators_survive_publish_verify_and_replay(self):
        key = "incoming/traffy/dt=2026-09-26/traffy_2026-09-26.json"
        for separator in ("\u0085", "\u2028", "\u2029"):
            with self.subTest(separator=ascii(separator)):
                env, manifest = traffy_day(n=3)
                description = f'น้ำท่วม{separator}ข้อความ "ทดสอบ"\\เส้นทาง\nบรรทัดใหม่'
                env["data"][0]["description"] = description
                env["records_sha256"] = manifest["records_sha256"] = records_hash(env["data"])
                manifest["quality_warnings"] = ["source total exceeds exposed rows"]
                self.storage.save_json(key, env)
                self.storage.save_json("checkpoints/traffy/dt=2026-09-26/manifest.json", manifest)
                result = pipeline.run_local(key, base_dir=self.tmp, force=True)
                self.assertEqual(result["final_state"], "Published")
                self.assertEqual(result["validation_status"], WARN)
                self.assertEqual(result["verified_rows"], 3)
                path = Path(self.tmp) / result["trusted_key"]
                text = path.read_text(encoding="utf-8")
                rows = [json.loads(line) for line in text.split("\n") if line.strip()]
                self.assertEqual(rows[0]["description"], description)
                # Both LF and CRLF JSONL are valid, including a missing final newline.
                pipeline.put_text(self.storage, result["trusted_key"],
                                  text.rstrip("\n").replace("\n", "\r\n"),
                                  "application/x-ndjson; charset=utf-8")
                self.assertEqual(pipeline.verify_batch(result, self.storage)["verified_rows"], 3)
                self.assertEqual(pipeline.run_local(key, base_dir=self.tmp)["final_state"], "Skipped")

    def test_local_demo_exports_complete_jsonl_records_with_unicode_separators(self):
        from scripts.run_pipeline_local import DEFECT_KEY

        env, manifest = traffy_day(n=4)
        for row in env["data"]:
            row["description"] = "น้ำท่วม\u0085ข้อความ\u2028ทดสอบ\u2029ครบถ้วน"
        env["records_sha256"] = manifest["records_sha256"] = records_hash(env["data"])
        manifest["quality_warnings"] = ["source total exceeds exposed rows"]
        self.storage.save_json("incoming/traffy/dt=2026-09-26/traffy_2026-09-26.json", env)
        self.storage.save_json("checkpoints/traffy/dt=2026-09-26/manifest.json", manifest)
        evidence = Path(self.tmp) / "demo-evidence"
        command = [sys.executable, "-X", "utf8", str(ROOT / "scripts/run_pipeline_local.py"),
                   "--data-dir", self.tmp, "--work-dir", str(Path(self.tmp) / "demo-work"),
                   "--inject-pr7-defect", "--evidence", str(evidence)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = load(evidence / "run_summary.json")
        self.assertTrue(all(r["final_state"] == "Published" for r in summary if r["key"] != DEFECT_KEY))
        self.assertEqual(next(r for r in summary if r["key"] == DEFECT_KEY)["final_state"], "BatchQuarantined")
        replay = result.stdout.split("Replay (idempotency):", 1)[1].split("Work dir:", 1)[0]
        self.assertEqual(replay.count("Skipped"), len(summary))
        sample = evidence / "trusted/traffy/dt=2026-09-26/traffy_2026-09-26.sample.jsonl"
        rows = [json.loads(line) for line in sample.read_text(encoding="utf-8").split("\n") if line.strip()]
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["description"] for r in rows], [r["description"] for r in env["data"][:3]])

    def test_lambda_handler_dispatch(self):
        with unittest.mock.patch.object(pipeline, "make_storage", return_value=self.storage):
            b = pipeline.lambda_handler({"step": "check", "event": {"detail": {
                "bucket": {"name": "b"}, "object": {"key": self.wl_key}}}})
            b = pipeline.lambda_handler({"step": "validate", "batch": b})
            self.assertEqual(b["validation_status"], WARN)
            with self.assertRaises(ValueError):
                pipeline.lambda_handler({"step": "nope", "batch": b})


class TestStateMachineDefinition(unittest.TestCase):
    """Static checks on statemachine/pipeline.asl.json (no AWS needed)."""

    def setUp(self):
        self.asl = load(ROOT / "statemachine" / "pipeline.asl.json")
        self.states = self.asl["States"]

    def test_all_transitions_point_to_existing_states(self):
        self.assertIn(self.asl["StartAt"], self.states)
        targets = []
        for st in self.states.values():
            targets += [st.get("Next"), st.get("Default")]
            targets += [c["Next"] for c in st.get("Choices", []) + st.get("Catch", [])]
        for t in filter(None, targets):
            self.assertIn(t, self.states)

    def test_every_task_calls_a_known_step_with_substituted_arn(self):
        steps = set()
        for st in self.states.values():
            if st["Type"] == "Task":
                self.assertEqual(st["Parameters"]["FunctionName"], "${PipelineFunctionArn}")
                steps.add(st["Parameters"]["Payload"]["step"])
        self.assertEqual(steps, {"check"} | set(pipeline.STEPS))

    def test_no_hardcoded_account_or_region(self):
        text = (ROOT / "statemachine" / "pipeline.asl.json").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\d{12}")
        self.assertNotIn("arn:aws:lambda", text)

    def test_fail_path_goes_through_quarantine(self):
        choice = self.states["ValidationPassed"]["Choices"][0]
        self.assertEqual((choice["StringEquals"], choice["Next"]), ("FAIL", "QuarantineBatch"))
        self.assertEqual(self.states["QuarantineBatch"]["Next"], "BatchQuarantined")
        self.assertEqual(self.states["BatchQuarantined"]["Type"], "Fail")


if __name__ == "__main__":
    unittest.main()
