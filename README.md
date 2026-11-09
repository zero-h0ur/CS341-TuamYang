# CS341-TuamYang
CS341 Term Project: Bangkok flood risk data pipeline (water level vs bank height + Traffy Fondue flood reports, by khwaeng)

## Traffy ingestion - Checkpoint (สมาชิก 2)

ดึงข้อมูล Traffy รายวัน/ย้อนหลัง เก็บข้อมูลจริง ตรวจข้อความน้ำท่วม และส่งข้อมูลให้ทีมทำ spatial join และ Q2 ต่อ

- [วิธีรันและสาธิต Checkpoint](docs/traffy_checkpoint.md)
- [ข้อตกลงข้อมูลสำหรับส่งต่อให้ทีม](reference/traffy_schema_contract.md)
- [ผลตรวจและแก้ช่องว่างตาม Issue #5: รายชั่วโมง, ย้อนหลังกลางปี 2565, precision](docs/traffy_issue5.md)
- [ผลดึงและตรวจข้อมูลย้อนหลังครบ 1,623 วัน](docs/traffy_full_backfill.md)
- [วิธีอ่านและตรวจตัวอย่าง 100 รายงาน](docs/traffy_review_100.md)
- [ผลสุ่มตรวจ 100 รายงานโดย AI - รอคนยืนยัน](evidence/traffy/review_summary.md)
- [หลักฐาน ingestion และรันซ้ำจากข้อมูลจริง](evidence/traffy/ingestion_summary.json)

```powershell
python -X utf8 -m src.backfill_traffy --start 2026-09-24 --end 2026-09-26 --backend local
python -X utf8 -m unittest tests.test_traffy tests.test_traffy_delivery -v
```

หลักฐานชุดนี้ทดสอบบนเครื่อง Local และเตรียม Lambda ZIP แล้ว การ deploy และทดสอบบน AWS ของทีมเป็นขั้นตอนถัดไปใน runbook
