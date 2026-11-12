# Curated & Analysis Checkpoint Evidence
> **เจ้าของงาน:** @nustapark6709650334 (Member 5 - Curated & Analysis, Issue #2)  
> **ช่วงเวลาประมวลผล:** 2026-09-24, 2026-09-25, 2026-09-26  
> **จำนวนแถวใน `curated/khwaeng_hourly`:** 3,972 แถว  
> **จำนวนรายงาน Traffy ทั้งหมดที่ใช้คำนวณ:** 9,614 รายงาน

---

## 1. ผลการวิเคราะห์ Q1: ปริมาณฝน $\rightarrow$ ระดับน้ำในคลอง
- Sep 24 (Pre-event): Moderate to heavy localized rain (up to 111.6mm at Lat Krabang Station 3, 71.6mm at Khlong Hok Wa Station 8). All usable canals retained positive freeboard.
- Sep 25 (Peak Storm Event): Extreme citywide rainfall (230.2mm at Bang Bua Station 1, 205.4mm at Bang Kapi Station 2, 200.6mm at Bang Na Station 3709). Rapid surge triggered immediate canal overflow at Khlong Hok Wa (Station 8, freeboard -0.069m) and Khlong Mahasawat (Station 5, freeboard -0.221m).
- Sep 26 (Lag Time & Upstream Runoff): Despite lower daily rainfall (20-76mm), accumulated upstream runoff caused severe backwater and peak canal levels: Khlong Ladprao (Station 1) surged to 2.824m MSL (exceeding 2.20m bank by 0.624m), Khlong Mahasawat (Station 5) reached 2.391m (-0.322m freeboard), Khlong Hok Wa (Station 8) reached 2.179m (-0.209m freeboard), and Khlong Lam Pla Thio (Station 3) reached 0.627m (-0.004m freeboard).

---

## 2. ผลการวิเคราะห์ Q2: ระยะตลิ่งต่ำ $\rightarrow$ สัดส่วนรายงานน้ำท่วม

| ระดับความเสี่ยง (Risk Tier) | เกณฑ์ระยะพ้นตลิ่ง (Freeboard) | ชั่วโมง-แขวง | รายงานทั้งหมด (ตัวหาร) | รายงานน้ำท่วม (ตัวตั้ง) | สัดส่วนรายงานน้ำท่วม (%) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **CRITICAL_OVERFLOW** | $\le 0.0\text{ m}$ (น้ำล้นตลิ่ง) | 912 | 2,710 | 2,350 | **86.72%** |
| **HIGH_ALERT** | $0.0 < \text{Freeboard} \le 0.5\text{ m}$ | 940 | 2,845 | 2,400 | **84.36%** |
| **WATCH** | $0.5 < \text{Freeboard} \le 1.0\text{ m}$ | 952 | 1,956 | 1,096 | **56.03%** |
| **NORMAL** | $> 1.0\text{ m}$ (สภาวะปกติ) | 661 | 911 | 232 | **25.47%** |
| **SUSPECT_DATUM** | สถานี ID 10 (`usable_for_freeboard=false`) | 319 | 543 | 367 | 67.59% |
| **NO_TELEMETRY** | ช่องเวลาที่ไม่มีสัญญาณระดับน้ำ | 188 | 649 | 576 | 88.75% |

### พัฒนาการรายวัน (24–26 ก.ย. 2569)
| วันที่ | รายงานทั้งหมด | รายงานน้ำท่วม | สัดส่วน (%) | จำนวนชั่วโมงแขวงที่น้ำล้นตลิ่ง |
| :---: | :---: | :---: | :---: | :---: |
| 2026-09-24 | 1,100 | 113 | 10.27% | 0 |
| 2026-09-25 | 2,673 | 1,894 | 70.86% | 322 |
| 2026-09-26 | 5,841 | 5,014 | 85.84% | 590 |

---

## 3. รายงานคุณภาพข้อมูล (Data Quality Audit)
- **พิกัดสมบูรณ์ (Valid Coordinates):** 100.0%
- **อัตราการสกัดชื่อแขวงสำเร็จ (% Khwaeng Extraction):** 99.07%
- **อัตราการสกัดชื่อเขตสำเร็จ (% Khet Extraction):** 89.55%
- **การจัดการ Outlier ระดับน้ำ (สถานีภาษีเจริญ ID 10):** ติดธง `SUSPECT_DATUM_MISMATCH` (`usable_for_freeboard=false`) แยกออกเป็น `SUSPECT_DATUM` ไม่ให้ปนเปื้อนกลุ่ม `NORMAL`
- **การจัดการเซ็นเซอร์ฝนขัดข้อง (สถานีอโศก ID 852087):** ติดธง `SUSPECT_RAIN_SENSOR_OUTAGE` (`usable_for_rainfall=false`) กรองออกจากการจับคู่สถานีฝนและเชื่อมโยงไปยังสถานี 3711 และสถานี 2 แทน
- **สถานีล่าช้า (Stale Station C.12):** รายงานความถี่รายชั่วโมงและพบดีเลย์ในการส่งข้อมูลจริง
