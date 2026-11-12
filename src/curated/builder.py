"""
Curated Data Mart Builder: khwaeng_hourly
Owner: Member 5 (nustapark6709650334)

Transforms validated trusted/ (JSONL from Issue #1 Step Functions pipeline) and
validated historical backfill partitions into the curated dimensional table:
  curated/khwaeng_hourly (1 row per khwaeng per Bangkok hour, partitioned by dt=YYYY-MM-DD)

Aligned with main branch contracts:
- PR #7 (ThaiWater):
  * 8 water stations + 14 verified rain stations
  * Composite deduplication key (api_station_id, timestamp_epoch) with latest-batch-wins
  * Respects usable_for_freeboard=false on Station 10 (SUSPECT_DATUM_MISMATCH)
  * Respects usable_for_rainfall=false on Station 852087 (SUSPECT_RAIN_SENSOR_OUTAGE)
- PR #10 (Traffy Fondue):
  * Preserves full daily report denominator (flood + non_flood + uncertain)
  * Deduplicates by ticket_id and verifies manifest / _SUCCESS.json status
- PR #12 (Orchestration & Validation):
  * Reads validated JSONL from trusted/{waterlevel,rain,traffy}/dt=YYYY-MM-DD/
  * Emits Hive-partitioned JSONL curated/khwaeng_hourly/dt=YYYY-MM-DD/khwaeng_hourly.jsonl
"""

import csv
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BKK = timezone(timedelta(hours=7))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two coordinates in kilometers."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def extract_khwaeng_khet(address: Optional[str]) -> Tuple[str, str]:
    """Extract subdistrict (khwaeng) and district (khet) from address string."""
    if not address:
        return "ไม่ระบุ", "ไม่ระบุ"

    m_kh = re.search(r"แขวง([^\s,]+)", address)
    m_khet = re.search(r"เขต([^\s,]+)", address)

    khwaeng = m_kh.group(1).strip() if m_kh else "ไม่ระบุ"
    khet = m_khet.group(1).strip() if m_khet else "ไม่ระบุ"
    return khwaeng, khet


def _dt_to_epoch_bkk(dt_str: str) -> int:
    """Parse 'YYYY-MM-DD HH:MM' in Bangkok time to Unix epoch seconds."""
    dt = datetime.strptime(dt_str[:16], "%Y-%m-%d %H:%M").replace(tzinfo=BKK)
    return int(dt.timestamp())


class CuratedBuilder:
    def __init__(
        self,
        base_dir: Optional[Path] = None,
        reference_dir: Optional[Path] = None,
        raw_thaiwater_dir: Optional[Path] = None,
        incoming_traffy_dir: Optional[Path] = None,
        trusted_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
    ):
        self.base_dir = Path(base_dir or Path(__file__).resolve().parent.parent.parent)
        self.ref_dir = Path(reference_dir or self.base_dir / "reference")
        self.raw_tw_dir = Path(raw_thaiwater_dir or self.base_dir / "data" / "raw" / "thaiwater")
        self.incoming_traffy_dir = Path(incoming_traffy_dir or self.base_dir / "data" / "incoming" / "traffy")
        self.trusted_dir = Path(trusted_dir or self.base_dir / "data" / "trusted")
        self.out_dir = Path(output_dir or self.base_dir / "data" / "curated" / "khwaeng_hourly")

        self.water_stations: List[Dict[str, Any]] = []
        self.water_map: Dict[int, Dict[str, Any]] = {}
        self.rain_stations: List[Dict[str, Any]] = []
        self.rain_map: Dict[int, Dict[str, Any]] = {}
        self._load_reference()

    def _load_reference(self) -> None:
        wl_ref_file = self.ref_dir / "stations_water.json"
        if wl_ref_file.exists():
            with open(wl_ref_file, "r", encoding="utf-8") as f:
                self.water_stations = json.load(f)
            self.water_map = {s["api_station_id"]: s for s in self.water_stations}

        rain_ref_file = self.ref_dir / "stations_rain.json"
        if rain_ref_file.exists():
            with open(rain_ref_file, "r", encoding="utf-8") as f:
                self.rain_stations = json.load(f)
            self.rain_map = {s["api_station_id"]: s for s in self.rain_stations}

    def find_nearest_water_station(self, lat: float, lon: float, usable_only: bool = False) -> Tuple[int, float]:
        """Find the nearest water station and distance in km."""
        candidates = [
            s for s in self.water_stations
            if (not usable_only or s.get("usable_for_freeboard", True))
        ]
        if not candidates:
            candidates = self.water_stations
        if not candidates:
            return 1, 0.0
        best_st = min(
            candidates,
            key=lambda s: haversine_km(lat, lon, s["latitude"], s["longitude"])
        )
        dist = haversine_km(lat, lon, best_st["latitude"], best_st["longitude"])
        return best_st["api_station_id"], dist

    def find_nearest_rain_station(self, lat: float, lon: float, usable_only: bool = True) -> Tuple[int, float]:
        """
        Find the nearest rain station and distance in km.
        By default filters to usable_for_rainfall=True per schema_contract.md section 5
        (excluding Station 852087 SUSPECT_RAIN_SENSOR_OUTAGE).
        """
        candidates = [
            s for s in self.rain_stations
            if (not usable_only or s.get("usable_for_rainfall", True))
        ]
        if not candidates:
            candidates = self.rain_stations
        if not candidates:
            return 1, 0.0
        best_st = min(
            candidates,
            key=lambda s: haversine_km(lat, lon, s["latitude"], s["longitude"])
        )
        dist = haversine_km(lat, lon, best_st["latitude"], best_st["longitude"])
        return best_st["api_station_id"], dist

    def load_waterlevel_hourly(self, dates: List[str]) -> Dict[Tuple[int, str, int], Dict[str, Any]]:
        """
        Loads waterlevel telemetry for requested dates (from both raw backfill and trusted JSONL),
        deduplicating by (api_station_id, timestamp_epoch) with latest-batch-wins,
        and aggregates to hourly metrics.
        Returns map: (station_id, date, hour) -> metrics dict
        """
        # Dedup map: (station_id, timestamp_epoch) -> (sort_key, record_dict)
        dedup_points: Dict[Tuple[int, int], Tuple[str, Dict[str, Any]]] = {}
        months_needed = sorted(list(set(d[:7] for d in dates)))

        # 1. Load historical backfill files from raw/thaiwater/waterlevel/
        for ym in months_needed:
            month_dir = self.raw_tw_dir / "waterlevel" / f"dt={ym}-01"
            if not month_dir.exists():
                continue
            for file_path in sorted(month_dir.glob("waterlevel_hist_st*.json")):
                with open(file_path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                st_id = doc.get("api_station_id")
                st_info = self.water_map.get(st_id, {})
                created_at = doc.get("created_at_utc", "")

                for record in doc.get("data", []):
                    dt_str = record.get("datetime", "")
                    if len(dt_str) < 16:
                        continue
                    rec_date = dt_str[:10]
                    if rec_date not in dates:
                        continue
                    val = record.get("waterlevel_msl")
                    if val is None:
                        continue
                    epoch = record.get("timestamp_epoch") or _dt_to_epoch_bkk(dt_str)
                    key = (st_id, int(epoch))
                    rec_norm = {
                        "api_station_id": st_id,
                        "datetime": dt_str[:16],
                        "waterlevel_msl": float(val),
                        "min_bank_msl": record.get("min_bank_msl", st_info.get("min_bank_msl")),
                        "diff_wl_bank_m": record.get("diff_wl_bank_m"),
                        "quality_flag": record.get("quality_flag", st_info.get("quality_flag", "NORMAL")),
                        "usable_for_freeboard": record.get(
                            "usable_for_freeboard", st_info.get("usable_for_freeboard", True)
                        ),
                    }
                    dedup_points[key] = (f"0_{created_at}", rec_norm)

        # 2. Load validated live JSONL batches from trusted/waterlevel/ (latest batch wins)
        for d in dates:
            t_dir = self.trusted_dir / "waterlevel" / f"dt={d}"
            if not t_dir.exists():
                continue
            for jsonl_path in sorted(t_dir.glob("*.jsonl")):
                for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    st_id = record.get("api_station_id")
                    dt_str = record.get("waterlevel_datetime", "")
                    val = record.get("waterlevel_msl")
                    if st_id is None or len(dt_str) < 16 or val is None:
                        continue
                    rec_date = dt_str[:10]
                    if rec_date not in dates:
                        continue
                    st_info = self.water_map.get(st_id, {})
                    epoch = record.get("timestamp_epoch") or _dt_to_epoch_bkk(dt_str)
                    sort_key = f"1_{record.get('ingested_at_utc', '')}_{record.get('batch_id', '')}"
                    key = (st_id, int(epoch))
                    prev = dedup_points.get(key)
                    if prev is None or sort_key >= prev[0]:
                        dedup_points[key] = (sort_key, {
                            "api_station_id": st_id,
                            "datetime": dt_str[:16],
                            "waterlevel_msl": float(val),
                            "min_bank_msl": record.get("min_bank_msl", st_info.get("min_bank_msl")),
                            "diff_wl_bank_m": record.get("diff_wl_bank_m"),
                            "quality_flag": record.get("quality_flag", st_info.get("quality_flag", "NORMAL")),
                            "usable_for_freeboard": record.get(
                                "usable_for_freeboard", st_info.get("usable_for_freeboard", True)
                            ),
                        })

        # 3. Group deduplicated points by (station_id, date, hour)
        hourly_data: Dict[Tuple[int, str, int], Dict[str, Any]] = defaultdict(
            lambda: {
                "vals": [],
                "fbs": [],
                "min_bank": None,
                "quality_flag": "NORMAL",
                "usable_for_freeboard": True,
            }
        )
        for (st_id, _epoch), (_sk, rec) in dedup_points.items():
            dt_str = rec["datetime"]
            rec_date = dt_str[:10]
            rec_hour = int(dt_str[11:13])
            bucket = hourly_data[(st_id, rec_date, rec_hour)]
            bucket["vals"].append(rec["waterlevel_msl"])
            if rec.get("diff_wl_bank_m") is not None:
                bucket["fbs"].append(float(rec["diff_wl_bank_m"]))
            bucket["min_bank"] = rec["min_bank_msl"]
            bucket["quality_flag"] = rec["quality_flag"]
            bucket["usable_for_freeboard"] = bool(rec["usable_for_freeboard"])

        result: Dict[Tuple[int, str, int], Dict[str, Any]] = {}
        for (st_id, rec_date, rec_hour), agg in hourly_data.items():
            vals = agg["vals"]
            min_bank = agg["min_bank"]
            usable = agg["usable_for_freeboard"]
            if not vals:
                continue

            avg_wl = round(sum(vals) / len(vals), 3)
            max_wl = round(max(vals), 3)
            min_wl = round(min(vals), 3)

            if agg["fbs"]:
                raw_fb = round(min(agg["fbs"]), 3)
            elif min_bank is not None:
                raw_fb = round(min_bank - max_wl, 3)
            else:
                raw_fb = None

            # Only expose min_freeboard_m and overflow when usable_for_freeboard is True
            min_freeboard = raw_fb if usable else None
            is_overflow = bool(usable and min_freeboard is not None and min_freeboard <= 0.0)

            result[(st_id, rec_date, rec_hour)] = {
                "avg_waterlevel_msl": avg_wl,
                "max_waterlevel_msl": max_wl,
                "min_waterlevel_msl": min_wl,
                "min_bank_msl": min_bank,
                "raw_freeboard_m": raw_fb,
                "min_freeboard_m": min_freeboard,
                "is_overflow": is_overflow,
                "water_quality_flag": agg["quality_flag"],
                "usable_for_freeboard": usable,
                "readings_count": len(vals),
            }
        return result

    def load_rainfall(self, dates: List[str]) -> Tuple[Dict[Tuple[int, str], Dict[str, Any]], Dict[Tuple[int, str, int], float]]:
        """
        Loads daily rainfall (from raw backfill) and hourly rainfall (from trusted/rain JSONL,
        deduplicated with latest-batch-wins per schema_contract.md 8.2).
        Returns:
          - daily_map: (rain_station_id, date) -> {"rainfall_daily_mm": float|None, "quality_flag": str, "usable_for_rainfall": bool}
          - hourly_map: (rain_station_id, date, hour) -> rain_mm (float)
        """
        daily_data: Dict[Tuple[int, str], Dict[str, Any]] = {}
        months_needed = sorted(list(set(d[:7] for d in dates)))

        for ym in months_needed:
            month_dir = self.raw_tw_dir / "rain" / f"dt={ym}-01"
            if not month_dir.exists():
                continue
            for file_path in sorted(month_dir.glob("rain_monthly_st*.json")):
                with open(file_path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                st_id = doc.get("api_station_id")
                st_info = self.rain_map.get(st_id, {})
                for record in doc.get("data", []):
                    rec_date = record.get("date")
                    if rec_date not in dates:
                        continue
                    qflag = record.get("quality_flag", st_info.get("quality_flag", "OK"))
                    usable = bool(record.get("usable_for_rainfall", st_info.get("usable_for_rainfall", True)))
                    val = record.get("rainfall_daily_mm")
                    daily_data[(st_id, rec_date)] = {
                        "raw_rainfall_daily_mm": float(val) if val is not None else None,
                        "rainfall_daily_mm": float(val) if (val is not None and usable) else None,
                        "rain_quality_flag": qflag,
                        "usable_for_rainfall": usable,
                    }

        # Hourly rainfall from trusted/rain/ (sliding window deduplicated via latest-batch-wins)
        hourly_dedup: Dict[Tuple[int, str, int], Tuple[str, float]] = {}
        for d in dates:
            t_dir = self.trusted_dir / "rain" / f"dt={d}"
            if not t_dir.exists():
                continue
            for jsonl_path in sorted(t_dir.glob("*.jsonl")):
                for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    if not rec.get("usable_for_rainfall", True):
                        continue
                    st_id = rec.get("api_station_id")
                    r_dt = rec.get("rain_datetime", "")
                    r_mm = rec.get("rain_mm")
                    if st_id is None or len(r_dt) < 13 or r_mm is None:
                        continue
                    rec_date = r_dt[:10]
                    if rec_date not in dates:
                        continue
                    rec_hour = int(r_dt[11:13])
                    sort_key = f"{rec.get('ingested_at_utc', '')}_{rec.get('batch_id', '')}"
                    key = (st_id, rec_date, rec_hour)
                    prev = hourly_dedup.get(key)
                    if prev is None or sort_key >= prev[0]:
                        hourly_dedup[key] = (sort_key, float(r_mm))

        hourly_rain = {k: v[1] for k, v in hourly_dedup.items()}
        return daily_data, hourly_rain

    def load_traffy_reports(self, dates: List[str]) -> List[Dict[str, Any]]:
        """
        Loads normalized Traffy reports for the requested dates.
        Prefers validated JSONL in trusted/traffy/dt=YYYY-MM-DD/traffy_YYYY-MM-DD.jsonl,
        falling back to incoming/traffy/dt=YYYY-MM-DD/traffy_YYYY-MM-DD.json.
        Deduplicates by ticket_id.
        """
        dedup_reports: Dict[str, Dict[str, Any]] = {}
        for d in dates:
            trusted_file = self.trusted_dir / "traffy" / f"dt={d}" / f"traffy_{d}.jsonl"
            incoming_file = self.incoming_traffy_dir / f"dt={d}" / f"traffy_{d}.json"

            if trusted_file.exists():
                for line in trusted_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    r = json.loads(line)
                    tid = r.get("ticket_id")
                    if tid:
                        dedup_reports[tid] = r
            elif incoming_file.exists():
                with open(incoming_file, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                for r in doc.get("data", []):
                    tid = r.get("ticket_id")
                    if tid:
                        dedup_reports[tid] = r
        return list(dedup_reports.values())

    def build_khwaeng_hourly(self, dates: List[str]) -> List[Dict[str, Any]]:
        """
        Assembles the curated dataset: 1 row per khwaeng per hour.
        Denominator: All reports in the khwaeng-hour.
        Numerator: is_flood_report IS True.
        """
        wl_hourly = self.load_waterlevel_hourly(dates)
        rain_daily, rain_hourly = self.load_rainfall(dates)
        reports = self.load_traffy_reports(dates)

        # 1. Aggregate Traffy reports by (date, hour, khwaeng, khet)
        traffy_agg: Dict[Tuple[str, int, str, str], Dict[str, Any]] = defaultdict(lambda: {
            "total_reports": 0,
            "flood_reports": 0,
            "uncertain_reports": 0,
            "non_flood_reports": 0,
            "lats": [],
            "lons": [],
        })

        for r in reports:
            dt_bkk = r.get("report_date_bkk")
            if dt_bkk not in dates:
                continue
            hr_bkk = int(r.get("report_hour_bkk", 0))
            addr = r.get("address")
            khwaeng, khet = extract_khwaeng_khet(addr)

            lat = r.get("latitude")
            lon = r.get("longitude")

            key = (dt_bkk, hr_bkk, khwaeng, khet)
            item = traffy_agg[key]
            item["total_reports"] += 1
            if lat is not None and lon is not None:
                item["lats"].append(float(lat))
                item["lons"].append(float(lon))

            is_fl = r.get("is_flood_report")
            cls_name = r.get("flood_classification")

            if is_fl is True:
                item["flood_reports"] += 1
            elif cls_name == "uncertain" or is_fl is None:
                item["uncertain_reports"] += 1
            else:
                item["non_flood_reports"] += 1

        # 2. Compute representative coordinates for each (khwaeng, khet)
        khwaeng_centers: Dict[Tuple[str, str], Tuple[float, float]] = {}
        for (_dt_bkk, _hr_bkk, khwaeng, khet), item in traffy_agg.items():
            if (khwaeng, khet) not in khwaeng_centers and item["lats"] and item["lons"]:
                khwaeng_centers[(khwaeng, khet)] = (
                    sum(item["lats"]) / len(item["lats"]),
                    sum(item["lons"]) / len(item["lons"]),
                )

        # 3. Assemble curated rows
        rows: List[Dict[str, Any]] = []
        for (dt_bkk, hr_bkk, khwaeng, khet), item in traffy_agg.items():
            center_lat, center_lon = khwaeng_centers.get(
                (khwaeng, khet),
                (13.7563, 100.5018),
            )
            water_st_id, dist_water_km = self.find_nearest_water_station(center_lat, center_lon, usable_only=False)
            rain_st_id, dist_rain_km = self.find_nearest_rain_station(center_lat, center_lon, usable_only=True)

            water_info = self.water_map.get(water_st_id, {})
            rain_info = self.rain_map.get(rain_st_id, {})

            wl_metrics = wl_hourly.get((water_st_id, dt_bkk, hr_bkk), {})
            r_daily_info = rain_daily.get((rain_st_id, dt_bkk), {})
            r_hourly_val = rain_hourly.get((rain_st_id, dt_bkk, hr_bkk))

            usable_fb = wl_metrics.get(
                "usable_for_freeboard",
                water_info.get("usable_for_freeboard", True)
            )
            water_qflag = wl_metrics.get(
                "water_quality_flag",
                water_info.get("quality_flag", "NORMAL")
            )
            freeboard = wl_metrics.get("min_freeboard_m")
            raw_freeboard = wl_metrics.get("raw_freeboard_m")

            if not usable_fb:
                risk_tier = "SUSPECT_DATUM"
            elif freeboard is not None:
                if freeboard <= 0.0:
                    risk_tier = "CRITICAL_OVERFLOW"
                elif freeboard <= 0.5:
                    risk_tier = "HIGH_ALERT"
                elif freeboard <= 1.0:
                    risk_tier = "WATCH"
                else:
                    risk_tier = "NORMAL"
            else:
                risk_tier = "NO_TELEMETRY"

            total_rep = item["total_reports"]
            flood_rep = item["flood_reports"]
            uncertain_rep = item["uncertain_reports"]
            flood_ratio = round(flood_rep / total_rep, 4) if total_rep > 0 else 0.0

            row = {
                "dt": dt_bkk,
                "hour": hr_bkk,
                "khwaeng": khwaeng,
                "khet": khet,
                "center_latitude": round(center_lat, 5),
                "center_longitude": round(center_lon, 5),
                "paired_water_station_id": water_st_id,
                "water_station_name": water_info.get("station_name_th", ""),
                "canal_name": water_info.get("river_name_th", ""),
                "water_station_dist_km": round(dist_water_km, 2),
                "avg_waterlevel_msl": wl_metrics.get("avg_waterlevel_msl"),
                "max_waterlevel_msl": wl_metrics.get("max_waterlevel_msl"),
                "min_bank_msl": wl_metrics.get("min_bank_msl", water_info.get("min_bank_msl")),
                "raw_freeboard_m": raw_freeboard,
                "min_freeboard_m": freeboard,
                "water_quality_flag": water_qflag,
                "usable_for_freeboard": bool(usable_fb),
                "is_overflow": wl_metrics.get("is_overflow", False),
                "risk_tier": risk_tier,
                "paired_rain_station_id": rain_st_id,
                "rain_station_name": rain_info.get("station_name_th", ""),
                "rain_station_dist_km": round(dist_rain_km, 2),
                "rain_mm": r_hourly_val,
                "rainfall_daily_mm": r_daily_info.get("rainfall_daily_mm"),
                "rain_quality_flag": r_daily_info.get("rain_quality_flag", rain_info.get("quality_flag", "OK")),
                "usable_for_rainfall": bool(r_daily_info.get("usable_for_rainfall", rain_info.get("usable_for_rainfall", True))),
                "total_reports": total_rep,
                "flood_reports": flood_rep,
                "uncertain_reports": uncertain_rep,
                "non_flood_reports": item["non_flood_reports"],
                "flood_report_ratio": flood_ratio,
            }
            rows.append(row)

        rows.sort(key=lambda x: (x["dt"], x["hour"], x["khet"], x["khwaeng"]))
        return rows

    def save_curated(self, rows: List[Dict[str, Any]], format_type: str = "json") -> Path:
        """
        Saves curated rows to disk in JSON, CSV, or Hive-partitioned JSONL
        (curated/khwaeng_hourly/dt=YYYY-MM-DD/khwaeng_hourly.jsonl for Athena).
        """
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if format_type == "csv":
            out_file = self.out_dir / "khwaeng_hourly.csv"
            if rows:
                with open(out_file, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
            return out_file
        elif format_type == "jsonl":
            by_dt: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for r in rows:
                by_dt[r["dt"]].append(r)
            last_path = self.out_dir
            for dt_val, dt_rows in sorted(by_dt.items()):
                part_dir = self.out_dir / f"dt={dt_val}"
                part_dir.mkdir(parents=True, exist_ok=True)
                last_path = part_dir / "khwaeng_hourly.jsonl"
                text = "".join(
                    json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n"
                    for r in dt_rows
                )
                last_path.write_text(text, encoding="utf-8")
            return self.out_dir
        else:
            out_file = self.out_dir / "khwaeng_hourly.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
            return out_file
