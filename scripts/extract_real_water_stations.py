import json
from pathlib import Path

sample_path = Path(__file__).resolve().parent.parent / "reference" / "sample_api_responses" / "waterlevel_load_sample.json"
with open(sample_path, encoding="utf-8") as f:
    data = json.load(f)

items = data.get("waterlevel_data", {}).get("data", [])

# Target 8 canal stations in Bangkok as identified in Proposal
# เจ้าพระยา 2 สถานี, คลองมหาสวัสดิ์, ลำปลาทิว, หกวา, บางบัว, แสนแสบ, ภาษีเจริญ
target_ids = [2599, 4, 5, 3, 8, 1, 852087, 10]

real_stations = []
for it in items:
    st = it.get("station") or {}
    st_id = st.get("id")
    if st_id in target_ids:
        st_name = st.get("tele_station_name") or {}
        name_th = st_name.get("th", "") if isinstance(st_name, dict) else str(st_name)
        name_en = st_name.get("en", "") if isinstance(st_name, dict) else str(st_name)
        geocode = it.get("geocode") or {}
        prov_th = geocode.get("province_name", {}).get("th", "")
        amp_th = geocode.get("amphoe_name", {}).get("th", "")
        tmb_th = geocode.get("tumbon_name", {}).get("th", "")

        river = it.get("river_name") or {}
        river_th = river.get("th", "") if isinstance(river, dict) else str(river)

        min_bank = it.get("min_bank") or st.get("bank_height") or st.get("ground_level")
        ground = st.get("ground_level")

        real_stations.append({
            "api_station_id": st_id,
            "tele_station_oldcode": st.get("tele_station_oldcode"),
            "station_name_th": name_th,
            "station_name_en": name_en,
            "river_name_th": river_th,
            "province_th": prov_th,
            "amphoe_th": amp_th,
            "tumbon_th": tmb_th,
            "latitude": float(st.get("tele_station_lat")) if st.get("tele_station_lat") else None,
            "longitude": float(st.get("tele_station_long")) if st.get("tele_station_long") else None,
            "min_bank_msl": float(min_bank) if min_bank is not None else None,
            "ground_level_msl": float(ground) if ground is not None else None,
            "latest_waterlevel_datetime": it.get("waterlevel_datetime"),
            "latest_waterlevel_msl": float(it.get("waterlevel_msl")) if it.get("waterlevel_msl") is not None else None,
            "latest_diff_wl_bank": float(it.get("diff_wl_bank")) if it.get("diff_wl_bank") is not None else None,
        })

print(f"Matched {len(real_stations)} stations:")
for s in real_stations:
    print(f"ID={s['api_station_id']} | Code={s['tele_station_oldcode']} | Name={s['station_name_th']} | River={s['river_name_th']} | Bank={s['min_bank_msl']} | Lat={s['latitude']}, Lon={s['longitude']}")

# Sort by api_station_id
real_stations.sort(key=lambda x: x["api_station_id"])

# Save updated reference/stations_water.json
ref_file = Path(__file__).resolve().parent.parent / "reference" / "stations_water.json"
with open(ref_file, "w", encoding="utf-8") as f:
    json.dump(real_stations, f, ensure_ascii=False, indent=2)
print(f"Updated {ref_file} with real ThaiWater station metadata!")
