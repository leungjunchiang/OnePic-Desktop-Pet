#requires -Version 7.2

[CmdletBinding()]
param(
    [string]$MigrationPath = "supabase/migrations/20260824000100_lili_focus_exact_reconciliation.sql",
    [string]$ConfigPath = "config/social_backend.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Require-Value {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowEmptyString()][string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw "Missing required configuration: $Name"
    }

    return $Value.Trim()
}

$config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$projectRef = if (-not [string]::IsNullOrWhiteSpace($env:SUPABASE_PROJECT_REF)) {
    $env:SUPABASE_PROJECT_REF.Trim()
} elseif (-not [string]::IsNullOrWhiteSpace($config.supabase_url)) {
    ([Uri]$config.supabase_url).Host.Split('.')[0]
} else {
    ""
}
$projectRef = Require-Value -Name "SUPABASE_PROJECT_REF" -Value $projectRef
$accessToken = Require-Value -Name "SUPABASE_ACCESS_TOKEN" -Value $env:SUPABASE_ACCESS_TOKEN
$sql = Get-Content -Raw -LiteralPath $MigrationPath
if ([string]::IsNullOrWhiteSpace($sql)) {
    throw "Migration file is empty: $MigrationPath"
}

# The Supabase Management API executes this migration directly in the target
# database, so CI does not need a database password or a second project field.
# The SQL is deliberately idempotent and can be safely retried if a workflow
# is re-run after a transient network failure.
$body = @{ query = $sql } | ConvertTo-Json -Compress
$encodedProjectRef = [Uri]::EscapeDataString($projectRef)
$uri = "https://api.supabase.com/v1/projects/$encodedProjectRef/database/query"
$headers = @{
    Authorization = "Bearer $accessToken"
    Accept = "application/json"
}

try {
    $null = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -ContentType "application/json" -Body $body
} catch {
    $statusCode = $null
    if ($null -ne $_.Exception.Response) {
        $statusCode = $_.Exception.Response.StatusCode.value__
    }

    if ($null -ne $statusCode) {
        throw "Supabase focus-history migration failed with HTTP $statusCode. The token needs database_write scope and the project must be reachable."
    }

    throw "Supabase focus-history migration failed. Check the token scope, project ref, and network access."
}

Write-Host "Supabase focus-history migration applied to project $projectRef."

