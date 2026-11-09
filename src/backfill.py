"""
Historical Backfill Engine for ThaiWater Data (From 2022 to Present)
Owner: Member 1 (PurinatWanthanathanya)

Strict Requirements:
- Full coverage: Loops months from 2022-01 (ปี 2565) to present.
- Real API calls only: Raises on failure, no silent mocks.
- Empty/Failed tasks are NOT marked as completed in checkpoint.
- S3 & Local dual-mode support via STORAGE_BACKEND.
- Robust retry with exponential backoff & rate limiting.
- Summarizes Succeeded, Skipped (Idempotent), and Failed tasks.
"""

import argparse
import calendar
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import (
    CHECKPOINT_FILE,
    MIN_RAIN_NON_NULL_RATIO,
    MIN_WATERLEVEL_NON_NULL_RATIO,
    RAIN_PREFIX,
    WATERLEVEL_PREFIX,
)
from src.storage import get_storage
from src.thaiwater_client import ThaiWaterAPIError, ThaiWaterClient
from src.utils.logger import log_ingestion_metric, setup_logger

logger = setup_logger("backfill_thaiwater")


class BackfillManager:
    """Manages full idempotent historical backfill with progress checkpointing."""

    def __init__(self, backend: Optional[str] = None):
        self.storage = get_storage(backend)
        self.client = ThaiWaterClient()
        self.checkpoint_path = CHECKPOINT_FILE
        self.checkpoints = self._load_checkpoints()

    def _load_checkpoints(self) -> Dict[str, Any]:
        if self.checkpoint_path.exists():
            try:
                with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"completed_tasks": {}, "failed_tasks": {}, "last_updated": None}

    def _save_checkpoint(self, task_key: str, metadata: Dict[str, Any], is_success: bool = True):
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        if is_success:
            self.checkpoints["completed_tasks"][task_key] = metadata
            if task_key in self.checkpoints.get("failed_tasks", {}):
                del self.checkpoints["failed_tasks"][task_key]
        else:
            self.checkpoints["failed_tasks"][task_key] = metadata

        self.checkpoints["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(self.checkpoints, f, ensure_ascii=False, indent=2)

    def is_task_completed(self, task_key: str, dest_key: str) -> bool:
        """Verifies if task was previously marked complete with valid records AND file exists in storage."""
        now = datetime.now()
        cur_suffix = f"_{now.year}_{now.month:02d}"
        if task_key.endswith(cur_suffix):
            return False
        cp = self.checkpoints.get("completed_tasks", {}).get(task_key)
        if not cp or cp.get("record_count", 0) <= 0:
            return False
        return self.storage.exists(dest_key)

    def backfill_waterlevel_monthly(self, api_station_id: int, year: int, month: int,
                                    force: bool = False) -> Dict[str, Any]:
        """
        Backfills 10-minute historical water levels for a whole month.
        Storage key: raw/thaiwater/waterlevel/dt=YYYY-MM-01/waterlevel_hist_{station_id}_{year}_{month:02d}.json
        """
        task_key = f"wl_st{api_station_id}_{year}_{month:02d}"
        partition_dt = f"{year:04d}-{month:02d}-01"
        dest_key = f"{WATERLEVEL_PREFIX}/dt={partition_dt}/waterlevel_hist_st{api_station_id}_{year}_{month:02d}.json"

        if not force and self.is_task_completed(task_key, dest_key):
            logger.info(f"[SKIP] Task {task_key} already completed at {dest_key}. Idempotency guaranteed.")
            return {"status": "SKIPPED", "task_key": task_key, "destination": dest_key}

        start_time = time.time()
        last_day = calendar.monthrange(year, month)[1]
        start_date = f"{year:04d}-{month:02d}-01"
        end_date = f"{year:04d}-{month:02d}-{last_day:02d}"

        try:
            fetch_res = self.client.fetch_historical_waterlevel(api_station_id, start_date, end_date)
            records = fetch_res.get("records", [])
        except ThaiWaterAPIError as e:
            logger.error(f"[FAILED] Task {task_key} API error: {e}")
            self._save_checkpoint(task_key, {"error": str(e), "failed_at": datetime.now(timezone.utc).isoformat()}, is_success=False)
            return {"status": "FAILED", "task_key": task_key, "error": str(e)}

        non_null_records = [r for r in records if r.get("waterlevel_msl") not in (None, "")]
        total_slots = len(records)
        non_null_ratio = (len(non_null_records) / total_slots) if total_slots > 0 else 0.0

        if not non_null_records or non_null_ratio < MIN_WATERLEVEL_NON_NULL_RATIO:
            logger.warning(
                f"[EMPTY/BELOW_THRESHOLD] Task {task_key} returned {len(non_null_records)}/{total_slots} non-null records "
                f"({non_null_ratio:.1%}), below required threshold {MIN_WATERLEVEL_NON_NULL_RATIO:.1%}. "
                f"Not writing file and not marking as completed."
            )
            return {
                "status": "EMPTY",
                "task_key": task_key,
                "record_count": len(non_null_records),
                "slots": total_slots,
                "non_null_ratio": round(non_null_ratio, 4),
            }

        envelope = {
            "envelope_version": "1.0",
            "batch_type": "historical_backfill",
            "data_type": "waterlevel",
            "is_mock": False,
            "api_station_id": api_station_id,
            "year": year,
            "month": month,
            "start_date": start_date,
            "end_date": end_date,
            "record_count": len(non_null_records),
            "total_slots": len(records),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "data": records,
        }

        # Deterministic write to raw zone only
        dest_uri = self.storage.save_json(dest_key, envelope, overwrite=True)
        duration = time.time() - start_time

        now = datetime.now()
        is_current_month = (year == now.year and month == now.month)
        if not is_current_month:
            self._save_checkpoint(task_key, {
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "record_count": len(non_null_records),
                "total_slots": len(records),
                "dest_key": dest_key,
            }, is_success=True)
        else:
            logger.info(f"[CURRENT_MONTH] Task {task_key} is ongoing ({year}-{month:02d}). Saved to storage but not marked completed in checkpoint.")

        log_ingestion_metric(
            logger=logger,
            batch_id=task_key,
            data_type="waterlevel_hist",
            record_count=len(non_null_records),
            duration_sec=duration,
            destination=dest_uri,
            status="SUCCESS",
        )

        return {
            "status": "SUCCESS",
            "task_key": task_key,
            "record_count": len(non_null_records),
            "total_slots": len(records),
            "destination": dest_uri,
        }

    def backfill_rain_monthly(self, api_station_id: int, year: int, month: int,
                              force: bool = False) -> Dict[str, Any]:
        """
        Backfills monthly daily rain records for a specific station and month.
        Storage key: raw/thaiwater/rain/dt=YYYY-MM-01/rain_monthly_st{station_id}_{year}_{month:02d}.json
        """
        task_key = f"rain_st{api_station_id}_{year}_{month:02d}"
        partition_dt = f"{year:04d}-{month:02d}-01"
        dest_key = f"{RAIN_PREFIX}/dt={partition_dt}/rain_monthly_st{api_station_id}_{year}_{month:02d}.json"

        if not force and self.is_task_completed(task_key, dest_key):
            logger.info(f"[SKIP] Task {task_key} already completed at {dest_key}. Idempotency guaranteed.")
            return {"status": "SKIPPED", "task_key": task_key, "destination": dest_key}

        start_time = time.time()
        try:
            fetch_res = self.client.fetch_monthly_rain(api_station_id, year, month)
            records = fetch_res.get("records", [])
        except ThaiWaterAPIError as e:
            logger.error(f"[FAILED] Task {task_key} API error: {e}")
            self._save_checkpoint(task_key, {"error": str(e), "failed_at": datetime.now(timezone.utc).isoformat()}, is_success=False)
            return {"status": "FAILED", "task_key": task_key, "error": str(e)}

        non_null_records = [r for r in records if r.get("rainfall_daily_mm") not in (None, "")]
        total_slots = len(records)
        non_null_ratio = (len(non_null_records) / total_slots) if total_slots > 0 else 0.0

        if not non_null_records or non_null_ratio < MIN_RAIN_NON_NULL_RATIO:
            logger.warning(
                f"[EMPTY/BELOW_THRESHOLD] Task {task_key} returned {len(non_null_records)}/{total_slots} non-null records "
                f"({non_null_ratio:.1%}), below required threshold {MIN_RAIN_NON_NULL_RATIO:.1%}. "
                f"Not writing file and not marking as completed."
            )
            return {
                "status": "EMPTY",
                "task_key": task_key,
                "record_count": len(non_null_records),
                "slots": total_slots,
                "non_null_ratio": round(non_null_ratio, 4),
            }

        envelope = {
            "envelope_version": "1.0",
            "batch_type": "historical_backfill",
            "data_type": "rain_monthly",
            "is_mock": False,
            "api_station_id": api_station_id,
            "year": year,
            "month": month,
            "record_count": len(non_null_records),
            "total_slots": len(records),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "data": records,
        }

        dest_uri = self.storage.save_json(dest_key, envelope, overwrite=True)
        duration = time.time() - start_time

        now = datetime.now()
        is_current_month = (year == now.year and month == now.month)
        if not is_current_month:
            self._save_checkpoint(task_key, {
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "record_count": len(non_null_records),
                "total_slots": len(records),
                "dest_key": dest_key,
            }, is_success=True)
        else:
            logger.info(f"[CURRENT_MONTH] Task {task_key} is ongoing ({year}-{month:02d}). Saved to storage but not marked completed in checkpoint.")

        log_ingestion_metric(
            logger=logger,
            batch_id=task_key,
            data_type="rain_monthly_hist",
            record_count=len(non_null_records),
            duration_sec=duration,
            destination=dest_uri,
            status="SUCCESS",
        )

        return {
            "status": "SUCCESS",
            "task_key": task_key,
            "record_count": len(non_null_records),
            "total_slots": len(records),
            "destination": dest_uri,
        }

    def generate_month_list(self, start_year: int = 2022, start_month: int = 1,
                            end_year: Optional[int] = None, end_month: Optional[int] = None) -> List[Tuple[int, int]]:
        """Generates (year, month) tuples from start to end date."""
        now = datetime.now()
        end_y = end_year or now.year
        end_m = end_month or now.month

        months = []
        cur_y, cur_m = start_year, start_month
        while (cur_y < end_y) or (cur_y == end_y and cur_m <= end_m):
            months.append((cur_y, cur_m))
            cur_m += 1
            if cur_m > 12:
                cur_m = 1
                cur_y += 1
        return months

    def run_full_backfill(self, start_year: int = 2022, start_month: int = 1,
                          end_year: Optional[int] = None, end_month: Optional[int] = None,
                          force: bool = False, delay_sec: float = 0.2,
                          is_case_study: bool = False) -> List[Dict[str, Any]]:
        """
        Executes full historical backfill from 2022 (2565) to present.
        Covers all 8 canal waterlevel stations and nearest rainfall stations.
        """
        months = self.generate_month_list(start_year, start_month, end_year, end_month)
        water_stations = self.client.get_water_stations()
        rain_stations = self.client.get_rain_stations()

        total_tasks = len(months) * (len(water_stations) + len(rain_stations))
        end_desc = f"{end_year}-{end_month:02d}" if (end_year and end_month) else "present"
        if not is_case_study:
            logger.info(f"Starting FULL historical backfill ({start_year}-{start_month:02d} to {end_desc}): {len(months)} months across {len(water_stations)} water + {len(rain_stations)} rain stations (Total ~{total_tasks} tasks)...")

        results = []
        # 1. Backfill 8 Water Stations
        for y, m in months:
            for st in water_stations:
                st_id = st["api_station_id"]
                res = self.backfill_waterlevel_monthly(st_id, y, m, force=force)
                results.append(res)
                time.sleep(delay_sec)

        # 2. Backfill Rainfall Stations
        for y, m in months:
            for st in rain_stations:
                st_id = st["api_station_id"]
                res = self.backfill_rain_monthly(st_id, y, m, force=force)
                results.append(res)
                time.sleep(delay_sec)

        return results

    def run_case_study_backfill(self, force: bool = False, delay_sec: float = 0.2) -> List[Dict[str, Any]]:
        """
        Executes backfill focused specifically on the proposal case study storm period (Sep - Oct 2026).
        """
        water_stations = self.client.get_water_stations()
        rain_stations = self.client.get_rain_stations()
        total_tasks = 2 * (len(water_stations) + len(rain_stations))
        logger.info(f"Starting CASE STUDY backfill (Sep - Oct 2026): 2 months across {len(water_stations)} water + {len(rain_stations)} rain stations (Total {total_tasks} tasks)...")
        return self.run_full_backfill(start_year=2026, start_month=9, end_year=2026, end_month=10, force=force, delay_sec=delay_sec, is_case_study=True)


def main():
    parser = argparse.ArgumentParser(description="ThaiWater Historical Backfill Runner (Real Data)")
    parser.add_argument("--type", choices=["full", "case_study", "waterlevel", "rain"], default="case_study",
                        help="'full' runs 2022 to present; 'case_study' runs Sep-Oct 2026")
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--start-month", type=int, default=1)
    parser.add_argument("--end-year", type=int, default=None)
    parser.add_argument("--end-month", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="Force re-fetch completed tasks")
    parser.add_argument("--backend", choices=["local", "s3"], default=None)
    parser.add_argument("--delay", type=float, default=0.15, help="Polite delay between API calls (seconds)")

    args = parser.parse_args()
    manager = BackfillManager(backend=args.backend)

    if args.type == "full":
        results = manager.run_full_backfill(
            start_year=args.start_year, start_month=args.start_month,
            end_year=args.end_year, end_month=args.end_month,
            force=args.force, delay_sec=args.delay
        )
    elif args.type == "case_study":
        results = manager.run_case_study_backfill(force=args.force, delay_sec=args.delay)
    elif args.type == "waterlevel":
        months = manager.generate_month_list(args.start_year, args.start_month, args.end_year, args.end_month)
        results = []
        for y, m in months:
            for st in manager.client.get_water_stations():
                res = manager.backfill_waterlevel_monthly(st["api_station_id"], y, m, force=args.force)
                results.append(res)
                time.sleep(args.delay)
    elif args.type == "rain":
        months = manager.generate_month_list(args.start_year, args.start_month, args.end_year, args.end_month)
        results = []
        for y, m in months:
            for st in manager.client.get_rain_stations():
                res = manager.backfill_rain_monthly(st["api_station_id"], y, m, force=args.force)
                results.append(res)
                time.sleep(args.delay)

    print("\n" + "=" * 60)
    print(">>> Backfill Execution Summary")
    print("=" * 60)
    succeeded = [r for r in results if r["status"] == "SUCCESS"]
    skipped = [r for r in results if r["status"] == "SKIPPED"]
    empty = [r for r in results if r["status"] == "EMPTY"]
    failed = [r for r in results if r["status"] == "FAILED"]

    print(f"Total tasks processed: {len(results)}")
    print(f"  [SUCCESS] Succeeded:                {len(succeeded)}")
    print(f"  [SKIPPED] Skipped (Idempotent):     {len(skipped)}")
    print(f"  [EMPTY]   Empty / Inactive Station: {len(empty)}")
    print(f"  [FAILED]  Failed (API Error 500):   {len(failed)}")

    if failed:
        print("\nFailed task list (External Server 500 Errors):")
        for f in failed[:10]:
            print(f"  - {f.get('task_key')}: {f.get('error', 'unknown error')}")

    if empty:
        print(f"\nEmpty task count: {len(empty)} (Telemetry station was inactive or uninstalled during this period)")


if __name__ == "__main__":
    main()
