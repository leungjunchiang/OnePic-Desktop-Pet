#requires -Version 7.2

[CmdletBinding()]
param(
    [string]$MigrationPath = "supabase/migrations/20260930100000_lili_discipline_state.sql",
    [string]$ConfigPath = "config/social_backend.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$projectRef = if (-not [string]::IsNullOrWhiteSpace($env:SUPABASE_PROJECT_REF)) {
    $env:SUPABASE_PROJECT_REF.Trim()
} else {
    ([Uri]$config.supabase_url).Host.Split('.')[0]
}
$token = $env:SUPABASE_ACCESS_TOKEN
if ([string]::IsNullOrWhiteSpace($projectRef) -or [string]::IsNullOrWhiteSpace($token)) {
    throw "Supabase project ref or access token is missing."
}
$sql = Get-Content -Raw -LiteralPath $MigrationPath
if ([string]::IsNullOrWhiteSpace($sql)) {
    throw "Discipline migration is empty."
}
$uri = "https://api.supabase.com/v1/projects/$([Uri]::EscapeDataString($projectRef))/database/query"
$headers = @{ Authorization = "Bearer $token"; Accept = "application/json" }
$body = @{ query = $sql } | ConvertTo-Json -Compress
$null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType "application/json" -Body $body

# Verify schema and RPC names only; no personal plans or reports are read.
$verify = @{ query = @"
select to_regclass('public.lili_discipline_settings') is not null as settings_ready,
       to_regclass('public.lili_discipline_events') is not null as events_ready,
       to_regclass('public.lili_discipline_supervisor_access') is not null as consent_ready,
       to_regprocedure('public.lili_discipline_sync(jsonb,timestamptz,jsonb)') is not null as sync_ready,
       to_regprocedure('public.lili_discipline_supervisor_snapshot()') is not null as consent_rpc_ready,
       to_regprocedure('public.lili_discipline_supervisor_report(uuid)') is not null as report_ready;
"@ } | ConvertTo-Json -Compress
$result = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType "application/json" -Body $verify
$row = @($result)[0]
if (-not ($row.settings_ready -and $row.events_ready -and $row.consent_ready -and
          $row.sync_ready -and $row.consent_rpc_ready -and $row.report_ready)) {
    throw "Discipline migration verification failed."
}
Write-Host "Discipline sync and consent schema/RPCs verified."
if ($MigrationPath -like '*lili_buddy_study_permissions.sql') {
    $verificationSql = Get-Content -Raw -LiteralPath "scripts/verify_buddy_study_permissions.sql"
    $verificationBody = @{ query = $verificationSql } | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType "application/json" -Body $verificationBody
    Write-Host "Buddy study RPC consent, field opt-outs, revocation, and non-buddy denial verified; synthetic fixtures rolled back."
}
