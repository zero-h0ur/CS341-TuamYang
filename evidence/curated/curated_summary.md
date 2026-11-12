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

### 2.1 ภาพรวมทั้ง กทม. (Pooled 3 วัน: 24–26 ก.ย. 2569)
| ระดับความเสี่ยง (Risk Tier) | เกณฑ์ระยะพ้นตลิ่ง (Freeboard) | ชั่วโมง-แขวง | รายงานทั้งหมด (ตัวหาร) | รายงานน้ำท่วม (ตัวตั้ง) | สัดส่วนรายงานน้ำท่วม (%) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **CRITICAL_OVERFLOW** | $\le 0.0\text{ m}$ (น้ำล้นตลิ่ง) | 861 | 2,615 | 2,273 | **86.92%** |
| **HIGH_ALERT** | $0.0 < \text{Freeboard} \le 0.5\text{ m}$ | 1,032 | 3,050 | 2,569 | **84.23%** |
| **WATCH** | $0.5 < \text{Freeboard} \le 1.0\text{ m}$ | 909 | 1,861 | 1,006 | **54.06%** |
| **NORMAL** | $> 1.0\text{ m}$ (สภาวะปกติ) | 610 | 825 | 187 | **22.67%** |
| **SUSPECT_DATUM** | สถานี ID 10 (`usable_for_freeboard=false`) | 381 | 651 | 447 | 68.66% |
| **NO_TELEMETRY** | ช่องเวลาที่ไม่มีสัญญาณระดับน้ำ | 179 | 612 | 539 | 88.07% |

### 2.2 จำกัดเฉพาะแขวงใกล้สถานีวัดน้ำ (`water_station_dist_km <= 2.0 km`, รวม 372 แถว)
| ระดับความเสี่ยง (Risk Tier) | ชั่วโมง-แขวง (`<= 2 km`) | รายงานทั้งหมด | รายงานน้ำท่วม | สัดส่วนรายงานน้ำท่วม (%) |
| :--- | :---: | :---: | :---: | :---: |
| **CRITICAL_OVERFLOW** | 89 | 180 | 149 | **82.78%** |
| **HIGH_ALERT** | 74 | 118 | 84 | **71.19%** |
| **WATCH** | 96 | 148 | 50 | **33.78%** |
| **NORMAL** | 71 | 86 | 14 | **16.28%** |
| **SUSPECT_DATUM** | 30 | 44 | 32 | 72.73% |
| **NO_TELEMETRY** | 12 | 22 | 16 | 72.73% |

### 2.3 เปรียบเทียบภายในวันเดียวกัน (Within-Day Comparison ลดผลกระทบ Confounding จากวันพายุเข้า)
| วันที่ | สัดส่วนภาพรวมรายวัน | CRITICAL_OVERFLOW (ทั้ง กทม. / `<= 2 km`) | HIGH_ALERT (ทั้ง กทม. / `<= 2 km`) | WATCH (ทั้ง กทม. / `<= 2 km`) | NORMAL (ทั้ง กทม. / `<= 2 km`) | NO_TELEMETRY (ทั้ง กทม. / `<= 2 km`) |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **2026-09-24** | 10.27% (113/1,100) | - / - | 0.0% / - | 12.39% / 17.78% | 7.46% / 5.26% | 0.0% / - |
| **2026-09-25** | 70.86% (1,894/2,673) | 79.61% / 75.47% | 80.26% / 67.86% | 58.28% / 29.82% | 24.02% / 32.26% | 92.31% / - |
| **2026-09-26** | 85.84% (5,014/5,841) | 89.21% / 85.83% | 86.52% / 74.19% | 79.97% / 54.35% | 57.89% / 11.76% | 87.78% / 72.73% |

---

## 3. รายงานคุณภาพข้อมูลและข้อจำกัดสำคัญ (Data Quality Audit & Limitations)
- **พิกัดสมบูรณ์ (Valid Coordinates):** 100.0%
- **อัตราการสกัดชื่อแขวง/เขตจาก `address`:** แขวง 99.07%, เขต 89.55% (ได้ 237 คู่แขวง/เขต และ 51 เขต จากจริง 180/50 เนื่องจากค่า "ไม่ระบุ" และการสะกดซ้ำ — หลัง Checkpoint จะเปลี่ยนเป็น Spatial Join กับขอบเขตแขวงของ Issue #3)
- **ระยะจับคู่สถานีวัดน้ำ (`water_station_dist_km`):** ทั้ง กทม. มีค่า median = 5.26 km, max = 20.33 km; เมื่อจำกัดเฉพาะแขวงใกล้สถานี (`<= 2.0 km`) เหลือ 372 แถว
- **การจัดการ Outlier ระดับน้ำ (สถานีภาษีเจริญ ID 10):** ติดธง `SUSPECT_DATUM_MISMATCH` (`usable_for_freeboard=false`) แยกออกเป็น `SUSPECT_DATUM` ไม่ให้ปนเปื้อนกลุ่ม `NORMAL`
- **การจัดการเซ็นเซอร์ฝนขัดข้อง (สถานีอโศก ID 852087):** ติดธง `SUSPECT_RAIN_SENSOR_OUTAGE` (`usable_for_rainfall=false`) กรองออกจากการจับคู่สถานีฝน
- **ลักษณะตาราง (Sparse Hourly Grid & Representative Center):** `khwaeng_centers` คำนวณพิกัดเฉลี่ยจากรายงานทุกชั่วโมงรวมกันของแขวงนั้น และตารางมีเฉพาะชั่วโมงที่มีรายงาน Traffy อย่างน้อย 1 รายการ
