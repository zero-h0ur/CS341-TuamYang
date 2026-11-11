# คู่มือตั้งค่า Admin Bootstrap & GitHub OIDC (`docs/cicd_setup.md`)

**Owner:** นายภูรินทร์ แก้วพ่วงเสก (`@PhurinKaewpuangsek`) & นายปพนพัชร์ มีวน (`@zero-h0ur`)
**คู่มือสำหรับสมาชิกในทีม:** ดูที่ [docs/cicd_team_guide.md](cicd_team_guide.md)

เอกสารนี้อธิบายขั้นตอนการตั้งค่าระบบ **2-Layer IAM Bootstrap** บน AWS Production Account (`ap-southeast-1`) และการผูก **GitHub OIDC + Environment Protection Rules** สำหรับโปรเจกต์ `CS341-TuamYang`

---

## 1. สถาปัตยกรรม IAM แบบ 2 ชั้น (2-Layer IAM Architecture)

เพื่อไม่ให้มีการเก็บ Long-lived AWS Access Keys บน GitHub และแยกสิทธิ์ออกจากโปรเจกต์อื่นในบัญชีเดียวกัน (`perimeter` ใช้ OIDC Provider ตัวเดียวกันแต่คนละ IAM Role 100%) ระบบใช้ Role 2 ตัวที่สร้างจาก `infrastructure/bootstrap/github-oidc.yaml`:

1. **`TuamYangGitHubDeployerRole`** (Assumed โดย GitHub Actions ผ่าน OIDC):
   - Trust Policy จำกัดเฉพาะ Repository `zero-h0ur/CS341-TuamYang` และ GitHub Environment `production` เท่านั้น (`token.actions.githubusercontent.com:sub`)
   - มีสิทธิ์สั่ง CloudFormation บน Stack `tuamyang-*` และ `aws-sam-cli-managed-default`, อ่าน/เขียน S3 Artifact Bucket ของ SAM (`aws-sam-cli-managed-default-*`), ส่ง `iam:PassRole` ไปยัง `TuamYangCfnExecutionRole` และรันคำสั่ง Smoke Verification หลัง Deploy
2. **`TuamYangCfnExecutionRole`** (CloudFormation Execution Role ส่งผ่าน `--role-arn`):
   - CloudFormation Assume Role นี้เพื่อสร้าง/แก้ไข/ลบ Resource จริงของโปรเจกต์ภายใต้ Prefix `tuamyang-*` ได้แก่:
     - S3 Data Lake Bucket (`tuamyang-prod-data-${AWS::AccountId}`)
     - Lambda Functions (`tuamyang-prod-ingest-thaiwater`, `tuamyang-prod-ingest-traffy`, `tuamyang-prod-pipeline-step`)
     - Step Functions State Machine (`tuamyang-prod-pipeline`) พร้อมสิทธิ์ `states:*`
     - EventBridge Rules (`tuamyang-prod-*`) พร้อมสิทธิ์ `events:PutRule`, `events:PutTargets`, `events:DeleteRule`, `events:RemoveTargets`, `events:DescribeRule`
     - CloudWatch LogGroups (`/aws/lambda/tuamyang-*`)
     - Least-privilege IAM Roles ที่ CloudFormation สร้างขึ้นโดยมีชื่อขึ้นต้นด้วย `tuamyang-*`

---

## 2. ขั้นตอนบน AWS Production Account (`ap-southeast-1`)

### 2.1 Cleanup & Pre-flight ก่อน Deploy ครั้งแรก
1. **ตรวจสอบบัญชีและ OIDC Provider เดิม:**
   ```bash
   aws sts get-caller-identity
   aws iam list-open-id-connect-providers
   ```
   *(เนื่องจากแชร์ AWS Account กับ `perimeter` จึงมี `oidc-provider/token.actions.githubusercontent.com` อยู่แล้ว ต้องใช้ `CreateOidcProvider=false`)*
2. **ลบ Resource ที่เคยสร้างด้วยมือ (ClickOps Cleanup):**
   ```bash
   # ตรวจสอบว่า Bucket ว่างเปล่าก่อนลบ
   aws s3 ls s3://cs341-tuamyang --recursive
   aws s3 rb s3://cs341-tuamyang --region ap-southeast-1

   # ลบ IAM Role ที่สร้างด้วยมือ
   aws iam delete-role --role-name cs341-lambda-role
   ```
3. **จัดการ IAM User `pokpong` และตั้งงบประมาณ:**
   - ลบ IAM User `pokpong` หรือจำกัดสิทธิ์ให้เหลือเฉพาะ `ReadOnlyAccess` พร้อมเปิดใช้งาน **MFA**
   - ตั้ง **AWS Budgets Alert** (Zero-Spend หรือ Monthly Cost Budget `$1.00`) ในหน้า Billing and Cost Management

### 2.2 Deploy Stack `tuamyang-github-oidc`
รันคำสั่งต่อไปนี้ใน AWS CloudShell หรือเครื่องที่ถือสิทธิ์ Admin ของ Prod Account:

```bash
aws cloudformation deploy \
  --template-file infrastructure/bootstrap/github-oidc.yaml \
  --stack-name tuamyang-github-oidc \
  --region ap-southeast-1 \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    CreateOidcProvider=false \
    GitHubOrg=zero-h0ur \
    GitHubRepo=CS341-TuamYang \
    GitHubEnvironment=production
```

ดึงค่า ARN ทั้ง 2 ตัวจาก Stack Outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name tuamyang-github-oidc \
  --region ap-southeast-1 \
  --query "Stacks[0].Outputs[].[OutputKey,OutputValue]" \
  --output table
```

จะได้ค่า:
- `DeployerRoleArn` = `arn:aws:iam::<ACCOUNT_ID>:role/TuamYangGitHubDeployerRole`
- `CfnExecutionRoleArn` = `arn:aws:iam::<ACCOUNT_ID>:role/TuamYangCfnExecutionRole`

---

## 3. ขั้นตอนตั้งค่าบน GitHub Repository (`zero-h0ur/CS341-TuamYang`)

### 3.1 สร้าง GitHub Environment `production`
1. ไปที่ **Settings → Environments → New environment** ตั้งชื่อ **`production`**
2. เปิด **Required reviewers** และเพิ่ม:
   - `zero-h0ur`
   - `PhurinKaewpuangsek`
3. ตั้งค่า **Deployment branches and tags** เป็น **Selected branches and tags** → เพิ่มเฉพาะ Branch **`main`**
4. ในหัวข้อ **Environment variables** กด **Add environment variable** จำนวน 2 ตัว:
   - `AWS_DEPLOYER_ROLE_ARN` = ค่า `DeployerRoleArn` จากขั้นตอน 2.2
   - `AWS_CFN_EXECUTION_ROLE_ARN` = ค่า `CfnExecutionRoleArn` จากขั้นตอน 2.2

### 3.2 ตั้งค่า Branch Protection สำหรับ `main`
*(หมายเหตุ: เปิด Required Status Check `ci` หลังจากที่ไฟล์ `.github/workflows/ci.yml` ทำงานและรายงานสถานะ `ci` บน GitHub แล้ว)*
1. ไปที่ **Settings → Branches → Add branch protection rule**
2. ช่อง **Branch name pattern** ใส่ **`main`**
3. เปิด **Require a pull request before merging** → ตั้ง **Required number of approvals before merging = 1**
4. เปิด **Require status checks to pass before merging** → ค้นหาและเลือก Status Check ชื่อ **`ci`**

---

## 4. กฎการอัปเดตสิทธิ์เมื่อเพิ่ม Resource ชนิดใหม่ในอนาคต

ทุกครั้งที่เปิด Pull Request ที่เพิ่ม Resource ชนิดใหม่ลงใน `template.yaml` (เช่น Glue Data Catalog หรือ Athena WorkGroup ใน Phase 3):
1. ต้องเพิ่มสิทธิ์ที่จำเป็นให้กับ `TuamYangCfnExecutionRole` ในไฟล์ `infrastructure/bootstrap/github-oidc.yaml` **ใน PR เดียวกัน**
2. **สำคัญ:** Admin ของ Prod Account ต้องรันคำสั่ง `aws cloudformation deploy --stack-name tuamyang-github-oidc ...` (ตามหัวข้อ 2.2) **ด้วยมือก่อนกด Merge PR** มิฉะนั้นเมื่อ CD รันหลัง Merge จะเกิดข้อผิดพลาด `AccessDenied` เนื่องจาก `TuamYangCfnExecutionRole` บน AWS ยังไม่ได้รับสิทธิ์ใหม่
