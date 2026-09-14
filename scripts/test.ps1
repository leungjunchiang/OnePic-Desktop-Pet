$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "尚未创建 .venv，请先运行 scripts\setup_environment.ps1。"
}
Set-Location $projectRoot
$testRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
$files = Get-ChildItem -Path tests -Filter 'test_*.py' | Sort-Object FullName
foreach ($file in $files) {
    Write-Host "=== Running $($file.FullName) ==="
    if ($file.Name -eq 'test_window.py') {
        # The window tests exercise native Qt handles.  Keeping the whole
        # parametrized module in one interpreter can exhaust Windows Qt
        # resources even when every individual test passes, so split the
        # collected node ids into bounded fresh processes.
        $nodes = @(& $python -m pytest --collect-only $file.FullName 2>$null |
            Where-Object { $_ -match '::' } |
            ForEach-Object { $_.Trim() })
        if ($nodes.Count -eq 0) {
            throw "No tests collected from $($file.FullName)."
        }
        for ($start = 0; $start -lt $nodes.Count; $start += 40) {
            $end = [Math]::Min($start + 39, $nodes.Count - 1)
            $chunk = @($nodes[$start..$end])
            & $python -m pytest --basetemp (Join-Path $testRoot ("lili-$($file.BaseName)-$start")) $chunk
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
    } else {
        & $python -m pytest --basetemp (Join-Path $testRoot "lili-$($file.BaseName)") $file.FullName
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}
& $python main.py --smoke-test-ms 1500
exit $LASTEXITCODE




