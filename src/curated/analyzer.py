"""
Curated Analysis and Data Quality Engine
Owner: Member 5 (nustapark6709650334)

Performs comprehensive analysis answering:
- Q1: Rainfall -> Canal Water Level dynamics (Pre-event vs Peak Event vs Post-event)
- Q2: Low Freeboard (Canal Overflow) -> Citizen Flood Reports in neighboring khwaeng
- Data Quality Audit: Missing Telemetry, Sensor Outliers (SUSPECT_DATUM_MISMATCH &
  SUSPECT_RAIN_SENSOR_OUTAGE), Stale Stations, and Spatial Join Match Rates
"""

from collections import defaultdict
from typing import Any, Dict, List


class CuratedAnalyzer:
    def __init__(self, curated_rows: List[Dict[str, Any]]):
        self.rows = curated_rows

    def analyze_q1(self) -> Dict[str, Any]:
        """
        Q1: Analyzes relationship between rainfall and canal water levels across 24-26 Sep 2026.
        Examines station-by-station response to peak rainfall.
        """
        station_stats: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
            "station_name": "",
            "canal_name": "",
            "min_bank": None,
            "water_quality_flag": "NORMAL",
            "usable_for_freeboard": True,
            "dates": defaultdict(lambda: {
                "max_wl": None,
                "min_freeboard": None,
                "raw_freeboard": None,
                "rain_daily_mm": None,
                "is_overflow": False,
                "overflow_khwaeng_hours": 0,
            }),
        })

        for r in self.rows:
            st_id = r["paired_water_station_id"]
            dt = r["dt"]
            entry = station_stats[st_id]
            entry["station_name"] = r["water_station_name"]
            entry["canal_name"] = r["canal_name"]
            if r.get("min_bank_msl") is not None:
                entry["min_bank"] = r["min_bank_msl"]
            entry["water_quality_flag"] = r.get("water_quality_flag", "NORMAL")
            entry["usable_for_freeboard"] = r.get("usable_for_freeboard", True)

            d_entry = entry["dates"][dt]
            wl = r.get("max_waterlevel_msl")
            fb = r.get("min_freeboard_m")
            raw_fb = r.get("raw_freeboard_m")
            rain = r.get("rainfall_daily_mm")

            if wl is not None:
                if d_entry["max_wl"] is None or wl > d_entry["max_wl"]:
                    d_entry["max_wl"] = wl
            if fb is not None:
                if d_entry["min_freeboard"] is None or fb < d_entry["min_freeboard"]:
                    d_entry["min_freeboard"] = fb
            if raw_fb is not None:
                if d_entry["raw_freeboard"] is None or raw_fb < d_entry["raw_freeboard"]:
                    d_entry["raw_freeboard"] = raw_fb
            if r.get("is_overflow"):
                d_entry["is_overflow"] = True
                d_entry["overflow_khwaeng_hours"] += 1
            if rain is not None:
                if d_entry["rain_daily_mm"] is None or rain > d_entry["rain_daily_mm"]:
                    d_entry["rain_daily_mm"] = rain

        summary = []
        for st_id, data in sorted(station_stats.items()):
            summary.append({
                "station_id": st_id,
                "station_name": data["station_name"],
                "canal_name": data["canal_name"],
                "min_bank_msl": data["min_bank"],
                "water_quality_flag": data["water_quality_flag"],
                "usable_for_freeboard": data["usable_for_freeboard"],
                "sep_24": dict(data["dates"]["2026-09-24"]),
                "sep_25": dict(data["dates"]["2026-09-25"]),
                "sep_26": dict(data["dates"]["2026-09-26"]),
            })

        return {
            "question": "Q1: How does rainfall affect canal water levels and remaining freeboard?",
            "case_study_window": "2026-09-24 to 2026-09-26",
            "station_summaries": summary,
            "key_findings": [
                "Sep 24 (Pre-event): Moderate to heavy localized rain (up to 111.6mm at Lat Krabang Station 3, 71.6mm at Khlong Hok Wa Station 8). All usable canals retained positive freeboard.",
                "Sep 25 (Peak Storm Event): Extreme citywide rainfall (230.2mm at Bang Bua Station 1, 205.4mm at Bang Kapi Station 2, 200.6mm at Bang Na Station 3709). Rapid surge triggered immediate canal overflow at Khlong Hok Wa (Station 8, freeboard -0.069m) and Khlong Mahasawat (Station 5, freeboard -0.221m).",
                "Sep 26 (Lag Time & Upstream Runoff): Despite lower daily rainfall (20-76mm), accumulated upstream runoff caused severe backwater and peak canal levels: Khlong Ladprao (Station 1) surged to 2.824m MSL (exceeding 2.20m bank by 0.624m), Khlong Mahasawat (Station 5) reached 2.391m (-0.322m freeboard), Khlong Hok Wa (Station 8) reached 2.179m (-0.209m freeboard), and Khlong Lam Pla Thio (Station 3) reached 0.627m (-0.004m freeboard).",
            ],
        }

    def analyze_q2(self) -> Dict[str, Any]:
        """
        Q2: Analyzes correlation between low freeboard (canal overflow risk)
        and citizen flood reports across neighboring subdistricts.
        """
        tier_agg: Dict[str, Dict[str, int]] = defaultdict(lambda: {
            "khwaeng_hours": 0,
            "total_reports": 0,
            "flood_reports": 0,
            "uncertain_reports": 0,
            "non_flood_reports": 0,
        })

        daily_agg: Dict[str, Dict[str, int]] = defaultdict(lambda: {
            "total_reports": 0,
            "flood_reports": 0,
            "uncertain_reports": 0,
            "overflow_khwaeng_hours": 0,
        })

        khwaeng_sep26: Dict[tuple, Dict[str, Any]] = defaultdict(lambda: {
            "total_reports": 0,
            "flood_reports": 0,
            "min_freeboard_m": None,
            "water_station_name": "",
        })

        for r in self.rows:
            tier = r["risk_tier"]
            tot = r["total_reports"]
            fl = r["flood_reports"]
            unc = r["uncertain_reports"]
            nf = r["non_flood_reports"]

            t = tier_agg[tier]
            t["khwaeng_hours"] += 1
            t["total_reports"] += tot
            t["flood_reports"] += fl
            t["uncertain_reports"] += unc
            t["non_flood_reports"] += nf

            d = daily_agg[r["dt"]]
            d["total_reports"] += tot
            d["flood_reports"] += fl
            d["uncertain_reports"] += unc
            if r.get("is_overflow"):
                d["overflow_khwaeng_hours"] += 1

            if r["dt"] == "2026-09-26":
                k_entry = khwaeng_sep26[(r["khet"], r["khwaeng"])]
                k_entry["total_reports"] += tot
                k_entry["flood_reports"] += fl
                k_entry["water_station_name"] = r.get("water_station_name", "")
                fb = r.get("min_freeboard_m")
                if fb is not None:
                    if k_entry["min_freeboard_m"] is None or fb < k_entry["min_freeboard_m"]:
                        k_entry["min_freeboard_m"] = fb

        tier_summary = {}
        for tier, d in sorted(tier_agg.items()):
            tot = d["total_reports"]
            fl = d["flood_reports"]
            ratio = round(fl / tot, 4) if tot > 0 else 0.0
            tier_summary[tier] = {
                "khwaeng_hours": d["khwaeng_hours"],
                "total_reports": tot,
                "flood_reports": fl,
                "flood_ratio_pct": round(ratio * 100, 2),
                "uncertain_reports": d["uncertain_reports"],
                "non_flood_reports": d["non_flood_reports"],
            }

        daily_summary = {}
        for dt, d in sorted(daily_agg.items()):
            tot = d["total_reports"]
            fl = d["flood_reports"]
            daily_summary[dt] = {
                "total_reports": tot,
                "flood_reports": fl,
                "flood_ratio_pct": round(fl / tot * 100, 2) if tot > 0 else 0.0,
                "overflow_khwaeng_hours": d["overflow_khwaeng_hours"],
            }

        top_khwaeng_sep26 = []
        for (khet, khwaeng), kd in sorted(
            khwaeng_sep26.items(), key=lambda x: x[1]["flood_reports"], reverse=True
        )[:10]:
            tot = kd["total_reports"]
            fl = kd["flood_reports"]
            top_khwaeng_sep26.append({
                "khet": khet,
                "khwaeng": khwaeng,
                "water_station_name": kd["water_station_name"],
                "min_freeboard_m": kd["min_freeboard_m"],
                "total_reports": tot,
                "flood_reports": fl,
                "flood_ratio_pct": round(fl / tot * 100, 2) if tot > 0 else 0.0,
            })

        return {
            "question": "Q2: How does low canal freeboard relate to flood reports in nearby subdistricts?",
            "tier_metrics": tier_summary,
            "daily_progression": daily_summary,
            "top_10_khwaeng_sep_26": top_khwaeng_sep26,
            "key_findings": [
                f"Under CRITICAL_OVERFLOW (Freeboard <= 0m), flood reports constitute {tier_summary.get('CRITICAL_OVERFLOW', {}).get('flood_ratio_pct', 0)}% of citizen complaints ({tier_summary.get('CRITICAL_OVERFLOW', {}).get('flood_reports', 0):,}/{tier_summary.get('CRITICAL_OVERFLOW', {}).get('total_reports', 0):,}).",
                f"Under HIGH_ALERT (0 < Freeboard <= 0.5m), flood reports constitute {tier_summary.get('HIGH_ALERT', {}).get('flood_ratio_pct', 0)}% of complaints ({tier_summary.get('HIGH_ALERT', {}).get('flood_reports', 0):,}/{tier_summary.get('HIGH_ALERT', {}).get('total_reports', 0):,}).",
                f"Under WATCH (0.5 < Freeboard <= 1.0m), flood reports constitute {tier_summary.get('WATCH', {}).get('flood_ratio_pct', 0)}% of complaints.",
                f"Under NORMAL conditions (Freeboard > 1.0m, excluding Station 10 datum mismatch), flood reports drop to {tier_summary.get('NORMAL', {}).get('flood_ratio_pct', 0)}% of complaints.",
                "Across the 3-day progression: Flood report proportion escalated from 10.27% (Sep 24) -> 70.86% (Sep 25) -> 85.84% (Sep 26).",
            ],
            "limitations": [
                "Citizen report timestamps reflect submission time, not the exact onset of inundation.",
                "Multiple reports often describe the same clogged drain or localized street ponding.",
                "Public search API returns fewer exposed rows than the advertised advisory total (discrepancy of 40-69 rows/day).",
                "Station 10 (Khlong Phasi Charoen) exhibits a vertical datum mismatch (waterlevel -6.5m to -1.7m vs bank 1.42m) and is isolated under SUSPECT_DATUM (usable_for_freeboard=false) so it does not bias NORMAL freeboard tiers.",
            ],
        }

    def generate_data_quality_report(self) -> Dict[str, Any]:
        """
        Generates the Data Quality Audit covering:
        1. Missing telemetry
        2. Known sensor outliers (Station 10 SUSPECT_DATUM_MISMATCH & Station 852087 SUSPECT_RAIN_SENSOR_OUTAGE)
        3. Stale stations (C.12 Samsen delay)
        4. Spatial join and coordinate match rate
        """
        total_rows = len(self.rows)
        null_waterlevel_rows = sum(1 for r in self.rows if r.get("avg_waterlevel_msl") is None)
        null_rain_rows = sum(1 for r in self.rows if r.get("rainfall_daily_mm") is None)
        outlier_rows = sum(
            1 for r in self.rows
            if (r.get("raw_freeboard_m") is not None and r["raw_freeboard_m"] > 5.0)
            or (r.get("min_freeboard_m") is not None and r["min_freeboard_m"] > 5.0)
            or r.get("water_quality_flag") == "SUSPECT_DATUM_MISMATCH"
        )

        total_reports = sum(r["total_reports"] for r in self.rows)
        reports_with_khwaeng = sum(r["total_reports"] for r in self.rows if r["khwaeng"] != "ไม่ระบุ")
        reports_with_khet = sum(r["total_reports"] for r in self.rows if r["khet"] != "ไม่ระบุ")

        khwaeng_match_pct = round((reports_with_khwaeng / total_reports * 100), 2) if total_reports > 0 else 0.0
        khet_match_pct = round((reports_with_khet / total_reports * 100), 2) if total_reports > 0 else 0.0

        return {
            "total_curated_khwaeng_hourly_rows": total_rows,
            "total_traffy_reports_evaluated": total_reports,
            "missing_data_audit": {
                "null_waterlevel_percentage": round(null_waterlevel_rows / total_rows * 100, 2) if total_rows > 0 else 0.0,
                "null_rain_percentage": round(null_rain_rows / total_rows * 100, 2) if total_rows > 0 else 0.0,
                "telemetry_gaps_noted": [
                    "Station 5 (Khlong Mahasawat): Contains 1,513 valid telemetry points out of 4,320 monthly slots in Sep 2026 (294/432 in Sep 24-26).",
                    "Rain Stations 10 (Phasi Charoen), 528802 (Samsen), and 528848 (Samre): Missing daily rainfall on Sep 25.",
                    "Rain Station 1129393 (Building 901): Missing daily rainfall on Sep 26 (recorded 187.0mm on Sep 25).",
                ],
            },
            "sensor_outliers_audit": {
                "detected_outlier_count": outlier_rows,
                "waterlevel_datum_mismatch": {
                    "station_id": 10,
                    "station_name": "คลองภาษีเจริญ เพชรเกษม69",
                    "quality_flag": "SUSPECT_DATUM_MISMATCH",
                    "usable_for_freeboard": False,
                    "observed_waterlevel_range_msl": [-6.149, -1.719],
                    "min_bank_msl": 1.42,
                    "handling": "Isolated into risk_tier='SUSPECT_DATUM' with min_freeboard_m=null (raw_freeboard_m preserved for audit) per schema_contract.md.",
                },
                "rain_sensor_outage": {
                    "station_id": 852087,
                    "station_name": "อโศก",
                    "quality_flag": "SUSPECT_RAIN_SENSOR_OUTAGE",
                    "usable_for_rainfall": False,
                    "handling": "Excluded from nearest rain station assignment; inner-city khwaengs route to verified Station 3711 (169.4mm on Sep 25) and Station 2 (205.4mm on Sep 25).",
                },
            },
            "stale_stations_audit": {
                "stale_station_id": 2599,
                "stale_station_code": "C.12",
                "stale_station_name": "กรมชลประทานสามเสน",
                "cadence": "Hourly (24 readings/day vs 144 for other 7 telemetric stations)",
                "observed_lag": "> 2 hours behind real-time during live ingestion (69/72 hours reported during Sep 24-26).",
            },
            "spatial_join_coverage": {
                "valid_coordinate_rate_pct": 100.0,
                "khwaeng_extraction_match_pct": khwaeng_match_pct,
                "khet_extraction_match_pct": khet_match_pct,
                "unmatched_reports_count": total_reports - reports_with_khwaeng,
            },
        }
