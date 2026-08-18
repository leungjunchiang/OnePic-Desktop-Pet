$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "尚未创建 .venv，请先运行 scripts\setup_environment.ps1。"
}
Set-Location $projectRoot
& $python -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python main.py --smoke-test-ms 1500
exit $LASTEXITCODE




