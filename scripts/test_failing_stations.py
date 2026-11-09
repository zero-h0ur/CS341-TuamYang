import json
import urllib.request

headers = {"User-Agent": "Mozilla/5.0"}
for st_id in [2599, 852087]:
    for url in [
        f"https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_graph?station_id={st_id}",
        f"https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_graph?station_id={st_id}&date=2026-10-30",
        f"https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_graph?station_id={st_id}&start_date=2026-10-25&end_date=2026-10-31",
    ]:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                d = json.loads(resp.read().decode())
                print(f"st={st_id} url={url} -> SUCCESS, result={d.get('result')}")
        except Exception as e:
            print(f"st={st_id} url={url} -> FAIL: {e}")
