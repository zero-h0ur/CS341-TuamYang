"""
Validation rules for incoming batches (Issue #1: Orchestration + Validation)
Owner: zero-h0ur

Pure functions only: no AWS, no network, no storage. Each validator takes one
ingestion envelope (as written by src/ingest_thaiwater.py or src/ingest_traffy.py)
and returns a report:

    status = PASS  -> publish to trusted/
    status = WARN  -> publish to trusted/, warnings kept in the report
    status = FAIL  -> move to quarantine/, never published

Rule sources:
  - reference/schema_contract.md (ThaiWater): dedupe key (api_station_id,
    timestamp_epoch), freeboard = min_bank_msl - waterlevel_msl, freshness
    > 1 h for waterlevel (section 7), rain SLA 2-3 h (section 8.1), files
    without `series` overcount rain_24h_mm (section 8.2).
  - reference/traffy_schema_contract.md: read a partition only when the
    manifest is SUCCESS and batch_id / records_sha256 match; ticket_id is the
    dedupe key; missing coordinates are flagged, not dropped.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from config.config import (
    BANK_HEIGHT_TOLERANCE_M,
    MIN_RAIN_NON_NULL_RATIO,
    MIN_WATERLEVEL_NON_NULL_RATIO,
    OUTLIER_FREEBOARD_DIFF_M,
)

VALIDATOR_VERSION = "tuamyang-validate-v1.0"
BKK = timezone(timedelta(hours=7))

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# --- thresholds (kept here so the report can cite them) -------------------
WATERLEVEL_FRESHNESS_HOURS = 1.0       # schema_contract.md section 7
RAIN_FRESHNESS_HOURS = 3.0             # schema_contract.md section 8.1 (SLA 2-3 h)
FUTURE_TOLERANCE_MINUTES = 15          # reading time later than ingestion time
WATERLEVEL_RANGE_MSL = (-3.0, 5.0)     # plausible canal / Chao Phraya level, m MSL
FREEBOARD_CONSISTENCY_M = BANK_HEIGHT_TOLERANCE_M
RAIN_HOURLY_MAX_MM = 150.0             # above this is almost certainly a sensor fault
RAIN_24H_MAX_MM = 500.0
RAIN_SUM_TOLERANCE_MM = 0.15
TRAFFY_MAX_BAD_COORD_RATIO = 0.05
TELE_INTERVAL_MINUTES = 10
HOURLY_STATIONS = {2599}               # C.12 Samsen reports hourly
# Bangkok bounding box with a small margin (lat, lon)
BKK_BBOX = {"lat": (13.45, 14.00), "lon": (100.30, 100.96)}

ENVELOPE_REQUIRED = ("envelope_version", "batch_id", "data_type", "ingested_at_utc",
                     "ingested_at_bkk", "record_count", "data")


class Report:
    """Collects check results and derives the batch status."""

    def __init__(self, envelope: Dict[str, Any]):
        self.envelope = envelope
        self.checks: List[Dict[str, Any]] = []
        self.record_flags: Dict[str, List[str]] = {}

    def add(self, rule: str, ok: bool, severity: str, detail: str,
            examples: Optional[Iterable[Any]] = None, count: Optional[int] = None) -> bool:
        item: Dict[str, Any] = {
            "rule": rule,
            "passed": bool(ok),
            "severity": PASS if ok else severity,
            "detail": detail,
        }
        if not ok:
            ex = list(examples or [])
            if ex:
                item["examples"] = ex[:5]
            item["count"] = count if count is not None else len(ex)
        self.checks.append(item)
        return ok

    def flag(self, key: Any, flag: str) -> None:
        self.record_flags.setdefault(str(key), [])
        if flag not in self.record_flags[str(key)]:
            self.record_flags[str(key)].append(flag)

    @property
    def status(self) -> str:
        severities = {c["severity"] for c in self.checks}
        if FAIL in severities:
            return FAIL
        if WARN in severities:
            return WARN
        return PASS

    def to_dict(self, checked_at: Optional[datetime] = None) -> Dict[str, Any]:
        env = self.envelope if isinstance(self.envelope, dict) else {}
        data = env.get("data") if isinstance(env.get("data"), list) else []
        return {
            "validator_version": VALIDATOR_VERSION,
            "status": self.status,
            "data_type": env.get("data_type"),
            "batch_id": env.get("batch_id"),
            "ingested_at_bkk": env.get("ingested_at_bkk"),
            "checked_at_utc": (checked_at or datetime.now(timezone.utc)).isoformat(),
            "record_count": len(data),
            "summary": {
                "fail": sum(c["severity"] == FAIL for c in self.checks),
                "warn": sum(c["severity"] == WARN for c in self.checks),
                "pass": sum(c["severity"] == PASS for c in self.checks),
            },
            "checks": self.checks,
            "record_flags": self.record_flags,
        }


# --- helpers ---------------------------------------------------------------

def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=BKK)


def _parse_bkk_local(value: Any) -> Optional[datetime]:
    """ThaiWater 'YYYY-MM-DD HH:MM' strings are Bangkok local time."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=BKK)
    except ValueError:
        return None


def in_bangkok(lat: Any, lon: Any) -> bool:
    return (_is_num(lat) and _is_num(lon)
            and BKK_BBOX["lat"][0] <= lat <= BKK_BBOX["lat"][1]
            and BKK_BBOX["lon"][0] <= lon <= BKK_BBOX["lon"][1])


def records_hash(records: List[Dict[str, Any]]) -> str:
    """Same hash as src/ingest_traffy.records_hash (kept local: no import of ingest code)."""
    value = json.dumps(records, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _check_envelope(rep: Report, env: Any, expected_type: str) -> bool:
    """Structural checks shared by all batch types. Returns False if unusable."""
    if not isinstance(env, dict):
        rep.add("envelope_is_object", False, FAIL, "Envelope is not a JSON object")
        return False
    missing = [k for k in ENVELOPE_REQUIRED if k not in env]
    if not rep.add("envelope_required_fields", not missing, FAIL,
                   "Envelope has all required fields", missing):
        return False
    rep.add("envelope_version", env.get("envelope_version") == "1.0", FAIL,
            f"envelope_version must be '1.0' (got {env.get('envelope_version')!r})")
    rep.add("data_type_matches", env.get("data_type") == expected_type, FAIL,
            f"data_type must be '{expected_type}' (got {env.get('data_type')!r})")
    if not rep.add("data_is_list", isinstance(env.get("data"), list), FAIL, "`data` is a list"):
        return False
    rep.add("record_count_matches", env.get("record_count") == len(env["data"]), FAIL,
            f"record_count={env.get('record_count')} vs len(data)={len(env['data'])}")
    rep.add("not_mock", env.get("is_mock") is False, FAIL,
            "Mock/fixture batches are never published (is_mock must be false)")
    rep.add("ingested_at_parseable", _parse_iso(env.get("ingested_at_bkk")) is not None, FAIL,
            "ingested_at_bkk is a valid ISO timestamp")
    return True


# --- ThaiWater: live waterlevel -------------------------------------------

def validate_waterlevel(env: Dict[str, Any], water_stations: List[Dict[str, Any]]) -> Report:
    rep = Report(env)
    if not _check_envelope(rep, env, "waterlevel"):
        return rep
    data = env["data"]
    ref = {s["api_station_id"]: s for s in water_stations}
    ingested = _parse_iso(env.get("ingested_at_bkk"))

    rep.add("non_empty", len(data) > 0, FAIL, "Batch has at least one record")

    required = ("api_station_id", "timestamp_epoch", "waterlevel_datetime",
                "waterlevel_msl", "min_bank_msl", "usable_for_freeboard")
    bad_schema = [r.get("api_station_id") for r in data
                  if not isinstance(r, dict) or any(k not in r for k in required)]
    if not rep.add("record_required_fields", not bad_schema, FAIL,
                   f"Every record has {', '.join(required)}", bad_schema):
        return rep

    unknown = sorted({r["api_station_id"] for r in data} - set(ref), key=str)
    rep.add("station_in_reference", not unknown, FAIL,
            "Every station is one of the 8 reference stations (reference/stations_water.json)", unknown)

    keys = [(r["api_station_id"], r["timestamp_epoch"]) for r in data]
    dups = sorted({k for k in keys if keys.count(k) > 1}, key=str)
    rep.add("unique_station_time", not dups, FAIL,
            "No duplicate (api_station_id, timestamp_epoch) in one batch", dups)

    non_null = sum(_is_num(r["waterlevel_msl"]) for r in data)
    ratio = non_null / len(data) if data else 0.0
    rep.add("waterlevel_non_null_ratio", ratio >= MIN_WATERLEVEL_NON_NULL_RATIO, FAIL,
            f"Non-null waterlevel ratio {ratio:.2f} >= {MIN_WATERLEVEL_NON_NULL_RATIO}")

    # min_bank must match reference: catches the PR #7 bug (ground_level used as bank)
    bank_bad = []
    for r in data:
        s = ref.get(r["api_station_id"])
        if s is None:
            continue
        if not _is_num(r["min_bank_msl"]) or abs(r["min_bank_msl"] - s["min_bank_msl"]) > BANK_HEIGHT_TOLERANCE_M:
            bank_bad.append({"station": r["api_station_id"], "min_bank_msl": r["min_bank_msl"],
                             "reference": s["min_bank_msl"]})
    rep.add("bank_matches_reference", not bank_bad, FAIL,
            f"min_bank_msl within {BANK_HEIGHT_TOLERANCE_M} m of reference", bank_bad)

    # epoch and local datetime must describe the same instant
    epoch_bad = []
    for r in data:
        local = _parse_bkk_local(r["waterlevel_datetime"])
        if local is None or not _is_num(r["timestamp_epoch"]) or int(local.timestamp()) != int(r["timestamp_epoch"]):
            epoch_bad.append(r["api_station_id"])
    rep.add("epoch_matches_local_time", not epoch_bad, FAIL,
            "timestamp_epoch equals waterlevel_datetime (Asia/Bangkok)", epoch_bad)

    future, stale, off_grid, out_range, fb_bad, fb_outlier, flag_bad = [], [], [], [], [], [], []
    for r in data:
        sid = r["api_station_id"]
        local = _parse_bkk_local(r["waterlevel_datetime"])
        if local and ingested:
            lag_h = (ingested - local).total_seconds() / 3600
            if lag_h < -FUTURE_TOLERANCE_MINUTES / 60:
                future.append(sid)
            elif lag_h > WATERLEVEL_FRESHNESS_HOURS:
                stale.append({"station": sid, "lag_hours": round(lag_h, 2)})
                rep.flag(sid, "STALE_READING")
            step = 60 if sid in HOURLY_STATIONS else TELE_INTERVAL_MINUTES
            if local.minute % step:
                off_grid.append({"station": sid, "time": r["waterlevel_datetime"]})
        usable = r["usable_for_freeboard"] is True
        wl = r["waterlevel_msl"]
        if usable and _is_num(wl) and not (WATERLEVEL_RANGE_MSL[0] <= wl <= WATERLEVEL_RANGE_MSL[1]):
            out_range.append({"station": sid, "waterlevel_msl": wl})
            rep.flag(sid, "WATERLEVEL_OUT_OF_RANGE")
        if usable and _is_num(wl) and _is_num(r["min_bank_msl"]):
            expected = r["min_bank_msl"] - wl
            diff = r.get("diff_wl_bank_m")
            if not _is_num(diff) or abs(diff - expected) > FREEBOARD_CONSISTENCY_M:
                fb_bad.append({"station": sid, "diff_wl_bank_m": diff, "expected": round(expected, 3)})
            if abs(expected) > OUTLIER_FREEBOARD_DIFF_M:
                fb_outlier.append({"station": sid, "freeboard": round(expected, 3)})
                rep.flag(sid, "FREEBOARD_OUTLIER")
        if not usable and r.get("quality_flag") in ("OK", "NORMAL"):
            flag_bad.append(sid)

    rep.add("no_future_readings", not future, FAIL,
            f"No reading more than {FUTURE_TOLERANCE_MINUTES} min after ingestion time", future)
    rep.add("freshness", not stale, WARN,
            f"Reading age <= {WATERLEVEL_FRESHNESS_HOURS} h at ingestion", stale)
    rep.add("reading_on_interval_grid", not off_grid, WARN,
            f"Reading time on the {TELE_INTERVAL_MINUTES}-min grid (hourly for C.12)", off_grid)
    rep.add("waterlevel_plausible_range", not out_range, WARN,
            f"Usable waterlevel within {WATERLEVEL_RANGE_MSL} m MSL", out_range)
    rep.add("freeboard_consistent", not fb_bad, WARN,
            f"diff_wl_bank_m equals min_bank_msl - waterlevel_msl (+/-{FREEBOARD_CONSISTENCY_M} m)", fb_bad)
    rep.add("freeboard_not_outlier", not fb_outlier, WARN,
            f"|freeboard| <= {OUTLIER_FREEBOARD_DIFF_M} m for usable stations", fb_outlier)
    rep.add("unusable_has_quality_flag", not flag_bad, WARN,
            "Stations excluded from freeboard carry a non-OK quality_flag", flag_bad)

    missing = sorted(set(ref) - {r["api_station_id"] for r in data}, key=str)
    rep.add("all_reference_stations_present", not missing, WARN,
            "All 8 reference stations reported in this batch", missing)
    return rep


# --- ThaiWater: live rain (24 h sliding window) ---------------------------

def validate_rain(env: Dict[str, Any], rain_stations: List[Dict[str, Any]]) -> Report:
    rep = Report(env)
    if not _check_envelope(rep, env, "rain"):
        return rep
    data = env["data"]
    ref = {s["api_station_id"]: s for s in rain_stations}
    ingested = _parse_iso(env.get("ingested_at_bkk"))

    rep.add("non_empty", len(data) > 0, FAIL, "Batch has at least one record")
    required = ("api_station_id", "rain_24h_points", "usable_for_rainfall")
    bad_schema = [r.get("api_station_id") for r in data
                  if not isinstance(r, dict) or any(k not in r for k in required)]
    if not rep.add("record_required_fields", not bad_schema, FAIL,
                   f"Every record has {', '.join(required)}", bad_schema):
        return rep

    legacy = [r["api_station_id"] for r in data if not isinstance(r.get("series"), list)]
    if not rep.add("has_hourly_series", not legacy, FAIL,
                   "Every record has `series` (files without it overcount rain_24h_mm, contract 8.2)", legacy):
        return rep

    unknown = sorted({r["api_station_id"] for r in data} - set(ref), key=str)
    rep.add("station_in_reference", not unknown, FAIL,
            "Every station is in reference/stations_rain.json", unknown)
    ids = [r["api_station_id"] for r in data]
    rep.add("unique_station", len(ids) == len(set(ids)), FAIL, "One record per station",
            sorted({i for i in ids if ids.count(i) > 1}, key=str))

    negative, bad_time, dup_time, too_high, sum_bad, stale, future = [], [], [], [], [], [], []
    for r in data:
        sid = r["api_station_id"]
        seen = set()
        values = []
        for p in r["series"]:
            t = _parse_bkk_local(p.get("datetime")) if isinstance(p, dict) else None
            v = p.get("rain_mm") if isinstance(p, dict) else None
            if t is None:
                bad_time.append(sid)
                continue
            if t in seen:
                dup_time.append({"station": sid, "time": p["datetime"]})
            seen.add(t)
            if v is None:
                continue
            if not _is_num(v) or v < 0:
                negative.append({"station": sid, "time": p["datetime"], "rain_mm": v})
                continue
            if v > RAIN_HOURLY_MAX_MM:
                too_high.append({"station": sid, "time": p["datetime"], "rain_mm": v})
            values.append((t, v))
        r24 = r.get("rain_24h_mm")
        if r24 is not None and (not _is_num(r24) or r24 < 0):
            negative.append({"station": sid, "rain_24h_mm": r24})
        elif _is_num(r24) and r24 > RAIN_24H_MAX_MM:
            too_high.append({"station": sid, "rain_24h_mm": r24})
        values.sort()
        last24 = values[-24:]
        if r24 is not None and _is_num(r24):
            if abs(sum(v for _, v in last24) - r24) > RAIN_SUM_TOLERANCE_MM or r.get("rain_24h_points") != len(last24):
                sum_bad.append({"station": sid, "rain_24h_mm": r24,
                                "sum_last_24": round(sum(v for _, v in last24), 2),
                                "points": r.get("rain_24h_points")})
        if values and ingested:
            latest = values[-1][0]
            lag_h = (ingested - latest).total_seconds() / 3600
            if lag_h < -FUTURE_TOLERANCE_MINUTES / 60:
                future.append(sid)
            elif lag_h > RAIN_FRESHNESS_HOURS and r["usable_for_rainfall"] is True:
                stale.append({"station": sid, "lag_hours": round(lag_h, 2)})
                rep.flag(sid, "STALE_RAIN")
        if not values:
            rep.flag(sid, "NO_RAIN_READINGS")

    rep.add("series_time_parseable", not bad_time, FAIL, "Every series point has a valid datetime",
            sorted(set(bad_time), key=str))
    rep.add("series_unique_time", not dup_time, FAIL, "No duplicate hour within a station series", dup_time)
    rep.add("rain_non_negative", not negative, FAIL, "Rain values are numbers >= 0 (null allowed)", negative)
    rep.add("no_future_readings", not future, FAIL,
            f"No rain point more than {FUTURE_TOLERANCE_MINUTES} min after ingestion time", future)
    rep.add("rain_plausible_max", not too_high, WARN,
            f"Hourly rain <= {RAIN_HOURLY_MAX_MM} mm and 24 h <= {RAIN_24H_MAX_MM} mm", too_high)
    rep.add("rain_24h_matches_series", not sum_bad, WARN,
            "rain_24h_mm equals the sum of the last 24 non-null hourly points", sum_bad)
    rep.add("freshness", not stale, WARN,
            f"Latest rain point age <= {RAIN_FRESHNESS_HOURS} h at ingestion (usable stations)", stale)

    usable = [r for r in data if r["usable_for_rainfall"] is True]
    reporting = [r for r in usable if (r.get("rain_24h_points") or 0) > 0]
    ratio = len(reporting) / len(usable) if usable else 0.0
    silent = [r["api_station_id"] for r in usable if (r.get("rain_24h_points") or 0) == 0]
    rep.add("rain_station_coverage", ratio >= MIN_RAIN_NON_NULL_RATIO, FAIL,
            f"{len(reporting)}/{len(usable)} usable stations reported rain points "
            f"(ratio {ratio:.2f} >= {MIN_RAIN_NON_NULL_RATIO})", silent)
    rep.add("all_usable_stations_reporting", not silent, WARN,
            "Every usable rain station has at least one point in the window", silent)
    if env.get("failed_count"):
        rep.add("no_failed_station_fetch", False, WARN,
                f"{env['failed_count']} station(s) failed to fetch during ingestion",
                [f.get("station_id") for f in env.get("failed_stations", [])])
    return rep


# --- Traffy daily all-report partition ------------------------------------

def validate_traffy(env: Dict[str, Any], manifest: Optional[Dict[str, Any]]) -> Report:
    rep = Report(env)
    if not _check_envelope(rep, env, "traffy"):
        return rep
    data = env["data"]

    # Contract: only read when manifest SUCCESS and batch/hash match. A RUNNING
    # manifest is handled upstream as NOT_READY (retry), so here it is a FAIL.
    m_ok = isinstance(manifest, dict) and manifest.get("status") == "SUCCESS"
    rep.add("manifest_success", m_ok, FAIL,
            f"Manifest status is SUCCESS (got {manifest.get('status') if isinstance(manifest, dict) else None!r})")
    if m_ok:
        rep.add("manifest_batch_matches", manifest.get("batch_id") == env.get("batch_id"), FAIL,
                "Manifest batch_id equals envelope batch_id")
        rep.add("manifest_hash_matches", manifest.get("records_sha256") == env.get("records_sha256"), FAIL,
                "Manifest records_sha256 equals envelope records_sha256")
    rep.add("records_hash_valid", records_hash(data) == env.get("records_sha256"), FAIL,
            "SHA-256 of data equals records_sha256 (file not truncated or edited)")

    required = ("ticket_id", "timestamp_epoch", "report_date_bkk", "report_hour_bkk",
                "flood_classification", "is_flood_report")
    bad_schema = [r.get("ticket_id") for r in data
                  if not isinstance(r, dict) or any(k not in r for k in required)]
    if not rep.add("record_required_fields", not bad_schema, FAIL,
                   f"Every record has {', '.join(required)}", bad_schema):
        return rep

    tickets = [r["ticket_id"] for r in data]
    empty = [i for i, t in enumerate(tickets) if not isinstance(t, str) or not t.strip()]
    rep.add("ticket_id_present", not empty, FAIL, "Every report has a non-empty ticket_id", empty)
    seen, dups = set(), set()
    for t in tickets:
        (dups if t in seen else seen).add(t)
    rep.add("unique_ticket_id", not dups, FAIL, "ticket_id is unique within the day", sorted(dups))

    day = env.get("date_bkk")
    wrong_day, bad_epoch = [], []
    for r in data:
        if not _is_num(r["timestamp_epoch"]):
            bad_epoch.append(r["ticket_id"])
            continue
        local = datetime.fromtimestamp(r["timestamp_epoch"], BKK)
        if local.date().isoformat() != r["report_date_bkk"] or local.hour != r["report_hour_bkk"]:
            bad_epoch.append(r["ticket_id"])
        if r["report_date_bkk"] != day:
            wrong_day.append(r["ticket_id"])
    rep.add("epoch_matches_bkk_keys", not bad_epoch, FAIL,
            "timestamp_epoch agrees with report_date_bkk / report_hour_bkk", bad_epoch)
    rep.add("reports_inside_day", not wrong_day, FAIL, f"Every report belongs to date_bkk={day}", wrong_day)

    cls_map = {"flood": True, "non_flood": False, "uncertain": None}
    cls_bad = [r["ticket_id"] for r in data
               if r["flood_classification"] not in cls_map
               or r["is_flood_report"] is not cls_map[r["flood_classification"]]]
    rep.add("flood_label_consistent", not cls_bad, FAIL,
            "is_flood_report matches flood_classification (flood/non_flood/uncertain -> true/false/null)", cls_bad)
    floods = sum(r["is_flood_report"] is True for r in data)
    rep.add("flood_count_matches", env.get("flood_count") == floods, FAIL,
            f"flood_count={env.get('flood_count')} vs counted={floods}")

    bad_coord = [r["ticket_id"] for r in data if not in_bangkok(r.get("latitude"), r.get("longitude"))]
    ratio = len(bad_coord) / len(data) if data else 0.0
    rep.add("coordinates_in_bangkok", ratio <= TRAFFY_MAX_BAD_COORD_RATIO, WARN,
            f"{len(bad_coord)} report(s) missing or outside Bangkok bbox "
            f"(ratio {ratio:.3f} <= {TRAFFY_MAX_BAD_COORD_RATIO})", bad_coord, count=len(bad_coord))

    provisional = env.get("is_provisional") is True
    rep.add("non_empty", len(data) > 0, WARN if provisional else FAIL,
            "A closed Bangkok day has at least one report")
    rep.add("closed_day", not provisional, WARN,
            "Partition is a closed day (provisional hourly refreshes are published with a warning)")
    if isinstance(manifest, dict) and manifest.get("quality_warnings"):
        rep.add("source_quality_warnings", False, WARN,
                "Ingestion recorded source warnings (kept for coverage reporting)",
                manifest["quality_warnings"])
    return rep


# --- ThaiWater: monthly historical backfill (raw/, 10-minute grid) --------

def validate_waterlevel_history(env: Dict[str, Any], water_stations: List[Dict[str, Any]]) -> Report:
    """Checks a backfill file such as raw/thaiwater/waterlevel/dt=YYYY-MM-01/waterlevel_hist_st1_YYYY_MM.json."""
    rep = Report(env)
    if not isinstance(env, dict) or not isinstance(env.get("data"), list):
        rep.add("envelope_is_object", False, FAIL, "Backfill envelope with a `data` list")
        return rep
    data = env["data"]
    sid = env.get("api_station_id")
    ref = {s["api_station_id"]: s for s in water_stations}
    rep.add("station_in_reference", sid in ref, FAIL, f"Station {sid} is a reference station")
    rep.add("not_mock", env.get("is_mock") is False, FAIL, "is_mock must be false")

    step = 60 if sid in HOURLY_STATIONS else TELE_INTERVAL_MINUTES
    times = [_parse_bkk_local(r.get("datetime")) for r in data]
    rep.add("datetime_parseable", all(times), FAIL, "Every slot has a valid datetime")
    valid_times = sorted(t for t in times if t)
    off_grid = [t.strftime("%Y-%m-%d %H:%M") for t in valid_times if t.minute % step]
    rep.add("interval_grid", not off_grid, FAIL, f"All slots on the {step}-minute grid", off_grid)
    dups = sorted({t for t in valid_times if valid_times.count(t) > 1})
    rep.add("unique_slot", not dups, FAIL, "No duplicate time slot", [d.isoformat() for d in dups])

    non_null = sum(_is_num(r.get("waterlevel_msl")) for r in data)
    ratio = non_null / len(data) if data else 0.0
    rep.add("waterlevel_non_null_ratio", ratio >= MIN_WATERLEVEL_NON_NULL_RATIO, FAIL,
            f"Non-null ratio {ratio:.3f} >= {MIN_WATERLEVEL_NON_NULL_RATIO} "
            f"(catches the null grid returned without station_type=tele_waterlevel)")
    if sid in ref:
        banks = {r.get("min_bank_msl") for r in data}
        bad = [b for b in banks if not _is_num(b) or abs(b - ref[sid]["min_bank_msl"]) > BANK_HEIGHT_TOLERANCE_M]
        rep.add("bank_matches_reference", not bad, FAIL,
                f"min_bank_msl within {BANK_HEIGHT_TOLERANCE_M} m of reference {ref[sid]['min_bank_msl']}", bad)
    gaps = 0
    for a, b in zip(valid_times, valid_times[1:]):
        if (b - a).total_seconds() > step * 60:
            gaps += 1
    rep.add("no_missing_slots", gaps == 0, WARN, f"Consecutive slots {step} min apart", count=gaps)
    return rep


# --- dispatcher -------------------------------------------------------------

def validate_envelope(env: Dict[str, Any], *, water_stations: List[Dict[str, Any]],
                      rain_stations: List[Dict[str, Any]], manifest: Optional[Dict[str, Any]] = None,
                      checked_at: Optional[datetime] = None) -> Dict[str, Any]:
    """Picks the right validator from data_type / batch_type and returns the report dict."""
    dtype = env.get("data_type") if isinstance(env, dict) else None
    if isinstance(env, dict) and env.get("batch_type") == "historical_backfill" and dtype == "waterlevel":
        rep = validate_waterlevel_history(env, water_stations)
    elif dtype == "waterlevel":
        rep = validate_waterlevel(env, water_stations)
    elif dtype == "rain":
        rep = validate_rain(env, rain_stations)
    elif dtype == "traffy":
        rep = validate_traffy(env, manifest)
    else:
        rep = Report(env if isinstance(env, dict) else {})
        rep.add("known_data_type", False, FAIL, f"Unknown data_type {dtype!r}")
    return rep.to_dict(checked_at)
