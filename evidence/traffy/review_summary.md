# Traffy 100-report review

Reviewer: ai / Codex (AI text review)

**These are provisional AI labels, not human ground truth.**

Population: 9614; sample: 100; seed: 341.
Rule version: `traffy-flood-v1.0`. Each sample was selected without seeing predictions.

| Reference \ Predicted | flood | non_flood | uncertain |
|---|---:|---:|---:|
| flood | 76 | 0 | 0 |
| non_flood | 0 | 18 | 0 |
| uncertain | 2 | 4 | 0 |

Three-class agreement: 94.0%.
Flood precision: 1.0; flood recall: 1.0.

Precision excludes uncertain reference labels. Recall counts uncertain predictions as missed positives.
The sample is uniform across all reports, not balanced by flood class. Metrics describe this period only.

## Disagreements

- `2026-HZRC7Z`: reference=uncertain, predicted=non_flood; ขอขนย้ายรถออกจากคอนโด แต่ข้อความไม่ระบุสาเหตุหรือน้ำท่วมโดยตรง
- `2026-UEHK3J`: reference=uncertain, predicted=non_flood; ขอทรายกันน้ำและระบุรถสัญจรไม่ได้ แต่ไม่ยืนยันว่ามีน้ำท่วมแล้ว
- `2026-8KGZGK`: reference=uncertain, predicted=non_flood; มีเพียงชื่อซอย ไม่มีรายละเอียดให้ยืนยันประเภทปัญหาจากข้อความ
- `2026-MUZF3L`: reference=uncertain, predicted=flood; เนื้อความบอกน้ำในคลองเกือบเสมอถนน แต่ช่องระยะเวลาบอกท่วมแล้ว ข้อความขัดกันจึงควรตรวจเพิ่ม
- `2026-V947RE`: reference=uncertain, predicted=non_flood; ข้อความติดตามสั้นและไม่ระบุว่าเป็นปัญหาอะไร
- `2026-MABXHM`: reference=uncertain, predicted=flood; ขอเดินอุโมงค์เพื่อบรรเทาน้ำท่วมหลายเขต แต่ไม่ยืนยันเหตุท่วมที่จุดรายงาน

## Human confirmation

Read the full local `sample_100.json`, fill `human_review_100.json`, and rerun evaluate with those labels.
Repository evidence contains IDs, labels and rationales; full descriptions stay in the local ignored sample.
