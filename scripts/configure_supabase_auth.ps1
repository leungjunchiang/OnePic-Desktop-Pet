#requires -Version 7.2

[CmdletBinding()]
param(
    [string]$ProjectRef = "",
    [string]$SiteUrl = "",
    [string]$SmtpPort = "",
    [string]$ConfigPath = "config/social_backend.json",
    [switch]$DryRun,
    [switch]$SkipSmtp
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

$resolvedPasswordResetUrl = if (-not [string]::IsNullOrWhiteSpace($env:SUPABASE_PASSWORD_RESET_URL)) {
    $env:SUPABASE_PASSWORD_RESET_URL
} elseif ($null -ne $publicConfig -and -not [string]::IsNullOrWhiteSpace($publicConfig.password_reset_redirect_to)) {
    $publicConfig.password_reset_redirect_to
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
$authConfig = [ordered]@{
    external_email_enabled = $true
    mailer_autoconfirm = $false
    password_min_length = 8
    # Recovery is intentionally a six-digit email OTP. Supabase Auth keeps
    # the temporary verifier server-side; Lili never persists the code.
    mailer_otp_length = 6
    mailer_otp_exp = 600
    mailer_subjects_recovery = "Lili 密码重置验证码"
    mailer_templates_recovery_content = @'
<h2>Lili 密码重置验证码</h2>
<p>你正在重置 Lili（六毛搭子自习室）的登录密码。</p>
<p style="font-size:28px;font-weight:700;letter-spacing:6px;">{{ .Token }}</p>
<p>验证码 10 分钟内有效，使用一次后立即失效。</p>
<p>如果不是你本人操作，请忽略这封邮件。</p>
'@
}

# HIBP protection is optional and is rejected by some Supabase plans. Keep it
# out of the baseline account-security patch so the supported password-length
# and redirect settings can still be applied automatically.
if (-not $SkipSmtp -and -not [string]::IsNullOrWhiteSpace($env:SUPABASE_ENABLE_HIBP)) {
    $authConfig.password_hibp_enabled = $env:SUPABASE_ENABLE_HIBP.Trim().ToLowerInvariant() -eq "true"
}

if (-not $SkipSmtp) {
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

    $authConfig.smtp_admin_email = $smtpAdminEmail
    $authConfig.smtp_host = $smtpHost
    $authConfig.smtp_port = [string]$smtpPortNumber
    $authConfig.smtp_user = $smtpUser
    $authConfig.smtp_pass = $smtpPassword
    $authConfig.smtp_sender_name = $smtpSenderName
}

if (-not [string]::IsNullOrWhiteSpace($resolvedSiteUrl)) {
    $authConfig.site_url = $resolvedSiteUrl.Trim()
}

if (-not [string]::IsNullOrWhiteSpace($resolvedPasswordResetUrl)) {
    # The Management API accepts this as a comma-separated redirect allow list.
    $authConfig.uri_allow_list = $resolvedPasswordResetUrl.Trim()
}

if ($DryRun) {
    $mode = if ($SkipSmtp) { "account-security" } else { "SMTP and account-security" }
    Write-Host "Supabase Auth $mode configuration validated for project $projectRef."
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
        $kind = if ($SkipSmtp) { "account-security" } else { "SMTP and account-security" }
        throw "Supabase Auth $kind update failed with HTTP $statusCode. Check the project ref, token scopes, plan permissions, and settings."
    }

    $kind = if ($SkipSmtp) { "account-security" } else { "SMTP and account-security" }
    throw "Supabase Auth $kind update failed. Check the project ref, token scopes, network access, plan permissions, and settings."
}

Write-Host "Supabase Auth custom SMTP configured for project $projectRef."


