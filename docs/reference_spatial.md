# Reference & Spatial Join Documentation (Issue #3)

> **เจ้าของงาน (Owner):** นายภูรินทร์ แก้วพ่วงเสก (`@PhurinKaewpuangsek`) — Member 3: Reference & Spatial Join  
> **เกี่ยวข้องกับ:** [Issue #3](https://github.com/zero-h0ur/CS341-TuamYang/issues/3) (`[Reference + Spatial join] ขอบเขตแขวง และการจับคู่แขวง ↔ สถานี`)  
> **สอดคล้องกับ:** PR #7 (`reference/schema_contract.md`), PR #10 (`reference/traffy_schema_contract.md`), PR #13 (`sql/create_khwaeng_hourly.sql`, `curated/khwaeng_hourly`)

---

## 1. แหล่งที่มาของข้อมูลขอบเขตแขวง (Data Source, License & Provenance)

| รายการ | รายละเอียด |
| :--- | :--- |
| **ชื่อชุดข้อมูล (Dataset Title)** | Thailand - Subnational Administrative Boundaries (`cod-ab-tha`) |
| **หน่วยงานผู้จัดทำ (Original Source)** | กรมแผนที่ทหาร (Royal Thai Survey Department — RTSD) |
| **ผู้เผยแพร่ (Distributor)** | OCHA Regional Office for Asia and the Pacific (ROAP) ผ่าน Humanitarian Data Exchange (HDX) |
| **URL หน้าชุดข้อมูล (Dataset URL)** | `https://data.humdata.org/dataset/cod-ab-tha` |
| **URL ดาวน์โหลดตรง (Direct Resource URL)** | `https://data.humdata.org/dataset/d24bdc45-eb4c-4e3d-8b16-44db02667c27/resource/89a09f13-7b83-458a-9531-4f2418613065/download/tha_admin_boundaries.geojson.zip` (`tha_admin3.geojson`, ADM1 = `TH10`) |
| **สัญญาอนุญาต (License)** | **Creative Commons Attribution for Intergovernmental Organisations (`CC BY-IGO`)** |
| **วันที่อัปเดตบน HDX (Dataset Modified)** | `2026-01-26` (เวอร์ชัน `v01`, `valid_on: 2022-01-22`) |
| **วันที่ดาวน์โหลดและประมวลผล (Download Date)** | `2026-11-12` |
| **ระบบพิกัดอ้างอิง (CRS)** | `WGS84 / OGC:CRS84` (ลำดับพิกัดใน WKT คือ `longitude latitude`) |

---

## 2. คำสั่งสร้างข้อมูลซ้ำ (Reproducible Generation Command)

สคริปต์ [`scripts/build_reference_spatial.py`](../scripts/build_reference_spatial.py) ดึงข้อมูลขอบเขตแขวงของกรุงเทพมหานคร (`adm1_pcode = 'TH10'`) จาก HDX โดยใช้ HTTP Range request บนไฟล์ ZIP ของ HDX (ไม่ต้องโหลดไฟล์เต็ม 436 MB ลงดิสก์ ใช้เวลา ~3 วินาที) แปลงรูปหลายเหลี่ยมเป็น OGC WKT (`POLYGON`) และคำนวณตารางจับคู่สถานีวัดน้ำ 8 สถานีและสถานีวัดฝนที่ใช้งานได้จริงใกล้ที่สุด:

```bash
# รันด้วยรัศมีเริ่มต้น 1.0 กม. (ปรับค่ารัศมีได้ผ่านพารามิเตอร์ --radius-km)
python -X utf8 scripts/build_reference_spatial.py --radius-km 1.0
```

---

## 3. โครงสร้างตารางอ้างอิงใน `reference/` (Schema & Column Documentation)

### 3.1 `reference/khwaeng_boundaries.csv` (169 แขวงในกรุงเทพมหานคร)

| ชื่อคอลัมน์ (Column) | ชนิดข้อมูล | คำอธิบาย |
| :--- | :---: | :--- |
| `khwaeng_code` | `STRING` | รหัส P-code ระดับแขวงของกรมแผนที่ทหาร (`TH100101` – `TH105001`) |
| `name_th` | `STRING` | ชื่อแขวงภาษาไทย (ไม่มีคำนำหน้า "แขวง" เช่น `อนุสาวรีย์`, `เสนานิคม`) |
| `name_en` | `STRING` | ชื่อแขวงภาษาอังกฤษ (เช่น `Anusawari`, `Sena Nikhom`) |
| `khet` | `STRING` | ชื่อเขตภาษาไทย (ไม่มีคำนำหน้า "เขต" เช่น `บางเขน`, `จตุจักร`) |
| `khet_code` | `STRING` | รหัส P-code ระดับเขต (`TH1001` – `TH1050`) |
| `khet_en` | `STRING` | ชื่อเขตภาษาอังกฤษ |
| `center_lat` | `DOUBLE` | ละติจูดจุดกึ่งกลางแขวง (WGS84) |
| `center_lon` | `DOUBLE` | ลองจิจูดจุดกึ่งกลางแขวง (WGS84) |
| `area_sqkm` | `DOUBLE` | พื้นที่แขวง (ตารางกิโลเมตร) |
| `wkt` | `STRING` | ขอบเขตเรขาคณิตในรูปแบบ OGC Well-Known Text (`POLYGON ((lon lat, ...))`) สำหรับใช้กับ `ST_GeometryFromText(wkt)` ใน Athena |

### 3.2 `reference/khwaeng_station_mapping.csv` (169 แขวง พร้อมธงคัดกรองรัศมี 1 กม.)

ตารางนี้รวมคอลัมน์ตามข้อกำหนดของ Issue #3 และคอลัมน์ที่ตาราง `curated/khwaeng_hourly` ของ `@nustapark6709650334` (Issue #2 / PR #13 ใน [`sql/create_khwaeng_hourly.sql`](../sql/create_khwaeng_hourly.sql)) เรียกใช้งานไว้ในไฟล์เดียวกัน:

| ชื่อคอลัมน์ (Column) | ชนิดข้อมูล | คำอธิบายและการเชื่อมต่อ |
| :--- | :---: | :--- |
| `khwaeng_code` | `STRING` | รหัสแขวง (`TH10xxxx`) สำหรับ Join กับ `reference.khwaeng_boundaries` |
| `khwaeng` | `STRING` | ชื่อแขวงภาษาไทย (**ตรงกับคีย์ Join `ref.khwaeng` ใน PR #13 `sql/create_khwaeng_hourly.sql`**) |
| `name_th` | `STRING` | ชื่อแขวงภาษาไทย (ตามสเปก Issue #3) |
| `name_en` | `STRING` | ชื่อแขวงภาษาอังกฤษ |
| `khet` | `STRING` | ชื่อเขตภาษาไทย (**ตรงกับคีย์ Join `ref.khet` ใน PR #13 `sql/create_khwaeng_hourly.sql`**) |
| `khet_code` | `STRING` | รหัสเขต (`TH10xx`) |
| `khet_en` | `STRING` | ชื่อเขตภาษาอังกฤษ |
| `center_lat`, `center_lon` | `DOUBLE` | พิกัดจุดกึ่งกลางแขวง |
| `within_radius_km` | `BOOLEAN` | `True` หากขอบเขตแขวงอยู่ภายในรัศมีที่กำหนด (`--radius-km`, ค่าเริ่มต้น `1.0` กม.) จากสถานีวัดน้ำอย่างน้อย 1 แห่ง (**28 แขวง** เมื่อ `radius_km = 1.0`) |
| `radius_threshold_km` | `DOUBLE` | ค่ารัศมีที่ใช้คัดกรอง (ค่าเริ่มต้น `1.0` กม.) |
| `min_water_boundary_dist_km` | `DOUBLE` | ระยะทางที่สั้นที่สุดจากขอบรูปหลายเหลี่ยมของแขวงไปยังสถานีวัดน้ำที่ใกล้ที่สุด (กม., เป็น `0.0` หากสถานีตั้งอยู่ในแขวงนั้น) |
| `within_1km_water_station_ids` | `STRING` | รหัสสถานีวัดน้ำทั้งหมดที่อยู่ภายในรัศมีของแขวงนั้น (คั่นด้วย `;`) |
| `containing_water_station_id` | `STRING` | รหัสสถานีวัดน้ำที่พิกัดตั้งอยู่ภายในรูปหลายเหลี่ยมของแขวงนั้น (`ST_Contains`) |
| `paired_water_station_id` | `INT` | รหัสสถานีวัดน้ำที่จับคู่ให้แขวง (**ตรงกับ `ref.paired_water_station_id` ใน PR #13**) |
| `water_station_code` | `STRING` | รหัสย่อสถานีวัดน้ำ (เช่น `BKK021`, `C.12`, `AIT001`) |
| `water_station_name` | `STRING` | ชื่อสถานีวัดน้ำภาษาไทย |
| `canal_name` | `STRING` | ชื่อคลอง/แม่น้ำของสถานีวัดน้ำ |
| `water_station_dist_km` | `DOUBLE` | ระยะทาง Haversine จากจุดกึ่งกลางแขวงไปยังสถานีวัดน้ำที่จับคู่ (กม.) (**ตรงกับ `ref.water_station_dist_km` ใน PR #13**) |
| `water_quality_flag` | `STRING` | สถานะคุณภาพข้อมูลสถานีวัดน้ำ (`NORMAL` หรือ `SUSPECT_DATUM_MISMATCH`) |
| `water_usable_for_freeboard` | `BOOLEAN` | `False` สำหรับสถานี ID 10 (คลองภาษีเจริญ เพชรเกษม 69) ตาม `reference/schema_contract.md` |
| `paired_rain_station_id` | `INT` | รหัสสถานีวัดน้ำฝนที่ใช้งานได้จริง (`usable_for_rainfall = true`) ที่ใกล้ที่สุด (**ตรงกับ `ref.paired_rain_station_id` ใน PR #13**) |
| `rain_station_code` | `STRING` | รหัสย่อสถานีวัดน้ำฝน |
| `rain_station_name` | `STRING` | ชื่อสถานีวัดน้ำฝนภาษาไทย |
| `rain_station_dist_km` | `DOUBLE` | ระยะทาง Haversine จากจุดกึ่งกลางแขวงไปยังสถานีวัดน้ำฝนที่จับคู่ (กม.) (**ตรงกับ `ref.rain_station_dist_km` ใน PR #13**) |
| `rain_boundary_dist_km` | `DOUBLE` | ระยะทางจากขอบเขตแขวงไปยังสถานีวัดน้ำฝนที่จับคู่ (กม.) |
| `rain_quality_flag` | `STRING` | สถานะคุณภาพข้อมูลสถานีวัดน้ำฝน (`OK`) |
| `rain_usable_for_rainfall` | `BOOLEAN` | `True` เสมอ (กรองสถานีอโศก `852087` ที่ติดธง `SUSPECT_RAIN_SENSOR_OUTAGE` ออกโดยอัตโนมัติ) |

---

## 4. รายงานตัวเลขคุณภาพข้อมูล (Data Quality & Spatial Audit Numbers)

จากการรัน `scripts/build_reference_spatial.py` (หลักฐานบันทึกใน [`evidence/reference_spatial/spatial_quality_report.json`](../evidence/reference_spatial/spatial_quality_report.json)):

1. **จำนวนแขวงใน กทม. ที่โหลดจากกรมแผนที่ทหาร (HDX COD-AB):** **169 แขวง** (ครบทุกเขต 50 เขตใน `TH10`)
2. **จำนวนแขวงที่อยู่ในรัศมี 1.0 กม. รอบสถานีวัดน้ำ 8 สถานี:** **28 แขวง**
3. **สถานีวัดน้ำที่มีจำนวนแขวงในรัศมี 1.0 กม. เป็นศูนย์:** **0 สถานี (`[]`)** — สถานีวัดน้ำทั้ง 8 แห่งมีแขวงในรัศมี 1.0 กม. อย่างน้อย 1–7 แขวง
4. **ผลการตรวจสอบ Point-in-Polygon (`ST_Contains`) ของพิกัดสถานีวัดน้ำทั้ง 8 สถานี:**
   - **7 ใน 8 สถานี** มีพิกัดอยู่ภายในรูปหลายเหลี่ยมของแขวงใน กทม. อย่างแม่นยำ (`distance = 0.0 km`)
   - **1 สถานี (ID 5: คลองมหาสวัสดิ์ บางกรวย-สวนผัก, พิกัด `13.79965, 100.43863`)** ตั้งอยู่กึ่งกลางร่องน้ำคลองมหาสวัสดิ์ซึ่งเป็นเส้นแบ่งแนวเขตจังหวัดกรุงเทพมหานคร (แขวงตลิ่งชัน เขตตลิ่งชัน) กับอำเภอบางกรวย จังหวัดนนทบุรี โดยจุดพิกัดอยู่นอกเส้นขอบรูปหลายเหลี่ยมของแขวงตลิ่งชันเพียง **0.3 เมตร (`0.0003 กม.`)** จึงถูกคัดเลือกเข้าอยู่ในรัศมี 1.0 กม. ของแขวงตลิ่งชัน (`TH101902`) และแขวงฉิมพลี (`TH101903`) อย่างครบถ้วน

| Station ID | รหัสย่อ | ชื่อสถานี | พิกัด (`lat, lon`) | `tumbon_th` ใน API | แขวงที่พิกัดตกอยู่จริง (`ST_Contains`) | จำนวนแขวงในรัศมี 1 กม. | รายชื่อแขวงในรัศมี 1 กม. |
| :---: | :---: | :--- | :---: | :--- | :--- | :---: | :--- |
| **1** | `BKK021` | คลองลาดพร้าว วัดบางบัว | `13.85402, 100.58746` | อนุสาวรีย์ (บางเขน) | **เสนานิคม (จตุจักร)** (`TH103002`, ห่างขอบแขวงอนุสาวรีย์ 12.6 ม. เพราะคลองบางบัวเป็นแนวแบ่งเขต) | **4** | เสนานิคม (`0.0 km`), อนุสาวรีย์ (`0.013 km`), ลาดยาว (`0.316 km`), ตลาดบางเขน (`0.531 km`) |
| **3** | `BKK009` | คลองลำปลาทิว ลาดกระบัง | `13.74070, 100.79468` | ลาดกระบัง (ลาดกระบัง) | **ลำปลาทิว (ลาดกระบัง)** (`TH101104`) | **2** | ลำปลาทิว (`0.0 km`), ทับยาว (`0.019 km`) |
| **4** | `CPY015` | สะพานกรุงเทพ | `13.70030, 100.49277` | ดาวคะนอง (ธนบุรี) | **บางคอแหลม (บางคอแหลม)** (`TH103101`, ฝั่งตะวันออกของสะพานกรุงเทพ ห่างแขวงดาวคะนอง 84.8 ม.) | **7** | บางคอแหลม (`0.0 km`), ดาวคะนอง (`0.085 km`), สำเหร่ (`0.085 km`), บางปะกอก (`0.635 km`), วัดพระยาไกร (`0.801 km`), บางลำภูล่าง (`0.831 km`), บุคคโล (`0.906 km`) |
| **5** | `BKK003` | คลองมหาสวัสดิ บางกรวย-สวนผัก | `13.79965, 100.43863` | ตลิ่งชัน (ตลิ่งชัน) | อยู่นอกขอบแขวงตลิ่งชัน **0.3 เมตร** (กลางคลองมหาสวัสดิ์ แนวแบ่ง กทม.–นนทบุรี) | **2** | ตลิ่งชัน (`0.0003 km`), ฉิมพลี (`0.798 km`) |
| **8** | `BKK020` | คลองลาดพร้าว ปากคลอง2สายใต้ | `13.93183, 100.63952` | ลาดพร้าว (ลาดพร้าว) | **สายไหม (สายไหม)** (`TH104201`) | **1** | สายไหม (`0.0 km`) |
| **10** | `BKK005` | คลองภาษีเจริญ เพชรเกษม69 | `13.69158, 100.38127` | หลักสอง (บางแค) | **หนองแขม (หนองแขม)** (`TH102302`, ห่างขอบแขวงหลักสอง 13.6 ม. บนแนวคลองภาษีเจริญ) | **2** | หนองแขม (`0.0 km`), หลักสอง (`0.014 km`) |
| **2599** | `C.12` | กรมชลประทานสามเสน | `13.78815, 100.50915` | ถนนนครไชยศรี (ดุสิต) | **ถนนนครไชยศรี (ดุสิต)** (`TH100206`) | **5** | ถนนนครไชยศรี (`0.0 km`), บางพลัด (`0.127 km`), บางอ้อ (`0.127 km`), วชิรพยาบาล (`0.371 km`), ดุสิต (`0.581 km`) |
| **852087** | `AIT001` | อโศก | `13.74325, 100.56216` | คลองเตยเหนือ (วัฒนา) | **คลองเตยเหนือ (วัฒนา)** (`TH103901`) | **5** | คลองเตยเหนือ (`0.0 km`), มักกะสัน (`0.525 km`), บางกะปิ-ห้วยขวาง (`0.530 km`), คลองตันเหนือ (`0.629 km`), คลองเตย (`0.660 km`) |

5. **อัตราความสำเร็จของ Spatial Join (`% รายงาน Traffy ที่ join เข้าแขวงสำเร็จ`):**
   - จากชุดข้อมูลกรณีศึกษา 24–26 ก.ย. 2569 จำนวน **9,614 รายงาน**:
     - การสกัดชื่อแขวงจากข้อความ `address` อย่างเดียว (วิธีเดิมใน PR #13) สำเร็จ **99.07%** (9,525 / 9,614 รายงาน; มี 89 รายงานที่ระบุเป็น `"ไม่ระบุ"` เพราะที่อยู่ไม่มีคำว่า `แขวง...`)
     - การทำ Point-in-Polygon (`ST_Contains`) ด้วยพิกัด `(longitude, latitude)` เข้ากับรูปหลายเหลี่ยม WKT ใน `reference/khwaeng_boundaries.csv` สามารถระบุแขวงได้ครบ **100.00%** (9,614 / 9,614 รายงาน รวมถึง 89 รายงานที่ไม่มีชื่อแขวงในข้อความที่อยู่)

---

## 5. ข้อสังเกตและการเชื่อมต่อกับ PR #13 (`curated/khwaeng_hourly` ของ `@nustapark6709650334`)

จากการตรวจสอบโค้ดและ SQL ของ PR #13 บน `main` พบจุดเชื่อมต่อและข้อแตกต่างของแหล่งข้อมูลดังนี้ (แจ้งไว้ชัดเจนตามข้อกำหนด *"Flag any mismatch rather than silently picking"*):

1. **ความเข้ากันได้ของชื่อคอลัมน์ใน Athena SQL (`sql/create_khwaeng_hourly.sql` บรรทัด 267–306):**
   - PR #13 อ้างอิงตาราง `reference.khwaeng_station_mapping ref` โดยใช้คอลัมน์ `ref.khwaeng`, `ref.khet`, `ref.paired_water_station_id`, `ref.water_station_dist_km`, `ref.paired_rain_station_id`, และ `ref.rain_station_dist_km`
   - เราได้ใส่คอลัมน์ทั้ง 6 ชื่อนี้ไว้ใน `reference/khwaeng_station_mapping.csv` ควบคู่กับคอลัมน์มาตรฐาน `khwaeng_code`, `name_th`, `name_en`, `within_radius_km` เพื่อให้ Query ใน `sql/create_khwaeng_hourly.sql` ทำงานได้ทันทีโดยไม่ต้องแก้ชื่อคอลัมน์
2. **ความแตกต่างของจำนวนแขวงระหว่าง กทม. ปัจจุบัน (180 แขวง) กับ กรมแผนที่ทหาร/HDX COD-AB (169 แขวง):**
   - ชุดข้อมูลทางการของกรมแผนที่ทหารบน HDX (`cod-ab-tha`) แบ่งกรุงเทพมหานครออกเป็น **169 แขวง** (ใช้ขอบเขตแขวงก่อนการแยกแขวงย่อยปี 2560 ในบางเขต เช่น เขตสายไหมมี 1 แขวงรวมใน HDX แทนที่จะแยกเป็นสายไหม/ออเงิน/คลองถนน หรือเขตบางบอนมี 1 แขวงรวมใน HDX)
   - ในขณะที่ `src/curated/builder.py` ของ PR #13 สกัดชื่อแขวงด้วย Regex จากสตริง `address` ของ Traffy จึงมีชื่อแขวงย่อยหลังปี 2560 ปะปนเข้ามา (และมีบางแถวที่ `khet = 'ไม่ระบุ'` ทำให้เกิดคู่ `(khwaeng, khet)` ที่ไม่ซ้ำกัน 237 คู่)
   - **แนวทางแก้ไขใน [`sql/spatial_join_traffy.sql`](../sql/spatial_join_traffy.sql):** เมื่อรัน Spatial Join ด้วย `ST_Contains(ST_GeometryFromText(b.wkt), ST_Point(t.longitude, t.latitude))` ใน Athena รายงาน Traffy ทุกใบจะถูกจัดเข้าสู่ `khwaeng_code` (`TH10xxxx`) ของกรมแผนที่ทหาร (169 แขวง) โดยตรง ทำให้ Join เข้ากับ `reference.khwaeng_station_mapping` ด้วย `khwaeng_code` ได้ครบ 100% โดยไม่เกิดปัญหาชื่อแขวง/เขตสะกดไม่ตรงกัน
