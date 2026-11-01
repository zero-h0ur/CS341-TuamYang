"""
Main Ingestion Lambda Function & CLI for ThaiWater Data (Real Data Engine)
Owner: Member 1 (PurinatWanthanathanya)

Usage:
  - AWS Lambda: handler is 'src.ingest_thaiwater.lambda_handler'
  - CLI: python -m src.ingest_thaiwater --type waterlevel|rain|all [--backend local|s3]
"""

import argparse
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import INCOMING_PREFIX, RAIN_PREFIX, WATERLEVEL_PREFIX
from src.storage import get_storage
from src.thaiwater_client import ThaiWaterAPIError, ThaiWaterClient
from src.utils.logger import log_ingestion_metric, setup_logger

BKK_TZ = timezone(timedelta(hours=7))
logger = setup_logger("ingest_thaiwater")


def create_envelope(data_type: str, source_endpoint: str, records: List[Dict[str, Any]],
                    is_mock: bool = False, raw_response: Optional[Any] = None) -> Dict[str, Any]:
    """Wraps fetched data into standardized ingestion envelope."""
    now_utc = datetime.now(timezone.utc)
    now_bkk = datetime.now(BKK_TZ)
    batch_suffix = uuid.uuid4().hex[:6]
    batch_id = f"tw-{data_type[:2]}-{now_bkk.strftime('%Y%m%d-%H%M%S')}-{batch_suffix}"

    return {
        "envelope_version": "1.0",
        "batch_id": batch_id,
        "data_type": data_type,
        "is_mock": is_mock,
        "source_endpoint": source_endpoint,
        "ingested_at_utc": now_utc.isoformat(),
        "ingested_at_bkk": now_bkk.isoformat(),
        "station_count": len(records),
        "record_count": len(records),
        "data": records,
        "raw_response": raw_response if raw_response is not None else {},
    }


def ingest_waterlevel(client: ThaiWaterClient, storage_driver: Any) -> Dict[str, Any]:
    """Ingests current waterlevel for 8 Bangkok target canal stations from real API."""
    start_time = time.time()
    now_bkk = datetime.now(BKK_TZ)
    dt_str = now_bkk.strftime("%Y-%m-%d")
    hour_str = now_bkk.strftime("%H")

    logger.info(f"Starting real waterlevel ingestion at {now_bkk.isoformat()}...")
    try:
        fetch_result = client.fetch_current_waterlevel()
    except ThaiWaterAPIError as e:
        logger.error(f"Waterlevel ingestion failed: {e}")
        return {"status": "FAILED", "data_type": "waterlevel", "error": str(e)}

    records = fetch_result.get("records", [])
    if not records:
        logger.warning("No waterlevel records matched Bangkok target stations.")
        return {"status": "EMPTY", "data_type": "waterlevel", "record_count": 0}

    is_mock = fetch_result.get("is_mock", False)
    envelope = create_envelope(
        data_type="waterlevel",
        source_endpoint=fetch_result.get("source", "waterlevel_load"),
        records=records,
        is_mock=is_mock,
        raw_response=fetch_result.get("raw_response", []),
    )

    batch_id = envelope["batch_id"]
    filename = f"waterlevel_{batch_id}.json"

    # 1. Write to incoming/thaiwater/dt=.../hour=.../ (for Step Functions trigger)
    incoming_key = f"{INCOMING_PREFIX}/dt={dt_str}/hour={hour_str}/{filename}"
    incoming_uri = storage_driver.save_json(incoming_key, envelope)

    # 2. Store original archive in raw/thaiwater/waterlevel/dt=.../hour=.../
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
        status="SUCCESS",
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


def ingest_rain(client: ThaiWaterClient, storage_driver: Any) -> Dict[str, Any]:
    """Ingests 24-hour rainfall for Bangkok telemetry stations from real API."""
    start_time = time.time()
    now_bkk = datetime.now(BKK_TZ)
    dt_str = now_bkk.strftime("%Y-%m-%d")
    hour_str = now_bkk.strftime("%H")

    logger.info(f"Starting real rainfall ingestion at {now_bkk.isoformat()}...")
    rain_stations = client.get_rain_stations()
    records = []
    failed_stations = []

    for st in rain_stations:
        st_id = st["api_station_id"]
        try:
            res = client.fetch_24h_rain_station(st_id)
            records.append(res)
        except Exception as e:
            logger.warning(f"Could not fetch 24h rain for station {st_id}: {e}")
            failed_stations.append({"station_id": st_id, "error": str(e)})

    if not records:
        logger.error("Failed to fetch rainfall records for all stations.")
        return {
            "status": "FAILED",
            "data_type": "rain",
            "error": "No rainfall stations responded",
            "failed_stations": failed_stations,
        }

    status = "SUCCESS" if not failed_stations else "PARTIAL"

    envelope = create_envelope(
        data_type="rain",
        source_endpoint="rain_24h_graph",
        records=records,
        is_mock=False,
    )
    if failed_stations:
        envelope["failed_stations"] = failed_stations
        envelope["failed_count"] = len(failed_stations)

    batch_id = envelope["batch_id"]
    filename = f"rain_{batch_id}.json"

    # 1. Write to incoming/thaiwater/dt=.../hour=.../ (for Step Functions trigger)
    incoming_key = f"{INCOMING_PREFIX}/dt={dt_str}/hour={hour_str}/{filename}"
    incoming_uri = storage_driver.save_json(incoming_key, envelope)

    # 2. Store original archive in raw/thaiwater/rain/dt=.../hour=.../
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
        status=status,
    )

    return {
        "status": status,
        "data_type": "rain",
        "batch_id": batch_id,
        "record_count": len(records),
        "failed_count": len(failed_stations),
        "failed_stations": failed_stations,
        "destination_uri": dest_uri,
        "incoming_uri": incoming_uri,
        "partition_key": partition_key,
        "incoming_key": incoming_key,
        "duration_sec": round(duration, 3),
    }


def lambda_handler(event: Optional[Dict[str, Any]] = None, context: Optional[Any] = None) -> Dict[str, Any]:
    """
    AWS Lambda entrypoint. Triggered by EventBridge Rules.
      {"type": "waterlevel"} -> triggered every 15 mins
      {"type": "rain"}       -> triggered every 1 hour
    """
    event = event or {}
    ingest_type = event.get("type") or event.get("source_type") or "waterlevel"
    backend = event.get("storage_backend", None)

    storage = get_storage(backend)
    client = ThaiWaterClient()

    results = []
    if ingest_type in ["waterlevel", "all"]:
        res_wl = ingest_waterlevel(client, storage)
        results.append(res_wl)

    if ingest_type in ["rain", "all"]:
        res_rn = ingest_rain(client, storage)
        results.append(res_rn)

    return {
        "statusCode": 200,
        "message": f"Processed ingestion for type '{ingest_type}'",
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="ThaiWater Ingestion Runner (Real Data)")
    parser.add_argument("--type", choices=["waterlevel", "rain", "all"], default="all")
    parser.add_argument("--backend", choices=["local", "s3"], default=None)

    args = parser.parse_args()
    res = lambda_handler({"type": args.type, "storage_backend": args.backend})
    print("\n--- Ingestion Result Summary ---")
    for r in res.get("results", []):
        print(f"[{r['data_type'].upper()}] Status: {r['status']} | Batch: {r.get('batch_id')} | Records: {r.get('record_count')}")


if __name__ == "__main__":
    main()
