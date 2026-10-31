# Deploy ingest_thaiwater to AWS Academy Learner Lab
# Owner: Member 1 (PurinatWanthanathanya)

param(
    [string]$BucketName = "cs341-tuamyang-data",
    [string]$FunctionName = "ingest_thaiwater",
    [string]$Region = "us-east-1"
)

$ErrorActionPreference = "Stop"

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host ">>> AWS Academy Learner Lab Deployment Script" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

# 1. Verify AWS CLI Connection and get Account ID
Write-Host "`n[1/5] Checking AWS credentials..." -ForegroundColor Yellow
try {
    $identityJson = aws sts get-caller-identity --output json | ConvertFrom-Json
    $accountId = $identityJson.Account
    Write-Host "  Authenticated as Account: $accountId" -ForegroundColor Green
} catch {
    Write-Host "[!] AWS CLI is not logged in. Copy credentials from Learner Lab 'AWS Details' into ~/.aws/credentials" -ForegroundColor Red
    exit 1
}

$labRoleArn = "arn:aws:iam::${accountId}:role/LabRole"
Write-Host "  Using Learner Lab pre-existing role: $labRoleArn" -ForegroundColor Green

# 2. Package Code (src, config, reference) into ZIP
Write-Host "`n[2/5] Packaging Lambda code..." -ForegroundColor Yellow
$projectRoot = Split-Path -Parent $PSScriptRoot
$distDir = Join-Path $projectRoot "dist"
$zipPath = Join-Path $distDir "ingest_thaiwater.zip"

if (Test-Path $distDir) { Remove-Item -Recurse -Force $distDir }
New-Item -ItemType Directory -Path $distDir | Out-Null

$tempPackageDir = Join-Path $distDir "package"
New-Item -ItemType Directory -Path $tempPackageDir | Out-Null

# Copy required directories
Copy-Item -Recurse (Join-Path $projectRoot "src") (Join-Path $tempPackageDir "src")
Copy-Item -Recurse (Join-Path $projectRoot "config") (Join-Path $tempPackageDir "config")
Copy-Item -Recurse (Join-Path $projectRoot "reference") (Join-Path $tempPackageDir "reference")

# Compress archive
Compress-Archive -Path "$tempPackageDir\*" -DestinationPath $zipPath -Force
Remove-Item -Recurse -Force $tempPackageDir

$zipSizeKb = [math]::Round((Get-Item $zipPath).Length / 1KB, 1)
Write-Host "  Created package: $zipPath ($zipSizeKb KB)" -ForegroundColor Green

# 3. Create S3 Bucket if it doesn't exist
Write-Host "`n[3/5] Verifying S3 bucket: $BucketName..." -ForegroundColor Yellow
aws s3 mb "s3://$BucketName" --region $Region 2>$null
Write-Host "  S3 bucket ready: s3://$BucketName" -ForegroundColor Green

# 4. Create or Update Lambda Function
Write-Host "`n[4/5] Deploying Lambda function '$FunctionName'..." -ForegroundColor Yellow
$lambdaExists = aws lambda get-function --function-name $FunctionName --region $Region 2>$null

if ($lambdaExists) {
    Write-Host "  Updating existing function code..."
    aws lambda update-function-code `
        --function-name $FunctionName `
        --zip-file "fileb://$zipPath" `
        --region $Region | Out-Null

    aws lambda update-function-configuration `
        --function-name $FunctionName `
        --timeout 300 `
        --memory-size 256 `
        --environment "Variables={STORAGE_BACKEND=s3,S3_BUCKET_NAME=$BucketName}" `
        --region $Region | Out-Null
} else {
    Write-Host "  Creating new Lambda function with LabRole..."
    aws lambda create-function `
        --function-name $FunctionName `
        --runtime python3.12 `
        --role $labRoleArn `
        --handler "src.ingest_thaiwater.lambda_handler" `
        --timeout 300 `
        --memory-size 256 `
        --zip-file "fileb://$zipPath" `
        --environment "Variables={STORAGE_BACKEND=s3,S3_BUCKET_NAME=$BucketName}" `
        --region $Region | Out-Null
}
Write-Host "  Lambda function deployed successfully!" -ForegroundColor Green

# 5. Configure EventBridge Rules (15 min water, 1 hr rain)
Write-Host "`n[5/5] Configuring EventBridge Schedule Rules..." -ForegroundColor Yellow
$lambdaArn = "arn:aws:lambda:${Region}:${accountId}:function:${FunctionName}"

# Rule 1: Water Level (every 15 mins)
$ruleWater = "tuamyang-thaiwater-waterlevel-15min"
aws events put-rule `
    --name $ruleWater `
    --schedule-expression "rate(15 minutes)" `
    --state ENABLED `
    --description "Trigger ingest_thaiwater waterlevel every 15 min" `
    --region $Region | Out-Null

aws events put-targets `
    --rule $ruleWater `
    --targets "Id"="TargetWaterLevel","Arn"="$lambdaArn","Input"="{\"type\":\"waterlevel\"}" `
    --region $Region | Out-Null

aws lambda add-permission `
    --function-name $FunctionName `
    --statement-id "AllowEventBridgeWaterLevel" `
    --action "lambda:InvokeFunction" `
    --principal "events.amazonaws.com" `
    --source-arn "arn:aws:events:${Region}:${accountId}:rule/${ruleWater}" `
    --region $Region 2>$null

# Rule 2: Rain (every 1 hour)
$ruleRain = "tuamyang-thaiwater-rain-1hr"
aws events put-rule `
    --name $ruleRain `
    --schedule-expression "rate(1 hour)" `
    --state ENABLED `
    --description "Trigger ingest_thaiwater rain every 1 hour" `
    --region $Region | Out-Null

aws events put-targets `
    --rule $ruleRain `
    --targets "Id"="TargetRain","Arn"="$lambdaArn","Input"="{\"type\":\"rain\"}" `
    --region $Region | Out-Null

aws lambda add-permission `
    --function-name $FunctionName `
    --statement-id "AllowEventBridgeRain" `
    --action "lambda:InvokeFunction" `
    --principal "events.amazonaws.com" `
    --source-arn "arn:aws:events:${Region}:${accountId}:rule/${ruleRain}" `
    --region $Region 2>$null

Write-Host "  EventBridge rules configured and granted permissions!" -ForegroundColor Green

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host ">>> DEPLOYMENT COMPLETE! HOW TO TEST & CAPTURE EVIDENCE:" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "1. Test Lambda Invocation manually:"
Write-Host "   aws lambda invoke --function-name $FunctionName --payload '{\"type\":\"waterlevel\"}' --cli-binary-format raw-in-base64-out response.json"
Write-Host "   Get-Content response.json"
Write-Host "`n2. Verify files landed on S3:"
Write-Host "   aws s3 ls s3://$BucketName/incoming/thaiwater/ --recursive"
Write-Host "   aws s3 ls s3://$BucketName/raw/thaiwater/ --recursive"
Write-Host "`n3. Test Learner Lab session persistence:"
Write-Host "   a) Note the current time."
Write-Host "   b) Click 'End Session' on AWS Academy Learner Lab."
Write-Host "   c) Wait 30-40 minutes (so at least two 15-minute runs occur)."
Write-Host "   d) Start a new session, run: python infrastructure/check_learner_lab.py --backend s3"
Write-Host "   e) Capture screenshots of S3 listings and CloudWatch logs for your PR evidence!"

