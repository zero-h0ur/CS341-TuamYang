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
  - **Q2:** สัดส่วนเรื่องร้องเรียนน้ำท่วมเพิ่มขึ้นเป็นขั้นบันไดตามความเสี่ยงระยะพ้นตลิ่ง: `NORMAL` (**25.47%**) $\rightarrow$ `WATCH` (**56.03%**) $\rightarrow$ `HIGH_ALERT` (**84.36%**) $\rightarrow$ `CRITICAL_OVERFLOW` (**86.72%**)
  - **Data Quality:** พิกัดสมบูรณ์ 100%, สกัดชื่อแขวงสำเร็จ 99.07%, แยก `SUSPECT_DATUM_MISMATCH` (ID 10) และ `SUSPECT_RAIN_SENSOR_OUTAGE` (ID 852087) ครบถ้วน
- **Unit Tests:** เพิ่มชุดทดสอบ `tests/test_curated.py` (7 tests) ทดสอบผ่าน **100% (134/134 tests ทั่วทั้งโปรเจกต์)** โดยไม่พึ่งพาไฟล์ PII ที่ถูก gitignore

## Deliverables & Evidence
- ตารางผลลัพธ์: `data/curated/khwaeng_hourly/` (`dt=2026-09-{24,25,26}/khwaeng_hourly.jsonl` และ `khwaeng_hourly.csv`, รวม 3,972 แถว)
- ไฟล์หลักฐานการรัน: `evidence/curated/q1_analysis.json`, `q2_analysis.json`, `data_quality_report.json`, `curated_summary.md`, `khwaeng_hourly.sample.jsonl`
- เอกสารสรุปผลฉบับสมบูรณ์: `docs/curated_checkpoint.md`
