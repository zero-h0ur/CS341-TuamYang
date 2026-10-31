"""
Telemetry Resolution & Gap Verification Script
Owner: Member 1 (PurinatWanthanathanya)

Verifies the exact sampling interval of ThaiWater historical telemetry data.
Demonstrates whether data is sampled at 15-minute intervals (96 rows/day).

Usage:
  python scripts/verify_telemetry_resolution.py --key raw/thaiwater/waterlevel/dt=2026-09-01/waterlevel_hist_st1_2026_09.json [--backend s3|local]
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.storage import get_storage


def verify_resolution(key: str, backend: str = "s3"):
    print("=" * 70)
    print(">>> ThaiWater Telemetry Resolution & Interval Analysis")
    print("=" * 70)
    print(f"Target Key: {key}")
    print(f"Backend:    {backend}")

    storage = get_storage(backend)
    if not storage.exists(key):
        print(f"[ERROR] Target file not found in storage: {key}")
        sys.exit(1)

    payload = storage.load_json(key)
    records = payload.get("data", [])
    record_count = len(records)
    print(f"Total Records Loaded: {record_count}")

    if record_count < 2:
        print("[WARNING] Insufficient records to analyze time gaps.")
        return

    # Parse datetimes
    parsed_times = []
    for r in records:
        dt_str = r.get("datetime") or r.get("waterlevel_datetime") or r.get("date")
        if not dt_str:
            continue
        try:
            if len(dt_str) == 16:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            else:
                dt = datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S")
            parsed_times.append(dt)
        except Exception as e:
            continue

    parsed_times.sort()
    intervals_min = []
    gaps = []

    for i in range(1, len(parsed_times)):
        diff_sec = (parsed_times[i] - parsed_times[i - 1]).total_seconds()
        diff_min = round(diff_sec / 60.0, 2)
        intervals_min.append(diff_min)
        if diff_min != 15.0:
            gaps.append((parsed_times[i - 1], parsed_times[i], diff_min))

    counter = Counter(intervals_min)

    print("\n--- Telemetry Sampling Frequency Distribution ---")
    for interval, count in counter.most_common():
        pct = (count / len(intervals_min)) * 100
        print(f"  - Delta = {interval:>5.1f} minutes: {count:>5d} transitions ({pct:>5.1f}%)")

    print("\n--- Summary Statistics ---")
    print(f"  First Record Time: {parsed_times[0].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Last Record Time:  {parsed_times[-1].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Min Interval:      {min(intervals_min)} min")
    print(f"  Max Interval:      {max(intervals_min)} min")
    print(f"  Average Interval:  {sum(intervals_min) / len(intervals_min):.2f} min")

    print("\n--- Conclusion for Data Contract & Schema ---")
    if all(i == 15.0 for i in intervals_min):
        print(" [CONFIRMED] Telemetry resolution is STRICTLY 15 MINUTES (0 gaps detected).")
        print("  - Daily records: 24h * 4 readings/h = 96 records/day.")
        print("  - 30-day month = 2,880 records.")
        print("  - 31-day month = 2,976 records.")
        print("  - Recommendation: Update contract to 4 readings/hour for downstream hourly joins.")
    else:
        print(f" [ANALYSIS] Primary interval is {counter.most_common(1)[0][0]} minutes.")
        print(f"  - Irregular intervals / gaps count: {len(gaps)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify telemetry sampling interval")
    parser.add_argument("--key", required=True, help="Storage key of backfill file")
    parser.add_argument("--backend", default="s3", choices=["s3", "local"])
    args = parser.parse_args()

    verify_resolution(args.key, backend=args.backend)
