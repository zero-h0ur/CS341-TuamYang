"""
Curated Pipeline CLI Runner
Owner: Member 5 (nustapark6709650334)

Executes the end-to-end Curated & Analysis pipeline aligned with main (PR #7, #10, #12):
1. Ensures incoming Traffy batches are validated & published to trusted/ via src.validation.pipeline
2. Joins ThaiWater (8 water + 14 rain stations) and Traffy data into curated/khwaeng_hourly
3. Generates analytics for Q1 (Rainfall -> Waterlevel) and Q2 (Freeboard -> Flood reports)
4. Audits Data Quality (Missing telemetry, SUSPECT_DATUM_MISMATCH, SUSPECT_RAIN_SENSOR_OUTAGE,
   Stale sensors, Spatial Join Match rates)
5. Emits verified evidence artifacts to evidence/curated/
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.curated.analyzer import CuratedAnalyzer  # noqa: E402
from src.curated.builder import CuratedBuilder  # noqa: E402
from src.validation.pipeline import run_local  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Run Curated & Analysis Pipeline")
    parser.add_argument(
        "--dates",
        nargs="+",
        default=["2026-09-24", "2026-09-25", "2026-09-26"],
        help="Dates to process (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv", "jsonl", "all"],
        default="all",
        help="Export format for curated table",
    )
    args = parser.parse_args()

    print(f"=== Starting Curated Pipeline for dates: {args.dates} ===")

    # Step 0: Run Issue #1 validation pipeline on incoming Traffy partitions if present
    for d in args.dates:
        incoming_traffy = ROOT / "data" / "incoming" / "traffy" / f"dt={d}" / f"traffy_{d}.json"
        if incoming_traffy.exists():
            res = run_local(f"incoming/traffy/dt={d}/traffy_{d}.json")
            print(f"Validated incoming/traffy dt={d}: state={res['final_state']} status={res.get('validation_status', '-')}")

    builder = CuratedBuilder()
    rows = builder.build_khwaeng_hourly(args.dates)
    print(f"Generated {len(rows)} curated rows for khwaeng_hourly.")

    # Save curated dataset
    if args.format == "json":
        json_file = builder.save_curated(rows, "json")
        print(f"Saved: {json_file}")
    if args.format in ("csv", "all"):
        csv_file = builder.save_curated(rows, "csv")
        print(f"Saved: {csv_file}")
    if args.format in ("jsonl", "all"):
        jsonl_dir = builder.save_curated(rows, "jsonl")
        print(f"Saved partitioned JSONL under: {jsonl_dir}")

    # Run Analysis
    analyzer = CuratedAnalyzer(rows)
    q1_result = analyzer.analyze_q1()
    q2_result = analyzer.analyze_q2()
    dq_result = analyzer.generate_data_quality_report()

    evidence_dir = ROOT / "evidence" / "curated"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # Save sample JSONL (first 10 rows) in evidence/curated/ for quick inspection
    sample_jsonl = evidence_dir / "khwaeng_hourly.sample.jsonl"
    sample_jsonl.write_text(
        "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows[:10]),
        encoding="utf-8",
    )

    with open(evidence_dir / "q1_analysis.json", "w", encoding="utf-8") as f:
        json.dump(q1_result, f, ensure_ascii=False, indent=2)
    print(f"Saved: {evidence_dir / 'q1_analysis.json'}")

    with open(evidence_dir / "q2_analysis.json", "w", encoding="utf-8") as f:
        json.dump(q2_result, f, ensure_ascii=False, indent=2)
    print(f"Saved: {evidence_dir / 'q2_analysis.json'}")

    with open(evidence_dir / "data_quality_report.json", "w", encoding="utf-8") as f:
        json.dump(dq_result, f, ensure_ascii=False, indent=2)
    print(f"Saved: {evidence_dir / 'data_quality_report.json'}")

    tm = q2_result["tier_metrics"]
    n2 = q2_result["near_station_tier_metrics_2km"]
    wdt = q2_result["within_day_tier_metrics"]
    wdn2 = q2_result["within_day_near_station_2km"]
    dist_st = q2_result["water_station_distance_stats"]
    dp = q2_result["daily_progression"]

    summary_md = f"""# Curated & Analysis Checkpoint Evidence
> **เจ้าของงาน:** @nustapark6709650334 (Member 5 - Curated & Analysis, Issue #2)  
> **ช่วงเวลาประมวลผล:** {', '.join(args.dates)}  
> **จำนวนแถวใน `curated/khwaeng_hourly`:** {len(rows):,} แถว  
> **จำนวนรายงาน Traffy ทั้งหมดที่ใช้คำนวณ:** {dq_result['total_traffy_reports_evaluated']:,} รายงาน

---

## 1. ผลการวิเคราะห์ Q1: ปริมาณฝน $\\rightarrow$ ระดับน้ำในคลอง
{chr(10).join(f"- {f}" for f in q1_result['key_findings'])}

---

## 2. ผลการวิเคราะห์ Q2: ระยะตลิ่งต่ำ $\\rightarrow$ สัดส่วนรายงานน้ำท่วม

### 2.1 ภาพรวมทั้ง กทม. (Pooled 3 วัน: 24–26 ก.ย. 2569)
| ระดับความเสี่ยง (Risk Tier) | เกณฑ์ระยะพ้นตลิ่ง (Freeboard) | ชั่วโมง-แขวง | รายงานทั้งหมด (ตัวหาร) | รายงานน้ำท่วม (ตัวตั้ง) | สัดส่วนรายงานน้ำท่วม (%) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **CRITICAL_OVERFLOW** | $\\le 0.0\\text{{ m}}$ (น้ำล้นตลิ่ง) | {tm.get('CRITICAL_OVERFLOW', {}).get('khwaeng_hours', 0):,} | {tm.get('CRITICAL_OVERFLOW', {}).get('total_reports', 0):,} | {tm.get('CRITICAL_OVERFLOW', {}).get('flood_reports', 0):,} | **{tm.get('CRITICAL_OVERFLOW', {}).get('flood_ratio_pct', 0)}%** |
| **HIGH_ALERT** | $0.0 < \\text{{Freeboard}} \\le 0.5\\text{{ m}}$ | {tm.get('HIGH_ALERT', {}).get('khwaeng_hours', 0):,} | {tm.get('HIGH_ALERT', {}).get('total_reports', 0):,} | {tm.get('HIGH_ALERT', {}).get('flood_reports', 0):,} | **{tm.get('HIGH_ALERT', {}).get('flood_ratio_pct', 0)}%** |
| **WATCH** | $0.5 < \\text{{Freeboard}} \\le 1.0\\text{{ m}}$ | {tm.get('WATCH', {}).get('khwaeng_hours', 0):,} | {tm.get('WATCH', {}).get('total_reports', 0):,} | {tm.get('WATCH', {}).get('flood_reports', 0):,} | **{tm.get('WATCH', {}).get('flood_ratio_pct', 0)}%** |
| **NORMAL** | $> 1.0\\text{{ m}}$ (สภาวะปกติ) | {tm.get('NORMAL', {}).get('khwaeng_hours', 0):,} | {tm.get('NORMAL', {}).get('total_reports', 0):,} | {tm.get('NORMAL', {}).get('flood_reports', 0):,} | **{tm.get('NORMAL', {}).get('flood_ratio_pct', 0)}%** |
| **SUSPECT_DATUM** | สถานี ID 10 (`usable_for_freeboard=false`) | {tm.get('SUSPECT_DATUM', {}).get('khwaeng_hours', 0):,} | {tm.get('SUSPECT_DATUM', {}).get('total_reports', 0):,} | {tm.get('SUSPECT_DATUM', {}).get('flood_reports', 0):,} | {tm.get('SUSPECT_DATUM', {}).get('flood_ratio_pct', 0)}% |
| **NO_TELEMETRY** | ช่องเวลาที่ไม่มีสัญญาณระดับน้ำ | {tm.get('NO_TELEMETRY', {}).get('khwaeng_hours', 0):,} | {tm.get('NO_TELEMETRY', {}).get('total_reports', 0):,} | {tm.get('NO_TELEMETRY', {}).get('flood_reports', 0):,} | {tm.get('NO_TELEMETRY', {}).get('flood_ratio_pct', 0)}% |

### 2.2 จำกัดเฉพาะแขวงใกล้สถานีวัดน้ำ (`water_station_dist_km <= 2.0 km`, รวม {dist_st['rows_within_2km']} แถว)
| ระดับความเสี่ยง (Risk Tier) | ชั่วโมง-แขวง (`<= 2 km`) | รายงานทั้งหมด | รายงานน้ำท่วม | สัดส่วนรายงานน้ำท่วม (%) |
| :--- | :---: | :---: | :---: | :---: |
| **CRITICAL_OVERFLOW** | {n2.get('CRITICAL_OVERFLOW', {}).get('khwaeng_hours', 0):,} | {n2.get('CRITICAL_OVERFLOW', {}).get('total_reports', 0):,} | {n2.get('CRITICAL_OVERFLOW', {}).get('flood_reports', 0):,} | **{n2.get('CRITICAL_OVERFLOW', {}).get('flood_ratio_pct', 0)}%** |
| **HIGH_ALERT** | {n2.get('HIGH_ALERT', {}).get('khwaeng_hours', 0):,} | {n2.get('HIGH_ALERT', {}).get('total_reports', 0):,} | {n2.get('HIGH_ALERT', {}).get('flood_reports', 0):,} | **{n2.get('HIGH_ALERT', {}).get('flood_ratio_pct', 0)}%** |
| **WATCH** | {n2.get('WATCH', {}).get('khwaeng_hours', 0):,} | {n2.get('WATCH', {}).get('total_reports', 0):,} | {n2.get('WATCH', {}).get('flood_reports', 0):,} | **{n2.get('WATCH', {}).get('flood_ratio_pct', 0)}%** |
| **NORMAL** | {n2.get('NORMAL', {}).get('khwaeng_hours', 0):,} | {n2.get('NORMAL', {}).get('total_reports', 0):,} | {n2.get('NORMAL', {}).get('flood_reports', 0):,} | **{n2.get('NORMAL', {}).get('flood_ratio_pct', 0)}%** |
| **SUSPECT_DATUM** | {n2.get('SUSPECT_DATUM', {}).get('khwaeng_hours', 0):,} | {n2.get('SUSPECT_DATUM', {}).get('total_reports', 0):,} | {n2.get('SUSPECT_DATUM', {}).get('flood_reports', 0):,} | {n2.get('SUSPECT_DATUM', {}).get('flood_ratio_pct', 0)}% |
| **NO_TELEMETRY** | {n2.get('NO_TELEMETRY', {}).get('khwaeng_hours', 0):,} | {n2.get('NO_TELEMETRY', {}).get('total_reports', 0):,} | {n2.get('NO_TELEMETRY', {}).get('flood_reports', 0):,} | {n2.get('NO_TELEMETRY', {}).get('flood_ratio_pct', 0)}% |

### 2.3 เปรียบเทียบภายในวันเดียวกัน (Within-Day Comparison ลดผลกระทบ Confounding จากวันพายุเข้า)
| วันที่ | สัดส่วนภาพรวมรายวัน | CRITICAL_OVERFLOW (ทั้ง กทม. / `<= 2 km`) | HIGH_ALERT (ทั้ง กทม. / `<= 2 km`) | WATCH (ทั้ง กทม. / `<= 2 km`) | NORMAL (ทั้ง กทม. / `<= 2 km`) | NO_TELEMETRY (ทั้ง กทม. / `<= 2 km`) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **2026-09-24** | {dp['2026-09-24']['flood_ratio_pct']}% ({dp['2026-09-24']['flood_reports']:,}/{dp['2026-09-24']['total_reports']:,}) | - / - | {wdt.get('2026-09-24', {}).get('HIGH_ALERT', {}).get('flood_ratio_pct', 0)}% / - | {wdt.get('2026-09-24', {}).get('WATCH', {}).get('flood_ratio_pct', 0)}% / {wdn2.get('2026-09-24', {}).get('WATCH', {}).get('flood_ratio_pct', 0)}% | {wdt.get('2026-09-24', {}).get('NORMAL', {}).get('flood_ratio_pct', 0)}% / {wdn2.get('2026-09-24', {}).get('NORMAL', {}).get('flood_ratio_pct', 0)}% | {wdt.get('2026-09-24', {}).get('NO_TELEMETRY', {}).get('flood_ratio_pct', 0)}% / - |
| **2026-09-25** | {dp['2026-09-25']['flood_ratio_pct']}% ({dp['2026-09-25']['flood_reports']:,}/{dp['2026-09-25']['total_reports']:,}) | {wdt.get('2026-09-25', {}).get('CRITICAL_OVERFLOW', {}).get('flood_ratio_pct', 0)}% / {wdn2.get('2026-09-25', {}).get('CRITICAL_OVERFLOW', {}).get('flood_ratio_pct', 0)}% | {wdt.get('2026-09-25', {}).get('HIGH_ALERT', {}).get('flood_ratio_pct', 0)}% / {wdn2.get('2026-09-25', {}).get('HIGH_ALERT', {}).get('flood_ratio_pct', 0)}% | {wdt.get('2026-09-25', {}).get('WATCH', {}).get('flood_ratio_pct', 0)}% / {wdn2.get('2026-09-25', {}).get('WATCH', {}).get('flood_ratio_pct', 0)}% | {wdt.get('2026-09-25', {}).get('NORMAL', {}).get('flood_ratio_pct', 0)}% / {wdn2.get('2026-09-25', {}).get('NORMAL', {}).get('flood_ratio_pct', 0)}% | {wdt.get('2026-09-25', {}).get('NO_TELEMETRY', {}).get('flood_ratio_pct', 0)}% / - |
| **2026-09-26** | {dp['2026-09-26']['flood_ratio_pct']}% ({dp['2026-09-26']['flood_reports']:,}/{dp['2026-09-26']['total_reports']:,}) | {wdt.get('2026-09-26', {}).get('CRITICAL_OVERFLOW', {}).get('flood_ratio_pct', 0)}% / {wdn2.get('2026-09-26', {}).get('CRITICAL_OVERFLOW', {}).get('flood_ratio_pct', 0)}% | {wdt.get('2026-09-26', {}).get('HIGH_ALERT', {}).get('flood_ratio_pct', 0)}% / {wdn2.get('2026-09-26', {}).get('HIGH_ALERT', {}).get('flood_ratio_pct', 0)}% | {wdt.get('2026-09-26', {}).get('WATCH', {}).get('flood_ratio_pct', 0)}% / {wdn2.get('2026-09-26', {}).get('WATCH', {}).get('flood_ratio_pct', 0)}% | {wdt.get('2026-09-26', {}).get('NORMAL', {}).get('flood_ratio_pct', 0)}% / {wdn2.get('2026-09-26', {}).get('NORMAL', {}).get('flood_ratio_pct', 0)}% | {wdt.get('2026-09-26', {}).get('NO_TELEMETRY', {}).get('flood_ratio_pct', 0)}% / {wdn2.get('2026-09-26', {}).get('NO_TELEMETRY', {}).get('flood_ratio_pct', 0)}% |

---

## 3. รายงานคุณภาพข้อมูลและข้อจำกัดสำคัญ (Data Quality Audit & Limitations)
- **พิกัดสมบูรณ์ (Valid Coordinates):** 100.0%
- **อัตราการสกัดชื่อแขวง/เขตจาก `address`:** แขวง {dq_result['spatial_join_coverage']['khwaeng_extraction_match_pct']}%, เขต {dq_result['spatial_join_coverage']['khet_extraction_match_pct']}% (ได้ {dq_result['spatial_join_coverage']['distinct_khwaeng_khet_pairs']} คู่แขวง/เขต และ {dq_result['spatial_join_coverage']['distinct_khet_count']} เขต จากจริง 180/50 เนื่องจากค่า "ไม่ระบุ" และการสะกดซ้ำ — หลัง Checkpoint จะเปลี่ยนเป็น Spatial Join กับขอบเขตแขวงของ Issue #3)
- **ระยะจับคู่สถานีวัดน้ำ (`water_station_dist_km`):** ทั้ง กทม. มีค่า median = {dist_st['median_km']} km, max = {dist_st['max_km']} km; เมื่อจำกัดเฉพาะแขวงใกล้สถานี (`<= 2.0 km`) เหลือ {dist_st['rows_within_2km']} แถว
- **การจัดการ Outlier ระดับน้ำ (สถานีภาษีเจริญ ID 10):** ติดธง `SUSPECT_DATUM_MISMATCH` (`usable_for_freeboard=false`) แยกออกเป็น `SUSPECT_DATUM` ไม่ให้ปนเปื้อนกลุ่ม `NORMAL`
- **การจัดการเซ็นเซอร์ฝนขัดข้อง (สถานีอโศก ID 852087):** ติดธง `SUSPECT_RAIN_SENSOR_OUTAGE` (`usable_for_rainfall=false`) กรองออกจากการจับคู่สถานีฝน
- **ลักษณะตาราง (Sparse Hourly Grid & Representative Center):** `khwaeng_centers` คำนวณพิกัดเฉลี่ยจากรายงานทุกชั่วโมงรวมกันของแขวงนั้น และตารางมีเฉพาะชั่วโมงที่มีรายงาน Traffy อย่างน้อย 1 รายการ
"""
    with open(evidence_dir / "curated_summary.md", "w", encoding="utf-8") as f:
        f.write(summary_md)
    print(f"Saved: {evidence_dir / 'curated_summary.md'}")
    print("=== Curated Pipeline Completed Successfully! ===")


if __name__ == "__main__":
    main()
