#requires -Version 7.2

[CmdletBinding()]
param(
    [string]$ProjectRef = "",
    [string]$SiteUrl = "",
    [string]$SmtpPort = "",
    [string]$ConfigPath = "config/social_backend.json",
    [switch]$DryRun
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

$publicConfig = $null
if (Test-Path -LiteralPath $ConfigPath) {
    try {
        $publicConfig = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
    } catch {
        throw "Could not parse public social backend configuration: $ConfigPath"
    }
}

$resolvedProjectRef = if (-not [string]::IsNullOrWhiteSpace($ProjectRef)) {
    $ProjectRef
} elseif (-not [string]::IsNullOrWhiteSpace($env:SUPABASE_PROJECT_REF)) {
    $env:SUPABASE_PROJECT_REF
} elseif ($null -ne $publicConfig -and -not [string]::IsNullOrWhiteSpace($publicConfig.supabase_url)) {
    ([Uri]$publicConfig.supabase_url).Host.Split('.')[0]
} else {
    ""
}

$resolvedSiteUrl = if (-not [string]::IsNullOrWhiteSpace($SiteUrl)) {
    $SiteUrl
} elseif (-not [string]::IsNullOrWhiteSpace($env:SUPABASE_SITE_URL)) {
    $env:SUPABASE_SITE_URL
} elseif ($null -ne $publicConfig) {
    $publicConfig.email_redirect_to
} else {
    ""
}

$resolvedPort = if (-not [string]::IsNullOrWhiteSpace($SmtpPort)) {
    $SmtpPort
} elseif (-not [string]::IsNullOrWhiteSpace($env:SUPABASE_SMTP_PORT)) {
    $env:SUPABASE_SMTP_PORT
} else {
    "587"
}

$projectRef = Require-Value -Name "SUPABASE_PROJECT_REF" -Value $resolvedProjectRef
$accessToken = Require-Value -Name "SUPABASE_ACCESS_TOKEN" -Value $env:SUPABASE_ACCESS_TOKEN
$smtpHost = Require-Value -Name "SUPABASE_SMTP_HOST" -Value $env:SUPABASE_SMTP_HOST
$smtpUser = Require-Value -Name "SUPABASE_SMTP_USER" -Value $env:SUPABASE_SMTP_USER
$smtpPassword = Require-Value -Name "SUPABASE_SMTP_PASSWORD" -Value $env:SUPABASE_SMTP_PASSWORD
$smtpAdminEmail = if (-not [string]::IsNullOrWhiteSpace($env:SUPABASE_SMTP_ADMIN_EMAIL)) {
    $env:SUPABASE_SMTP_ADMIN_EMAIL.Trim()
} else {
    $smtpUser
}
$smtpSenderName = if (-not [string]::IsNullOrWhiteSpace($env:SUPABASE_SMTP_SENDER_NAME)) {
    $env:SUPABASE_SMTP_SENDER_NAME.Trim()
} else {
    "Lili"
}

[int]$smtpPortNumber = 0
if (-not [int]::TryParse($resolvedPort, [ref]$smtpPortNumber) -or $smtpPortNumber -lt 1 -or $smtpPortNumber -gt 65535) {
    throw "SUPABASE_SMTP_PORT must be an integer between 1 and 65535"
}

$authConfig = [ordered]@{
    external_email_enabled = $true
    mailer_autoconfirm = $false
    smtp_admin_email = $smtpAdminEmail
    smtp_host = $smtpHost
    smtp_port = [string]$smtpPortNumber
    smtp_user = $smtpUser
    smtp_pass = $smtpPassword
    smtp_sender_name = $smtpSenderName
}

if (-not [string]::IsNullOrWhiteSpace($resolvedSiteUrl)) {
    $authConfig.site_url = $resolvedSiteUrl.Trim()
}

if ($DryRun) {
    Write-Host "Supabase Auth SMTP configuration validated for project $projectRef."
    exit 0
}

$encodedProjectRef = [Uri]::EscapeDataString($projectRef)
$uri = "https://api.supabase.com/v1/projects/$encodedProjectRef/config/auth"
$headers = @{
    Authorization = "Bearer $accessToken"
    Accept = "application/json"
}
$body = $authConfig | ConvertTo-Json -Compress

try {
    $null = Invoke-RestMethod -Method Patch -Uri $uri -Headers $headers -ContentType "application/json" -Body $body
} catch {
    $statusCode = $null
    if ($null -ne $_.Exception.Response) {
        $statusCode = $_.Exception.Response.StatusCode.value__
    }

    if ($null -ne $statusCode) {
        throw "Supabase Auth SMTP update failed with HTTP $statusCode. Check the project ref, token scopes, and SMTP settings."
    }

    throw "Supabase Auth SMTP update failed. Check the project ref, token scopes, network access, and SMTP settings."
}

Write-Host "Supabase Auth custom SMTP configured for project $projectRef."

