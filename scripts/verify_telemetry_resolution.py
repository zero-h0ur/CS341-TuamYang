"""
Telemetry Resolution & Gap Verification Script
Owner: Member 1 (PurinatWanthanathanya)

Verifies the exact sampling interval of ThaiWater historical telemetry data.
Demonstrates whether data is sampled at 10-minute intervals (144 rows/day) or 1-hour intervals (24 rows/day).

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
        except Exception:
            continue

    parsed_times.sort()
    intervals_min = []

    for i in range(1, len(parsed_times)):
        diff_sec = (parsed_times[i] - parsed_times[i - 1]).total_seconds()
        diff_min = round(diff_sec / 60.0, 2)
        intervals_min.append(diff_min)

    counter = Counter(intervals_min)
    primary_interval, primary_count = counter.most_common(1)[0] if counter else (None, 0)

    gaps = []
    for i in range(1, len(parsed_times)):
        diff_min = round((parsed_times[i] - parsed_times[i - 1]).total_seconds() / 60.0, 2)
        if diff_min != primary_interval:
            gaps.append((parsed_times[i - 1], parsed_times[i], diff_min))

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
    if primary_interval == 10.0:
        print(f" [CONFIRMED] Telemetry resolution is STRICTLY 10 MINUTES ({primary_count}/{len(intervals_min)} transitions).")
        print("  - Daily records: 24h * 6 readings/h = 144 records/day.")
        print("  - 30-day month = 4,320 records (slots).")
        print("  - 31-day month = 4,464 records (slots).")
        print("  - Recommendation: Downstream joins expect up to 6 readings per hour for canal stations.")
    elif primary_interval == 60.0:
        print(f" [CONFIRMED] Telemetry resolution is 1 HOUR ({primary_count}/{len(intervals_min)} transitions, e.g. RID C.12).")
        print("  - Daily records: 24h * 1 reading/h = 24 records/day.")
        print("  - 30-day month = 720 records (slots).")
    else:
        print(f" [ANALYSIS] Primary interval is {primary_interval} minutes.")
        print(f"  - Irregular intervals / gaps count: {len(gaps)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify telemetry sampling interval")
    parser.add_argument("--key", required=True, help="Storage key of backfill file")
    parser.add_argument("--backend", default="s3", choices=["s3", "local"])
    args = parser.parse_args()

    verify_resolution(args.key, backend=args.backend)
