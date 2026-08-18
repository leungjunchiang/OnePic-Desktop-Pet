$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

& "$PSScriptRoot\check_environment.ps1"
Set-Location $projectRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv .venv
    } else {
        & python -m venv .venv
    }
}

& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -e ".[dev]"
Write-Host "[OnePic] 项目环境已经安装到 .venv。"




