"""
ThaiWater API Discovery and Probing Script
Owner: Member 1 (PurinatWanthanathanya)

Purpose:
1. Probes real endpoints on ThaiWater public API (api-v3.thaiwater.net).
2. Discovers real station IDs and parameters for Bangkok canal waterlevel and rainfall stations.
3. Saves sample real API payloads to reference/sample_api_responses/ for verification.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

SAMPLE_DIR = PROJECT_ROOT / "reference" / "sample_api_responses"
SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "th,en-US;q=0.9,en;q=0.8",
}

BASE_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public"


def fetch_url(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content = response.read().decode("utf-8")
        return json.loads(content)


def probe_waterlevel_load():
    print("\n" + "=" * 60)
    print(">>> 1. Probing /waterlevel_load...")
    print("=" * 60)
    url = f"{BASE_URL}/waterlevel_load"
    try:
        data = fetch_url(url)
        with open(SAMPLE_DIR / "waterlevel_load_sample.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f" Saved full sample to {SAMPLE_DIR / 'waterlevel_load_sample.json'}")

        items = data.get("data", []) if isinstance(data, dict) else data
        print(f"Total stations in ThaiWater network: {len(items)}")

        # Search for Bangkok stations and bank heights
        bkk_stations = []
        target_keywords = ["เจ้าพระยา", "มหาสวัสดิ์", "ลำปลาทิว", "หกวา", "บางบัว", "แสนแสบ", "ภาษีเจริญ"]
        matched_targets = []

        for item in items:
            st = item.get("station", {})
            st_name = st.get("tele_station_name", {})
            name_th = st_name.get("th", "") if isinstance(st_name, dict) else str(st_name)
            geocode = item.get("geocode", {})
            prov = geocode.get("province_name", {}).get("th", "") if isinstance(geocode, dict) else ""

            st_id = item.get("station_id") or st.get("id")
            old_code = st.get("tele_station_oldcode")
            min_bank = item.get("min_bank") or item.get("bank_height") or item.get("ground_level")
            diff_bank = item.get("diff_wl_bank")
            wl_msl = item.get("waterlevel_msl") or item.get("waterlevel_m")
            dt_str = item.get("waterlevel_datetime")

            if "กรุงเทพ" in prov or any(kw in name_th for kw in target_keywords):
                station_summary = {
                    "api_station_id": st_id,
                    "tele_station_oldcode": old_code,
                    "name_th": name_th,
                    "province": prov,
                    "latitude": st.get("tele_station_lat"),
                    "longitude": st.get("tele_station_long"),
                    "waterlevel_msl": wl_msl,
                    "min_bank": min_bank,
                    "diff_wl_bank": diff_bank,
                    "datetime": dt_str,
                }
                bkk_stations.append(station_summary)

                for kw in target_keywords:
                    if kw in name_th:
                        matched_targets.append((kw, station_summary))

        print(f"Bangkok & surrounding canal stations found: {len(bkk_stations)}")
        print("\n--- Target Canal Stations with Bank Height ---")
        for kw, s in matched_targets[:15]:
            print(f"[{kw}] ID={s['api_station_id']} | Code={s['tele_station_oldcode']} | Name={s['name_th']} | Bank={s['min_bank']} | Diff={s['diff_wl_bank']}")

        with open(SAMPLE_DIR / "bkk_waterlevel_stations.json", "w", encoding="utf-8") as f:
            json.dump(bkk_stations, f, ensure_ascii=False, indent=2)

        return bkk_stations

    except Exception as e:
        print(f"[!] Failed to probe /waterlevel_load: {e}")
        return []


def probe_waterlevel_graph(sample_station_id: int):
    print("\n" + "=" * 60)
    print(f">>> 2. Probing /waterlevel_graph for station_id={sample_station_id}...")
    print("=" * 60)

    today = datetime.now()
    yesterday = today - timedelta(days=1)
    d_str = yesterday.strftime("%Y-%m-%d")

    # Try different query parameter variants
    test_params = [
        {"station_id": sample_station_id, "start_date": d_str, "end_date": today.strftime("%Y-%m-%d")},
        {"station_id": sample_station_id, "date": d_str},
        {"station_id": sample_station_id},
    ]

    for params in test_params:
        query_str = urllib.parse.urlencode(params)
        url = f"{BASE_URL}/waterlevel_graph?{query_str}"
        try:
            data = fetch_url(url)
            sample_file = SAMPLE_DIR / f"waterlevel_graph_{params.get('station_id')}.json"
            with open(sample_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f" Success with params {params}! Response saved to {sample_file}")
            
            records = data.get("data", []) if isinstance(data, dict) else data
            print(f" Records returned: {len(records)}")
            if records:
                print(" Sample record:", records[0])
            break
        except Exception as e:
            print(f" Attempt with {params} failed: {e}")


def probe_rain_endpoints():
    print("\n" + "=" * 60)
    print(">>> 3. Probing Rainfall Endpoints (/rain_24h_graph & /rain_monthly_graph)...")
    print("=" * 60)

    # 1. rain_24h_graph
    url_24h = f"{BASE_URL}/rain_24h_graph"
    try:
        data_24h = fetch_url(url_24h)
        with open(SAMPLE_DIR / "rain_24h_sample.json", "w", encoding="utf-8") as f:
            json.dump(data_24h, f, ensure_ascii=False, indent=2)
        items_24h = data_24h.get("data", []) if isinstance(data_24h, dict) else data_24h
        print(f" rain_24h_graph success! Total stations: {len(items_24h)}")
        if items_24h:
            print(" Sample rain record:", items_24h[0])
    except Exception as e:
        print(f"[!] rain_24h_graph failed: {e}")

    # 2. rain_monthly_graph
    # Try with year and month
    now = datetime.now()
    url_monthly = f"{BASE_URL}/rain_monthly_graph?station_id=1&year={now.year}&month={now.month}"
    try:
        data_m = fetch_url(url_monthly)
        with open(SAMPLE_DIR / "rain_monthly_sample.json", "w", encoding="utf-8") as f:
            json.dump(data_m, f, ensure_ascii=False, indent=2)
        print(f" rain_monthly_graph success! Saved to {SAMPLE_DIR / 'rain_monthly_sample.json'}")
    except Exception as e:
        print(f"[!] rain_monthly_graph failed: {e}")


if __name__ == "__main__":
    bkk_st = probe_waterlevel_load()
    sample_id = bkk_st[0]["api_station_id"] if bkk_st else 1
    probe_waterlevel_graph(sample_id)
    probe_rain_endpoints()
    print("\n>>> Probing complete. Check output in reference/sample_api_responses/")
