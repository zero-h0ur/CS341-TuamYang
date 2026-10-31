"""
Mock ThaiWater Client for Offline Unit Testing Only
Owner: Member 1 (PurinatWanthanathanya)

Strict Rule: This mock is ONLY imported inside tests/ and must NEVER be used in production pipelines.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from src.thaiwater_client import ThaiWaterClient

BKK_TZ = timezone(timedelta(hours=7))


class MockThaiWaterClient(ThaiWaterClient):
    """Provides synthetic telemetry solely for unit tests."""

    def fetch_current_waterlevel(self) -> Dict[str, Any]:
        records = []
        now_bkk = datetime.now(BKK_TZ)
        mock_levels = {
            1: 1.45,       # Bang Bua
            3: 1.25,       # Lam Pla Thio
            4: 1.80,       # Saphan Krungthep
            5: 1.65,       # Maha Sawat
            8: 1.70,       # Hok Wa
            10: -5.42,     # Phasi Charoen (Known outlier sensor reading)
            2599: 1.95,    # Sam Sen
            852087: 1.50,  # Saen Saep
        }
        for st in self._water_stations:
            st_id = st["api_station_id"]
            wl = mock_levels.get(st_id, 1.5)
            min_bank = st.get("min_bank_msl", 2.0)
            diff = round(min_bank - wl, 3)
            records.append({
                "api_station_id": st_id,
                "tele_station_oldcode": st.get("tele_station_oldcode", ""),
                "station_name_th": st.get("station_name_th", ""),
                "waterlevel_datetime": now_bkk.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp_iso": now_bkk.isoformat(),
                "timestamp_epoch": int(now_bkk.timestamp()),
                "waterlevel_msl": wl,
                "min_bank_msl": min_bank,
                "diff_wl_bank_m": diff,
            })

        return {
            "source": "MOCK_WATERLEVEL_LOAD",
            "status": "success",
            "is_mock": True,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "records": records,
        }

    def fetch_historical_waterlevel(self, api_station_id: int, start_date: str, end_date: str) -> Dict[str, Any]:
        records = []
        meta = next((s for s in self._water_stations if s["api_station_id"] == api_station_id), None)
        min_bank = meta.get("min_bank_msl", 2.0) if meta else 2.0

        for h in range(24):
            for m in (0, 30):
                wl = round(min_bank - 0.5 + (h * 0.01), 2)
                records.append({
                    "api_station_id": api_station_id,
                    "datetime": f"{start_date} {h:02d}:{m:02d}",
                    "waterlevel_msl": wl,
                    "min_bank_msl": min_bank,
                    "diff_wl_bank_m": round(min_bank - wl, 3),
                })

        return {
            "source": "MOCK_WATERLEVEL_GRAPH",
            "status": "success",
            "is_mock": True,
            "api_station_id": api_station_id,
            "record_count": len(records),
            "records": records,
        }

    def fetch_monthly_rain(self, api_station_id: int, year: int, month: int) -> Dict[str, Any]:
        records = []
        for day in range(1, 31):
            rain_val = 45.0 if (month == 9 and day in (24, 25, 26)) else 2.5
            records.append({
                "api_station_id": api_station_id,
                "date": f"{year:04d}-{month:02d}-{day:02d}",
                "rainfall_daily_mm": rain_val,
            })
        return {
            "source": "MOCK_RAIN_MONTHLY",
            "status": "success",
            "is_mock": True,
            "api_station_id": api_station_id,
            "year": year,
            "month": month,
            "record_count": len(records),
            "records": records,
        }

    def fetch_24h_rain_station(self, api_station_id: int) -> Dict[str, Any]:
        meta = next((s for s in self._rain_stations if s["api_station_id"] == api_station_id), None)
        return {
            "api_station_id": api_station_id,
            "station_name_th": meta.get("station_name_th", "") if meta else "",
            "rainfall_datetime": datetime.now(BKK_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "rain_24h_mm": 18.5,
            "rain_latest_mm": 2.0,
            "data_points": 48,
        }
