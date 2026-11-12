## Summary
เพิ่มระบบประมวลผล Curated Data Mart รายแขวง–รายชั่วโมง (`curated/khwaeng_hourly`), สคริปต์ Glue / Athena SQL สำหรับโซน `trusted.*` และ `curated.khwaeng_hourly`, แบบสอบถามวิเคราะห์ตอบคำถามวิจัย Q1 (ฝน $\rightarrow$ ระดับน้ำ) และ Q2 (ระยะตลิ่งต่ำ $\rightarrow$ รายงานน้ำท่วม) พร้อมจัดทำ Data Quality Report ที่สอดคล้องกับ `main` ล่าสุด (PR #7, PR #10, PR #12)

## Related Issue
Closes #2 — [Curated + Analysis] ตารางรายแขวง รายชั่วโมง และ query ตอบ Q1–Q2  
Owner: @nustapark6709650334 (นายณัฐภาคย์ ไกรวิชญ์ชนาพร)

## Changes
- **เชื่อมต่อกับโซน `trusted/` (PR #12) และ Data Contract ล่าสุด (PR #7, #10)** —
  - รองรับการอ่าน JSONL จาก `trusted/{waterlevel,rain,traffy}/dt=YYYY-MM-DD/` และ Historical Backfill
  - Deduplicate ด้วยคีย์ `(api_station_id, timestamp_epoch)` และกติกา **`latest batch wins`** สำหรับ Sliding Window 24 ชม. ของข้อมูลฝน
  - เคารพธง `usable_for_freeboard=false` ของสถานีภาษีเจริญ (ID 10 `SUSPECT_DATUM_MISMATCH`) โดยแยกออกเป็น `SUSPECT_DATUM` ไม่ให้บิดเบือนกลุ่ม `NORMAL`
  - เคารพธง `usable_for_rainfall=false` ของสถานีวัดฝนอโศก (ID 852087 `SUSPECT_RAIN_SENSOR_OUTAGE`) โดยกรองออกจากการจับคู่สถานีฝนใกล้เคียง
  - คงตัวหารทั้งหมดของรายงาน Traffy Fondue (`total_reports`) สำหรับคำนวณสัดส่วน Q2 (`src/curated/builder.py`)
- **เขียน SQL Athena สำหรับ Glue Catalog, การสร้างตาราง และการวิเคราะห์** —
  - `sql/create_khwaeng_hourly.sql`: DDL สำหรับ `trusted.waterlevel`, `trusted.rain`, `trusted.traffy`, `curated.khwaeng_hourly` และ Transformation Query
  - `sql/analysis_q1_q2.sql`: Query ตอบโจทย์ Q1 และ Q2 อย่างเป็นระบบ
  - `sql/data_quality_checks.sql`: Query ตรวจสอบคุณภาพข้อมูล (Missing, Outliers/Datum Mismatch, Stale data, Join rate)
- **ผลการวิเคราะห์กรณีศึกษา 24–26 ก.ย. 2569 (จาก 9,614 รายงาน Traffy, 3,972 แถว Curated)** —
  - **Q1:** พายุฝนหนักสุดวันที่ 25 ก.ย. (สูงสุด 230.2 mm ที่วัดบางบัว, 205.4 mm ที่บางกะปิ) ทำให้คลองหกวาล้นตลิ่งทันที (-0.069 m) และเกิด Lag Time ของน้ำหลากตอนบนทำให้คลองลาดพร้าวขึ้นแตะจุดสูงสุด 2.824 m MSL (ล้นตลิ่ง 0.624 m) ในวันที่ 26 ก.ย.
  - **Q2:**
    - ภาพรวมทั้ง กทม. (Pooled 3 วัน): `NORMAL` (**22.67%**) $\rightarrow$ `WATCH` (**54.06%**) $\rightarrow$ `HIGH_ALERT` (**84.23%**) $\rightarrow$ `CRITICAL_OVERFLOW` (**86.92%**) และ `NO_TELEMETRY` (**88.07%**) โดยระบุข้อจำกัดเรื่อง **Temporal Confounding จากวันพายุฝน** (สัดส่วนรายวัน 10.27% $\rightarrow$ 70.86% $\rightarrow$ 85.84%)
    - เมื่อจำกัดเฉพาะแขวงใกล้สถานี (`water_station_dist_km <= 2.0 km`, 372 แถว): `CRITICAL_OVERFLOW` (**82.78%**, 89 แถว) > `HIGH_ALERT` (**71.19%**, 74 แถว) > `WATCH` (**33.78%**, 96 แถว) > `NORMAL` (**16.28%**, 71 แถว) และเปรียบเทียบภายในวันเดียวกัน (Within-day) ในวันที่ 26 ก.ย. (`CRITICAL_OVERFLOW` 85.83% vs `NORMAL` 11.76%)
  - **Data Quality & Limitations:** พิกัดสมบูรณ์ 100%, สกัดชื่อแขวงจาก `address` สำเร็จ 99.07% (ได้ 237 คู่แขวง/เขต, 51 เขต จากจริง 180/50 — เตรียมเปลี่ยนเป็น Spatial Join กับ Polygon ของ Issue #3 หลัง Checkpoint), คำนวณ `khwaeng_centers` จากพิกัดเฉลี่ยทุกชั่วโมงรวมกัน และระบุลักษณะ Sparse Grid (เฉพาะชั่วโมงที่มีรายงาน $\ge 1$ รายการ)
- **Unit Tests:** เพิ่มชุดทดสอบ `tests/test_curated.py` (8 tests) ทดสอบผ่าน **100% (135/135 tests ทั่วทั้งโปรเจกต์)** โดยไม่พึ่งพาไฟล์ PII ที่ถูก gitignore

## Deliverables & Evidence
- ตารางผลลัพธ์: `data/curated/khwaeng_hourly/` (`dt=2026-09-{24,25,26}/khwaeng_hourly.jsonl` และ `khwaeng_hourly.csv`, รวม 3,972 แถว)
- ไฟล์หลักฐานการรัน: `evidence/curated/q1_analysis.json`, `q2_analysis.json`, `data_quality_report.json`, `curated_summary.md`, `khwaeng_hourly.sample.jsonl`
- เอกสารสรุปผลฉบับสมบูรณ์: `docs/curated_checkpoint.md`
