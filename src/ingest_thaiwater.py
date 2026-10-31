"""
Main Ingestion Lambda Function & CLI for ThaiWater Data
Owner: Member 1 (ThaiWater Ingestion)

Usage:
  - AWS Lambda: handler is 'src.ingest_thaiwater.lambda_handler'
  - CLI: python -m src.ingest_thaiwater --type waterlevel|rain|all [--backend local|s3]
"""

import argparse
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typing import Any, Dict, List, Optional
from config.config import INCOMING_PREFIX, WATERLEVEL_PREFIX, RAIN_PREFIX
from src.storage import get_storage
from src.thaiwater_client import ThaiWaterClient
from src.utils.logger import setup_logger, log_ingestion_metric

BKK_TZ = timezone(timedelta(hours=7))
logger = setup_logger("ingest_thaiwater")


def create_envelope(data_type: str, source_endpoint: str, records: List[Dict[str, Any]],
                    raw_response: Optional[Any] = None) -> Dict[str, Any]:
    """Wraps fetched data into standardized ingestion envelope."""
    now_utc = datetime.now(timezone.utc)
    now_bkk = datetime.now(BKK_TZ)
    batch_suffix = uuid.uuid4().hex[:6]
    batch_id = f"tw-{data_type[:2]}-{now_bkk.strftime('%Y%m%d-%H%M%S')}-{batch_suffix}"

    return {
        "envelope_version": "1.0",
        "batch_id": batch_id,
        "data_type": data_type,
        "source_endpoint": source_endpoint,
        "ingested_at_utc": now_utc.isoformat(),
        "ingested_at_bkk": now_bkk.isoformat(),
        "station_count": len(records),
        "record_count": len(records),
        "data": records,
        "raw_response": raw_response if raw_response is not None else {},
    }


def ingest_waterlevel(client: ThaiWaterClient, storage_driver: Any, use_mock: bool = False) -> Dict[str, Any]:
    """Ingests current waterlevel for 8 Bangkok target canal stations."""
    start_time = time.time()
    now_bkk = datetime.now(BKK_TZ)
    dt_str = now_bkk.strftime("%Y-%m-%d")
    hour_str = now_bkk.strftime("%H")

    logger.info(f"Starting waterlevel ingestion at {now_bkk.isoformat()}...")
    fetch_result = client.fetch_current_waterlevel(use_offline_mock=use_mock)
    records = fetch_result.get("records", [])

    envelope = create_envelope(
        data_type="waterlevel",
        source_endpoint=fetch_result.get("source", "waterlevel_load"),
        records=records,
        raw_response=fetch_result.get("raw_response"),
    )

    batch_id = envelope["batch_id"]
    filename = f"waterlevel_{batch_id}.json"

    # 1. Write to incoming/thaiwater/dt=.../hour=.../ (for Step Functions trigger)
    incoming_key = f"{INCOMING_PREFIX}/dt={dt_str}/hour={hour_str}/{filename}"
    incoming_uri = storage_driver.save_json(incoming_key, envelope)

    # 2. Store original in raw/thaiwater/waterlevel/dt=.../hour=.../ (raw zone archive)
    partition_key = f"{WATERLEVEL_PREFIX}/dt={dt_str}/hour={hour_str}/{filename}"
    dest_uri = storage_driver.save_json(partition_key, envelope)
    duration = time.time() - start_time

    log_ingestion_metric(
        logger=logger,
        batch_id=batch_id,
        data_type="waterlevel",
        record_count=len(records),
        duration_sec=duration,
        destination=f"{incoming_uri} & {dest_uri}",
        status="SUCCESS" if records else "EMPTY",
    )

    return {
        "status": "SUCCESS",
        "data_type": "waterlevel",
        "batch_id": batch_id,
        "record_count": len(records),
        "destination_uri": dest_uri,
        "incoming_uri": incoming_uri,
        "partition_key": partition_key,
        "incoming_key": incoming_key,
        "duration_sec": round(duration, 3),
    }


def ingest_rain(client: ThaiWaterClient, storage_driver: Any, use_mock: bool = False) -> Dict[str, Any]:
    """Ingests 24-hour rainfall for Bangkok telemetry stations."""
    start_time = time.time()
    now_bkk = datetime.now(BKK_TZ)
    dt_str = now_bkk.strftime("%Y-%m-%d")
    hour_str = now_bkk.strftime("%H")

    logger.info(f"Starting rainfall ingestion at {now_bkk.isoformat()}...")
    fetch_result = client.fetch_24h_rain()
    records = fetch_result.get("records", [])

    envelope = create_envelope(
        data_type="rain",
        source_endpoint=fetch_result.get("source", "rain_24h_graph"),
        records=records,
        raw_response=fetch_result.get("raw_response"),
    )

    batch_id = envelope["batch_id"]
    filename = f"rain_{batch_id}.json"

    # 1. Write to incoming/thaiwater/dt=.../hour=.../ (for Step Functions trigger)
    incoming_key = f"{INCOMING_PREFIX}/dt={dt_str}/hour={hour_str}/{filename}"
    incoming_uri = storage_driver.save_json(incoming_key, envelope)

    # 2. Store original in raw/thaiwater/rain/dt=.../hour=.../ (raw zone archive)
    partition_key = f"{RAIN_PREFIX}/dt={dt_str}/hour={hour_str}/{filename}"
    dest_uri = storage_driver.save_json(partition_key, envelope)
    duration = time.time() - start_time

    log_ingestion_metric(
        logger=logger,
        batch_id=batch_id,
        data_type="rain",
        record_count=len(records),
        duration_sec=duration,
        destination=f"{incoming_uri} & {dest_uri}",
        status="SUCCESS" if records else "EMPTY",
    )

    return {
        "status": "SUCCESS",
        "data_type": "rain",
        "batch_id": batch_id,
        "record_count": len(records),
        "destination_uri": dest_uri,
        "incoming_uri": incoming_uri,
        "partition_key": partition_key,
        "incoming_key": incoming_key,
        "duration_sec": round(duration, 3),
    }


def lambda_handler(event: Optional[Dict[str, Any]] = None, context: Optional[Any] = None) -> Dict[str, Any]:
    """
    AWS Lambda entrypoint. Triggered by EventBridge Scheduler.
    Event structure:
      {"type": "waterlevel"} -> triggered every 15 mins
      {"type": "rain"}       -> triggered every 1 hour
      {"type": "all"}        -> triggered manually or for full run
    """
    event = event or {}
    ingest_type = event.get("type") or event.get("source_type") or "waterlevel"
    use_mock = event.get("mock", False)
    backend = event.get("storage_backend", None)

    storage = get_storage(backend)
    client = ThaiWaterClient()

    results = []
    if ingest_type in ["waterlevel", "all"]:
        res_wl = ingest_waterlevel(client, storage, use_mock=use_mock)
        results.append(res_wl)

    if ingest_type in ["rain", "all"]:
        res_rn = ingest_rain(client, storage, use_mock=use_mock)
        results.append(res_rn)

    return {
        "statusCode": 200,
        "message": f"Successfully processed ingestion for type '{ingest_type}'",
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="ThaiWater Ingestion Runner (Member 1)")
    parser.add_argument(
        "--type",
        choices=["waterlevel", "rain", "all"],
        default="all",
        help="Type of data to ingest (default: all)"
    )
    parser.add_argument(
        "--backend",
        choices=["local", "s3"],
        default=None,
        help="Storage driver override (local or s3)"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock data if offline or sandbox network is restricted"
    )

    args = parser.parse_args()
    res = lambda_handler({"type": args.type, "mock": args.mock, "storage_backend": args.backend})
    print("\n--- Ingestion Result Summary ---")
    for r in res.get("results", []):
        print(f"[{r['data_type'].upper()}] Batch: {r['batch_id']} | Records: {r['record_count']} | Saved: {r['destination_uri']}")


if __name__ == "__main__":
    main()
