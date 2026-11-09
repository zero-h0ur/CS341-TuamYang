# คู่มือการทดสอบระบบ ThaiWater Ingestion (Verification Runbook สำหรับตรวจรับ PR)

เอกสารฉบับนี้รวบรวมขั้นตอนและชุดคำสั่งสำหรับรันบน **AWS CloudShell** และบนเครื่อง Local Workspace เพื่อตรวจสอบความสมบูรณ์และถูกต้องของงาน **ThaiWater Ingestion Engine** (Issue: Ingest ThaiWater Data) ทั้งระบบ Live Ingestion (Serverless Lambda + EventBridge), Dual-Zone S3 Storage, Historical Backfill (10-Minute Resolution), และ Checkpoint Resiliency

---

## สรุปเกณฑ์การผ่าน PR (Acceptance Criteria Matrix)

| หมวดหมู่การทดสอบ | เกณฑ์การผ่าน (Acceptance Criteria) | วิธีการตรวจวัด | สถานะ |
| :--- | :--- | :--- | :---: |
| **1. Unit Tests** | ผ่านครบทุก Test (16/16 tests pass) ไม่พบ Error/Failure | `python3 -m unittest tests/test_ingest_thaiwater.py` | PASS |
| **2. EventBridge Scheduler** | มี 2 Rules สถานะ `ENABLED` ดึงระดับน้ำทุก 15 นาที และฝนทุก 1 ชม. | `aws events list-rules --name-prefix tuamyang` | PASS |
| **3. Live Ingestion Lambda** | รันผ่านตรงตามเวลาจริง บันทึกไฟล์ลงทั้ง `incoming/` และ `raw/` | `aws lambda invoke` ทดสอบทั้ง `waterlevel` และ `rain` | PASS |
| **4. Dual-Zone S3 Storage** | มีไฟล์ปรากฏใน `incoming/` และสำเนาถาวรใน `raw/` ตาม Partition `dt=YYYY-MM-DD` | `aws s3 ls` ตรวจสอบทั้ง 2 โซน | PASS |
| **5. 10-Min Telemetry Backfill** | ความละเอียดระดับน้ำ $\Delta t = 10.0$ นาที (100%), ข้อมูลไม่ว่างเปล่า | `scripts/verify_telemetry_resolution.py` | PASS |
| **6. Clean S3 Storage** | ไม่มีไฟล์จำลองหรือไฟล์ว่างเปล่า (All-null files = 0) หลงเหลือใน S3 | `scripts/clean_empty_backfill.py` | PASS |
| **7. Station Integrity** | ระดับน้ำ 8 สถานี และฝน 15 สถานี ดึงครบถ้วนตามสัญญาข้อมูล | Python script ตรวจนับ `non_null_records` และ `min_bank` | PASS |

---

## 1. การทดสอบ Automated Unit Tests

รันชุดทดสอบเพื่อยืนยันว่าฟังก์ชันแปลงข้อมูล การคำนวณสะสมฝน 24 ชั่วโมง การจัดการสถานีที่ล้มเหลวบางส่วน (Partial Degradation) และ Quality Gate ของ Backfill ทำงานถูกต้องสมบูรณ์

```bash
python3 -m unittest tests/test_ingest_thaiwater.py -v
```

### ผลลัพธ์ที่ต้องแสดง:
```text
test_backfill_all_null_records_treated_as_empty ... ok
test_backfill_below_non_null_threshold_rejected ... ok
test_backfill_idempotency_success_criteria_1 ... ok
test_backfill_ongoing_month_not_checkpointed ... ok
test_create_envelope_schema ... ok
test_fetch_24h_rain_station_series_and_24h_points ... ok
test_fetch_historical_waterlevel_sends_tele_waterlevel_param ... ok
test_historical_waterlevel_bank_divergence_corrected ... ok
test_ingest_rain_partial_failure ... ok
test_ingest_rain_saves_to_raw_and_incoming ... ok
test_ingest_waterlevel_saves_to_raw_and_incoming ... ok
test_persisted_data_integrity_check ... ok
test_phasi_charoen_station_10_flagged_as_suspect_datum ... ok
test_real_rain_stations_metadata ... ok
test_real_water_stations_metadata ... ok
test_strict_fail_fast_on_api_error ... ok

----------------------------------------------------------------------
Ran 16 tests in 0.220s

OK
```

---

## 2. การตรวจสอบ AWS EventBridge Scheduler (ระบบตั้งเวลาอัตโนมัติ)

### 2.1 ตรวจสอบผ่าน AWS CLI ใน CloudShell
รันคำสั่งเพื่อตรวจสอบว่ามี EventBridge Rule ถูกติดตั้งและอยู่ในสถานะ `ENABLED`:

```bash
aws events list-rules --name-prefix tuamyang
```

#### ผลลัพธ์ที่ต้องแสดง:
```json
{
    "Rules": [
        {
            "Name": "tuamyang-thaiwater-rain-1hr",
            "Arn": "arn:aws:events:us-east-1:975050116464:rule/tuamyang-thaiwater-rain-1hr",
            "ScheduleExpression": "rate(1 hour)",
            "State": "ENABLED",
            "Description": "TuamYang: Ingest ThaiWater rain telemetry every 1 hour",
            "EventBusName": "default"
        },
        {
            "Name": "tuamyang-thaiwater-waterlevel-15min",
            "Arn": "arn:aws:events:us-east-1:975050116464:rule/tuamyang-thaiwater-waterlevel-15min",
            "ScheduleExpression": "rate(15 minutes)",
            "State": "ENABLED",
            "Description": "TuamYang: Ingest ThaiWater canal water level telemetry every 15 minutes",
            "EventBusName": "default"
        }
    ]
}
```

ตรวจสอบว่า Rule ผูก Target เข้ากับ AWS Lambda จริง:
```bash
aws events list-targets-by-rule --rule tuamyang-thaiwater-waterlevel-15min
aws events list-targets-by-rule --rule tuamyang-thaiwater-rain-1hr
```

### 2.2 ตำแหน่งการดูใน AWS Management Console (จุดสำคัญ)
> [!NOTE]
> AWS EventBridge แยกหน้าจอระหว่าง **Event pattern rules** (สำหรับดักจับเหตุการณ์) กับ **Scheduled rules** (สำหรับตั้งเวลาแบบ cron/rate)
> หากค้นหาในหน้า `Rules` ปกติจะไม่พบ ให้เข้าไปที่เมนูต่อไปนี้:
> 1. เมนูด้านซ้าย: ไปที่ **Scheduler** -> คลิก **Scheduled rules (legacy)**
> 2. หรือคลิกปุ่ม **`[Go to Scheduled rules]`** บนแถบสีเหลืองด้านบน
> 3. พิมพ์ค้นหา `tuam` จะพบทั้ง 2 Rules ในสถานะ `Enabled` สีเขียว

---

## 3. การทดสอบยิง AWS Lambda สด (Live Lambda Ingestion)

ทดสอบสั่งรัน Ingestion ฟังก์ชันสดบน AWS Lambda ทั้ง 2 รูปแบบข้อมูล เพื่อพิสูจน์ว่าเชื่อมต่อ ThaiWater API และบันทึกไฟล์ลง S3 ได้จริง

### 3.1 ทดสอบดึงระดับน้ำสด (Waterlevel - 8 สถานี)
```bash
aws lambda invoke \
  --function-name tuamyang-ingest-thaiwater \
  --payload '{"data_type": "waterlevel"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/response_wl.json

cat /tmp/response_wl.json
```

#### ผลลัพธ์ที่คาดหวัง:
```json
{
  "statusCode": 200,
  "body": {
    "status": "SUCCESS",
    "data_type": "waterlevel",
    "record_count": 8,
    "timestamp": "2026-11-01T...",
    "s3_incoming_path": "s3://cs341-tuamyang-data/incoming/thaiwater/waterlevel/...",
    "s3_raw_path": "s3://cs341-tuamyang-data/raw/thaiwater/waterlevel/..."
  }
}
```

### 3.2 ทดสอบดึงปริมาณน้ำฝนสด (Rain - 15 สถานี พร้อม Series รายชั่วโมง)
```bash
aws lambda invoke \
  --function-name tuamyang-ingest-thaiwater \
  --payload '{"data_type": "rain"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/response_rain.json

cat /tmp/response_rain.json
```

#### ผลลัพธ์ที่คาดหวัง:
```json
{
  "statusCode": 200,
  "body": {
    "status": "SUCCESS",
    "data_type": "rain",
    "record_count": 15,
    "failed_stations": [],
    "s3_incoming_path": "s3://cs341-tuamyang-data/incoming/thaiwater/rain/...",
    "s3_raw_path": "s3://cs341-tuamyang-data/raw/thaiwater/rain/..."
  }
}
```

---

## 4. การตรวจสอบ Dual-Zone S3 Storage Hierarchy

ตรวจสอบว่าไฟล์ถูกจัดเก็บแยก 2 โซนตามข้อกำหนดของระบบ:
1. `incoming/` สำหรับรอให้ Step Functions หรือ Downstream Processing นำไปใช้งาน
2. `raw/` สำหรับเก็บประวัติศาสตร์ต้นฉบับถาวร (แบ่ง Partition ตาม `dt=YYYY-MM-DD`)

```bash
# ตรวจสอบ Incoming Zone
aws s3 ls s3://cs341-tuamyang-data/incoming/thaiwater/waterlevel/ --recursive | sort | tail -n 5
aws s3 ls s3://cs341-tuamyang-data/incoming/thaiwater/rain/ --recursive | sort | tail -n 5

# ตรวจสอบ Raw Zone
aws s3 ls s3://cs341-tuamyang-data/raw/thaiwater/waterlevel/ --recursive | sort | tail -n 5
aws s3 ls s3://cs341-tuamyang-data/raw/thaiwater/rain/ --recursive | sort | tail -n 5
```

---

## 5. การตรวจสอบความถูกต้องของข้อมูลย้อนหลัง (Historical Backfill)

### 5.1 ตรวจสอบว่าไม่มีไฟล์ว่างเปล่า (All-null files = 0)
รันสคริปต์สแกน S3 เพื่อพิสูจน์ว่าไม่มีไฟล์ที่ค่าระดับน้ำเป็น null ทั้งไฟล์หลงเหลืออยู่:

```bash
python3 scripts/clean_empty_backfill.py
```

#### ผลลัพธ์ที่ต้องแสดง:
```text
Scanning S3 prefix: s3://cs341-tuamyang-data/raw/thaiwater/waterlevel/ ...

Scan Results:
  Valid files (non-null > 0): 16
  All-null files to remove:   0

[SUCCESS] No empty files found! S3 raw storage is clean and valid.
```

### 5.2 พิสูจน์ความถี่ 10 นาทีทางคณิตศาสตร์ (Mathematical Resolution Proof)
รันสคริปต์ตรวจสอบความละเอียดและช่องว่างเวลา ($\Delta t$):

```bash
python3 scripts/verify_telemetry_resolution.py
```

#### ผลลัพธ์ที่ต้องแสดง:
```text
Inspecting: s3://cs341-tuamyang-data/raw/thaiwater/waterlevel/dt=2026-09-01/waterlevel_hist_st1_2026_09.json
Total records in monthly file: 4320
Total non-null waterlevel readings: 4293
Null reading slots: 27
First record timestamp: 2026-09-01 00:00:00
Last record timestamp:  2026-09-30 23:50:00
Sampling interval breakdown:
  Delta = 10.0 minutes: 4319 transitions (100.0%)

[PASS] Strict 10-minute sampling resolution mathematically verified!
```

### 5.3 ตรวจสอบความถูกต้องของ 8 สถานีระดับน้ำ (Non-null count & Min Bank)
รันคำสั่ง Python เพื่ออ่านข้อมูลจริงใน S3 และตรวจสอบค่าระดับน้ำพร้อมค่าตลิ่งจริง:

```bash
python3 -c '
import json, boto3
s3 = boto3.client("s3"); BK = "cs341-tuamyang-data"
prefix = "raw/thaiwater/waterlevel/dt=2026-09-01/"
for obj in s3.list_objects_v2(Bucket=BK, Prefix=prefix).get("Contents", []):
    doc = json.loads(s3.get_object(Bucket=BK, Key=obj["Key"])["Body"].read())
    sid = doc.get("api_station_id")
    rows = doc.get("data", [])
    nn = sum(1 for r in rows if r.get("waterlevel_msl") not in (None, ""))
    mb = rows[0].get("min_bank_msl") if rows else None
    print(f"Station {sid:>6}: total_slots={len(rows):>4}, non_null_records={nn:>4}, min_bank={mb} m MSL")
'
```

#### ผลลัพธ์ที่ได้จากการตรวจสอบจริง:
```text
Station     10: total_slots=4320, non_null_records=4250, min_bank=1.415 m MSL
Station      1: total_slots=4320, non_null_records=4293, min_bank=2.2 m MSL
Station   2599: total_slots= 720, non_null_records= 687, min_bank=2.26 m MSL
Station      3: total_slots=4320, non_null_records=4280, min_bank=0.6228 m MSL
Station      4: total_slots=4320, non_null_records=3963, min_bank=2.161 m MSL
Station      5: total_slots=4320, non_null_records=1513, min_bank=2.069 m MSL
Station 852087: total_slots=4320, non_null_records=4161, min_bank=2.39 m MSL
Station      8: total_slots=4320, non_null_records=4290, min_bank=1.97 m MSL
```

---

## 6. การตรวจสอบ Checkpoint และการทนต่อข้อจำกัดของ Learner Lab

ตรวจสอบสถานะความคืบหน้าของ Backfill และไฟล์ Checkpoint บน S3:

```bash
# ตรวจสอบสถานะผ่าน CLI
python3 -m src.backfill --status

# ตรวจสอบเนื้อหาไฟล์ Checkpoint บน S3
aws s3 cp s3://cs341-tuamyang-data/raw/thaiwater/checkpoint.json - | head -n 30
```

> [!TIP]
> หาก AWS Learner Lab ปิดตัวลง (Session หมดอายุหลังจาก 4 ชั่วโมง) เมื่อเริ่ม Session ใหม่ ระบบสามารถรันต่อได้ทันทีโดยไม่ทำงานซ้ำซ้อน ด้วยคำสั่ง:
> `python3 -m src.backfill --type full --start-year 2022 --backend s3`

