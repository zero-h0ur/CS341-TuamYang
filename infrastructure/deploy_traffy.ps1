# Creates or updates only the Traffy Lambda, using the existing Learner Lab role.
# Daily scheduling is opt-in. Run from PowerShell with valid AWS credentials.
param(
    [Parameter(Mandatory=$true)][string]$BucketName,
    [string]$Region = "us-east-1",
    [string]$FunctionName = "ingest_traffy",
    [switch]$EnableDailySchedule,
    [switch]$EnableHourlySchedule
)
$ErrorActionPreference = "Stop"

function Invoke-AwsCli {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CliArguments)
    $result = & aws @CliArguments
    if ($LASTEXITCODE -ne 0) { throw "AWS CLI failed: $($CliArguments[0]) $($CliArguments[1])" }
    return $result
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $identity = Invoke-AwsCli sts get-caller-identity --output json | ConvertFrom-Json
    $roleArn = "arn:aws:iam::$($identity.Account):role/LabRole"
    # Require the team's existing bucket; do not create/replace shared resources.
    Invoke-AwsCli s3api head-bucket --bucket $BucketName --region $Region | Out-Null
    & python -X utf8 -m scripts.package_traffy
    if ($LASTEXITCODE -ne 0) { throw "Packaging failed" }
    $environmentPath = Join-Path $projectRoot "dist/traffy_environment.json"
    $environment = @{ Variables = @{ STORAGE_BACKEND = "s3"; S3_BUCKET_NAME = $BucketName } }
    [System.IO.File]::WriteAllText($environmentPath, ($environment | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))
    # List functions first so permission/network failures are not mistaken for
    # a missing function and followed by an unintended create.
    $listing = Invoke-AwsCli lambda list-functions --region $Region --output json | ConvertFrom-Json
    $existing = $listing.Functions | Where-Object { $_.FunctionName -eq $FunctionName }
    if ($existing) {
        Invoke-AwsCli lambda update-function-code --function-name $FunctionName --zip-file fileb://dist/ingest_traffy.zip --region $Region | Out-Null
        Invoke-AwsCli lambda wait function-updated --function-name $FunctionName --region $Region | Out-Null
        Invoke-AwsCli lambda update-function-configuration --function-name $FunctionName --handler src.ingest_traffy.lambda_handler --timeout 300 --memory-size 256 --environment "file://$environmentPath" --region $Region | Out-Null
        Invoke-AwsCli lambda wait function-updated --function-name $FunctionName --region $Region | Out-Null
    } else {
        Invoke-AwsCli lambda create-function --function-name $FunctionName --runtime python3.12 --role $roleArn --handler src.ingest_traffy.lambda_handler --timeout 300 --memory-size 256 --zip-file fileb://dist/ingest_traffy.zip --environment "file://$environmentPath" --region $Region | Out-Null
    }
    Invoke-AwsCli lambda wait function-active --function-name $FunctionName --region $Region | Out-Null
    $schedules = @()
    if ($EnableDailySchedule) {
        $schedules += @{ Name = "tuamyang-traffy-daily"; File = "infrastructure/traffy_daily_rule.json"; Sid = "traffy-daily-rule"; Input = '{"mode":"daily","storage_backend":"s3","force":true}' }
    }
    if ($EnableHourlySchedule) {
        $schedules += @{ Name = "tuamyang-traffy-hourly"; File = "infrastructure/traffy_hourly_rule.json"; Sid = "traffy-hourly-rule"; Input = '{"mode":"hourly","storage_backend":"s3"}' }
    }
    foreach ($schedule in $schedules) {
        $rule = Invoke-AwsCli events put-rule --cli-input-json "file://$($schedule.File)" --region $Region --output json | ConvertFrom-Json
        # A new Lambda has no resource policy yet. Only ResourceNotFound is an
        # expected absence; permission/network errors must still stop the run.
        $policyErrorPath = Join-Path $projectRoot "dist/traffy_policy_error.txt"
        $previousErrorPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            $policyOutput = & aws lambda get-policy --function-name $FunctionName --region $Region --output json 2> $policyErrorPath
            $policyExit = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousErrorPreference
        }
        $permission = $null
        if ($policyExit -eq 0) {
            $permission = $policyOutput | ConvertFrom-Json
        } elseif ((Get-Content -LiteralPath $policyErrorPath -Raw) -notmatch "ResourceNotFoundException") {
            throw "Cannot read Lambda resource policy. Check AWS permissions and credentials."
        }
        $statementExists = $false
        if ($permission -and $permission.Policy) {
            $statementExists = @((($permission.Policy | ConvertFrom-Json).Statement | Where-Object { $_.Sid -eq $schedule.Sid })).Count -gt 0
        }
        if (-not $statementExists) {
            Invoke-AwsCli lambda add-permission --function-name $FunctionName --statement-id $schedule.Sid --action lambda:InvokeFunction --principal events.amazonaws.com --source-arn $rule.RuleArn --region $Region | Out-Null
        }
        $targetPath = Join-Path $projectRoot "dist/$($schedule.Name)_target.json"
        $target = @(@{ Id = $schedule.Name; Arn = "arn:aws:lambda:${Region}:$($identity.Account):function:${FunctionName}"; Input = $schedule.Input })
        [System.IO.File]::WriteAllText($targetPath, (ConvertTo-Json -InputObject $target -Depth 4), [System.Text.UTF8Encoding]::new($false))
        $targetsResult = Invoke-AwsCli events put-targets --rule $schedule.Name --targets "file://$targetPath" --region $Region --output json | ConvertFrom-Json
        if ($targetsResult.FailedEntryCount -gt 0) { throw "EventBridge target registration failed" }
    }
    Write-Host "Traffy Lambda ready: $FunctionName ($Region). No invocation was performed."
} finally {
    Pop-Location
}
