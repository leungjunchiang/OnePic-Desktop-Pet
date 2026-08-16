param(
    [string]$Version = "0.22.50"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$compiler = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $compiler) {
    throw "Inno Setup 6 was not found; cannot build the Windows installer."
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "dist\Lili\Lili.exe"))) {
    throw "Run scripts\build.ps1 before building the Windows installer."
}

Push-Location $projectRoot
try {
    & $compiler "/DMyAppVersion=$Version" ".\packaging\windows\Lili.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
