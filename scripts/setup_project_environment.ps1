# Create a project-only Python runtime and .venv; never edit PATH or the registry.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required for project-local Python installation. No system software was installed.'
}
& uv python install 3.12 --install-dir .python-runtime --no-bin --no-registry --cache-dir .uv-cache --no-progress
if ($LASTEXITCODE -ne 0) { throw 'Python download failed.' }
$runtime = Get-ChildItem .python-runtime -Directory -Filter 'cpython-3.12.*-windows-x86_64-none' |
    Sort-Object Name -Descending | Select-Object -First 1
if (-not $runtime) { throw 'Project Python 3.12 was not found.' }
if (-not (Test-Path .venv\Scripts\python.exe)) {
    & (Join-Path $runtime.FullName 'python.exe') -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv.' }
}
& uv pip install --python .venv\Scripts\python.exe --cache-dir .uv-cache -e '.[dev]' --no-progress
if ($LASTEXITCODE -ne 0) { throw 'Project dependency installation failed.' }
& .\scripts\check_environment.ps1
