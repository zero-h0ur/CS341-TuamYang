"""Controlled Local history download and independent per-day delivery audit.

No AWS calls. Existing daily ingestion owns publication; this runner only
schedules distinct dates, paces requests, retries and checks delivered files.
"""

import argparse
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import threading
import time
from urllib.request import urlopen

from config.traffy import HISTORY_START_DATE, MANIFEST_PREFIX
from src.ingest_traffy import BKK, TraffyIngestor, records_hash
from src.storage import LocalStorageDriver
from src.traffy_client import TraffyClient
from src.traffy_filter import RULE_VERSION


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, payload, replace_attempts=10):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Windows readers/antivirus may briefly deny replacement of an open file.
    # Keep the last complete JSON readable, then retry the atomic replacement.
    for attempt in range(replace_attempts):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt + 1 == replace_attempts:
                raise
            time.sleep(min(0.05 * 2 ** attempt, 0.5))


def days_between(start, end):
    first, last = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if first > last:
        raise ValueError("start must be <= end")
    if last >= datetime.now(BKK).date():
        raise ValueError("History audit requires closed Bangkok days")
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


class PacedOpener:
    """One request-start clock shared by every worker, including retries."""

    def __init__(self, interval=0.5, opener=urlopen, clock=time.monotonic, sleep=time.sleep):
        if interval < 0.5:
            raise ValueError("Use an interval of at least 0.5 seconds")
        self.interval, self.opener, self.clock, self.sleep = interval, opener, clock, sleep
        self.lock = threading.Lock()
        self.next_start = 0.0

    def __call__(self, *args, **kwargs):
        with self.lock:
            delay = self.next_start - self.clock()
            if delay > 0:
                self.sleep(delay)
            self.next_start = self.clock() + self.interval
        return self.opener(*args, **kwargs)


def audit_day(storage, day):
    """Check actual files, never infer completion from a log or exit code."""
    manifest = storage.load_json(f"{MANIFEST_PREFIX}/dt={day}/manifest.json")
    if (manifest.get("status") != "SUCCESS" or manifest.get("date_bkk") != day
            or manifest.get("closed_day") is not True or manifest.get("rule_version") != RULE_VERSION
            or manifest.get("source_exhausted") is not True):
        raise ValueError("No successful closed-day checkpoint with exhausted source")
    incoming = storage.load_json(manifest["incoming_key"])
    floods = storage.load_json(manifest["flood_key"])
    for payload, count, digest in ((incoming, manifest["record_count"], manifest["records_sha256"]),
                                   (floods, manifest["flood_count"], manifest["flood_records_sha256"])):
        if (payload.get("is_mock") is not False or payload.get("date_bkk") != day
                or payload.get("batch_id") != manifest["batch_id"]
                or payload.get("record_count") != count or len(payload["data"]) != count
                or payload.get("records_sha256") != digest or records_hash(payload["data"]) != digest):
            raise ValueError("Published count/hash/batch/date mismatch, or non-real data")
    records = incoming["data"]
    if (len({r["ticket_id"] for r in records}) != len(records)
            or any(r["report_date_bkk"] != day for r in records)):
        raise ValueError("Duplicate report or wrong Bangkok date")
    expected_floods = [r for r in records if r["is_flood_report"] is True]
    uncertain = sum(r["is_flood_report"] is None for r in records)
    non_flood = sum(r["is_flood_report"] is False for r in records)
    if (records_hash(expected_floods) != manifest["flood_records_sha256"]
            or uncertain != manifest["uncertain_count"] or non_flood != manifest["non_flood_count"]
            or len(expected_floods) + uncertain + non_flood != len(records)):
        raise ValueError("Flood subset or all-report denominator mismatch")
    raw_keys = manifest["raw_page_keys"]
    if not raw_keys or any(not storage.exists(key) for key in raw_keys):
        raise ValueError("Missing raw provenance file")
    terminal = storage.load_json(raw_keys[-1])
    if (terminal.get("source_exhausted") is not True or terminal["response"]["results"] != []
            or terminal["response"].get("total") not in (None, 0)):
        raise ValueError("No explicit terminal empty source page")
    return {"date_bkk": day, "record_count": len(records), "flood_count": len(expected_floods),
            "uncertain_count": uncertain, "non_flood_count": non_flood,
            "records_sha256": manifest["records_sha256"],
            "flood_records_sha256": manifest["flood_records_sha256"],
            "raw_page_count": len(raw_keys), "batch_id": manifest["batch_id"],
            "source_total_delta": manifest.get("source_total_delta"),
            "quality_warnings": manifest.get("quality_warnings", [])}


def audit_range(storage, start, end):
    expected = days_between(start, end)
    verified, gaps = [], []
    for day in expected:
        try:
            verified.append(audit_day(storage, day))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            gaps.append({"date_bkk": day, "reason": f"{type(exc).__name__}: {exc}"})
    return {"generated_at_utc": timestamp(), "environment": "Local; no AWS deployment",
            "range_start_bkk": str(start), "range_end_bkk": str(end),
            "expected_days": len(expected), "verified_days": len(verified),
            "range_complete": not gaps, "gap_count": len(gaps), "gaps": gaps,
            "record_count": sum(r["record_count"] for r in verified),
            "flood_count": sum(r["flood_count"] for r in verified),
            "uncertain_count": sum(r["uncertain_count"] for r in verified),
            "non_flood_count": sum(r["non_flood_count"] for r in verified),
            "days_with_source_coverage_warning": sum(bool(r["quality_warnings"]) for r in verified),
            "coverage_boundary": "Only range_complete=true means every requested date passed; API advertised totals may exceed exposed rows.",
            "checks": ["both output hashes/counts and batch IDs", "real-data flags", "Bangkok day membership",
                       "unique IDs within each date", "flood subset and denominator", "raw references and terminal empty page"],
            "partitions": verified}


def run_download(ingest, pending_days, *, workers=4, attempts=3, progress=None, stop=None, event=None):
    """Bound the queue; never schedule two attempts for one date concurrently."""
    if type(workers) is not int or not 1 <= workers <= 4 or type(attempts) is not int or attempts < 1:
        raise ValueError("workers must be 1..4; attempts must be >= 1")
    if len(set(pending_days)) != len(pending_days):
        raise ValueError("Dates must be unique")
    queue = deque((day, 1) for day in pending_days)
    completed, failures, active = set(), {}, {}
    stopped = False
    with ThreadPoolExecutor(max_workers=workers) as executor:
        while queue or active:
            stopped = stopped or bool(stop and stop())
            while queue and len(active) < workers and not stopped:
                day, attempt = queue.popleft()
                active[executor.submit(ingest, day)] = (day, attempt)
            if not active:
                break
            finished, _ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
            for future in finished:
                day, attempt = active.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
                success = result.get("status") in ("SUCCESS", "SKIPPED")
                if success:
                    completed.add(day)
                    failures.pop(day, None)
                else:
                    failures[day] = result.get("error", "Unknown failure")
                    if attempt < attempts:
                        queue.append((day, attempt + 1))
                if event:
                    event({"at_utc": timestamp(), "date_bkk": day, "attempt": attempt,
                           "status": result.get("status"), "record_count": result.get("record_count"),
                           "error": result.get("error")})
            if progress:
                try:
                    progress({"completed_dates_this_run": len(completed), "failed_dates": failures,
                              "active_dates": sorted(d for d, _ in active.values()),
                              "queued_attempts": len(queue), "stopping": stopped})
                except PermissionError as exc:
                    # An optional dashboard must not abort successful ingestion.
                    # The next loop retries; the final delivery audit is strict.
                    if event:
                        event({"at_utc": timestamp(), "event": "PROGRESS_WRITE_DELAYED", "error": str(exc)})
    return {"completed": sorted(completed), "failures": failures, "stopped": stopped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=HISTORY_START_DATE)
    parser.add_argument("--end", default=(datetime.now(BKK).date() - timedelta(days=1)).isoformat())
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--request-interval", type=float, default=0.5)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--run-dir", default="data/checkpoints/traffy/runs/full_history")
    parser.add_argument("--output-dir", default="evidence/traffy/full_backfill")
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("attempts must be >= 1")
    paced = PacedOpener(args.request_interval)
    storage = LocalStorageDriver(args.data_dir)
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / "runner.lock"
    try:
        lock = lock_path.open("x", encoding="utf-8")
    except FileExistsError:
        parser.error(f"Existing runner lock: {lock_path}. Check its PID before removing a stale lock.")
    try:
        with lock:
            lock.write(str(os.getpid()))
        started = timestamp()
        initial = audit_range(storage, args.start, args.end)
        base = {"pid": os.getpid(), "started_at_utc": started,
                "range_start_bkk": args.start, "range_end_bkk": args.end,
                "expected_days": initial["expected_days"], "previously_verified_days": initial["verified_days"],
                "workers": args.workers, "request_interval_seconds": args.request_interval,
                "status": "RUNNING", "range_complete": False}
        def progress(state):
            write_json(run_dir / "progress.json", {**base, **state, "updated_at_utc": timestamp()})
        def event(row):
            with (run_dir / "events.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps(row, ensure_ascii=False), flush=True)
        progress({"completed_dates_this_run": 0, "queued_attempts": initial["gap_count"], "active_dates": []})
        result = {"stopped": False, "failures": {}}
        if not args.audit_only:
            ingestor = TraffyIngestor(TraffyClient(opener=paced), storage)
            def ingest(day):
                if shutil.disk_usage(storage.base_dir).free < 2 * 1024 ** 3:
                    (run_dir / "STOP").touch()
                    raise OSError("Less than 2 GiB disk space; stopping after active dates finish")
                return ingestor.ingest_day(day)
            result = run_download(ingest, [g["date_bkk"] for g in initial["gaps"]],
                                  workers=args.workers, attempts=args.attempts, progress=progress,
                                  stop=lambda: (run_dir / "STOP").exists(), event=event)
        summary = audit_range(storage, args.start, args.end)
        status = "SUCCESS" if summary["range_complete"] else ("STOPPED" if result["stopped"] else "INCOMPLETE")
        summary.update(status=status, started_at_utc=started, workers=args.workers,
                       request_interval_seconds=args.request_interval, aws_verified=False)
        write_json(Path(args.output_dir) / "summary.json", summary)
        write_json(run_dir / "progress.json", {**base, "status": status,
                   "range_complete": summary["range_complete"], "verified_days": summary["verified_days"],
                   "gap_count": summary["gap_count"], "updated_at_utc": timestamp()})
        print(json.dumps({k: v for k, v in summary.items() if k not in ("partitions", "gaps")}), flush=True)
        return 0 if summary["range_complete"] else 1
    finally:
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
