"""Resumable daily Traffy backfill. Dates are inclusive Bangkok calendar days."""

import argparse
import json
from datetime import date, timedelta

from src.ingest_traffy import TraffyIngestor
from src.storage import LocalStorageDriver
from src.traffy_client import TraffyClient


def backfill(start, end, ingestor, force=False):
    day, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if day > end:
        raise ValueError("start must be <= end")
    while day <= end:
        result = ingestor.ingest_day(day, force=force)
        yield result
        if result["status"] == "FAILED":
            return
        day += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
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
        for result in backfill(args.start, args.end, ingestor, args.force):
            print(json.dumps(result, ensure_ascii=False))
            if result["status"] == "FAILED":
                return 1
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Traffy backfill error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
