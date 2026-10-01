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
if ($MigrationPath -like '*lili_supervision_policy.sql' -or $MigrationPath -like '*lili_study_plan_semantics.sql') {
    # Upgrade all discipline RPCs atomically, so older definitions cannot
    # temporarily bypass an owner's already revoked policy during deployment.
    $baseSql = Get-Content -Raw -LiteralPath 'supabase/migrations/20260930100000_lili_discipline_state.sql'
    $viewSql = Get-Content -Raw -LiteralPath 'supabase/migrations/20260930120000_lili_buddy_study_permissions.sql'
    $policySql = if ($MigrationPath -like '*lili_study_plan_semantics.sql') {
        Get-Content -Raw -LiteralPath 'supabase/migrations/20260930150000_lili_supervision_policy.sql'
    } else { '' }
    $projectionSql = ''
    if ($MigrationPath -like '*lili_study_plan_semantics.sql') {
        # Restore the latest function definitions if an older standalone focus
        # workflow ran. Do not replay its backfill or alter stored focus facts.
        $projectionSource = Get-Content -Raw -LiteralPath 'supabase/migrations/20260911100000_lili_focus_legacy_compatibility_ledger.sql'
        $projectionStart = $projectionSource.IndexOf('create or replace function public.lili_record_legacy_focus_day(')
        if ($projectionStart -lt 0) { throw 'Latest canonical projection definitions are missing.' }
        $projectionSql = $projectionSource.Substring($projectionStart)
    }
    $coachingSql = if ($MigrationPath -like '*lili_study_plan_semantics.sql') {
        (Get-Content -Raw -LiteralPath 'supabase/coaching_lifecycle.sql') + "`n" +
        (Get-Content -Raw -LiteralPath 'supabase/beijing_time_contract.sql') + "`n" +
        (Get-Content -Raw -LiteralPath 'supabase/interaction_center.sql') + "`n" +
        (Get-Content -Raw -LiteralPath 'supabase/presence_lifecycle.sql')
    } else { '' }
    $sql = "begin;`n$projectionSql`n$baseSql`n$viewSql`n$policySql`n$sql`n$coachingSql`ncommit;"
}
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
if ($MigrationPath -like '*lili_supervision_policy.sql') {
    $verificationSql = Get-Content -Raw -LiteralPath 'scripts/verify_supervision_policy.sql'
    $verificationBody = @{ query = $verificationSql } | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType 'application/json' -Body $verificationBody
    Write-Host 'Owner policy, multiple supervisors, officer opt-in, stale-device conflicts, field redaction, revocation, legacy denial and nudge cooldown verified; fixtures rolled back.'
    $upgradeSql = Get-Content -Raw -LiteralPath 'scripts/verify_supervision_upgrade.sql'
    $upgradeSql = $upgradeSql.Replace('-- REPLAY_MIGRATION_HERE', (Get-Content -Raw -LiteralPath $MigrationPath))
    $upgradeBody = @{ query = $upgradeSql } | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType 'application/json' -Body $upgradeBody
    Write-Host 'Legacy accepted scopes, disabled local rules, officer eligibility and idempotent migration verified; fixtures rolled back.'
}
if ($MigrationPath -like '*lili_buddy_study_permissions.sql') {
    $verificationSql = Get-Content -Raw -LiteralPath "scripts/verify_buddy_study_permissions.sql"
    $verificationBody = @{ query = $verificationSql } | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType "application/json" -Body $verificationBody
    Write-Host "Buddy study RPC consent, field opt-outs, revocation, and non-buddy denial verified; synthetic fixtures rolled back."
}

if ($MigrationPath -like '*lili_study_plan_semantics.sql') {
    $verificationSql = Get-Content -Raw -LiteralPath 'scripts/verify_study_plan_semantics.sql'
    $verificationBody = @{ query = $verificationSql } | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType 'application/json' -Body $verificationBody
    Write-Host 'Weekly plan, legacy device protection, pair pause, exemption, redaction and dynamic reminders verified; synthetic fixtures rolled back.'
    $deltaSql = Get-Content -Raw -LiteralPath 'scripts/verify_discipline_delta.sql'
    $deltaBody = @{ query = $deltaSql } | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType 'application/json' -Body $deltaBody
    Write-Host 'Discipline delta ACK, bounded commit cursor, no config polling, earlier-device correction, final summaries, legacy IDs and owner isolation verified; fixtures rolled back.'
    $coachingTest = Get-Content -Raw -LiteralPath 'scripts/verify_coaching_lifecycle.sql'
    $coachingBody = @{ query = $coachingTest } | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType 'application/json' -Body $coachingBody
    Write-Host 'Bilateral coaching explanation/review/makeup/closure, strict-only permissions, CAS, retries, union focus, incremental cursor and revocation verified; fixtures rolled back.'
    $timezoneTest = Get-Content -Raw -LiteralPath 'scripts/verify_beijing_timezone.sql'
    $timezoneBody = @{ query = $timezoneTest } | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType 'application/json' -Body $timezoneBody
    Write-Host 'Actual timestamptz schema, UTC return shape, Beijing midnight/week, 06:00 genuine session start, legacy device offset and wall-clock plan verified; fixtures rolled back.'
    $presenceTest = Get-Content -Raw -LiteralPath 'scripts/verify_presence_lifecycle.sql'
    $presenceReplay = Get-Content -Raw -LiteralPath 'supabase/presence_lifecycle.sql'
    $presenceTest = $presenceTest.Replace('-- REPLAY_PRESENCE_HERE', "$presenceReplay`n$presenceReplay")
    $presenceBody = @{ query = $presenceTest } | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType 'application/json' -Body $presenceBody
    Write-Host 'Presence lifecycle, idle/rest/focus, multi-device exit, sequence fencing, TTL, recovery, privacy and legacy protocol verified; fixtures rolled back.'
    $inboxTest = Get-Content -Raw -LiteralPath 'scripts/verify_interaction_center.sql'
    $inboxBody = @{ query = $inboxTest } | ConvertTo-Json -Compress
    $null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType 'application/json' -Body $inboxBody
    Write-Host 'Beijing distinct-day quota, ninth-day legacy rejection, cancellation/re-enable, monthly reset, inbox baseline, read receipts and receiver isolation verified; fixtures rolled back.'
}
