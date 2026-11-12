## Summary

แก้ pipeline ที่ล้มด้วย `JSONDecodeError: Unterminated string` เมื่อรายงาน Traffy
มีอักขระขึ้นบรรทัด Unicode อยู่ในข้อความ เช่น ข้อมูลจริงวันที่ `2023-04-09`
เดิม `.splitlines()` ตัดข้อความภายใน JSON จนอ่านไม่ได้
จึงเปลี่ยนจุดอ่าน JSONL ให้แบ่งเฉพาะ `\n` และเก็บข้อความเดิมครบ

- แก้ขั้น Verify, การส่งออกตัวอย่างหลักฐาน และตัวอ่าน trusted ของ Curated ทั้งสาม dataset
- เพิ่ม regression tests สำหรับ `U+0085`, `U+2028`, `U+2029`, LF / CRLF และไฟล์ที่ไม่มี newline ท้ายสุด
- อธิบาย `Published WARN`, ขอบเขตข้อมูลที่สคริปต์อ่าน และการรันใหม่หลังแก้ใน README / คู่มือ

## Verification

- ก่อนแก้ regression tests ทำให้เกิด JSONDecodeError ซ้ำได้ หลังแก้ชุดทดสอบทั้ง repo ผ่าน **148/148**
- รัน `scripts/run_pipeline_local.py --inject-pr7-defect --evidence ...` บนสำเนาข้อมูลจริง
  วันที่ 8–10 เม.ย. 2566 รวม **1,650 รายงาน** กับ ThaiWater สาม batch:
  **Published 6, BatchQuarantined 1, Replay Skipped 7**
- วันที่เคยล้ม (`2023-04-09`) ผ่านครบ **481 รายงาน**; Curated อ่านทั้งสามวันได้ครบและข้อความตรงต้นฉบับ
- ตัวอย่างหลักฐานเป็น JSONL ที่อ่านได้ครบสามแถวต่อไฟล์ และ source files ไม่เปลี่ยน

ผลทดสอบจริงที่ไม่มีข้อความรายงานหรือพิกัด:
`evidence/orchestration/jsonl_unicode_regression.json`

`source_quality_warnings` ยังคงแสดงตามข้อจำกัดที่ ingestion บันทึกไว้
ผลข้างต้นเป็นการทดสอบบน Local เฉพาะช่วงที่เคยล้ม ไม่ใช่การรันซ้ำทั้งประวัติหรือผล deploy บน AWS

Refs #1, #2, #5.
