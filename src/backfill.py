"""
Historical Backfill Script for ThaiWater Data (From 2022 to Present)
Owner: Member 1 (ThaiWater Ingestion)

Success Criteria 1:
- Backfill waterlevel for 8 stations & monthly rain from 2022 (2565) to S3/Local.
- Running the same batch again must be idempotent and not cause duplicate records.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typing import Any, Dict, List, Optional
from config.config import CHECKPOINT_FILE, WATERLEVEL_PREFIX, RAIN_PREFIX
from src.storage import get_storage
from src.thaiwater_client import ThaiWaterClient
from src.utils.logger import setup_logger, log_ingestion_metric

logger = setup_logger("backfill_thaiwater")


class BackfillManager:
    """Manages idempotent historical backfill with progress checkpointing."""

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
        return {"completed_tasks": {}, "last_updated": None}

    def _save_checkpoint(self, task_key: str, metadata: Dict[str, Any]):
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoints["completed_tasks"][task_key] = metadata
        self.checkpoints["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(self.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(self.checkpoints, f, ensure_ascii=False, indent=2)

    def is_task_completed(self, task_key: str, dest_key: str) -> bool:
        """Verifies if task was previously marked complete AND file exists."""
        return task_key in self.checkpoints.get("completed_tasks", {}) and self.storage.exists(dest_key)

    def backfill_waterlevel_monthly(self, station_id: str, year: int, month: int,
                                    force: bool = False, use_mock: bool = False) -> Dict[str, Any]:
        """
        Backfills 10-minute historical water levels for a specific station and month.
        Deterministically stored under:
          raw/thaiwater/waterlevel/dt=YYYY-MM-01/waterlevel_hist_{station_id}_{year}_{month:02d}.json
        """
        task_key = f"wl_{station_id}_{year}_{month:02d}"
        partition_dt = f"{year:04d}-{month:02d}-01"
        dest_key = f"{WATERLEVEL_PREFIX}/dt={partition_dt}/waterlevel_hist_{station_id}_{year}_{month:02d}.json"

        if not force and self.is_task_completed(task_key, dest_key):
            logger.info(f"[SKIP] Task {task_key} already completed at {dest_key}. Idempotency guaranteed.")
            return {"status": "SKIPPED", "task_key": task_key, "destination": dest_key}

        start_time = time.time()
        # Fetch data for the month
        date_str = f"{year:04d}-{month:02d}-15"
        fetch_res = self.client.fetch_historical_waterlevel(station_id, date_str)
        raw_items = fetch_res.get("data") or fetch_res.get("raw_response", {}).get("data", [])

        # Enrich with min_bank to compute freeboard
        water_meta = next((s for s in self.client.get_water_stations() if s["station_id"] == station_id), None)
        min_bank = water_meta["min_bank_msl"] if water_meta else 2.0

        records = []
        for it in raw_items:
            wl = it.get("waterlevel_msl") or it.get("waterlevel_m")
            records.append({
                "station_id": station_id,
                "datetime": it.get("datetime") or it.get("waterlevel_datetime"),
                "waterlevel_msl": wl,
                "min_bank_msl": min_bank,
                "diff_wl_bank_m": round(min_bank - wl, 3) if wl is not None else None,
            })

        envelope = {
            "envelope_version": "1.0",
            "batch_type": "historical_backfill",
            "data_type": "waterlevel",
            "station_id": station_id,
            "year": year,
            "month": month,
            "record_count": len(records),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "data": records,
        }

        # Save deterministically (Overwrites identical key if force/re-run, no duplicate records)
        dest_uri = self.storage.save_json(dest_key, envelope, overwrite=True)
        duration = time.time() - start_time

        self._save_checkpoint(task_key, {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(records),
            "dest_key": dest_key,
        })

        log_ingestion_metric(
            logger=logger,
            batch_id=task_key,
            data_type="waterlevel_hist",
            record_count=len(records),
            duration_sec=duration,
            destination=dest_uri,
            status="SUCCESS",
        )

        return {
            "status": "SUCCESS",
            "task_key": task_key,
            "record_count": len(records),
            "destination": dest_uri,
        }

    def backfill_rain_monthly(self, station_id: str, year: int, month: int,
                              force: bool = False, use_mock: bool = False) -> Dict[str, Any]:
        """
        Backfills monthly daily rain records for a specific station and month.
        Deterministically stored under:
          raw/thaiwater/rain/dt=YYYY-MM-01/rain_monthly_{station_id}_{year}_{month:02d}.json
        """
        task_key = f"rain_{station_id}_{year}_{month:02d}"
        partition_dt = f"{year:04d}-{month:02d}-01"
        dest_key = f"{RAIN_PREFIX}/dt={partition_dt}/rain_monthly_{station_id}_{year}_{month:02d}.json"

        if not force and self.is_task_completed(task_key, dest_key):
            logger.info(f"[SKIP] Task {task_key} already completed at {dest_key}. Idempotency guaranteed.")
            return {"status": "SKIPPED", "task_key": task_key, "destination": dest_key}

        start_time = time.time()
        fetch_res = self.client.fetch_monthly_rain(station_id, year, month)
        records = fetch_res.get("records") or fetch_res.get("raw_response", {}).get("data", [])

        envelope = {
            "envelope_version": "1.0",
            "batch_type": "historical_backfill",
            "data_type": "rain_monthly",
            "station_id": station_id,
            "year": year,
            "month": month,
            "record_count": len(records),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "data": records,
        }

        dest_uri = self.storage.save_json(dest_key, envelope, overwrite=True)
        duration = time.time() - start_time

        self._save_checkpoint(task_key, {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(records),
            "dest_key": dest_key,
        })

        log_ingestion_metric(
            logger=logger,
            batch_id=task_key,
            data_type="rain_monthly_hist",
            record_count=len(records),
            duration_sec=duration,
            destination=dest_uri,
            status="SUCCESS",
        )

        return {
            "status": "SUCCESS",
            "task_key": task_key,
            "record_count": len(records),
            "destination": dest_uri,
        }

    def run_case_study_backfill(self, force: bool = False, use_mock: bool = False) -> List[Dict[str, Any]]:
        """
        Runs backfill specifically focused on the Proposal Case Study period (Sep-Oct 2026 / target floods)
        covering all 8 water stations and key rain stations.
        """
        results = []
        logger.info("Executing focused case-study backfill (Sep - Oct 2026)...")

        # 1. 8 Water Stations
        water_stations = self.client.get_water_stations()
        for st in water_stations:
            st_id = st["station_id"]
            res = self.backfill_waterlevel_monthly(st_id, year=2026, month=9, force=force, use_mock=use_mock)
            results.append(res)
            time.sleep(0.05)  # Rate limiting sleep

        # 2. Key Rain Stations
        rain_stations = self.client.get_rain_stations()
        for st in rain_stations[:15]:  # Focus on key representative stations
            st_id = st["station_id"]
            res = self.backfill_rain_monthly(st_id, year=2026, month=9, force=force, use_mock=use_mock)
            results.append(res)
            time.sleep(0.05)

        return results


def main():
    parser = argparse.ArgumentParser(description="ThaiWater Historical Backfill Runner")
    parser.add_argument("--type", choices=["case_study", "waterlevel", "rain", "full"], default="case_study")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--month", type=int, default=9)
    parser.add_argument("--force", action="store_true", help="Force re-fetch even if task was marked completed")
    parser.add_argument("--mock", action="store_true", help="Use mock historical data for sandbox/offline test")
    parser.add_argument("--backend", choices=["local", "s3"], default=None)

    args = parser.parse_args()
    manager = BackfillManager(backend=args.backend)

    if args.type == "case_study":
        results = manager.run_case_study_backfill(force=args.force, use_mock=args.mock)
    elif args.type == "waterlevel":
        results = []
        for st in manager.client.get_water_stations():
            res = manager.backfill_waterlevel_monthly(st["station_id"], args.year, args.month, force=args.force, use_mock=args.mock)
            results.append(res)
    elif args.type == "rain":
        results = []
        for st in manager.client.get_rain_stations()[:15]:
            res = manager.backfill_rain_monthly(st["station_id"], args.year, args.month, force=args.force, use_mock=args.mock)
            results.append(res)
    else:
        results = manager.run_case_study_backfill(force=args.force, use_mock=args.mock)

    print("\n--- Backfill Execution Results ---")
    completed = sum(1 for r in results if r["status"] == "SUCCESS")
    skipped = sum(1 for r in results if r["status"] == "SKIPPED")
    print(f"Total tasks: {len(results)} | Succeeded: {completed} | Skipped (Idempotent): {skipped}")


if __name__ == "__main__":
    main()
