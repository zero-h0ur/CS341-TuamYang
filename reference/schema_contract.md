# ThaiWater Data Contract & Interface Specification
> **เจ้าของเอกสาร:** สมาชิกคนที่ 1 (ThaiWater Ingestion)  
> **กลุ่มเป้าหมาย:** สมาชิกคนที่ 3 (Reference & Spatial Join), สมาชิกคนที่ 4 (Orchestration & Validation), สมาชิกคนที่ 5 (Curated & Analysis)

---

## 1. วัตถุประสงค์
เอกสารฉบับนี้กำหนดสัญญาข้อมูล (Data Contract) โครงสร้างไดเรกทอรี รูปแบบไฟล์ และนิยามตัวแปรทั้งหมดที่สร้างจากขั้นตอน **ThaiWater Ingestion (`ingest_thaiwater`)** เพื่อให้สมาชิกทีมคนอื่นสามารถนำไปประมวลผลต่อ (Spatial join, Validate, Transform, Merge into Iceberg) ได้ทันทีโดยไม่เกิด Breaking Change

---

## 2. โครงสร้าง Storage Partition (Dual-mode: S3 / Local)

ไม่ว่าจะรันบน **AWS S3** (`s3://<bucket>/...`) หรือ **Local Filesystem** (`data/...`) จะใช้ Path มาตรฐานเดียวกันดังนี้:

```text
raw/
└── thaiwater/
    ├── waterlevel/
    │   └── dt=YYYY-MM-DD/
    │       ├── hour=HH/
    │       │   └── waterlevel_batch_{batch_id}_{timestamp}.json    # ดึงสดทุก 15 นาที
    │       └── waterlevel_hist_{station_id}_{year_month}.json     # ข้อมูล Backfill รายเดือน
    └── rain/
        └── dt=YYYY-MM-DD/
            ├── hour=HH/
            │   └── rain_24h_batch_{batch_id}_{timestamp}.json       # ดึงสดทุก 1 ชม.
            └── rain_hist_{station_id}_{year_month}.json           # ข้อมูล Backfill รายเดือน
```

---

## 3. รูปแบบ Raw Data Ingestion Envelope (JSON)

เพื่อรองรับการตรวจสอบ Data Freshness, Row Count, และ Idempotency ของสมาชิกคนที่ 4 (Step Functions) ทุกไฟล์ JSON ในชั้น Raw จะถูกห่อหุ้มด้วย Ingestion Envelope:

```json
{
  "envelope_version": "1.0",
  "batch_id": "tw-wl-20261031-153000-a1b2",
  "data_type": "waterlevel", 
  "source_endpoint": "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load",
  "ingested_at_utc": "2026-10-31T08:30:00Z",
  "ingested_at_bkk": "2026-10-31T15:30:00+07:00",
  "station_count": 8,
  "record_count": 8,
  "data": [
    {
      "station_id": "BKK001",
      "station_code": "CPY_PKL",
      "timestamp_bkk": "2026-10-31 15:20:00",
      "timestamp_iso": "2026-10-31T15:20:00+07:00",
      "timestamp_epoch": 1793434800,
      "waterlevel_msl": 1.95,
      "min_bank_msl": 2.80,
      "diff_wl_bank_m": 0.85,
      "raw_api_payload": { ... }
    }
  ]
}
```

---

## 4. ตัวแปรและฟิลด์มาตรฐาน (Field Definitions & Units)

### 4.1 ข้อมูลระดับน้ำ (Water Level)
| ชื่อฟิลด์ | ชนิดข้อมูล | หน่วย | คำอธิบาย |
| :--- | :--- | :--- | :--- |
| `station_id` | String | - | รหัสสถานีอ้างอิง เช่น `BKK001` - `BKK008` |
| `station_code` | String | - | รหัสย่อ เช่น `CPY_PKL`, `KL_MSW` |
| `station_name_th` | String | - | ชื่อสถานีภาษาไทย |
| `timestamp_iso` | String (ISO-8601) | - | เวลาตรวจวัดของสถานีในรูปแบบ `YYYY-MM-DDTHH:MM:SS+07:00` |
| `timestamp_epoch` | Long | วินาที | Unix Epoch Time (ใช้สำหรับ Join/Sort ข้าม Timezone ได้แม่นยำ) |
| `waterlevel_msl` | Float | เมตร (ม.รทก.) | ระดับน้ำเทียบกับระดับน้ำทะเลปานกลาง (MSL) |
| `min_bank_msl` | Float | เมตร (ม.รทก.) | ระดับความสูงสันตลิ่งขั้นต่ำ |
| `diff_wl_bank_m` | Float | เมตร | ระยะตลิ่งคงเหลือ (Freeboard) คำนวณจาก `min_bank_msl - waterlevel_msl` (ยิ่งน้อยหรือติดลบ = น้ำท่วม) |

### 4.2 ข้อมูลปริมาณฝน (Rainfall)
| ชื่อฟิลด์ | ชนิดข้อมูล | หน่วย | คำอธิบาย |
| :--- | :--- | :--- | :--- |
| `station_id` | String | - | รหัสสถานีวัดฝน เช่น `RAIN_BKK_001` |
| `timestamp_iso` | String (ISO-8601) | - | เวลาที่วัดปริมาณฝน |
| `rain_24h_mm` | Float | มม. (mm) | ปริมาณน้ำฝนสะสม 24 ชั่วโมงล่าสุด |
| `rain_1h_mm` | Float | มม. (mm) | ปริมาณน้ำฝนสะสมในรอบ 1 ชั่วโมง (คำนวณจาก Delta ของสะสม หรือ API) |
| `rain_daily_mm` | Float | มม. (mm) | ปริมาณฝนรวมรายวัน (สำหรับการ Backfill ย้อนหลัง) |

---

## 5. การส่งต่องานให้สมาชิกคนอื่น (Downstream Handoff)

1. **สำหรับสมาชิกคนที่ 3 (Reference + Spatial Join):**
   - ใช้งานพิกัดสถานีจาก [`reference/stations_water.json`](stations_water.json) (8 สถานี) และ [`reference/stations_rain.json`](stations_rain.json) (134 สถานี)
   - ใช้ฟิลด์ `latitude` และ `longitude` ในการรัน SQL `ST_Contains` หรือ `ST_Distance` ร่วมกับ Shapefile ขอบเขตแขวง 180 แขวง
2. **สำหรับสมาชิกคนที่ 4 (Orchestration & Validation):**
   - Step Functions ตรวจสอบไฟล์ใหม่ผ่าน EventBridge `ObjectCreated` หรือ S3 prefix `raw/thaiwater/`
   - ตรวจสอบ `record_count > 0`
   - ตรวจสอบ Data Freshness: `ingested_at_bkk` ห่างจาก `timestamp_bkk` เกิน 1 ชั่วโมงหรือไม่ (หากเกินให้แจ้ง Warning)
   - ตรวจสอบ Outlier: เช่น กรณีคลองภาษีเจริญ (`BKK008`) ที่เคยรายงานระยะตลิ่งผิดปกติ 7.42 เมตร
3. **สำหรับสมาชิกคนที่ 5 (Curated & Analysis):**
   - ใช้ข้อมูลจาก `diff_wl_bank_m` ร่วมกับรายงาน Traffy ในช่วงเหตุการณ์วันที่ 24–26 ก.ย. 2569 เพื่อตอบคำถาม Q1-Q2

