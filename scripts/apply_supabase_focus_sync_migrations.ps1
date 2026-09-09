#requires -Version 7.2

[CmdletBinding()]
param(
    [string]$ConfigPath = "config/social_backend.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# This is the ordered contract for the account-scoped sealed-segment ledger.
# Keep it explicit: applying only the first migration leaves a deployment with
# a client that can call v2 but a database/relay that cannot acknowledge or
# reconcile it.  Every SQL file is idempotent and the existing Management API
# wrapper stops on the first failure.
$migrationPaths = @(
    "supabase/migrations/20260824000100_lili_focus_exact_reconciliation.sql",
    "supabase/migrations/20260906090000_lili_incremental_focus_segments_sync.sql",
    "supabase/migrations/20260906093000_lili_incremental_focus_segments_invoker.sql",
    "supabase/migrations/20260907090000_lili_focus_live_projection_read.sql",
    "supabase/migrations/20260907110000_lili_focus_union_device_presence.sql",
    "supabase/migrations/20260907130000_lili_sealed_focus_contract.sql",
    "supabase/migrations/20260907143000_lili_focus_delta_composite_cursor.sql",
    "supabase/migrations/20260907150000_lili_focus_segment_invoker_grants.sql",
    "supabase/migrations/20260907170000_lili_focus_delta_explicit_upload_ack.sql",
    "supabase/migrations/20260907190000_lili_focus_delta_empty_cursor_guard.sql",
    "supabase/migrations/20260907213000_lili_focus_segment_integrity_audit.sql",
    "supabase/migrations/20260907230000_lili_focus_segment_reconciliation_manifest.sql",
    "supabase/migrations/20260908090000_lili_focus_week_canonical_union.sql",
    "supabase/migrations/20260909143000_lili_dashboard_canonical_focus_totals.sql",
    "supabase/migrations/20260909150000_lili_focus_union_search_path.sql",
    "supabase/migrations/20260910090000_lili_focus_legacy_daily_compat.sql",
    "supabase/migrations/20260910093000_lili_focus_legacy_daily_source_marker.sql",
    "supabase/migrations/20260910110000_lili_focus_frozen_legacy_floor.sql",
    "supabase/migrations/20260910113000_lili_focus_monotonic_sync_restore.sql",
    "supabase/migrations/20260910120000_lili_focus_effective_projection_unified.sql"
)

$scriptPath = Join-Path $PSScriptRoot "apply_supabase_focus_history.ps1"
foreach ($migrationPath in $migrationPaths) {
    Write-Host "Applying ordered focus-sync migration: $migrationPath"
    & $scriptPath -MigrationPath $migrationPath -ConfigPath $ConfigPath
    if (-not $?) {
        throw "Focus-sync migration failed: $migrationPath"
    }
}

Write-Host "All ordered focus-sync migrations applied successfully."
