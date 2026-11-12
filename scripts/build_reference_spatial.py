"""
Reproducible Reference & Spatial Join Builder (Issue #3)
Owner: นายภูรินทร์ แก้วพ่วงเสก (PhurinKaewpuangsek)

Tasks implemented:
1. Downloads Bangkok khwaeng (adm3 subdistrict) boundaries from the Royal Thai Survey Department
   (RTSD) via OCHA Humanitarian Data Exchange (HDX COD-AB Thailand), converts geometries to WKT,
   and stores `reference/khwaeng_boundaries.csv`.
2. Selects wards within a configurable radius (default 1.0 km) of the 8 water-level stations
   in `reference/stations_water.json`, and evaluates point-in-polygon (`ST_Contains`) for the
   stations themselves.
3. Builds the ward <-> water-station and nearest usable rain-station (`reference/stations_rain.json`)
   mapping table at `reference/khwaeng_station_mapping.csv` (aligned with PR #13 / Issue #2's
   `sql/create_khwaeng_hourly.sql` schema as well as Issue #3's specification).
4. Evaluates spatial join (`ST_Contains`) coverage on the case-study Traffy coordinates and writes
   `evidence/reference_spatial/spatial_quality_report.json`.
"""

import argparse
import csv
import json
import math
import re
import struct
import sys
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

HDX_PACKAGE_API_URL = "https://data.humdata.org/api/3/action/package_show?id=cod-ab-tha"
HDX_DATASET_PAGE_URL = "https://data.humdata.org/dataset/cod-ab-tha"
HDX_GEOJSON_ZIP_URL = (
    "https://data.humdata.org/dataset/d24bdc45-eb4c-4e3d-8b16-44db02667c27/"
    "resource/89a09f13-7b83-458a-9531-4f2418613065/download/tha_admin_boundaries.geojson.zip"
)
HDX_LICENSE = "Creative Commons Attribution for Intergovernmental Organisations (CC BY-IGO)"
HDX_SOURCE_ORG = "Royal Thai Survey Department (RTSD) via OCHA Regional Office for Asia and the Pacific (HDX)"

DEFAULT_RADIUS_KM = 1.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two WGS84 coordinates in kilometers."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def _format_ring_wkt(ring: List[List[float]]) -> str:
    coords_str = ", ".join(f"{pt[0]} {pt[1]}" for pt in ring)
    return f"({coords_str})"


def geojson_geometry_to_wkt(geometry: Dict[str, Any]) -> str:
    """Convert a GeoJSON Polygon or MultiPolygon geometry dictionary to OGC WKT string."""
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "Polygon":
        rings_str = ", ".join(_format_ring_wkt(ring) for ring in coords)
        return f"POLYGON ({rings_str})"
    elif gtype == "MultiPolygon":
        polys_str = ", ".join(
            "(" + ", ".join(_format_ring_wkt(ring) for ring in poly) + ")"
            for poly in coords
        )
        return f"MULTIPOLYGON ({polys_str})"
    else:
        raise ValueError(f"Unsupported GeoJSON geometry type for WKT conversion: {gtype}")


def parse_wkt_polygons(wkt: str) -> List[List[List[Tuple[float, float]]]]:
    """
    Parse a WKT POLYGON or MULTIPOLYGON string into a list of polygons,
    where each polygon is a list of rings, and each ring is a list of (lon, lat) tuples.
    Raises ValueError if WKT is malformed.
    """
    text = (wkt or "").strip()
    if text.upper().startswith("POLYGON"):
        body = text[len("POLYGON"):].strip()
        if not (body.startswith("((") and body.endswith("))")):
            raise ValueError("Malformed POLYGON WKT")
        inner = body[1:-1].strip()
        rings = _parse_wkt_rings(inner)
        return [rings]
    elif text.upper().startswith("MULTIPOLYGON"):
        body = text[len("MULTIPOLYGON"):].strip()
        if not (body.startswith("(((") and body.endswith(")))")):
            raise ValueError("Malformed MULTIPOLYGON WKT")
        inner = body[1:-1].strip()
        # Split top-level polygons separated by )), ((
        poly_chunks = re.split(r"\)\s*\)\s*,\s*\(\s*\(", inner)
        polygons: List[List[List[Tuple[float, float]]]] = []
        for idx, chunk in enumerate(poly_chunks):
            c = chunk.strip()
            if not c.startswith("(("):
                c = "((" + c.lstrip("(")
            if not c.endswith("))"):
                c = c.rstrip(")") + "))"
            polygons.append(_parse_wkt_rings(c[1:-1].strip()))
        return polygons
    else:
        raise ValueError(f"WKT must start with POLYGON or MULTIPOLYGON: {text[:40]}")


def _parse_wkt_rings(rings_body: str) -> List[List[Tuple[float, float]]]:
    ring_matches = re.findall(r"\(([^()]+)\)", rings_body)
    if not ring_matches:
        raise ValueError("No coordinate rings found in WKT")
    rings: List[List[Tuple[float, float]]] = []
    for rm in ring_matches:
        pts: List[Tuple[float, float]] = []
        for pair in rm.split(","):
            parts = pair.strip().split()
            if len(parts) < 2:
                raise ValueError(f"Invalid coordinate pair in WKT: {pair!r}")
            lon = float(parts[0])
            lat = float(parts[1])
            pts.append((lon, lat))
        if len(pts) < 4:
            raise ValueError("WKT linear ring must have at least 4 points")
        rings.append(pts)
    return rings


def _point_in_ring(lon: float, lat: float, ring: List[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-ring test (inclusive of boundary segments)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        # Check exact vertex coincidence
        if abs(lon - xi) < 1e-12 and abs(lat - yi) < 1e-12:
            return True
        intersect = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-15) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def _point_in_single_polygon(lon: float, lat: float, rings: List[List[Tuple[float, float]]]) -> bool:
    if not rings:
        return False
    if not _point_in_ring(lon, lat, rings[0]):
        return False
    for hole in rings[1:]:
        if _point_in_ring(lon, lat, hole):
            return False
    return True


def point_in_wkt(lat: float, lon: float, wkt: str) -> bool:
    """Return True if (lat, lon) lies inside the WKT POLYGON / MULTIPOLYGON."""
    polygons = parse_wkt_polygons(wkt)
    return any(_point_in_single_polygon(lon, lat, poly) for poly in polygons)


def _point_to_segment_dist_km(
    px: float, py: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    """Minimum distance in km from point (lon=px, lat=py) to segment (x1,y1)-(x2,y2)."""
    cos_lat = math.cos(math.radians(py))
    dx = (x2 - x1) * cos_lat
    dy = y2 - y1
    if dx == 0.0 and dy == 0.0:
        return haversine_km(py, px, y1, x1)
    t = ((px - x1) * cos_lat * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_lon = x1 + t * (x2 - x1)
    proj_lat = y1 + t * (y2 - y1)
    return haversine_km(py, px, proj_lat, proj_lon)


def _point_to_polygons_distance_km(
    lat: float, lon: float, polygons: List[List[List[Tuple[float, float]]]]
) -> float:
    if any(_point_in_single_polygon(lon, lat, poly) for poly in polygons):
        return 0.0
    min_d = float("inf")
    for poly in polygons:
        for ring in poly:
            for i in range(len(ring) - 1):
                d = _point_to_segment_dist_km(
                    lon, lat, ring[i][0], ring[i][1], ring[i + 1][0], ring[i + 1][1]
                )
                if d < min_d:
                    min_d = d
    return min_d


def point_to_wkt_distance_km(lat: float, lon: float, wkt: str) -> float:
    """
    Return minimum distance in kilometers from (lat, lon) to a WKT polygon.
    Returns 0.0 if the point is inside the polygon.
    """
    polygons = parse_wkt_polygons(wkt)
    return _point_to_polygons_distance_km(lat, lon, polygons)


def fetch_bangkok_wards_from_hdx(
    zip_url: str = HDX_GEOJSON_ZIP_URL, timeout: int = 30
) -> List[Dict[str, Any]]:
    """
    Download Bangkok (adm1_pcode == 'TH10') subdistrict (adm3 / khwaeng) features
    directly from the official HDX GeoJSON archive (`tha_admin_boundaries.geojson.zip`).
    Uses HTTP Range requests against the ZIP central directory and `tha_admin3.geojson`
    entry so it completes in seconds without writing a 436 MB archive to disk.
    """
    # 1. Read ZIP End of Central Directory (last 64 KB)
    tail_req = urllib.request.Request(
        zip_url,
        headers={"User-Agent": "CS341-TuamYang-ReferenceBuilder/1.0", "Range": "bytes=-65536"},
    )
    with urllib.request.urlopen(tail_req, timeout=timeout) as resp:
        tail = resp.read()

    eocd_pos = tail.rfind(b"PK\x05\x06")
    if eocd_pos == -1:
        raise RuntimeError("Could not locate ZIP End of Central Directory from HDX GeoJSON archive")

    _, _, _, _, total_entries, cd_size, _, _ = struct.unpack(
        "<4sHHHHIIH", tail[eocd_pos : eocd_pos + 22]
    )
    cd_start = eocd_pos - cd_size
    cd_data = tail[cd_start:eocd_pos]

    adm3_offset: Optional[int] = None
    adm3_comp_size: Optional[int] = None
    pos = 0
    for _ in range(total_entries):
        if pos + 46 > len(cd_data):
            break
        (
            _,
            _,
            _,
            _,
            _,
            _,
            _,
            _,
            comp_size,
            _,
            fname_len,
            extra_len,
            comment_len,
            _,
            _,
            _,
            local_offset,
        ) = struct.unpack("<4sHHHHHHIIIHHHHHII", cd_data[pos : pos + 46])
        fname = cd_data[pos + 46 : pos + 46 + fname_len].decode("utf-8", errors="replace")
        if fname == "tha_admin3.geojson":
            adm3_offset = local_offset
            adm3_comp_size = comp_size
            break
        pos += 46 + fname_len + extra_len + comment_len

    if adm3_offset is None or adm3_comp_size is None:
        raise RuntimeError("tha_admin3.geojson not found in HDX archive central directory")

    # 2. Stream compressed chunks of tha_admin3.geojson until all TH10 (Bangkok) features are read.
    # Bangkok (TH10) is ordered first in RTSD COD-AB (TH100101..TH105001, ~3 MB uncompressed, <1.5 MB compressed).
    chunk_len = min(2_000_000, adm3_comp_size + 512)
    range_end = adm3_offset + chunk_len
    entry_req = urllib.request.Request(
        zip_url,
        headers={
            "User-Agent": "CS341-TuamYang-ReferenceBuilder/1.0",
            "Range": f"bytes={adm3_offset}-{range_end}",
        },
    )
    with urllib.request.urlopen(entry_req, timeout=timeout) as resp:
        buf = resp.read()

    _, _, _, _, _, _, _, _, _, flen, elen = struct.unpack("<4sHHHHHIIIHH", buf[:30])
    data_start = 30 + flen + elen
    decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
    text = decompressor.decompress(buf[data_start:]).decode("utf-8", errors="ignore")

    wards: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line_s = line.strip().rstrip(",")
        if not line_s.startswith("{") or '"TH10"' not in line_s:
            continue
        feat = json.loads(line_s)
        props = feat.get("properties", {})
        if props.get("adm1_pcode") != "TH10":
            continue
        wkt = geojson_geometry_to_wkt(feat["geometry"])
        wards.append(
            {
                "khwaeng_code": str(props["adm3_pcode"]).strip(),
                "name_th": str(props["adm3_name1"]).strip(),
                "name_en": str(props["adm3_name"]).strip(),
                "khet": str(props["adm2_name1"]).strip(),
                "khet_code": str(props["adm2_pcode"]).strip(),
                "khet_en": str(props["adm2_name"]).strip(),
                "center_lat": round(float(props["center_lat"]), 6),
                "center_lon": round(float(props["center_lon"]), 6),
                "area_sqkm": round(float(props["area_sqkm"]), 6),
                "wkt": wkt,
            }
        )

    wards.sort(key=lambda w: w["khwaeng_code"])
    return wards


def select_wards_within_radius(
    wards: List[Dict[str, Any]],
    water_stations: List[Dict[str, Any]],
    radius_km: float = DEFAULT_RADIUS_KM,
) -> List[Dict[str, Any]]:
    """
    Return the subset of wards whose polygon boundary is within `radius_km`
    (default 1.0 km) of at least one water-level station.
    """
    selected: List[Dict[str, Any]] = []
    for w in wards:
        polygons = parse_wkt_polygons(w["wkt"])
        min_dist = min(
            _point_to_polygons_distance_km(
                float(st["latitude"]), float(st["longitude"]), polygons
            )
            for st in water_stations
        )
        if min_dist <= radius_km:
            selected.append(w)
    return selected


def build_ward_station_mapping(
    wards: List[Dict[str, Any]],
    water_stations: List[Dict[str, Any]],
    rain_stations: List[Dict[str, Any]],
    radius_km: float = DEFAULT_RADIUS_KM,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Build the ward <-> water-station and nearest usable rain-station mapping rows,
    along with a comprehensive data-quality summary dict.
    """
    usable_rain_stations = [
        s for s in rain_stations if s.get("usable_for_rainfall", True)
    ]
    if not usable_rain_stations:
        usable_rain_stations = list(rain_stations)

    # Pre-parse WKT polygons once per ward
    parsed_by_code: Dict[str, List[List[List[Tuple[float, float]]]]] = {
        w["khwaeng_code"]: parse_wkt_polygons(w["wkt"]) for w in wards
    }

    # Evaluate point-in-polygon and radius coverage per water station
    station_pip_results: List[Dict[str, Any]] = []
    station_wards_within_radius: Dict[int, List[str]] = {}
    for st in water_stations:
        st_id = int(st["api_station_id"])
        st_lat = float(st["latitude"])
        st_lon = float(st["longitude"])
        containing_wards: List[Dict[str, str]] = []
        wards_in_radius: List[Dict[str, Any]] = []

        for w in wards:
            polys = parsed_by_code[w["khwaeng_code"]]
            is_inside = any(_point_in_single_polygon(st_lon, st_lat, p) for p in polys)
            b_dist = 0.0 if is_inside else _point_to_polygons_distance_km(st_lat, st_lon, polys)
            if is_inside:
                containing_wards.append(
                    {
                        "khwaeng_code": w["khwaeng_code"],
                        "name_th": w["name_th"],
                        "khet": w["khet"],
                    }
                )
            if b_dist <= radius_km:
                wards_in_radius.append(
                    {
                        "khwaeng_code": w["khwaeng_code"],
                        "name_th": w["name_th"],
                        "khet": w["khet"],
                        "boundary_dist_km": round(b_dist, 4),
                    }
                )

        station_wards_within_radius[st_id] = [x["khwaeng_code"] for x in wards_in_radius]
        station_pip_results.append(
            {
                "api_station_id": st_id,
                "station_name_th": st["station_name_th"],
                "river_name_th": st.get("river_name_th", ""),
                "latitude": st_lat,
                "longitude": st_lon,
                "expected_tumbon_th": st.get("tumbon_th", ""),
                "expected_amphoe_th": st.get("amphoe_th", ""),
                "usable_for_freeboard": bool(st.get("usable_for_freeboard", True)),
                "quality_flag": st.get("quality_flag", "NORMAL"),
                "point_in_polygon_matched": len(containing_wards) > 0,
                "containing_wards": containing_wards,
                "wards_within_radius_count": len(wards_in_radius),
                "wards_within_radius": wards_in_radius,
            }
        )

    mapping_rows: List[Dict[str, Any]] = []
    for w in wards:
        code = w["khwaeng_code"]
        polys = parsed_by_code[code]
        c_lat = float(w["center_lat"])
        c_lon = float(w["center_lon"])

        # Distances from each water station to this ward's polygon boundary and centroid
        water_candidates = []
        within_st_ids: List[str] = []
        containing_st_ids: List[str] = []
        for st in water_stations:
            st_id = int(st["api_station_id"])
            st_lat = float(st["latitude"])
            st_lon = float(st["longitude"])
            is_inside = any(_point_in_single_polygon(st_lon, st_lat, p) for p in polys)
            b_dist = 0.0 if is_inside else _point_to_polygons_distance_km(st_lat, st_lon, polys)
            c_dist = haversine_km(c_lat, c_lon, st_lat, st_lon)
            if is_inside:
                containing_st_ids.append(str(st_id))
            if b_dist <= radius_km:
                within_st_ids.append(str(st_id))
            water_candidates.append((b_dist, c_dist, st))

        # Sort by polygon boundary distance first, breaking ties by centroid distance
        water_candidates.sort(key=lambda item: (round(item[0], 6), item[1]))
        min_b_dist, best_c_dist, best_water = water_candidates[0]

        # Nearest usable rain station (by ward centroid Haversine distance, matching CuratedBuilder)
        best_rain = min(
            usable_rain_stations,
            key=lambda rs: haversine_km(c_lat, c_lon, float(rs["latitude"]), float(rs["longitude"])),
        )
        rain_dist_km = haversine_km(
            c_lat, c_lon, float(best_rain["latitude"]), float(best_rain["longitude"])
        )
        rain_boundary_dist_km = _point_to_polygons_distance_km(
            float(best_rain["latitude"]), float(best_rain["longitude"]), polys
        )

        mapping_rows.append(
            {
                "khwaeng_code": code,
                "khwaeng": w["name_th"],
                "name_th": w["name_th"],
                "name_en": w["name_en"],
                "khet": w["khet"],
                "khet_code": w["khet_code"],
                "khet_en": w["khet_en"],
                "center_lat": w["center_lat"],
                "center_lon": w["center_lon"],
                "within_radius_km": len(within_st_ids) > 0,
                "radius_threshold_km": radius_km,
                "min_water_boundary_dist_km": round(min_b_dist, 4),
                "within_1km_water_station_ids": ";".join(within_st_ids),
                "containing_water_station_id": ";".join(containing_st_ids),
                "paired_water_station_id": int(best_water["api_station_id"]),
                "water_station_code": best_water.get("tele_station_oldcode", ""),
                "water_station_name": best_water.get("station_name_th", ""),
                "canal_name": best_water.get("river_name_th", ""),
                "water_station_dist_km": round(best_c_dist, 2),
                "water_quality_flag": best_water.get("quality_flag", "NORMAL"),
                "water_usable_for_freeboard": bool(best_water.get("usable_for_freeboard", True)),
                "paired_rain_station_id": int(best_rain["api_station_id"]),
                "rain_station_code": best_rain.get("tele_station_oldcode", ""),
                "rain_station_name": best_rain.get("station_name_th", ""),
                "rain_station_dist_km": round(rain_dist_km, 2),
                "rain_boundary_dist_km": round(rain_boundary_dist_km, 4),
                "rain_quality_flag": best_rain.get("quality_flag", "OK"),
                "rain_usable_for_rainfall": bool(best_rain.get("usable_for_rainfall", True)),
            }
        )

    selected_count = sum(1 for r in mapping_rows if r["within_radius_km"])
    zero_ward_stations = [
        s["api_station_id"]
        for s in station_pip_results
        if s["wards_within_radius_count"] == 0
    ]

    dq_summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_dataset": HDX_DATASET_PAGE_URL,
        "source_download_url": HDX_GEOJSON_ZIP_URL,
        "source_organization": HDX_SOURCE_ORG,
        "license": HDX_LICENSE,
        "radius_km": radius_km,
        "total_bangkok_wards_loaded": len(wards),
        "wards_selected_within_radius_count": selected_count,
        "stations_with_zero_wards_in_radius": zero_ward_stations,
        "water_stations_evaluated": len(water_stations),
        "water_stations_pip_inside_count": sum(
            1 for s in station_pip_results if s["point_in_polygon_matched"]
        ),
        "station_point_in_polygon_and_radius_checks": station_pip_results,
    }
    return mapping_rows, dq_summary


def evaluate_traffy_spatial_join_coverage(
    wards: List[Dict[str, Any]],
    curated_csv_path: Path,
) -> Dict[str, Any]:
    """
    Evaluate `ST_Contains` spatial join match rate against the 9,614 Traffy case-study
    reports in the repository (via `data/curated/khwaeng_hourly/khwaeng_hourly.csv`
    representative coordinates and text-extracted khwaeng names).
    """
    if not curated_csv_path.is_file():
        return {"evaluated": False, "reason": f"{curated_csv_path} not found"}

    parsed_wards = [
        (w["khwaeng_code"], w["name_th"], w["khet"], parse_wkt_polygons(w["wkt"]))
        for w in wards
    ]

    with open(curated_csv_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    total_reports = 0
    st_contains_matched_reports = 0
    address_regex_matched_reports = 0

    for r in rows:
        cnt = int(r.get("total_reports", 0))
        total_reports += cnt
        if r.get("khwaeng") and r.get("khwaeng") != "ไม่ระบุ":
            address_regex_matched_reports += cnt
        lat = float(r["center_latitude"])
        lon = float(r["center_longitude"])
        matched = False
        for _code, _nth, _khet, polys in parsed_wards:
            if any(_point_in_single_polygon(lon, lat, p) for p in polys):
                matched = True
                break
        if matched:
            st_contains_matched_reports += cnt

    return {
        "evaluated": True,
        "total_reports": total_reports,
        "st_contains_matched_reports": st_contains_matched_reports,
        "st_contains_match_rate_pct": round(
            (st_contains_matched_reports / total_reports * 100.0) if total_reports else 0.0, 2
        ),
        "address_regex_matched_reports": address_regex_matched_reports,
        "address_regex_match_rate_pct": round(
            (address_regex_matched_reports / total_reports * 100.0) if total_reports else 0.0, 2
        ),
    }


def save_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Bangkok ward WKT boundaries and ward <-> station mapping (Issue #3)."
    )
    parser.add_argument(
        "--radius-km",
        type=float,
        default=DEFAULT_RADIUS_KM,
        help="Radius in km around the 8 water stations to select nearby wards (default: 1.0)",
    )
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=PROJECT_ROOT / "reference",
        help="Path to reference/ directory",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=PROJECT_ROOT / "evidence" / "reference_spatial",
        help="Path to write data quality & spatial join evidence JSON",
    )
    args = parser.parse_args()

    ref_dir: Path = args.reference_dir
    water_stations = json.loads((ref_dir / "stations_water.json").read_text(encoding="utf-8"))
    rain_stations = json.loads((ref_dir / "stations_rain.json").read_text(encoding="utf-8"))

    print(f"Downloading Bangkok adm3 (khwaeng) boundaries from HDX ({HDX_GEOJSON_ZIP_URL})...")
    wards = fetch_bangkok_wards_from_hdx()
    print(f"Loaded {len(wards)} Bangkok wards from Royal Thai Survey Department (HDX COD-AB).")

    boundaries_csv = ref_dir / "khwaeng_boundaries.csv"
    save_csv(boundaries_csv, wards)
    print(f"Saved WKT boundaries to: {boundaries_csv}")

    mapping_rows, dq_summary = build_ward_station_mapping(
        wards=wards,
        water_stations=water_stations,
        rain_stations=rain_stations,
        radius_km=args.radius_km,
    )
    mapping_csv = ref_dir / "khwaeng_station_mapping.csv"
    save_csv(mapping_csv, mapping_rows)
    print(f"Saved ward <-> station mapping to: {mapping_csv}")

    curated_csv = PROJECT_ROOT / "data" / "curated" / "khwaeng_hourly" / "khwaeng_hourly.csv"
    dq_summary["traffy_spatial_join_evaluation"] = evaluate_traffy_spatial_join_coverage(
        wards, curated_csv
    )

    evidence_dir: Path = args.evidence_dir
    evidence_dir.mkdir(parents=True, exist_ok=True)
    report_path = evidence_dir / "spatial_quality_report.json"
    report_path.write_text(json.dumps(dq_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved data quality report to: {report_path}")

    print(
        f"Summary: wards_loaded={dq_summary['total_bangkok_wards_loaded']}, "
        f"selected_within_{args.radius_km}km={dq_summary['wards_selected_within_radius_count']}, "
        f"zero_ward_stations={dq_summary['stations_with_zero_wards_in_radius']}, "
        f"stations_pip_inside={dq_summary['water_stations_pip_inside_count']}/{dq_summary['water_stations_evaluated']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
