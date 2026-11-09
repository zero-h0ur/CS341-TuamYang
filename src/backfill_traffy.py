"""Resumable daily Traffy backfill. Dates are inclusive Bangkok calendar days."""

import argparse
import json
from datetime import date, datetime, timedelta

from config.traffy import HISTORY_START_DATE
from src.ingest_traffy import BKK, TraffyIngestor
from src.storage import LocalStorageDriver
from src.traffy_client import TraffyClient


def backfill(start, end, ingestor, force=False, max_days=None):
    day, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if day > end:
        raise ValueError("start must be <= end")
    if max_days is not None and (type(max_days) is not int or max_days < 1):
        raise ValueError("max_days must be a positive integer")
    processed = 0
    while day <= end:
        result = ingestor.ingest_day(day, force=force)
        failed = result["status"] == "FAILED"
        if result["status"] != "SKIPPED":
            processed += 1
        next_day = day if failed else day + timedelta(days=1)
        more = next_day <= end
        budget_exhausted = bool(not failed and more and max_days is not None and processed >= max_days)
        yield {**result, "backfill_progress": {
            "range_start": str(start), "range_end": end.isoformat(),
            "processed_days_this_run": processed, "range_complete": not more and not failed,
            "budget_exhausted": budget_exhausted,
            "next_date": next_day.isoformat() if more else None,
        }}
        if failed or budget_exhausted:
            return
        day += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=HISTORY_START_DATE,
                        help="Inclusive Bangkok start date (default: mid-2022 / 2022-06-01)")
    parser.add_argument("--end", default=(datetime.now(BKK).date() - timedelta(days=1)).isoformat(),
                        help="Inclusive Bangkok end date (default: yesterday)")
    parser.add_argument("--max-days", type=int,
                        help="Limit newly processed dates per run; checkpoint skips do not use this budget")
    parser.add_argument("--backend", choices=["local", "s3"], default=None)
    parser.add_argument("--local-dir")
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--force", action="store_true", help="Refresh completed dates in place")
    args = parser.parse_args()
    if args.local_dir and args.backend == "s3":
        parser.error("--local-dir requires local storage")
    storage = LocalStorageDriver(args.local_dir) if args.local_dir else None
    ingestor = TraffyIngestor(TraffyClient(page_size=args.page_size), storage, args.backend)
    try:
        for result in backfill(args.start, args.end, ingestor, args.force, args.max_days):
            print(json.dumps(result, ensure_ascii=False))
            if result["status"] == "FAILED":
                return 1
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Traffy backfill error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
