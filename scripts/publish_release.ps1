[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,

    [string]$NotesFile = "",

    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is not installed. Install it once, then run this command again."
}

& gh auth status
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run: gh auth login"
}

$branch = (& git branch --show-current).Trim()
if ($branch -ne "main") {
    throw "Release publishing must run from main. Current branch: $branch"
}

$changes = & git status --porcelain
if ($changes) {
    throw "The working tree is not clean. Commit the intended files before publishing."
}

if (-not $SkipTests) {
    & (Join-Path $PSScriptRoot "test.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Tests or the source smoke test failed. Nothing was uploaded."
    }
}

$tag = "v$Version"
$head = (& git rev-parse HEAD).Trim()

& git push origin main
if ($LASTEXITCODE -ne 0) {
    throw "Failed to push main."
}

& git rev-parse --verify --quiet "refs/tags/$tag" | Out-Null
if ($LASTEXITCODE -eq 0) {
    $tagHead = (& git rev-list -n 1 $tag).Trim()
    if ($tagHead -ne $head) {
        throw "Tag $tag already points to another commit. Refusing to move it automatically."
    }
} else {
    & git tag -a $tag -m "Lili $Version"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create $tag."
    }
}

& git push origin $tag
if ($LASTEXITCODE -ne 0) {
    throw "Failed to push $tag."
}

$temporaryNotes = $null
try {
    if ($NotesFile) {
        $resolvedNotes = (Resolve-Path -LiteralPath $NotesFile).Path
    } else {
        $temporaryNotes = Join-Path ([IO.Path]::GetTempPath()) "lili-$Version-release-notes.md"
        @"
Lili $Version has been published automatically.

GitHub Actions will attach the Windows installer, Windows portable ZIP, and unsigned macOS DMGs after all tests pass.
"@ | Set-Content -LiteralPath $temporaryNotes -Encoding UTF8
        $resolvedNotes = $temporaryNotes
    }

    & gh release view $tag --json url | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & gh release edit $tag --title "Lili $tag" --notes-file $resolvedNotes --latest
    } else {
        & gh release create $tag --title "Lili $tag" --notes-file $resolvedNotes --latest --verify-tag
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create or update the GitHub Release."
    }
} finally {
    if ($temporaryNotes -and (Test-Path -LiteralPath $temporaryNotes)) {
        Remove-Item -LiteralPath $temporaryNotes -Force
    }
}

$releaseUrl = (& gh release view $tag --json url --jq '.url').Trim()
Write-Host "Published $tag. GitHub Actions is building the cross-platform downloads."
Write-Host $releaseUrl
