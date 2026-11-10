"""
Step Functions pipeline steps (Issue #1: Orchestration + Validation)
Owner: zero-h0ur

One Lambda, one handler, dispatched by `event["step"]`:

    check      -> CheckBatch     parse the S3 key, find batch_id, skip if already processed
    validate   -> ValidateBatch  run src/validation/rules.py, write the report
    quarantine -> (on FAIL)      copy envelope + report to quarantine/, mark _QUARANTINED.json
    transform  -> TransformBatch flatten envelope to JSON Lines in trusted/ (Athena-readable)
    publish    -> PublishBatch   write processed/.../_SUCCESS.json (idempotency marker)
    verify     -> VerifyBatch    re-read trusted/ + marker and check row counts

Zones (same keys locally under data/ and in S3):
    incoming/  written by ingestion, triggers the state machine
    quarantine/{dataset}/dt=YYYY-MM-DD/<file>              failed batches + report
    trusted/{dataset}/dt=YYYY-MM-DD/<batch>.jsonl          validated rows
    processed/{dataset}/dt=YYYY-MM-DD/<batch_id>/          report + _SUCCESS.json / _QUARANTINED.json

Curated tables (khwaeng x hour, Athena SQL) belong to issue #2 and read trusted/.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from config.config import RAIN_STATIONS_FILE, S3_BUCKET_NAME, STORAGE_BACKEND, WATER_STATIONS_FILE
from src.storage import LocalStorageDriver, S3StorageDriver, StorageDriver
from src.validation.rules import BKK, FAIL, VALIDATOR_VERSION, validate_envelope

THAIWATER_KEY = re.compile(
    r"^incoming/thaiwater/dt=(?P<dt>\d{4}-\d{2}-\d{2})/hour=(?P<hour>\d{2})/"
    r"(?P<dataset>waterlevel|rain)_(?P<batch_id>tw-[a-z]{2}-\d{8}-\d{6}-[0-9a-f]+)\.json$")
TRAFFY_KEY = re.compile(
    r"^incoming/traffy/dt=(?P<dt>\d{4}-\d{2}-\d{2})/traffy_(?P=dt)\.json$")


class BatchNotReady(Exception):
    """Traffy manifest still RUNNING for this batch: Step Functions retries."""


class VerificationError(Exception):
    """Published batch does not match what was written."""


# --- storage helpers (StorageDriver only speaks JSON; trusted/ needs JSONL) --

def make_storage(bucket: Optional[str] = None, backend: Optional[str] = None,
                 base_dir: Optional[str] = None) -> StorageDriver:
    selected = (backend or STORAGE_BACKEND or "local").lower()
    if selected == "s3":
        return S3StorageDriver(bucket_name=bucket or S3_BUCKET_NAME)
    return LocalStorageDriver(Path(base_dir)) if base_dir else LocalStorageDriver()


def put_text(storage: StorageDriver, key: str, text: str, content_type: str) -> str:
    key = key.replace("\\", "/").lstrip("/")
    if isinstance(storage, S3StorageDriver):
        storage.s3_client.put_object(Bucket=storage.bucket_name, Key=key,
                                     Body=text.encode("utf-8"), ContentType=content_type)
        return f"s3://{storage.bucket_name}/{key}"
    path = storage._resolve_path(key)  # LocalStorageDriver
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def get_text(storage: StorageDriver, key: str) -> str:
    key = key.replace("\\", "/").lstrip("/")
    if isinstance(storage, S3StorageDriver):
        resp = storage.s3_client.get_object(Bucket=storage.bucket_name, Key=key)
        return resp["Body"].read().decode("utf-8")
    return storage._resolve_path(key).read_text(encoding="utf-8")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_reference() -> Dict[str, List[Dict[str, Any]]]:
    with open(WATER_STATIONS_FILE, encoding="utf-8") as f:
        water = json.load(f)
    with open(RAIN_STATIONS_FILE, encoding="utf-8") as f:
        rain = json.load(f)
    return {"water_stations": water, "rain_stations": rain}


def _base(batch: Dict[str, Any]) -> str:
    return f"processed/{batch['dataset']}/dt={batch['dt']}/{batch['batch_id']}"


def _trusted_key(batch: Dict[str, Any]) -> str:
    if batch["dataset"] == "traffy":
        # one canonical file per Bangkok day: a refresh replaces it, Athena never double counts
        return f"trusted/traffy/dt={batch['dt']}/traffy_{batch['dt']}.jsonl"
    return f"trusted/{batch['dataset']}/dt={batch['dt']}/{batch['batch_id']}.jsonl"


# --- steps --------------------------------------------------------------------

def parse_event(event: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Accepts an EventBridge 'Object Created' event, its `detail`, or {bucket, key}."""
    detail = event.get("detail", event)
    bucket = detail.get("bucket")
    key = detail.get("key")
    if isinstance(bucket, dict):
        bucket = bucket.get("name")
    obj = detail.get("object")
    if isinstance(obj, dict):
        key = obj.get("key")
    return {"bucket": bucket, "key": key}


def check_batch(event: Dict[str, Any], storage: StorageDriver) -> Dict[str, Any]:
    loc = parse_event(event)
    key = (loc["key"] or "").lstrip("/")
    batch: Dict[str, Any] = {"bucket": loc["bucket"], "key": key, "skip": False,
                             "already_processed": False, "force": bool(event.get("force"))}

    m = THAIWATER_KEY.match(key)
    t = TRAFFY_KEY.match(key)
    if m:
        batch.update(source="thaiwater", dataset=m["dataset"], dt=m["dt"], hour=m["hour"],
                     batch_id=m["batch_id"])
    elif t:
        manifest_key = f"checkpoints/traffy/dt={t['dt']}/manifest.json"
        if not storage.exists(manifest_key):
            raise BatchNotReady(f"No manifest yet for {key}")
        manifest = storage.load_json(manifest_key)
        if manifest.get("status") == "RUNNING":
            raise BatchNotReady(f"Manifest RUNNING for {key} (batch {manifest.get('batch_id')})")
        batch.update(source="traffy", dataset="traffy", dt=t["dt"], hour=None,
                     batch_id=manifest.get("batch_id"), manifest_key=manifest_key)
    else:
        batch.update(skip=True, reason="Key is not an incoming batch file")
        return batch

    base = _base(batch)
    batch["processed_prefix"] = base
    done = [k for k in (f"{base}/_SUCCESS.json", f"{base}/_QUARANTINED.json") if storage.exists(k)]
    if done and not batch["force"]:
        batch.update(already_processed=True, marker_key=done[0])
    return batch


def validate_batch(batch: Dict[str, Any], storage: StorageDriver,
                   reference: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ref = reference or _load_reference()
    try:
        envelope = storage.load_json(batch["key"])
    except (ValueError, json.JSONDecodeError) as exc:  # unreadable JSON is a data failure
        envelope = {"_parse_error": str(exc)}
    manifest = storage.load_json(batch["manifest_key"]) if batch.get("manifest_key") else None
    report = validate_envelope(envelope, manifest=manifest, **ref)
    report.update(source_key=batch["key"], dataset=batch["dataset"], dt=batch["dt"])
    if batch["dataset"] != report.get("data_type") and report["status"] != FAIL:
        report["status"] = FAIL
        report["checks"].append({"rule": "key_matches_data_type", "passed": False, "severity": FAIL,
                                 "detail": f"S3 key says {batch['dataset']}, envelope says {report.get('data_type')}"})
    report_key = f"{_base(batch)}/validation_report.json"
    storage.save_json(report_key, report)
    return {**batch, "validation_status": report["status"], "report_key": report_key,
            "validation_summary": report["summary"],
            "warnings": [c["rule"] for c in report["checks"] if c["severity"] == "WARN"],
            "failures": [c["rule"] for c in report["checks"] if c["severity"] == FAIL]}


def quarantine_batch(batch: Dict[str, Any], storage: StorageDriver) -> Dict[str, Any]:
    filename = batch["key"].rsplit("/", 1)[-1]
    qdir = f"quarantine/{batch['dataset']}/dt={batch['dt']}"
    q_key = f"{qdir}/{filename}"
    put_text(storage, q_key, get_text(storage, batch["key"]), "application/json; charset=utf-8")
    storage.save_json(f"{qdir}/{batch['batch_id']}.validation.json", storage.load_json(batch["report_key"]))
    marker = {
        "status": "QUARANTINED", "batch_id": batch["batch_id"], "source_key": batch["key"],
        "quarantine_key": q_key, "report_key": batch["report_key"], "failures": batch.get("failures", []),
        "validator_version": VALIDATOR_VERSION, "quarantined_at_utc": _now().isoformat(),
    }
    marker_key = f"{_base(batch)}/_QUARANTINED.json"
    storage.save_json(marker_key, marker)
    return {**batch, "quarantine_key": q_key, "marker_key": marker_key}


def _rows(envelope: Dict[str, Any], report: Dict[str, Any]) -> List[Dict[str, Any]]:
    meta = {"batch_id": envelope["batch_id"], "ingested_at_utc": envelope["ingested_at_utc"],
            "validation_status": report["status"]}
    flags = report.get("record_flags", {})
    dtype = envelope["data_type"]
    rows: List[Dict[str, Any]] = []
    if dtype == "waterlevel":
        for r in envelope["data"]:
            row = {k: r.get(k) for k in (
                "api_station_id", "tele_station_oldcode", "station_name_th", "latitude", "longitude",
                "waterlevel_datetime", "timestamp_iso", "timestamp_epoch", "waterlevel_msl",
                "min_bank_msl", "diff_wl_bank_m", "diff_source", "quality_flag", "usable_for_freeboard")}
            row["validation_flags"] = flags.get(str(r["api_station_id"]), [])
            rows.append({**row, **meta})
    elif dtype == "rain":
        for r in envelope["data"]:
            for p in r["series"]:
                t = datetime.strptime(p["datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=BKK)
                rows.append({
                    "api_station_id": r["api_station_id"], "station_name_th": r.get("station_name_th"),
                    "latitude": r.get("latitude"), "longitude": r.get("longitude"),
                    "rain_datetime": p["datetime"], "timestamp_epoch": int(t.timestamp()),
                    "rain_mm": p.get("rain_mm"), "quality_flag": r.get("quality_flag"),
                    "usable_for_rainfall": r.get("usable_for_rainfall"),
                    "validation_flags": flags.get(str(r["api_station_id"]), []), **meta})
    elif dtype == "traffy":
        for r in envelope["data"]:
            rows.append({**r, "date_bkk": envelope["date_bkk"],
                         "is_provisional": envelope.get("is_provisional", False), **meta})
    return rows


def transform_batch(batch: Dict[str, Any], storage: StorageDriver) -> Dict[str, Any]:
    envelope = storage.load_json(batch["key"])
    report = storage.load_json(batch["report_key"])
    rows = _rows(envelope, report)
    text = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)
    key = _trusted_key(batch)
    put_text(storage, key, text, "application/x-ndjson; charset=utf-8")
    return {**batch, "trusted_key": key, "row_count": len(rows)}


def publish_batch(batch: Dict[str, Any], storage: StorageDriver) -> Dict[str, Any]:
    marker = {
        "status": "SUCCESS", "batch_id": batch["batch_id"], "dataset": batch["dataset"], "dt": batch["dt"],
        "source_key": batch["key"], "trusted_key": batch["trusted_key"], "row_count": batch["row_count"],
        "validation_status": batch["validation_status"], "warnings": batch.get("warnings", []),
        "report_key": batch["report_key"], "validator_version": VALIDATOR_VERSION,
        "published_at_utc": _now().isoformat(),
    }
    marker_key = f"{_base(batch)}/_SUCCESS.json"
    storage.save_json(marker_key, marker)
    return {**batch, "marker_key": marker_key}


def verify_batch(batch: Dict[str, Any], storage: StorageDriver) -> Dict[str, Any]:
    marker = storage.load_json(batch["marker_key"])
    lines = [ln for ln in get_text(storage, batch["trusted_key"]).splitlines() if ln.strip()]
    problems = []
    if marker.get("status") != "SUCCESS":
        problems.append(f"marker status {marker.get('status')}")
    if len(lines) != marker.get("row_count"):
        problems.append(f"trusted rows {len(lines)} != marker row_count {marker.get('row_count')}")
    ids = {json.loads(ln).get("batch_id") for ln in lines}
    if ids and ids != {batch["batch_id"]}:
        problems.append(f"trusted file holds batch ids {sorted(map(str, ids))}")
    if problems:
        raise VerificationError("; ".join(problems))
    return {**batch, "verified": True, "verified_rows": len(lines)}


STEPS = {
    "validate": validate_batch, "quarantine": quarantine_batch, "transform": transform_batch,
    "publish": publish_batch, "verify": verify_batch,
}


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Single entry point for every state. Input: {"step": ..., "batch": {...}} (check takes the S3 event)."""
    step = event.get("step")
    if step == "check":
        loc = parse_event(event.get("event", event))
        storage = make_storage(bucket=loc["bucket"])
        return check_batch({**event.get("event", event), "force": event.get("force", False)}, storage)
    if step not in STEPS:
        raise ValueError(f"Unknown step {step!r}")
    batch = event["batch"]
    storage = make_storage(bucket=batch.get("bucket"))
    return STEPS[step](batch, storage)


def run_local(key: str, base_dir: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Runs the same path as the state machine, in order, against local storage."""
    storage = make_storage(backend="local", base_dir=base_dir)
    batch = check_batch({"bucket": "local", "key": key, "force": force}, storage)
    if batch["skip"] or batch["already_processed"]:
        return {**batch, "final_state": "Skipped"}
    batch = validate_batch(batch, storage)
    if batch["validation_status"] == FAIL:
        return {**quarantine_batch(batch, storage), "final_state": "BatchQuarantined"}
    for step in (transform_batch, publish_batch, verify_batch):
        batch = step(batch, storage)
    return {**batch, "final_state": "Published"}
