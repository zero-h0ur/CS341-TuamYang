#!/bin/bash
# Deploy ingest_thaiwater in AWS CloudShell / Linux (AWS Academy Learner Lab)
# Owner: Member 1 (PurinatWanthanathanya)

set -e

BUCKET_NAME=${1:-"cs341-tuamyang-data"}
FUNCTION_NAME=${2:-"ingest_thaiwater"}
REGION=${3:-"us-east-1"}

echo "========================================================"
echo ">>> AWS Academy Learner Lab CloudShell Deployment"
echo "========================================================"

# 1. Identity Check
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
LAB_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/LabRole"
echo " Authenticated as Account: ${ACCOUNT_ID}"
echo " Using Learner Lab role: ${LAB_ROLE_ARN}"

# 2. Package Code (src, config, reference) into ZIP
echo -e "\n[1/4] Packaging Lambda deployment zip..."
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

rm -rf dist
mkdir -p dist/package
cp -r src config reference dist/package/
cd dist/package
zip -r ../ingest_thaiwater.zip . -q
cd "$PROJECT_ROOT"
rm -rf dist/package
echo " Created package: dist/ingest_thaiwater.zip"

# 3. S3 Bucket
echo -e "\n[2/4] Ensuring S3 Bucket exists: s3://${BUCKET_NAME}..."
aws s3 mb "s3://${BUCKET_NAME}" --region "${REGION}" 2>/dev/null || true

# 4. Deploy or Update Lambda
echo -e "\n[3/4] Deploying Lambda function '${FUNCTION_NAME}'..."
if aws lambda get-function --function-name "${FUNCTION_NAME}" --region "${REGION}" >/dev/null 2>&1; then
    echo " Updating existing function code and config..."
    aws lambda update-function-code \
        --function-name "${FUNCTION_NAME}" \
        --zip-file "fileb://dist/ingest_thaiwater.zip" \
        --region "${REGION}" >/dev/null

    aws lambda update-function-configuration \
        --function-name "${FUNCTION_NAME}" \
        --timeout 300 \
        --memory-size 256 \
        --environment "Variables={STORAGE_BACKEND=s3,S3_BUCKET_NAME=${BUCKET_NAME}}" \
        --region "${REGION}" >/dev/null
else
    echo " Creating new Lambda function with LabRole..."
    aws lambda create-function \
        --function-name "${FUNCTION_NAME}" \
        --runtime python3.12 \
        --role "${LAB_ROLE_ARN}" \
        --handler "src.ingest_thaiwater.lambda_handler" \
        --timeout 300 \
        --memory-size 256 \
        --zip-file "fileb://dist/ingest_thaiwater.zip" \
        --environment "Variables={STORAGE_BACKEND=s3,S3_BUCKET_NAME=${BUCKET_NAME}}" \
        --region "${REGION}" >/dev/null
fi
echo " Lambda function '${FUNCTION_NAME}' deployed successfully!"

# 5. EventBridge Rules
echo -e "\n[4/4] Configuring EventBridge Rules (15-min WaterLevel, 1-hr Rain)..."
LAMBDA_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"

# Rule 1: Water Level (every 15 mins)
RULE_WATER="tuamyang-thaiwater-waterlevel-15min"
aws events put-rule \
    --name "${RULE_WATER}" \
    --schedule-expression "rate(15 minutes)" \
    --state ENABLED \
    --description "Trigger ingest_thaiwater waterlevel every 15 min" \
    --region "${REGION}" >/dev/null

aws events put-targets \
    --rule "${RULE_WATER}" \
    --targets "Id=TargetWaterLevel,Arn=${LAMBDA_ARN},Input={\"type\":\"waterlevel\"}" \
    --region "${REGION}" >/dev/null

aws lambda add-permission \
    --function-name "${FUNCTION_NAME}" \
    --statement-id "AllowEventBridgeWaterLevel" \
    --action "lambda:InvokeFunction" \
    --principal "events.amazonaws.com" \
    --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${RULE_WATER}" \
    --region "${REGION}" 2>/dev/null || true

# Rule 2: Rain (every 1 hour)
RULE_RAIN="tuamyang-thaiwater-rain-1hr"
aws events put-rule \
    --name "${RULE_RAIN}" \
    --schedule-expression "rate(1 hour)" \
    --state ENABLED \
    --description "Trigger ingest_thaiwater rain every 1 hour" \
    --region "${REGION}" >/dev/null

aws events put-targets \
    --rule "${RULE_RAIN}" \
    --targets "Id=TargetRain,Arn=${LAMBDA_ARN},Input={\"type\":\"rain\"}" \
    --region "${REGION}" >/dev/null

aws lambda add-permission \
    --function-name "${FUNCTION_NAME}" \
    --statement-id "AllowEventBridgeRain" \
    --action "lambda:InvokeFunction" \
    --principal "events.amazonaws.com" \
    --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${RULE_RAIN}" \
    --region "${REGION}" 2>/dev/null || true

echo " EventBridge rules configured successfully!"

echo -e "\n========================================================"
echo ">>> DEPLOYMENT COMPLETE IN CLOUDSHELL!"
echo "========================================================"
echo "Run live ingestion directly in CloudShell:"
echo "  python3 -m src.ingest_thaiwater --type all --backend s3"
echo "  python3 -m src.backfill --type case_study --backend s3"
echo "  aws s3 ls s3://${BUCKET_NAME}/raw/thaiwater/ --recursive"
