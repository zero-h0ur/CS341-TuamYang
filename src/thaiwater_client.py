"""
ThaiWater API Client (Real Data Production Engine)
Owner: Member 1 (PurinatWanthanathanya)

Principles:
- Strict Fail-Fast: Never silently inject mock data into production/S3 pipelines.
- Accurate parsing of real ThaiWater API schemas.
- Safe type conversions (string to float, preserving 0.0, handling nulls).
- Preserves original measurement timestamps from telemetry sensors.
- Uses API's station.min_bank and diff_wl_bank directly with consistency check.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from config.config import (
    BANK_HEIGHT_TOLERANCE_M,
    OUTLIER_FREEBOARD_DIFF_M,
    RAIN_24H_GRAPH_ENDPOINT,
    RAIN_MONTHLY_GRAPH_ENDPOINT,
    RAIN_STATIONS_FILE,
    WATER_STATIONS_FILE,
    WATERLEVEL_GRAPH_ENDPOINT,
    WATERLEVEL_LOAD_ENDPOINT,
)

BKK_TZ = timezone(timedelta(hours=7))
logger = logging.getLogger("thaiwater_client")


class ThaiWaterAPIError(Exception):
    """Raised when ThaiWater API request fails or returns invalid response."""
    pass


class ThaiWaterClient:
    """Production client for ThaiWater public REST APIs."""

    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CS341-TuamYang-Pipeline/1.0",
            "Accept": "application/json, text/plain, */*",
        }
        self._water_stations = self._load_water_stations()
        self._rain_stations = self._load_rain_stations()

    def _load_water_stations(self) -> List[Dict[str, Any]]:
        if WATER_STATIONS_FILE.exists():
            with open(WATER_STATIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _load_rain_stations(self) -> List[Dict[str, Any]]:
        if RAIN_STATIONS_FILE.exists():
            with open(RAIN_STATIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def get_water_stations(self) -> List[Dict[str, Any]]:
        return self._water_stations

    def get_rain_stations(self) -> List[Dict[str, Any]]:
        return self._rain_stations

    def _request_json(self, url: str, retries: int = 3) -> Dict[str, Any]:
        """Makes an HTTP GET request with exponential backoff retry. Raises on failure."""
        last_error = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw_bytes = resp.read()
                    return json.loads(raw_bytes.decode("utf-8"))
            except Exception as e:
                last_error = e
                wait_sec = 2 ** attempt
                logger.warning(f"GET {url} failed (attempt {attempt + 1}/{retries}): {e}. Retrying in {wait_sec}s...")
                time.sleep(wait_sec)

        raise ThaiWaterAPIError(f"Failed to fetch {url} after {retries} retries: {last_error}")

    def fetch_current_waterlevel(self) -> Dict[str, Any]:
        """
        Fetches current waterlevel data from /waterlevel_load.
        Maps the 8 target Bangkok canal stations with bank height.
        """
        url = WATERLEVEL_LOAD_ENDPOINT
        raw_data = self._request_json(url)

        # Real ThaiWater response schema: {'waterlevel_data': {'data': [...]}}
        wl_container = raw_data.get("waterlevel_data", {})
        items = wl_container.get("data", []) if isinstance(wl_container, dict) else []

        if not items:
            raise ThaiWaterAPIError(f"Endpoint {url} returned empty waterlevel_data")

        records, matched_raw = self._parse_waterlevel_load(items)
        now_utc = datetime.now(timezone.utc)
        now_bkk = datetime.now(BKK_TZ)

        return {
            "source": url,
            "status": "success",
            "is_mock": False,
            "fetched_at_utc": now_utc.isoformat(),
            "fetched_at_bkk": now_bkk.isoformat(),
            "records": records,
            "raw_response": matched_raw,
        }

    def _parse_waterlevel_load(self, items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Parses real API response into standardized record structure and extracts raw matched items."""
        results = []
        matched_raw = []
        # Station maps by numeric ID and old code
        station_id_map = {s["api_station_id"]: s for s in self._water_stations}
        station_code_map = {s["tele_station_oldcode"]: s for s in self._water_stations if s.get("tele_station_oldcode")}

        for item in items:
            st = item.get("station") or {}
            st_id = st.get("id")
            old_code = st.get("tele_station_oldcode")

            meta = station_id_map.get(st_id) or station_code_map.get(old_code)
            if not meta:
                continue

            matched_raw.append(item)

            # Safe float parsing
            wl_raw = item.get("waterlevel_msl")
            if wl_raw is None:
                wl_raw = item.get("waterlevel_m")

            wl_msl = None
            if wl_raw not in (None, ""):
                try:
                    wl_msl = float(wl_raw)
                except (ValueError, TypeError):
                    wl_msl = None

            mb_raw = st.get("min_bank")
            mb_api = None
            if mb_raw not in (None, ""):
                try:
                    mb_api = float(mb_raw)
                except (ValueError, TypeError):
                    mb_api = None

            ref_bank = meta.get("min_bank_msl")
            # Validation: discard negative ground_level and fallback to reference
            if mb_api is not None and mb_api <= 0:
                mb_api = ref_bank
            elif mb_api is not None and ref_bank is not None:
                if abs(mb_api - ref_bank) > BANK_HEIGHT_TOLERANCE_M:
                    logger.warning(
                        f"Station {meta['api_station_id']} API min_bank ({mb_api}) diverges from reference ({ref_bank}) "
                        f"by > {BANK_HEIGHT_TOLERANCE_M}m. Using reference."
                    )
                    mb_api = ref_bank

            min_bank = mb_api if mb_api is not None else ref_bank

            diff_raw = item.get("diff_wl_bank")
            diff_api = None
            if diff_raw not in (None, ""):
                try:
                    diff_api = float(diff_raw)
                except (ValueError, TypeError):
                    diff_api = None

            derived_diff = round(min_bank - wl_msl, 3) if (min_bank is not None and wl_msl is not None) else None

            if diff_api is not None and derived_diff is not None:
                if abs(derived_diff - diff_api) > 0.02:
                    logger.warning(
                        f"Freeboard diff discrepancy for station {meta['api_station_id']}: "
                        f"derived={derived_diff}, api={diff_api}"
                    )

            diff_bank = diff_api if diff_api is not None else derived_diff
            diff_source = "api" if diff_api is not None else "derived"

            # Check quality flag and usability for freeboard
            quality_flag = meta.get("quality_flag", "NORMAL")
            usable_for_freeboard = meta.get("usable_for_freeboard", True)
            if diff_bank is not None and abs(diff_bank) > OUTLIER_FREEBOARD_DIFF_M:
                quality_flag = "SUSPECT_DATUM_MISMATCH"
                usable_for_freeboard = False

            left_raw = st.get("left_bank")
            left_msl = float(left_raw) if left_raw not in (None, "") else meta.get("left_bank_msl")
            right_raw = st.get("right_bank")
            right_msl = float(right_raw) if right_raw not in (None, "") else meta.get("right_bank_msl")
            ground_raw = st.get("ground_level")
            ground_msl = float(ground_raw) if ground_raw not in (None, "") else meta.get("ground_level_msl")

            # Actual measurement timestamp from sensor
            dt_raw = item.get("waterlevel_datetime") or datetime.now(BKK_TZ).strftime("%Y-%m-%d %H:%M:%S")
            try:
                # ThaiWater format is typically "YYYY-MM-DD HH:MM" or "YYYY-MM-DD HH:MM:SS"
                if len(dt_raw) == 16:
                    parsed_dt = datetime.strptime(dt_raw, "%Y-%m-%d %H:%M").replace(tzinfo=BKK_TZ)
                else:
                    parsed_dt = datetime.strptime(dt_raw[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BKK_TZ)
                iso_time = parsed_dt.isoformat()
                epoch_time = int(parsed_dt.timestamp())
            except Exception:
                parsed_dt = datetime.now(BKK_TZ)
                iso_time = parsed_dt.isoformat()
                epoch_time = int(parsed_dt.timestamp())

            results.append({
                "api_station_id": meta["api_station_id"],
                "tele_station_oldcode": meta.get("tele_station_oldcode", ""),
                "station_name_th": meta.get("station_name_th", ""),
                "river_name_th": meta.get("river_name_th", ""),
                "latitude": meta.get("latitude"),
                "longitude": meta.get("longitude"),
                "waterlevel_datetime": dt_raw,
                "timestamp_iso": iso_time,
                "timestamp_epoch": epoch_time,
                "waterlevel_msl": wl_msl,
                "min_bank_msl": min_bank,
                "left_bank_msl": left_msl,
                "right_bank_msl": right_msl,
                "ground_level_msl": ground_msl,
                "diff_wl_bank_m": diff_bank,
                "diff_source": diff_source,
                "quality_flag": quality_flag,
                "usable_for_freeboard": usable_for_freeboard,
            })

        return results, matched_raw

    def fetch_historical_waterlevel(self, api_station_id: int, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        Fetches historical waterlevel telemetry data from /waterlevel_graph with station_type=tele_waterlevel.
        Query params: station_id, start_date, end_date, station_type=tele_waterlevel.
        Resolution: 10 minutes for Bangkok canal telemetry stations (6 readings/hr), 1 hour for RID stations.
        """
        params = {
            "station_id": api_station_id,
            "start_date": start_date,
            "end_date": end_date,
            "station_type": "tele_waterlevel",
        }
        query_str = urllib.parse.urlencode(params)
        url = f"{WATERLEVEL_GRAPH_ENDPOINT}?{query_str}"

        raw_data = self._request_json(url)
        graph_data = raw_data.get("data", {})
        raw_items = graph_data.get("graph_data", []) if isinstance(graph_data, dict) else []

        # Find min_bank: prioritize min_bank directly returned by API, fallback to metadata
        meta = next((s for s in self._water_stations if s["api_station_id"] == api_station_id), None)
        api_min_bank = graph_data.get("min_bank") if isinstance(graph_data, dict) else None
        min_bank = None
        if api_min_bank not in (None, ""):
            try:
                min_bank = float(api_min_bank)
            except (ValueError, TypeError):
                min_bank = None

        ref_bank = meta.get("min_bank_msl") if meta else None
        # Validation: discard non-positive bank (ground_level) and ensure consistency with reference
        if min_bank is not None and min_bank <= 0:
            logger.warning(
                f"Station {api_station_id} returned non-positive min_bank ({min_bank}). "
                f"Falling back to reference bank {ref_bank}."
            )
            min_bank = ref_bank
        elif min_bank is not None and ref_bank is not None:
            if abs(min_bank - ref_bank) > BANK_HEIGHT_TOLERANCE_M:
                logger.warning(
                    f"Station {api_station_id} API min_bank ({min_bank}) diverges from reference ({ref_bank}) "
                    f"by > {BANK_HEIGHT_TOLERANCE_M}m. Using reference bank."
                )
                min_bank = ref_bank

        if min_bank is None:
            min_bank = ref_bank

        quality_flag_meta = meta.get("quality_flag", "NORMAL") if meta else "NORMAL"
        usable_for_freeboard_meta = meta.get("usable_for_freeboard", True) if meta else True

        records = []
        for it in raw_items:
            dt_str = it.get("datetime")
            val_raw = it.get("value")
            wl_msl = None
            if val_raw not in (None, ""):
                try:
                    wl_msl = float(val_raw)
                except (ValueError, TypeError):
                    wl_msl = None

            diff_bank = round(min_bank - wl_msl, 3) if (min_bank is not None and wl_msl is not None) else None

            # Detect datum mismatch or excessive freeboard divergence
            record_quality = quality_flag_meta
            record_usable = usable_for_freeboard_meta
            if diff_bank is not None and abs(diff_bank) > OUTLIER_FREEBOARD_DIFF_M:
                record_quality = "SUSPECT_DATUM_MISMATCH"
                record_usable = False

            records.append({
                "api_station_id": api_station_id,
                "datetime": dt_str,
                "waterlevel_msl": wl_msl,
                "min_bank_msl": min_bank,
                "diff_wl_bank_m": diff_bank,
                "diff_source": "derived",
                "quality_flag": record_quality,
                "usable_for_freeboard": record_usable,
            })

        non_null_count = sum(1 for r in records if r["waterlevel_msl"] is not None)

        return {
            "source": url,
            "status": "success",
            "is_mock": False,
            "api_station_id": api_station_id,
            "start_date": start_date,
            "end_date": end_date,
            "record_count": non_null_count,
            "total_slots": len(records),
            "records": records,
        }

    def fetch_monthly_rain(self, api_station_id: int, year: int, month: int) -> Dict[str, Any]:
        """
        Fetches monthly daily rainfall from /rain_monthly_graph.
        Query params: station_id, year, month
        """
        params = {"station_id": api_station_id, "year": year, "month": month}
        query_str = urllib.parse.urlencode(params)
        url = f"{RAIN_MONTHLY_GRAPH_ENDPOINT}?{query_str}"

        raw_data = self._request_json(url)
        items = raw_data.get("data", []) if isinstance(raw_data, dict) else []

        meta = next((s for s in self._rain_stations if s["api_station_id"] == api_station_id), None)
        station_name = meta.get("station_name_th", "") if meta else ""

        records = []
        for it in items:
            dt_str = it.get("rainfall_datetime")
            val_raw = it.get("rainfall_value")
            rain_mm = None
            if val_raw is not None:
                try:
                    rain_mm = float(val_raw)
                except (ValueError, TypeError):
                    rain_mm = None

            records.append({
                "api_station_id": api_station_id,
                "station_name_th": station_name,
                "date": dt_str,
                "rainfall_daily_mm": rain_mm,
            })

        return {
            "source": url,
            "status": "success",
            "is_mock": False,
            "api_station_id": api_station_id,
            "year": year,
            "month": month,
            "record_count": len(records),
            "records": records,
        }

    def fetch_24h_rain_station(self, api_station_id: int) -> Dict[str, Any]:
        """
        Fetches 24-hour latest rainfall for a specific station from /rain_24h_graph.
        Returns full series of hourly data points, calculates rain_24h_mm from the last 24 points.
        """
        params = {"station_id": api_station_id}
        url = f"{RAIN_24H_GRAPH_ENDPOINT}?{urllib.parse.urlencode(params)}"
        raw_data = self._request_json(url)
        items = raw_data.get("data", []) if isinstance(raw_data, dict) else []

        if not items:
            raise ThaiWaterAPIError(f"Empty rainfall series for station {api_station_id}")

        meta = next((s for s in self._rain_stations if s["api_station_id"] == api_station_id), None)

        series = []
        for it in items:
            dt_raw = it.get("rainfall_datetime")
            val_raw = it.get("rainfall_value")
            val = float(val_raw) if val_raw not in (None, "") else None
            series.append({
                "datetime": dt_raw,
                "rain_mm": val,
            })

        valid = [p for p in series if p["rain_mm"] is not None]
        last = valid[-1] if valid else (series[-1] if series else None)

        FMT = "%Y-%m-%d %H:%M"

        def _parse_rain_dt(dt_str: Optional[str]) -> Optional[datetime]:
            if not dt_str:
                return None
            try:
                if len(dt_str) >= 19:
                    return datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S")
                return datetime.strptime(dt_str[:16], FMT)
            except Exception:
                return None

        last_dt = _parse_rain_dt(last.get("datetime")) if last else None
        last24 = [
            p for p in valid
            if _parse_rain_dt(p.get("datetime")) and last_dt and _parse_rain_dt(p.get("datetime")) > last_dt - timedelta(hours=24)
        ] if last_dt else []

        return {
            "api_station_id": api_station_id,
            "station_name_th": meta.get("station_name_th", "") if meta else "",
            "latitude": meta.get("latitude") if meta else None,
            "longitude": meta.get("longitude") if meta else None,
            "rainfall_datetime": last.get("datetime") if last else "",
            "rain_24h_mm": round(sum(p["rain_mm"] for p in last24), 1) if last24 else None,
            "rain_24h_points": len(last24),
            "rain_latest_mm": round(last["rain_mm"], 1) if (last and last.get("rain_mm") is not None) else None,
            "window_start": series[0]["datetime"] if series else None,
            "window_end": series[-1]["datetime"] if series else None,
            "series": series,
        }
