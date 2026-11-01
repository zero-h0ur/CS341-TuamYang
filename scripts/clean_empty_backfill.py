"""
Clean Empty Historical Backfill Files & Reset Waterlevel Checkpoints
Owner: Member 1 (PurinatWanthanathanya)

Purpose:
Removes previous dummy/empty historical waterlevel files (where non-null count is 0)
from S3 and resets corresponding wl_* tasks in backfill_checkpoint.json
so the system can re-backfill with real telemetry data using station_type=tele_waterlevel.

Usage:
  python scripts/clean_empty_backfill.py          # Dry-run
  python scripts/clean_empty_backfill.py --apply  # Apply deletion to S3 & reset checkpoint
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import CHECKPOINT_FILE, S3_BUCKET_NAME


def main():
    parser = argparse.ArgumentParser(description="Clean empty waterlevel backfill files")
    parser.add_argument("--apply", action="store_true", help="Apply deletions to S3")
    parser.add_argument("--bucket", default=S3_BUCKET_NAME, help="S3 Bucket Name")
    args = parser.parse_args()

    import boto3
    s3 = boto3.client("s3")

    prefix = "raw/thaiwater/waterlevel/"
    print(f"Scanning S3 prefix: s3://{args.bucket}/{prefix} ...")

    empty_keys = []
    valid_keys = []

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=args.bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if "waterlevel_hist_" not in k or not k.endswith(".json"):
                continue

            resp = s3.get_object(Bucket=args.bucket, Key=k)
            doc = json.loads(resp["Body"].read().decode("utf-8"))
            data_rows = doc.get("data", [])
            non_null = sum(1 for r in data_rows if r.get("waterlevel_msl") not in (None, ""))

            if non_null == 0:
                empty_keys.append(k)
            else:
                valid_keys.append((k, non_null))

    print(f"\nScan Results:")
    print(f"  Valid files (non-null > 0): {len(valid_keys)}")
    print(f"  All-null files to remove:   {len(empty_keys)}")

    if args.apply:
        print(f"\nDeleting {len(empty_keys)} empty files from S3...")
        for i in range(0, len(empty_keys), 1000):
            batch = empty_keys[i:i+1000]
            delete_payload = {"Objects": [{"Key": k} for k in batch]}
            s3.delete_objects(Bucket=args.bucket, Delete=delete_payload)
            print(f"  Deleted {len(batch)} objects...")

        # Reset checkpoint wl_* tasks
        if CHECKPOINT_FILE.exists():
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                cp = json.load(f)

            completed = cp.get("completed_tasks", {})
            failed = cp.get("failed_tasks", {})

            removed_comp = [k for k in list(completed.keys()) if k.startswith("wl_")]
            removed_fail = [k for k in list(failed.keys()) if k.startswith("wl_")]

            for k in removed_comp:
                del completed[k]
            for k in removed_fail:
                del failed[k]

            with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
                json.dump(cp, f, ensure_ascii=False, indent=2)

            print(f"\nReset checkpoint: removed {len(removed_comp)} completed wl_* tasks and {len(removed_fail)} failed wl_* tasks.")
            print(f"Remaining completed tasks (e.g. rain): {len(completed)}")

        print("\n[SUCCESS] Cleanup completed. You can now re-run backfill with station_type=tele_waterlevel.")
    else:
        print("\n[DRY-RUN] No files deleted. Run with --apply to execute cleanup.")


if __name__ == "__main__":
    main()
