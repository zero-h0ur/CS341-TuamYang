"""Daily Traffy ingestion, usable locally and as src.ingest_traffy.lambda_handler."""

import argparse
import hashlib
import json
import logging
import math
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone

from config.traffy import ANNOTATED_PREFIX, INCOMING_PREFIX, MANIFEST_PREFIX, RAW_PREFIX
from src.storage import LocalStorageDriver, get_storage
from src.traffy_client import TraffyClient
from src.traffy_filter import RULE_VERSION, classify_flood

BKK = timezone(timedelta(hours=7))
UTC = timezone.utc
LOGGER = logging.getLogger("ingest_traffy")


def utc_iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_source_time(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing report timestamp")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # Legacy public search timestamps are UTC; Exchange API is different.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def records_hash(records):
    value = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_report(raw):
    ticket_id = str(raw.get("ticket_id") or "").strip()
    if not ticket_id:
        raise ValueError("Missing ticket_id")
    reported = parse_source_time(raw.get("timestamp"))
    local = reported.astimezone(BKK)
    flags = []
    if datetime.fromisoformat(raw["timestamp"].strip().replace("Z", "+00:00")).tzinfo is None:
        flags.append("naive_timestamp_assumed_utc")
    latitude = longitude = None
    try:
        coords = raw.get("coords")
        if not isinstance(coords, (list, tuple)) or len(coords) != 2:
            raise ValueError("Expected [longitude, latitude]")
        longitude, latitude = (float(x) for x in coords)
        if not (math.isfinite(longitude) and math.isfinite(latitude)
                and -180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("Coordinate out of bounds")
    except (ValueError, TypeError):
        latitude = longitude = None
        flags.append("missing_or_invalid_coordinates")

    categories = []
    for key in ("type", "problem_type_abdul"):
        values = raw.get(key) or []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            flags.append("invalid_source_category")
            continue
        categories.extend(str(x).strip() for x in values if x and str(x).strip())
    categories = sorted(set(categories))
    last_activity = None
    if raw.get("last_activity"):
        try:
            last_activity = utc_iso(parse_source_time(raw["last_activity"]))
        except ValueError:
            flags.append("invalid_last_activity")
    description = str(raw.get("description") or "")
    return {
        "ticket_id": ticket_id,
        "description": description,
        "address": raw.get("address"),
        "longitude": longitude,
        "latitude": latitude,
        "source_categories": categories,
        "reported_at_utc": utc_iso(reported),
        "reported_at_bkk": local.isoformat(),
        "timestamp_iso": local.isoformat(),
        "timestamp_epoch": int(reported.timestamp()),
        "timestamp_source": raw["timestamp"],
        "report_date_bkk": local.date().isoformat(),
        "report_hour_bkk": local.hour,
        "last_activity_utc": last_activity,
        "state": raw.get("state"),
        "quality_flags": flags,
        **classify_flood(description, categories),
    }


class TraffyIngestor:
    def __init__(self, client=None, storage=None, backend=None, now=None):
        self.client = client if client is not None else TraffyClient()
        self.storage = storage if storage is not None else get_storage(backend)
        self.now = now or (lambda: datetime.now(UTC))

    def _valid_checkpoint(self, day, manifest_key):
        if not self.storage.exists(manifest_key):
            return None
        manifest = self.storage.load_json(manifest_key)
        if (not isinstance(manifest, dict)
                or manifest.get("status") != "SUCCESS" or not manifest.get("closed_day")
                or manifest.get("rule_version") != RULE_VERSION
                or manifest.get("date_bkk") != day.isoformat()):
            return None
        # Verify both published outputs. An orphan checkpoint must not skip work.
        for key_name, hash_name in (("incoming_key", "records_sha256"),
                                   ("flood_key", "flood_records_sha256")):
            key = manifest.get(key_name)
            if not key or not self.storage.exists(key):
                return None
            saved = self.storage.load_json(key)
            if (not isinstance(saved, dict) or not isinstance(saved.get("data"), list)
                    or records_hash(saved["data"]) != manifest.get(hash_name)):
                return None
        return manifest

    def ingest_day(self, day, force=False, allow_open_day=False, remaining_ms=None):
        day = date.fromisoformat(str(day))
        now = self.now()
        today = now.astimezone(BKK).date()
        if day > today or (day == today and not allow_open_day):
            raise ValueError("Use a closed Bangkok day, or explicitly allow today's provisional data")
        closed = day < today
        dt = day.isoformat()
        manifest_key = f"{MANIFEST_PREFIX}/dt={dt}/manifest.json"
        if closed and not force:
            try:
                completed = self._valid_checkpoint(day, manifest_key)
            except (FileNotFoundError, KeyError, ValueError, TypeError):
                completed = None
            if completed:
                return {**completed, "status": "SKIPPED", "manifest_key": manifest_key}

        start = datetime.combine(day, time.min, BKK).astimezone(UTC)
        end = start + timedelta(days=1)
        if not closed:
            # A live poll is a provisional view up to the invocation time,
            # rather than an assertion about the rest of today's observations.
            end = min(end, now.astimezone(UTC))
        # API date endpoints are inclusive and interpreted in UTC. Pad the UTC
        # dates then apply the exact half-open Bangkok-day interval ourselves.
        source_start = start.date().isoformat()
        source_end = (end - timedelta(microseconds=1)).date().isoformat()
        # Before 07:00 Bangkok, even yesterday's padded UTC query can include
        # today's newly submitted reports. Bound the output by report time and
        # tolerate moving totals in that source window as well.
        source_window_live = source_end >= now.astimezone(UTC).date().isoformat()
        batch_id = f"tf-{dt}-{uuid.uuid4().hex[:12]}"
        manifest = {
            "status": "RUNNING", "batch_id": batch_id, "date_bkk": dt,
            "closed_day": closed, "rule_version": RULE_VERSION,
            "is_provisional": not closed,
            "source_window_live": source_window_live,
            "started_at_utc": utc_iso(now),
            "source_endpoint": self.client.api_url,
            "window_start_utc": utc_iso(start), "window_end_utc_exclusive": utc_iso(end),
            "source_start_date_utc": source_start, "source_end_date_utc": source_end,
            "raw_page_keys": [], "source_rows_fetched": 0,
        }
        self.storage.save_json(manifest_key, manifest)
        try:
            unique = {}
            excluded, duplicates, expected_total = 0, 0, None
            source_exhausted = False
            totals_observed = set()
            for page in self.client.iter_pages(source_start, source_end, remaining_ms=remaining_ms,
                                               allow_total_change=source_window_live):
                raw_key = f"{RAW_PREFIX}/dt={dt}/batch={batch_id}/page-{page['index']:05d}.json"
                raw = {"source_endpoint": self.client.api_url,
                       "fetched_at_utc": utc_iso(self.now()), **page}
                self.storage.save_json(raw_key, raw)
                manifest["raw_page_keys"].append(raw_key)
                rows = page["response"]["results"]
                if page["response"]["total"] is not None and (rows or expected_total is None):
                    expected_total = page["response"]["total"]
                    totals_observed.add(expected_total)
                source_exhausted = bool(page.get("source_exhausted"))
                manifest["source_rows_fetched"] += len(rows)
                for source in rows:
                    # Required keys/time fail the batch instead of silently
                    # shrinking the all-report denominator used by Q2.
                    record = normalize_report(source)
                    reported = parse_source_time(source.get("timestamp"))
                    if not start <= reported < end:
                        excluded += 1
                        continue
                    ticket = record["ticket_id"]
                    previous = unique.get(ticket)
                    if previous is not None:
                        duplicates += 1
                        # Stable deterministic choice when source rows repeat.
                        if (record.get("last_activity_utc") or "", records_hash([record])) <= (
                            previous.get("last_activity_utc") or "", records_hash([previous])
                        ):
                            continue
                    unique[ticket] = record
            if not source_exhausted:
                raise ValueError("Source did not confirm the terminal empty page")
            total_delta = (expected_total or 0) - manifest["source_rows_fetched"]
            warnings = ["source_total_differs_from_exposed_rows"] if total_delta else []
            if len(totals_observed) > 1:
                warnings.append("source_total_changed_during_provisional_poll" if not closed
                                else "source_total_changed_in_adjacent_live_utc_day")
            records = sorted(unique.values(), key=lambda x: (x["reported_at_utc"], x["ticket_id"]))
            floods = [x for x in records if x["is_flood_report"] is True]
            review_count = sum(x["needs_review"] for x in records)
            incoming_key = f"{INCOMING_PREFIX}/dt={dt}/traffy_{dt}.json"
            flood_key = f"{ANNOTATED_PREFIX}/dt={dt}/flood_reports.json"
            manifest.update({
                "source_exhausted": True, "source_total_advisory": expected_total,
                "source_total_delta": total_delta,
                "source_totals_observed": sorted(totals_observed),
                "quality_warnings": warnings,
                "record_count": len(records), "flood_count": len(floods),
                "uncertain_count": review_count,
                "non_flood_count": len(records) - len(floods) - review_count,
                "excluded_outside_bkk_day": excluded, "duplicate_count": duplicates,
                "invalid_coordinate_count": sum(x["latitude"] is None for x in records),
                "records_sha256": records_hash(records),
                "flood_records_sha256": records_hash(floods),
                "incoming_key": incoming_key, "flood_key": flood_key,
            })
            envelope = {
                "envelope_version": "1.0", "batch_id": batch_id,
                "data_type": "traffy", "is_mock": bool(getattr(self.client, "is_mock", False)),
                "source_endpoint": self.client.api_url,
                "ingested_at_utc": utc_iso(self.now()),
                "ingested_at_bkk": self.now().astimezone(BKK).isoformat(),
                "date_bkk": dt, "rule_version": RULE_VERSION,
                "is_provisional": not closed,
                "window_end_utc_exclusive": utc_iso(end),
                "manifest_key": manifest_key, "raw_page_keys": manifest["raw_page_keys"],
                "record_count": len(records), "records_sha256": records_hash(records),
                "flood_count": len(floods), "uncertain_count": review_count,
                "data": records,
            }
            # Publish the single canonical all-report partition last so a replay
            # overwrites the same key; raw archives deliberately retain runs.
            self.storage.save_json(flood_key, {
                **envelope, "data_type": "traffy_flood", "data": floods,
                "record_count": len(floods), "records_sha256": records_hash(floods),
                "all_report_count": len(records),
            })
            self.storage.save_json(incoming_key, envelope)
            manifest.update(status="SUCCESS", completed_at_utc=utc_iso(self.now()))
            self.storage.save_json(manifest_key, manifest)
            LOGGER.info(json.dumps({"event": "TRAFFY_INGESTION", "status": "SUCCESS",
                                    "date_bkk": dt, "records": len(records),
                                    "floods": len(floods), "batch_id": batch_id}))
            return {**manifest, "manifest_key": manifest_key}
        except Exception as exc:
            manifest.update(status="FAILED", error=str(exc), failed_at_utc=utc_iso(self.now()))
            self.storage.save_json(manifest_key, manifest)
            LOGGER.error(json.dumps({"event": "TRAFFY_INGESTION", "status": "FAILED",
                                     "date_bkk": dt, "error": str(exc)}))
            return {**manifest, "manifest_key": manifest_key}


def ingest_hourly(ingestor, now=None, remaining_ms=None):
    """Refresh today's partial data; also catch yesterday at Bangkok midnight.

    The daily rule remains responsible for a closed-day refresh. Offset
    pagination on a live source is not a transactionally consistent snapshot.
    """
    now = (now or datetime.now(BKK)).astimezone(BKK)
    days = [now.date()]
    if now.hour == 0:
        days.insert(0, now.date() - timedelta(days=1))
    results = []
    for day in days:
        result = ingestor.ingest_day(day, force=True, allow_open_day=True,
                                     remaining_ms=remaining_ms)
        results.append(result)
        if result["status"] == "FAILED":
            break
    return {"mode": "hourly", "status": "FAILED" if any(
        x["status"] == "FAILED" for x in results) else "SUCCESS", "results": results}


def lambda_handler(event=None, context=None):
    if event is None:
        event = {}
    if not isinstance(event, dict):
        raise ValueError("Event must be a JSON object")
    for option in ("force", "allow_open_day"):
        if option in event and type(event[option]) is not bool:
            raise ValueError(f"{option} must be a JSON boolean, not a string")
    mode = event.get("mode", "daily")
    if mode not in ("daily", "hourly"):
        raise ValueError("mode must be daily or hourly")
    if mode == "hourly" and "date" in event:
        raise ValueError("hourly mode selects today's Bangkok date; use daily mode for an explicit date")
    # Scheduled invocation processes yesterday in Bangkok; no date arithmetic in
    # EventBridge is needed. A historical date can be passed explicitly.
    day = event.get("date") or (datetime.now(BKK).date() - timedelta(days=1)).isoformat()
    backend = event.get("storage_backend") or ("s3" if os.getenv("AWS_LAMBDA_FUNCTION_NAME") else None)
    if backend is not None and backend not in ("local", "s3"):
        raise ValueError("storage_backend must be local or s3")
    ingestor = TraffyIngestor(backend=backend)
    remaining_ms = getattr(context, "get_remaining_time_in_millis", None)
    if mode == "hourly":
        result = ingest_hourly(ingestor, remaining_ms=remaining_ms)
    else:
        result = ingestor.ingest_day(
            day, force=event.get("force", False),
            allow_open_day=event.get("allow_open_day", False), remaining_ms=remaining_ms,
        )
    if result["status"] == "FAILED":
        # Raise for Lambda/EventBridge retry semantics; a 200 with an error body
        # would look successful to the scheduler.
        error = result.get("error") or next(x["error"] for x in result["results"] if x["status"] == "FAILED")
        raise RuntimeError(f"Traffy ingestion failed: {error}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Ingest one Bangkok calendar day from Traffy")
    parser.add_argument("--date")
    parser.add_argument("--mode", choices=["daily", "hourly"], default="daily")
    parser.add_argument("--backend", choices=["local", "s3"], default=None)
    parser.add_argument("--local-dir", help="Local output root; cannot be combined with --backend s3")
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-open-day", action="store_true")
    args = parser.parse_args()
    if args.local_dir and args.backend == "s3":
        parser.error("--local-dir requires local storage")
    if args.mode == "hourly" and args.date:
        parser.error("--mode hourly selects today's date; omit --date")
    storage = LocalStorageDriver(args.local_dir) if args.local_dir else None
    try:
        ingestor = TraffyIngestor(TraffyClient(page_size=args.page_size), storage, args.backend)
        if args.mode == "hourly":
            result = ingest_hourly(ingestor)
        else:
            day = args.date or (datetime.now(BKK).date() - timedelta(days=1)).isoformat()
            result = ingestor.ingest_day(day, force=args.force, allow_open_day=args.allow_open_day)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Traffy ingestion error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
