# คู่มือการใช้งาน CI/CD & AWS SAM สำหรับทีม (`docs/cicd_team_guide.md`)

**Owner:** นายภูรินทร์ แก้วพ่วงเสก (`@PhurinKaewpuangsek`)
**คู่มือสำหรับ Admin Bootstrap:** ดูที่ [docs/cicd_setup.md](cicd_setup.md)
**เอกสาร Orchestration & Validation (#1):** ดูที่ [docs/orchestration_validation.md](orchestration_validation.md)

เอกสารนี้อธิบายวิธีการทำงานร่วมกันของทีมสำหรับโปรเจกต์ `CS341-TuamYang` ทั้งการรันทดสอบบนเครื่อง, การ Deploy ลง **AWS Academy Learner Lab (`dev`)** ของแต่ละคน และการนำโค้ดขึ้น **Production (`prod`)** ผ่าน GitHub Actions CI/CD

---

## 1. ภาพรวมสภาพแวดล้อม: `dev` vs `prod`

| หัวข้อ | `dev` (AWS Academy Learner Lab) | `prod` (AWS Free Tier Production) |
|---|---|---|
| **วัตถุประสงค์** | สภาพแวดล้อมส่วนตัวของสมาชิกแต่ละคนสำหรับพัฒนาและทดสอบ | สภาพแวดล้อมหลักของทีมสำหรับเก็บข้อมูลจริงและใช้สาธิต |
| **AWS Region** | `us-east-1` (ข้อจำกัดของ Learner Lab) | `ap-southeast-1` (Singapore) |
| **CloudFormation Stack** | `tuamyang-dev` | `tuamyang-prod` |
| **IAM Role ของ Resources** | ใช้ `LabRole` (`ExistingRoleName=LabRole`) ทุกจุด | สร้าง Least-privilege Role แยกตามหน้าที่อัตโนมัติ (`ExistingRoleName=""`) |
| **S3 Data Bucket เริ่มต้น** | `tuamyang-dev-data-${AWS::AccountId}` (สร้างและเปิด EventBridge ให้อัตโนมัติ) | `tuamyang-prod-data-${AWS::AccountId}` (สร้างและเปิด EventBridge ให้อัตโนมัติ) |
| **วิธีสั่ง Deploy** | รันผ่าน CLI บนเครื่องตัวเอง (`sam deploy --config-env dev`) | **GitHub Actions (`cd-prod.yml`) เท่านั้น** ผ่าน GitHub OIDC หลัง Merge เข้า `main` |

---

## 2. วิธี Deploy ลง AWS Academy Learner Lab (`dev`) สำหรับสมาชิกในทีม

สมาชิกทุกคนสามารถ Deploy ระบบทั้งหมด (S3 Bucket + Lambda Ingestion + EventBridge Schedules + Step Functions Validation Pipeline) ลง Learner Lab ของตัวเองได้โดยชื่อ S3 Bucket ไม่ชนกัน:

### ขั้นตอนที่ 1: เปิด Learner Lab และตั้งค่า AWS Credentials
1. กด **Start Lab** บนหน้าเว็บ AWS Academy Learner Lab รอจนไฟเป็นสีเขียว
2. คลิก **AWS Details** → กด **Show** ที่หัวข้อ **AWS CLI** แล้วคัดลอกค่า `aws_access_key_id`, `aws_secret_access_key`, `aws_session_token` ไปใส่ในไฟล์ `~/.aws/credentials` (หรือ `%USERPROFILE%\.aws\credentials` บน Windows)
3. ตรวจสอบว่าเชื่อมต่อสำเร็จและ `LabRole` รองรับ Lambda, Step Functions และ EventBridge:
   ```bash
   aws sts get-caller-identity --region us-east-1
   aws iam get-role --role-name LabRole --query "Role.AssumeRolePolicyDocument.Statement[].Principal.Service" --output json
   ```

### ขั้นตอนที่ 2: Stage โค้ด, Build และ Deploy (`dev`)
รันคำสั่งต่อไปนี้จากโฟลเดอร์รากของโปรเจกต์:

```bash
python -X utf8 scripts/stage_lambda.py
sam build
sam deploy --config-env dev
```

สิ่งที่เกิดขึ้นโดยอัตโนมัติเมื่อรัน `sam deploy --config-env dev`:
- ใช้ค่าจาก `samconfig.toml` (`[dev.deploy.parameters]`): `Stage="dev"`, `ExistingRoleName="LabRole"`, `CreateDataBucket="true"`, `EnableStateMachine="true"`
- สร้าง S3 Bucket ชื่อ `tuamyang-dev-data-<AccountId ของคุณ>` พร้อมเปิด **EventBridge Notification** ให้ทันที
- สร้าง Lambda 3 ตัว (`tuamyang-dev-ingest-thaiwater`, `tuamyang-dev-ingest-traffy`, `tuamyang-dev-pipeline-step`), Step Functions (`tuamyang-dev-pipeline`) และ EventBridge Rules ครบทุกตัวโดยใช้ `LabRole`

### กรณีต้องการใช้ S3 Bucket เดิมที่มีข้อมูลอยู่แล้วใน `dev`
หากใน Learner Lab ของคุณมี Bucket เดิมอยู่แล้ว (เช่น `cs341-tuamyang-data`) และไม่ต้องการให้สร้าง Bucket ใหม่:
1. เปิด EventBridge Notification บน Bucket เดิม (ทำครั้งเดียว):
   ```bash
   aws s3api put-bucket-notification-configuration \
     --bucket cs341-tuamyang-data \
     --notification-configuration '{"EventBridgeConfiguration": {}}'
   ```
2. สั่ง Deploy โดยส่ง `--parameter-overrides`:
   ```bash
   python -X utf8 scripts/stage_lambda.py
   sam build
   sam deploy --config-env dev \
     --parameter-overrides Stage=dev ExistingRoleName=LabRole CreateDataBucket=false DataBucketName=cs341-tuamyang-data EnableStateMachine=true
   ```

---

## 3. ขั้นตอนการทำงานตั้งแต่เปิด PR จนถึง Deploy ขึ้น `prod`

```text
สร้าง Feature Branch ──► เปิด Pull Request เข้า main
                              │
                              ▼
              GitHub Actions รัน Job `ci` (ไม่ใช้ AWS)
       (Unit Tests + Traffy Checks + stage_lambda.py + cfn-lint + sam validate + sam build)
                              │
                              ▼
               อย่างน้อย 1 PR Approval + กด Merge เข้า main
                              │
                              ▼
         GitHub Actions รัน `cd-prod.yml` (เมื่อไฟล์ใน paths เปลี่ยน)
                              │
                              ▼
        รอ Reviewer กด Approve ใน Environment `production`
             (`zero-h0ur` หรือ `PhurinKaewpuangsek`)
                              │
                              ▼
        Assume `TuamYangGitHubDeployerRole` ผ่าน OIDC (`ap-southeast-1`)
         ──► `sam build` ──► `sam deploy --config-env prod --role-arn ...`
         ──► Smoke Verification (Stack Status + Lambda DryRun + EventBridge ENABLED)
```

### Path Filter ของ `cd-prod.yml`
Workflow `cd-prod.yml` จะทำงานอัตโนมัติเมื่อ Merge เข้า `main` เฉพาะเมื่อมีการแก้ไขไฟล์ที่เกี่ยวข้องกับระบบรันจริง ได้แก่:
- `template.yaml`, `samconfig.toml`
- `src/**`, `config/**`, `reference/**`, `statemachine/**`
- `scripts/stage_lambda.py`
- `.github/workflows/cd-prod.yml`

*(หากแก้ไขเฉพาะเอกสารใน `docs/` หรือ `README.md` จะรันเฉพาะ `ci.yml` ไม่ทริกเกอร์ `cd-prod.yml` หรือหากต้องการสั่ง Deploy ซ้ำโดยไม่มี Commit ใหม่ สามารถกดปุ่ม **Run workflow** (`workflow_dispatch`) ในหน้า Actions ของ `cd-prod.yml` ได้)*

---

## 4. ตารางสรุป: อะไรทำงานอัตโนมัติ vs อะไรต้องทำมือ

| งาน | ประเภท | ผู้รับผิดชอบ / วิธีทำ |
|---|---|---|
| รัน Unit Tests, `cfn-lint`, `sam validate`, `sam build` ทุก PR | **อัตโนมัติ (`ci.yml`)** | GitHub Actions รันทุกครั้งที่เปิด/อัปเดต PR และเมื่อ Push เข้า `main` |
| Deploy Stack `tuamyang-prod` ขึ้น `ap-southeast-1` | **อัตโนมัติหลัง Approve (`cd-prod.yml`)** | GitHub Actions รันเมื่อ Merge เข้า `main` และผ่านการกด Approve |
| กด Approve การ Deploy ขึ้น `production` | **ทำมือบน GitHub UI** | `zero-h0ur` หรือ `PhurinKaewpuangsek` กด Review deployments ในหน้า Actions |
| Deploy ลง `dev` (Learner Lab `us-east-1`) | **ทำมือบนเครื่องตัวเอง** | สมาชิกแต่ละคนรัน `python -X utf8 scripts/stage_lambda.py && sam build && sam deploy --config-env dev` |
| อัปเดต IAM Bootstrap (`github-oidc.yaml`) | **ทำมือบน AWS Prod** | Admin รัน `aws cloudformation deploy --stack-name tuamyang-github-oidc ...` ก่อน Merge PR ที่เพิ่ม Resource ชนิดใหม่ |
| รัน Backfill ข้อมูลย้อนหลัง | **ทำมือ (อยู่นอก CD)** | รันผ่านสคริปต์ Backfill โดยผู้ได้รับอนุญาต ห้ามใส่ไว้ใน CD Pipeline |

---

## 5. กฎเหล็กสำหรับสมาชิกและ AI Agent (Hard Rules)

1. **ห้ามให้ GitHub Actions สั่ง Deploy ไป `dev`:** ไม่มี Workflow ใดใช้ `--config-env dev` เพราะ Learner Lab ไม่รองรับ OIDC และใช้ Session Token ชั่วคราว
2. **`sam deploy --config-env prod` อยู่ใน `cd-prod.yml` เท่านั้น:** ห้ามใส่ใน `ci.yml` และห้ามรันบน `pull_request`
3. **ห้ามใส่คำสั่งทำลายข้อมูลหรือ Backfill ใน CD:** ห้ามมี `aws s3 rm`, `aws s3 rb`, `aws cloudformation delete-stack` หรือการรัน Backfill ใน `.github/workflows/cd-prod.yml`
4. **ทุกคำสั่ง Python ต้องใส่ `-X utf8`:** เช่น `python -X utf8 scripts/stage_lambda.py` และ `python -X utf8 -m unittest discover -s tests -v` เพื่อรองรับภาษาไทยทั้งบน Windows และ Linux
5. **เมื่อเพิ่ม Resource ชนิดใหม่ต้องอัปเดต Bootstrap Role ใน PR เดียวกัน:** แก้ไข `TuamYangCfnExecutionRole` ใน `infrastructure/bootstrap/github-oidc.yaml` และแจ้ง Admin ให้ Deploy Bootstrap Stack ก่อน Merge
6. **เมื่อเปลี่ยนขั้นตอนการ Deploy ต้องอัปเดตคู่มือนี้ใน PR เดียวกัน:** หากเปลี่ยนชื่อ Workflow, Path Filter, ชื่อ Variable หรือคำสั่ง `sam deploy` ต้องแก้ไข `docs/cicd_team_guide.md` ให้ตรงกันเสมอ
7. **หาก `sam deploy` บน `dev` ฟ้อง `already exists`:** ให้หยุดตรวจสอบทันที ห้ามลบ Resource เดิมของเพื่อนร่วมทีม

---

## 6. การ Rollback และคำถามที่พบบ่อย (FAQ)

### Q1: ถ้า Deploy ขึ้น `prod` แล้วเกิดปัญหาระหว่างอัปเดต Stack จะเกิดอะไรขึ้น?
- CloudFormation จะทำ **Automatic Rollback** กลับไปยังสถานะล่าสุดที่ทำงานได้ (`UPDATE_ROLLBACK_COMPLETE`) และเนื่องจาก `DataBucket` ตั้งค่า `DeletionPolicy: Retain` และ `UpdateReplacePolicy: Retain` ข้อมูลใน S3 จะไม่ถูกลบไม่ว่ากรณีใด
- หากโค้ดใน `main` มีบั๊กหลัง Deploy สำเร็จ ให้เปิด PR ทำ `git revert` เพื่อผ่าน `ci` และให้ `cd-prod.yml` Deploy เวอร์ชันก่อนหน้าขึ้นไปแทน

### Q2: ทำไมต้องรัน `python -X utf8 scripts/stage_lambda.py` ก่อน `sam build`?
- เพื่อคัดลอกเฉพาะโค้ดที่แต่ละ Lambda ต้องใช้ไปยัง `.aws-sam/stage/thaiwater/` และ `.aws-sam/stage/traffy/` โดยไม่เผลอรวมโฟลเดอร์ `data/`, `evidence/`, `.git/` หรือไฟล์ `.env` เข้าไปในแพ็กเกจ Lambda

### Q3: ถ้าต้องการเปิดตารางเวลาดึงข้อมูล Traffy รายชั่วโมง (`tuamyang-${Stage}-traffy-hourly`) ทำอย่างไร?
- ค่าเริ่มต้นคือ `EnableTraffyHourly=false` (สร้าง Rule ไว้ในสถานะ `DISABLED` เพื่อประหยัดโควตา) หากต้องการเปิดใช้งาน สามารถส่ง `EnableTraffyHourly=true` ใน `parameter_overrides` ได้ทันที
