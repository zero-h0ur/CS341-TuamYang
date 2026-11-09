import json
from pathlib import Path

path = Path("reference/sample_api_responses/waterlevel_graph_1.json")
if not path.exists():
    path = Path(__file__).resolve().parent.parent / "reference" / "sample_api_responses" / "waterlevel_graph_1.json"

with open(path, encoding="utf-8") as f:
    d = json.load(f)

graph_data = d.get("data")
print("type(graph_data):", type(graph_data))
if isinstance(graph_data, dict):
    print("keys in data:", list(graph_data.keys()))
    for k, v in graph_data.items():
        if isinstance(v, list):
            print(f"Key '{k}': list of {len(v)} items")
            if v:
                print("  Sample item:", v[0])
        elif isinstance(v, dict):
            print(f"Key '{k}': dict keys={list(v.keys())}")
        else:
            print(f"Key '{k}': {v}")
elif isinstance(graph_data, list):
    print("len:", len(graph_data))
    if graph_data:
        print("Sample item:", graph_data[0])
