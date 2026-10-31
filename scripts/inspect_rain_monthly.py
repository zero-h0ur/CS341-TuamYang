import json
from pathlib import Path

path = Path(__file__).resolve().parent.parent / "reference" / "sample_api_responses" / "rain_monthly_sample.json"
with open(path, encoding="utf-8") as f:
    d = json.load(f)

print("rain_monthly keys:", list(d.keys()))
rain_data = d.get("data")
print("type(rain_data):", type(rain_data))
if isinstance(rain_data, dict):
    print("keys in data:", list(rain_data.keys()))
    for k, v in rain_data.items():
        if isinstance(v, list):
            print(f"Key '{k}': list of {len(v)} items")
            if v:
                print("  Sample item:", v[0])
elif isinstance(rain_data, list):
    print("len:", len(rain_data))
    if rain_data:
        print("Sample item:", rain_data[0])
