"""
Recompute Freeboard and Bank Height on S3 / Local Storage
Owner: Member 1 (PurinatWanthanathanya)

Corrects min_bank_msl and diff_wl_bank_m in existing S3 / Local JSON files
based on verified station.min_bank (fixed from ground_level).
Only updates files where values actually changed (idempotent patch).

Usage:
  python scripts/recompute_freeboard.py           # Dry-run
  python scripts/recompute_freeboard.py --apply   # Apply changes to S3
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import S3_BUCKET_NAME, WATER_STATIONS_FILE


def main():
    parser = argparse.ArgumentParser(description="Recompute Freeboard on S3")
    parser.add_argument("--apply", action="store_true", help="Apply updates to S3")
    parser.add_argument("--bucket", default=S3_BUCKET_NAME, help="S3 Bucket Name")
    args = parser.parse_args()

    # Load corrected reference
    with open(WATER_STATIONS_FILE, "r", encoding="utf-8") as f:
        stations = json.load(f)

    bank = {str(s["api_station_id"]): float(s["min_bank_msl"]) for s in stations if s.get("min_bank_msl") is not None}
    print(f"Loaded bank heights for {len(bank)} stations from {WATER_STATIONS_FILE.name}:")
    for sid, b in sorted(bank.items(), key=lambda x: int(x[0])):
        print(f"  Station {sid:>6}: min_bank = {b} m MSL")

    import boto3
    s3 = boto3.client("s3")
    files_count = 0
    records_fixed = 0
    records_skipped = 0

    print("\nScanning S3 prefixes: raw/thaiwater/waterlevel/ and incoming/thaiwater/ ...")
    for prefix in ("raw/thaiwater/waterlevel/", "incoming/thaiwater/"):
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=args.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if "waterlevel" not in key or not key.endswith(".json"):
                    continue

                resp = s3.get_object(Bucket=args.bucket, Key=key)
                content = resp["Body"].read().decode("utf-8")
                env = json.loads(content)

                touched = 0
                data_list = env.get("data") or []
                file_sid = str(env.get("api_station_id") or env.get("station_id") or "")

                for r in data_list:
                    sid = str(r.get("api_station_id") or r.get("station_id") or file_sid)
                    if sid not in bank:
                        records_skipped += 1
                        continue

                    target_bank = bank[sid]
                    wl = r.get("waterlevel_msl")
                    expected_diff = None
                    wl_float = None

                    if wl not in (None, ""):
                        try:
                            wl_float = float(wl)
                            expected_diff = round(target_bank - wl_float, 3)
                        except (ValueError, TypeError):
                            expected_diff = None

                    # Check if update is genuinely needed
                    curr_bank = r.get("min_bank_msl")
                    curr_diff = r.get("diff_wl_bank_m")
                    curr_source = r.get("diff_source")

                    is_changed = (
                        curr_bank != target_bank
                        or curr_diff != expected_diff
                        or curr_source != "derived"
                    )

                    if is_changed:
                        r["waterlevel_msl"] = wl_float
                        r["min_bank_msl"] = target_bank
                        r["diff_wl_bank_m"] = expected_diff
                        r["diff_source"] = "derived"
                        touched += 1

                if touched:
                    files_count += 1
                    records_fixed += touched
                    env["min_bank_source"] = "station.min_bank (corrected from ground_level)"
                    if args.apply:
                        body_bytes = json.dumps(env, ensure_ascii=False).encode("utf-8")
                        s3.put_object(
                            Bucket=args.bucket,
                            Key=key,
                            ContentType="application/json; charset=utf-8",
                            Body=body_bytes,
                        )

    status_str = "APPLIED CHANGES TO S3" if args.apply else "DRY-RUN (Pass --apply to write to S3)"
    print("\n" + "=" * 60)
    print(f"[{status_str}]")
    print(f"  Files genuinely modified: {files_count}")
    print(f"  Records updated:          {records_fixed}")
    print(f"  Records skipped (no st):  {records_skipped}")
    print("=" * 60)


if __name__ == "__main__":
    main()
