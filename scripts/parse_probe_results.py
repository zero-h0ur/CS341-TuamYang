import json
from pathlib import Path

sample_path = Path(__file__).resolve().parent.parent / "reference" / "sample_api_responses" / "waterlevel_load_sample.json"
output_path = Path(__file__).resolve().parent.parent / "reference" / "parsed_real_stations.json"
if not sample_path.exists():
    print("Sample file not found")
    exit(1)

with open(sample_path, encoding="utf-8") as f:
    data = json.load(f)

items = data.get("waterlevel_data", {}).get("data", [])
keywords = ["เจ้าพระยา", "มหาสวัสดิ์", "ลำปลาทิว", "หกวา", "บางบัว", "แสนแสบ", "ภาษีเจริญ"]
candidates = []

for it in items:
    geocode = it.get("geocode") or {}
    prov_name = geocode.get("province_name") or {}
    prov_th = prov_name.get("th", "") if isinstance(prov_name, dict) else str(prov_name)
    st = it.get("station") or {}
    st_name = st.get("tele_station_name") or {}
    name_th = st_name.get("th", "") if isinstance(st_name, dict) else str(st_name)
    river = it.get("river_name") or {}
    river_th = river.get("th", "") if isinstance(river, dict) else str(river)

    diff = it.get("diff_wl_bank")
    ground = st.get("ground_level")
    bank = st.get("bank_height") or st.get("min_bank")
    lat = st.get("tele_station_lat")
    lon = st.get("tele_station_long")

    is_bkk = "กรุงเทพ" in prov_th
    is_keyword = any(k in name_th or k in river_th for k in keywords)

    if diff is not None or is_bkk or is_keyword:
        candidates.append({
            "tele_station_id": st.get("id"),
            "tele_station_oldcode": st.get("tele_station_oldcode"),
            "name_th": name_th,
            "river_th": river_th,
            "province": prov_th,
            "waterlevel_datetime": it.get("waterlevel_datetime"),
            "waterlevel_msl": it.get("waterlevel_msl"),
            "diff_wl_bank": diff,
            "bank_height": bank,
            "ground_level": ground,
            "lat": lat,
            "lon": lon,
        })

print(f"Total candidates: {len(candidates)}")
print("--- STATIONS WITH BANK HEIGHT / DIFF_WL_BANK ---")
with_bank = [c for c in candidates if c["diff_wl_bank"] is not None or c["bank_height"] is not None]
print(f"Stations with bank info: {len(with_bank)}")
for c in with_bank:
    print(f"ID={c['tele_station_id']} | Code={c['tele_station_oldcode']} | Name={c['name_th']} | River={c['river_th']} | Prov={c['province']} | Diff={c['diff_wl_bank']} | Bank={c['bank_height']}")

print("\n--- BANGKOK STATIONS ---")
bkk_only = [c for c in candidates if "กรุงเทพ" in c["province"]]
print(f"Bangkok stations: {len(bkk_only)}")
for c in bkk_only[:20]:
    print(f"ID={c['tele_station_id']} | Code={c['tele_station_oldcode']} | Name={c['name_th']} | River={c['river_th']} | WL={c['waterlevel_msl']} | Diff={c['diff_wl_bank']}")

# Save parsed candidates to file
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(candidates, f, ensure_ascii=False, indent=2)
print(f"Saved to {output_path}")
