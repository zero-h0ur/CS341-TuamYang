# Setup EventBridge Scheduler via AWS CLI (PowerShell)
# Owner: Member 1 (ThaiWater Ingestion)

param(
    [string]$LambdaArn = "arn:aws:lambda:us-east-1:123456789012:function:ingest_thaiwater",
    [string]$RuleWater = "tuamyang-thaiwater-waterlevel-15min",
    [string]$RuleRain = "tuamyang-thaiwater-rain-1hr"
)

Write-Host ">>> Configuring EventBridge Rules for CS341-TuamYang..." -ForegroundColor Cyan

# 1. Create 15-minute Rule for Water Level
Write-Host "Creating Water Level rule (15 min)..."
aws events put-rule `
    --name $RuleWater `
    --schedule-expression "rate(15 minutes)" `
    --state ENABLED `
    --description "Triggers ingest_thaiwater for canal water level every 15 minutes"

aws events put-targets `
    --rule $RuleWater `
    --targets "Id"="ThaiWaterTargetWater","Arn"="$LambdaArn","Input"="{\"type\":\"waterlevel\"}"

# 2. Create 1-hour Rule for Rain
Write-Host "Creating Rain rule (1 hour)..."
aws events put-rule `
    --name $RuleRain `
    --schedule-expression "rate(1 hour)" `
    --state ENABLED `
    --description "Triggers ingest_thaiwater for rainfall telemetry every 1 hour"

aws events put-targets `
    --rule $RuleRain `
    --targets "Id"="ThaiWaterTargetRain","Arn"="$LambdaArn","Input"="{\"type\":\"rain\"}"

Write-Host ">>> EventBridge rules configured successfully!" -ForegroundColor Green

