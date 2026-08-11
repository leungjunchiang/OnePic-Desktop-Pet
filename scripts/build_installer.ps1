param(
    [string]$Version = "0.14.0"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$compiler = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $compiler) {
    throw "没有找到 Inno Setup 6，无法生成 Windows 安装程序。"
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "dist\Lili\Lili.exe"))) {
    throw "请先运行 scripts\build.ps1 生成 dist\Lili。"
}

Push-Location $projectRoot
try {
    & $compiler "/DMyAppVersion=$Version" ".\packaging\windows\Lili.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup 构建失败，退出码：$LASTEXITCODE"
    }
} finally {
    Pop-Location
}
