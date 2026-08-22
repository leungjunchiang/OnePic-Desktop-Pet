#requires -Version 7.2

[CmdletBinding()]
param(
    [string]$ProjectRef = "",
    [string]$SiteUrl = "",
    [string]$SmtpPort = "",
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

$resolvedProjectRef = if (-not [string]::IsNullOrWhiteSpace($ProjectRef)) {
    $ProjectRef
} else {
    $env:SUPABASE_PROJECT_REF
}

$resolvedSiteUrl = if (-not [string]::IsNullOrWhiteSpace($SiteUrl)) {
    $SiteUrl
} else {
    $env:SUPABASE_SITE_URL
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
$smtpAdminEmail = Require-Value -Name "SUPABASE_SMTP_ADMIN_EMAIL" -Value $env:SUPABASE_SMTP_ADMIN_EMAIL
$smtpSenderName = Require-Value -Name "SUPABASE_SMTP_SENDER_NAME" -Value $env:SUPABASE_SMTP_SENDER_NAME

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

