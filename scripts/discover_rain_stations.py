import json
import urllib.request
from pathlib import Path

headers = {"User-Agent": "Mozilla/5.0"}
rain_results = []

# Probe station IDs 1 to 50 to discover valid Bangkok rain stations
for st_id in range(1, 51):
    url = f"https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_monthly_graph?station_id={st_id}&year=2026&month=10"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            d = json.loads(resp.read().decode())
            data = d.get("data", [])
            if isinstance(data, list) and len(data) > 0:
                valid_vals = [x.get("rainfall_value") for x in data if x.get("rainfall_value") is not None]
                if valid_vals:
                    rain_results.append({
                        "api_station_id": st_id,
                        "record_count": len(data),
                        "max_rain": max(valid_vals),
                        "sample_date": data[0].get("rainfall_datetime"),
                    })
                    print(f"Station ID {st_id}: VALID RAIN STATION ({len(data)} days, max={max(valid_vals)} mm)")
    except Exception:
        continue

print(f"Total discovered valid rain stations in 1-50: {len(rain_results)}")
out_file = Path(__file__).resolve().parent.parent / "reference" / "discovered_rain_stations.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(rain_results, f, indent=2)
