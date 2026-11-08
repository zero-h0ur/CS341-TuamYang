# Exercise deployment argument construction with mocked AWS; no cloud calls.
$ErrorActionPreference = "Stop"
$global:TraffyDeploymentTestCalls = [System.Collections.ArrayList]::new()
$global:TraffyDeploymentTestFailTargets = $false
function global:aws {
    param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CliArguments)
    [void]$global:TraffyDeploymentTestCalls.Add(@($CliArguments))
    $global:LASTEXITCODE = 0
    $operation = "$($CliArguments[0]) $($CliArguments[1])"
    switch ($operation) {
        "sts get-caller-identity" { '{"Account":"123456789012"}' }
        "s3api head-bucket" { '{}' }
        "lambda list-functions" { '{"Functions":[]}' }
        "lambda create-function" { '{}' }
        "lambda wait" { '{}' }
        "lambda get-policy" { '{"Policy":"{\"Statement\":[]}"}' }
        "lambda add-permission" { '{}' }
        "events put-rule" {
            $index = [array]::IndexOf($CliArguments, "--cli-input-json")
            $path = $CliArguments[$index + 1].Substring(7)
            $definition = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
            @{ RuleArn = "arn:aws:events:us-east-1:123456789012:rule/$($definition.Name)" } | ConvertTo-Json -Compress
        }
        "events put-targets" {
            if ($global:TraffyDeploymentTestFailTargets) { '{"FailedEntryCount":1}' }
            else { '{"FailedEntryCount":0}' }
        }
        default { throw "Unexpected AWS operation in deployment test: $operation" }
    }
}
try {
    if ((Get-Command aws).CommandType -ne "Function") { throw "AWS mock is not active" }
    $root = Split-Path -Parent $PSScriptRoot
    & (Join-Path $root "infrastructure/deploy_traffy.ps1") -BucketName "fixture-bucket" -FunctionName "fixture-traffy" -EnableDailySchedule -EnableHourlySchedule | Out-Null
    $targets = @($global:TraffyDeploymentTestCalls | Where-Object { $_[0] -eq "events" -and $_[1] -eq "put-targets" })
    if ($targets.Count -ne 2) { throw "Both schedules must register a target" }
    foreach ($targetArgs in $targets) {
        $index = [array]::IndexOf($targetArgs, "--targets")
        $path = $targetArgs[$index + 1].Substring(7)
        $target = @(Get-Content -LiteralPath $path -Raw | ConvertFrom-Json)[0]
        $event = $target.Input | ConvertFrom-Json
        if ($target.Id -eq "tuamyang-traffy-hourly" -and $event.mode -ne "hourly") { throw "Hourly mode missing" }
        if ($target.Id -eq "tuamyang-traffy-daily" -and (-not $event.force -or $event.mode -ne "daily")) { throw "Daily refresh must force yesterday" }
    }
    $hourly = Get-Content -LiteralPath (Join-Path $root "infrastructure/traffy_hourly_rule.json") -Raw | ConvertFrom-Json
    if ($hourly.ScheduleExpression -ne "cron(0 * * * ? *)") { throw "Hourly cron is incorrect" }
    $global:TraffyDeploymentTestFailTargets = $true
    $failedAsExpected = $false
    try {
        & (Join-Path $root "infrastructure/deploy_traffy.ps1") -BucketName "fixture-bucket" -FunctionName "fixture-traffy" -EnableHourlySchedule | Out-Null
    } catch {
        if ($_.Exception.Message -match "target registration failed") { $failedAsExpected = $true }
        else { throw }
    }
    if (-not $failedAsExpected) { throw "Failed target registration must not be reported as success" }
    Write-Output "PASS: daily/hourly modes, cron, and failed target registration (mocked AWS only)"
} finally {
    Remove-Item -LiteralPath Function:\aws
    Remove-Variable -Name TraffyDeploymentTestCalls,TraffyDeploymentTestFailTargets -Scope Global
}
