"""
Check Bank and Metadata for 8 Bangkok Canal Water Stations
Owner: Member 1 (PurinatWanthanathanya)

Validates min_bank vs waterlevel + diff_wl_bank from ThaiWater live API.
Generates reference/stations_water.new.json with corrected min_bank_msl.
"""

import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.config import WATERLEVEL_LOAD_ENDPOINT, WATER_STATIONS_FILE
from src.thaiwater_client import ThaiWaterClient


def f(x):
    return float(x) if x not in (None, "") else None


def main():
    client = ThaiWaterClient()
    d = client._request_json(WATERLEVEL_LOAD_ENDPOINT)
    live = {it["station"]["id"]: it for it in d["waterlevel_data"]["data"]}
    with open(WATER_STATIONS_FILE, "r", encoding="utf-8") as f_in:
        ref = json.load(f_in)

    out = []
    print("=" * 95)
    print(f"{'ID':<7} {'Code':<8} {'Type':<10} {'Agency':<8} {'WL':<7} {'MinBank':<8} {'Left':<7} {'Right':<7} {'Ground':<8} {'APIDiff':<8} {'Check(~0)':<10}")
    print("=" * 95)

    for s in ref:
        it = live.get(s["api_station_id"])
        if not it:
            print(f"{s['api_station_id']} NOT IN LIVE DATA")
            out.append(s)
            continue

        st = it.get("station") or {}
        wl = f(it.get("waterlevel_msl"))
        mb = f(st.get("min_bank"))
        diff = f(it.get("diff_wl_bank"))
        chk = round(mb - wl - diff, 3) if None not in (wl, mb, diff) else None
        agency = it.get("agency", {}).get("agency_shortname", {}).get("en", "N/A")
        st_type = it.get("station_type", "N/A")

        print(f"{s['api_station_id']:<7} {st.get('tele_station_oldcode', ''):<8} {st_type:<10} {agency:<8} "
              f"{str(wl):<7} {str(mb):<8} {str(st.get('left_bank')):<7} {str(st.get('right_bank')):<7} "
              f"{str(st.get('ground_level')):<8} {str(diff):<8} {str(chk):<10}")

        n = dict(s)
        n["min_bank_msl"] = mb
        n["left_bank_msl"] = f(st.get("left_bank"))
        n["right_bank_msl"] = f(st.get("right_bank"))
        n["ground_level_msl"] = f(st.get("ground_level"))
        for k in ("latest_waterlevel_datetime", "latest_waterlevel_msl", "latest_diff_wl_bank"):
            n.pop(k, None)
        out.append(n)

    new_ref_path = Path(WATER_STATIONS_FILE).parent / "stations_water.new.json"
    with open(new_ref_path, "w", encoding="utf-8") as f_out:
        json.dump(out, f_out, ensure_ascii=False, indent=2)

    print("=" * 95)
    print(f"Wrote updated reference to: {new_ref_path}")


if __name__ == "__main__":
    main()
