"""
Learner Lab Session Continuity Checker
Owner: Member 1 (PurinatWanthanathanya)

Purpose:
Verifies whether EventBridge Scheduler successfully triggered Lambda executions
while the AWS Learner Lab session was closed/stopped.
Extracts timestamps directly from filenames (or payload envelopes), avoiding S3 LastModified.

Usage:
  python infrastructure/check_learner_lab.py [--backend local|s3] [--type waterlevel|rain] [--max-gap-minutes 25]
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import INCOMING_PREFIX, RAW_PREFIX
from src.storage import get_storage

BKK_TZ = timezone(timedelta(hours=7))


def extract_timestamp_from_key(key: str) -> datetime | None:
    """Extracts Bangkok datetime from standardized filename: *_YYYYMMDD-HHMMSS-*"""
    match = re.search(r"tw-(?:wa|ra)-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", key)
    if match:
        y, m, d, h, mi, s = map(int, match.groups())
        return datetime(y, m, d, h, mi, s, tzinfo=BKK_TZ)
    return None


def verify_session_continuity(backend: str = "s3", ingest_type: str = "waterlevel", max_gap_minutes: int = 25):
    print("=" * 75)
    print(">>> AWS Academy Learner Lab Session Continuity Verification")
    print("=" * 75)
    print(f"Storage Backend: {backend}")
    print(f"Target Telemetry: {ingest_type} (Max allowed gap: {max_gap_minutes} mins)")

    storage = get_storage(backend)
    prefix = f"{RAW_PREFIX}/{ingest_type}/"
    keys = storage.list_keys(prefix)

    if not keys:
        # Fallback to incoming
        prefix = f"{INCOMING_PREFIX}/"
        keys = [k for k in storage.list_keys(prefix) if ingest_type in k]

    # Filter to live ingestion batches only (hour=HH partition)
    live_keys = [k for k in keys if "hour=" in k and k.endswith(".json")]

    if not live_keys:
        print(f"[!] No live ingestion files found for type '{ingest_type}' at prefix: {prefix}")
        return

    print(f"Total live execution batches inspected: {len(live_keys)}")

    # Extract timestamps from filenames first (fast and immune to S3 LastModified resets)
    timestamps = []
    for k in live_keys:
        dt = extract_timestamp_from_key(k)
        if dt:
            timestamps.append((dt, k))
        else:
            # Fallback: load envelope
            try:
                doc = storage.load_json(k)
                iso_time = doc.get("ingested_at_bkk") or doc.get("ingested_at_utc")
                if iso_time:
                    dt = datetime.fromisoformat(iso_time)
                    timestamps.append((dt, k))
            except Exception:
                continue

    timestamps.sort(key=lambda x: x[0])

    if len(timestamps) < 2:
        print(f"[i] Only 1 timestamp recorded ({timestamps[0][0].strftime('%Y-%m-%d %H:%M:%S BKK')}).")
        print("    Let EventBridge run across at least 2 schedule intervals to detect continuity.")
        return

    print("\n--- Live Telemetry Execution Timeline (BKK Time UTC+7) ---")
    for dt, k in timestamps[-8:]:
        filename = k.split("/")[-1]
        print(f"  - {dt.strftime('%Y-%m-%d %H:%M:%S')} | {filename}")

    gaps_found = []
    for i in range(1, len(timestamps)):
        prev_dt, prev_key = timestamps[i - 1]
        curr_dt, curr_key = timestamps[i]
        diff_minutes = (curr_dt - prev_dt).total_seconds() / 60.0

        if diff_minutes > max_gap_minutes:
            gaps_found.append({
                "gap_minutes": round(diff_minutes, 1),
                "from_bkk": prev_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "to_bkk": curr_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "prev_file": prev_key.split("/")[-1],
                "curr_file": curr_key.split("/")[-1],
            })

    print("\n--- Evaluation Summary ---")
    print(f"First Batch Time: {timestamps[0][0].strftime('%Y-%m-%d %H:%M:%S BKK')}")
    print(f"Latest Batch Time:{timestamps[-1][0].strftime('%Y-%m-%d %H:%M:%S BKK')}")

    if not gaps_found:
        print(f"\n [PASSED] Executions ran continuously without gaps > {max_gap_minutes} mins.")
        print("  Conclusion: EventBridge and Lambda continued triggering across Learner Lab session boundaries.")
    else:
        print(f"\n [DETECTED {len(gaps_found)} INGESTION GAP(S) > {max_gap_minutes} MINS]")
        for g in gaps_found:
            print(f"  * Missing interval: {g['gap_minutes']} mins (Between {g['from_bkk']} and {g['to_bkk']})")
        print("\n [RECOMMENDATION]:")
        print("  AWS Academy Learner Lab suspends executions when the lab session is in 'Stopped' status.")
        print("  Document this finding clearly in the PR:")
        print("  'EventBridge Scheduler operates normally during active sessions, but AWS Learner Lab freeze'")
        print("  'suspends execution when session is stopped, requiring session reactivation or GitHub Actions fallback.'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check Learner Lab persistence")
    parser.add_argument("--backend", default="s3", choices=["local", "s3"])
    parser.add_argument("--type", default="waterlevel", choices=["waterlevel", "rain"])
    parser.add_argument("--max-gap-minutes", type=int, default=25)
    args = parser.parse_args()

    verify_session_continuity(backend=args.backend, ingest_type=args.type, max_gap_minutes=args.max_gap_minutes)
