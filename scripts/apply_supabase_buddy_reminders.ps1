#requires -Version 7.2

[CmdletBinding()]
param(
    [string]$MigrationPath = "supabase/migrations/20260929180000_lili_durable_buddy_reminders.sql",
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
    throw "Buddy reminder migration is empty."
}
$uri = "https://api.supabase.com/v1/projects/$([Uri]::EscapeDataString($projectRef))/database/query"
$headers = @{ Authorization = "Bearer $token"; Accept = "application/json" }
$body = @{ query = $sql } | ConvertTo-Json -Compress
$null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType "application/json" -Body $body

# Read back only schema metadata; no subscriber or work-event rows leave the database.
$verify = @{ query = @"
select to_regclass('public.lili_work_events') is not null as events_ready,
       to_regclass('public.lili_work_status') is not null as status_ready,
       to_regprocedure('public.lili_buddy_reminder_snapshot()') is not null as feed_ready,
       to_regprocedure('public.lili_set_buddy_reminder(uuid,text,boolean)') is not null as setter_ready;
"@ } | ConvertTo-Json -Compress
$result = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType "application/json" -Body $verify
$row = @($result)[0]
if (-not ($row.events_ready -and $row.status_ready -and $row.feed_ready -and $row.setter_ready)) {
    throw "Buddy reminder migration verification failed."
}
Write-Host "Buddy reminder schema and RPCs verified."
