"""
Learner Lab Session Continuity Checker
Owner: Member 1 (PurinatWanthanathanya)

Purpose:
Verifies whether EventBridge Scheduler successfully triggered Lambda executions
while the AWS Learner Lab session was closed/stopped.

Usage:
  python infrastructure/check_learner_lab.py [--backend local|s3] [--max-gap-minutes 30]
"""

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import INCOMING_PREFIX, RAW_PREFIX, DATA_DIR
from src.storage import get_storage

BKK_TZ = timezone(timedelta(hours=7))


def verify_session_continuity(backend: str = "local", max_gap_minutes: int = 30):
    print("=" * 70)
    print(">>> AWS Academy Learner Lab Session Continuity Verification")
    print("=" * 70)

    storage = get_storage(backend)
    keys = storage.list_keys(INCOMING_PREFIX)
    if not keys:
        keys = storage.list_keys(RAW_PREFIX)

    if not keys:
        print("[!] No ingestion files found in storage. Run ingest_thaiwater first.")
        return

    print(f"Total ingestion files inspected: {len(keys)}")

    # Extract timestamps from envelopes
    timestamps = []
    for k in keys:
        try:
            doc = storage.load_json(k)
            iso_time = doc.get("ingested_at_utc") or doc.get("created_at_utc")
            if iso_time:
                dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
                timestamps.append((dt, k))
        except Exception:
            continue

    timestamps.sort(key=lambda x: x[0])

    if len(timestamps) < 2:
        print("[i] Only 1 timestamp recorded. Let the scheduler run for > 30 minutes to detect gaps.")
        return

    print("\n--- Telemetry Ingestion Timeline ---")
    gaps_found = []
    for i in range(1, len(timestamps)):
        prev_dt, prev_key = timestamps[i - 1]
        curr_dt, curr_key = timestamps[i]
        diff_minutes = (curr_dt - prev_dt).total_seconds() / 60.0

        if diff_minutes > max_gap_minutes:
            gaps_found.append({
                "gap_minutes": round(diff_minutes, 1),
                "from_utc": prev_dt.isoformat(),
                "to_utc": curr_dt.isoformat(),
            })

    print(f"Oldest record: {timestamps[0][0].strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Newest record: {timestamps[-1][0].strftime('%Y-%m-%d %H:%M:%S UTC')}")

    print("\n--- Evaluation Result ---")
    if not gaps_found:
        print(f"[PASSED] Ingestion ran continuously without gaps > {max_gap_minutes} mins.")
        print("Conclusion: EventBridge and Lambda continue executing across session transitions.")
    else:
        print(f"[DETECTED {len(gaps_found)} GAPS > {max_gap_minutes} MINS]")
        for g in gaps_found:
            print(f"  - Missing data gap: {g['gap_minutes']} mins (Between {g['from_utc']} and {g['to_utc']})")
        print("\n[RECOMMENDATION]:")
        print("AWS Learner Lab suspends executions when the 4-hour session expires.")
        print("Activate the fallback GitHub Actions runner (.github/workflows/fallback_scheduler.yml) ")
        print("or run local cron on member machine to maintain unbroken hourly rainfall history.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check Learner Lab persistence")
    parser.add_argument("--backend", default="local", choices=["local", "s3"])
    parser.add_argument("--max-gap-minutes", type=int, default=30)
    args = parser.parse_args()
    verify_session_continuity(backend=args.backend, max_gap_minutes=args.max_gap_minutes)
