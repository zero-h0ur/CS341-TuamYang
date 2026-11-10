# Orchestration + Validation (Issue #1)

Owner: นายปพนพัชร์ มีวน (@zero-h0ur)

## ภาพรวม

ไฟล์ใหม่ใน `incoming/` → EventBridge (S3 Object Created) → Step Functions `tuamyang-${Stage}-pipeline`

```text
CheckBatch ──skip/ทำไปแล้ว──► Skipped
    │
ValidateBatch ──FAIL──► QuarantineBatch ──► BatchQuarantined (Fail)
    │ PASS / WARN
TransformBatch ──► PublishBatch ──► VerifyBatch ──► Published
```

| State | ทำอะไร | เขียนที่ไหน |
|---|---|---|
| CheckBatch | อ่าน S3 key หา dataset / dt / batch_id; ถ้ามี `_SUCCESS.json` หรือ `_QUARANTINED.json` แล้ว → ข้าม (idempotent). Traffy: รอ manifest จน status ไม่ใช่ RUNNING (retry 6 ครั้ง) | – |
| ValidateBatch | รันกติกาใน `src/validation/rules.py` ได้ PASS / WARN / FAIL | `processed/<dataset>/dt=<d>/<batch_id>/validation_report.json` |
| QuarantineBatch | FAIL เท่านั้น: copy ไฟล์ต้นฉบับ + report | `quarantine/<dataset>/dt=<d>/`, `_QUARANTINED.json` |
| TransformBatch | แปลง envelope เป็น JSON Lines (1 แถว/บรรทัด) ให้ Athena อ่าน | `trusted/<dataset>/dt=<d>/<batch_id>.jsonl` (Traffy: 1 ไฟล์/วัน แทนที่ของเดิม) |
| PublishBatch | เขียน marker พร้อมจำนวนแถวและ warning | `processed/<dataset>/dt=<d>/<batch_id>/_SUCCESS.json` |
| VerifyBatch | อ่านกลับ: จำนวนแถวใน trusted = row_count ใน marker และ batch_id ตรง | – |

ทุก state ใช้ Lambda ตัวเดียว `src.validation.pipeline.lambda_handler` แยกด้วย `{"step": ...}` (ลดจำนวน resource/IAM)

ตาราง curated รายแขวง × รายชั่วโมง (Athena SQL) เป็นงาน #2 อ่านจาก `trusted/`.
ฝนรายชั่วโมงจากหลาย batch จะซ้อนกัน (sliding window 24 ชม.) ฝั่ง curated ใช้ `latest batch wins` ตาม `reference/schema_contract.md` 8.2

## กติกาตรวจข้อมูล

FAIL = ไม่ publish, ย้ายไป quarantine. WARN = publish ได้ แต่บันทึกใน report และ `_SUCCESS.json`

| Dataset | FAIL | WARN |
|---|---|---|
| ทุกชนิด | envelope ไม่ครบ, `envelope_version` ≠ 1.0, `record_count` ≠ จำนวนจริง, `is_mock` = true, JSON เสีย | – |
| waterlevel (live) | สถานีไม่อยู่ใน reference, key `(api_station_id, timestamp_epoch)` ซ้ำ, ค่าไม่ null < 30%, `min_bank_msl` ต่างจาก reference > 0.05 m, epoch ไม่ตรงเวลาไทย, เวลาอ่านค่าล้ำหน้าเวลาดึง > 15 นาที | ค่าเก่ากว่า 1 ชม., เวลาไม่อยู่บนกริด 10 นาที (C.12 รายชั่วโมง), ระดับน้ำนอกช่วง −3 ถึง 5 m MSL, `diff_wl_bank_m` ≠ `min_bank − ระดับน้ำ`, \|freeboard\| > 5 m, สถานีหายจาก batch |
| rain (live) | ไม่มี `series` (ไฟล์เก่านับเกิน), ฝนติดลบ, ชั่วโมงซ้ำ, สถานีที่มีข้อมูล < 50% ของสถานีที่ใช้ได้ | ฝน/ชม. > 150 mm หรือ 24 ชม. > 500 mm, `rain_24h_mm` ≠ ผลรวม 24 จุดล่าสุด, ค่าเก่ากว่า 3 ชม., บางสถานีไม่มีข้อมูล |
| traffy (รายวัน) | manifest ไม่ SUCCESS / batch_id หรือ hash ไม่ตรง, SHA-256 ของ data ไม่ตรง, ticket_id ว่างหรือซ้ำ, epoch ไม่ตรงกับ `report_date_bkk`/`hour`, รายงานไม่อยู่ในวันนั้น, `is_flood_report` ขัดกับ `flood_classification`, `flood_count` ไม่ตรง, วันปิดแล้วแต่ไม่มีรายงาน | พิกัดหาย/อยู่นอก bbox กทม. > 5%, ข้อมูล provisional, warning จาก ingestion |
| waterlevel backfill (raw/) | ไม่อยู่บนกริด 10 นาที, slot ซ้ำ, ค่าไม่ null < 30%, `min_bank_msl` ไม่ตรง reference | slot ขาดช่วง |

เกณฑ์ตัวเลขอยู่ต้นไฟล์ `src/validation/rules.py` (บางตัวดึงจาก `config/config.py`)

## ผลจากข้อมูลจริงใน repo (รันบนเครื่อง)

```bash
python -X utf8 scripts/run_pipeline_local.py --inject-pr7-defect --evidence evidence/orchestration
```

| Batch | ผล | เหตุผล |
|---|---|---|
| `waterlevel_tw-wa-20261110-002522-07161d` | Published, WARN | ดึงตอน 00:25 แต่ค่าล่าสุดของทุกสถานีเก่า 2.4–3.4 ชม. |
| `rain_tw-ra-20261110-*` (2 ไฟล์) | Published, WARN | ค่าเก่า 3.4 ชม. และสถานี กรมอุตุฯ 4 แห่ง (3709–3712) ไม่มีข้อมูลเลย |
| `waterlevel_...-dead01` (ใส่ bug ของ PR #7: `min_bank` = ระดับท้องคลอง) | BatchQuarantined | `bank_matches_reference` |
| รันซ้ำทุกไฟล์ | Skipped | idempotent ด้วย `_SUCCESS.json` / `_QUARANTINED.json` |

ไฟล์ backfill ของ PR #7 ก่อนแก้ (commit f1d66ba, เก็บ 1 วันไว้ที่ `tests/fixtures/`) ถูก FAIL ครบทั้ง 3 bug: กริด 15 นาที, ค่า null 100%, min_bank = ground level. ไฟล์ backfill ปัจจุบันทั้ง 8 สถานีผ่าน (PASS)

ผลลัพธ์อยู่ใน `evidence/orchestration/`. Traffy ทดสอบด้วยข้อมูลสังเคราะห์ใน unit test เพราะ payload จริงมีข้อมูลส่วนบุคคลและไม่ commit

## ทดสอบ

```bash
python -X utf8 -m unittest tests.test_validation -v     # 46 tests, ไม่ต้องใช้ AWS
python -X utf8 -m unittest discover -s tests             # ทั้ง repo
```

## Deploy (ผ่าน template.yaml ของ #11)

ใส่ใน slot `HasStateMachineDefinition` ของ `template.yaml` หลัง PR Phase 1 ของ #11 merge แล้ว (ชื่อ parameter/condition ปรับตามของจริงใน template):

```yaml
  PipelineStepFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: !Sub tuamyang-${Stage}-pipeline-step
      Handler: src.validation.pipeline.lambda_handler
      CodeUri: .aws-sam/stage/thaiwater/      # ต้องมี src/ config/ reference/ เหมือน ingest_thaiwater
      Runtime: python3.12
      Timeout: 120
      MemorySize: 512                          # Traffy วันเดียว ~6 MB JSON
      Environment:
        Variables:
          STORAGE_BACKEND: s3
          S3_BUCKET_NAME: !Ref DataBucketName
      Role: !If [UseExistingRole, !Sub "arn:aws:iam::${AWS::AccountId}:role/${ExistingLambdaRoleName}", !Ref AWS::NoValue]
      Policies:                                # SAM ไม่ใช้ Policies เมื่อระบุ Role (dev/LabRole)
        - S3CrudPolicy: {BucketName: !Ref DataBucketName}

  PipelineStateMachine:
    Type: AWS::Serverless::StateMachine
    Properties:
      Name: !Sub tuamyang-${Stage}-pipeline
      DefinitionUri: statemachine/pipeline.asl.json
      DefinitionSubstitutions:
        PipelineFunctionArn: !GetAtt PipelineStepFunction.Arn
      Role: !If [UseExistingRole, !Sub "arn:aws:iam::${AWS::AccountId}:role/${ExistingLambdaRoleName}", !Ref AWS::NoValue]
      Policies:
        - LambdaInvokePolicy: {FunctionName: !Ref PipelineStepFunction}
      Events:
        IncomingObject:
          Type: EventBridgeRule
          Properties:
            Pattern:
              source: [aws.s3]
              detail-type: [Object Created]
              detail:
                bucket: {name: [!Ref DataBucketName]}
                object: {key: [{prefix: incoming/}]}
```

หมายเหตุ:
- Learner Lab สร้าง IAM role ไม่ได้: state machine และ EventBridge rule ต้องใช้ LabRole ด้วย ไม่ใช่แค่ Lambda. event `EventBridgeRule` ของ SAM จะสร้าง role ให้ rule เอง ถ้า dev deploy ไม่ผ่านเพราะเรื่องนี้ ให้แยกเป็น `AWS::Events::Rule` ที่ระบุ `RoleArn` = LabRole แทน (ยังไม่ได้ทดสอบใน Learner Lab)
- bucket ต้องเปิด EventBridge notification (`NotificationConfiguration.EventBridgeConfiguration.EventBridgeEnabled: true`, D8 ใน #11)
- `TuamYangCfnExecutionRole` ต้องมีสิทธิ์ `states:*` บน `tuamyang-*` ก่อน merge ไม่งั้น CD ล้ม AccessDenied
- ค่าใช้จ่าย: waterlevel ทุก 15 นาที + ฝนทุกชั่วโมง ≈ 3,600 execution/เดือน × ~6 transition ≈ 22,000 transition เกิน free 4,000 ของ Standard workflow ราว $0.45/เดือน (หักจากเครดิต Free plan)
- ตรวจ ASL กับ AWS ได้ใน Learner Lab: `aws stepfunctions validate-state-machine-definition --definition file://statemachine/pipeline.asl.json` (ต้องแทน `${PipelineFunctionArn}` ก่อน)

## ยังเหลือ

- [ ] deploy ใน Learner Lab ของตัวเองแล้วเก็บหลักฐานจาก S3 จริง (execution graph, `_SUCCESS.json`, ไฟล์ใน `quarantine/`)
- [ ] ใส่ resource ข้างบนใน `template.yaml` หลัง #11 Phase 1 merge
- [ ] ตกลงกับ #2 เรื่อง schema ของ `trusted/` (คอลัมน์ใน JSONL) สำหรับ Glue table
