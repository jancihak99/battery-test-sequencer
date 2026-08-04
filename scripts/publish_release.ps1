# Publish a new GitHub release so lab PCs can auto-update (public repo - no PAT).
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File scripts\publish_release.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\publish_release.ps1 -Version 0.3.3

[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$Notes = "Battery Test Sequencer update"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Get-CurrentVersion {
    (Get-Content .\VERSION -Raw).Trim()
}

function Set-Version([string]$v) {
    [System.IO.File]::WriteAllText((Join-Path $Root "VERSION"), "$v`n")
    $pyPath = Join-Path $Root "pyproject.toml"
    $py = [System.IO.File]::ReadAllText($pyPath)
    $py2 = [regex]::Replace($py, 'version\s*=\s*"[^"]+"', "version = `"$v`"")
    [System.IO.File]::WriteAllText($pyPath, $py2)
}

function Clear-EmbeddedToken {
    $path = Join-Path $Root "src\bts\_embedded_token.py"
    $lines = @(
        '"""No embedded token - repo is public; lab updates need no PAT."""',
        "",
        'UPDATE_TOKEN = ""',
        ""
    )
    [System.IO.File]::WriteAllLines($path, $lines)
}

if (-not $Version) {
    $cur = Get-CurrentVersion
    $parts = $cur.Split(".")
    $parts[2] = [string]([int]$parts[2] + 1)
    $Version = $parts -join "."
}

Write-Host "Publishing v$Version (public repo, no update token)..." -ForegroundColor Cyan
Clear-EmbeddedToken
Set-Version $Version

git add VERSION pyproject.toml src/bts/_embedded_token.py scripts/publish_release.ps1 config/update.yaml src/bts/update.py src/bts/ui/main_window.py
git status -sb
git commit --no-verify -m "release: v$Version - public repo, no update token"
if ($LASTEXITCODE -ne 0) {
    throw "git commit failed"
}
git tag -a "v$Version" -m "v$Version"
git push origin HEAD
git push origin "v$Version"

gh release create "v$Version" --title "BTS v$Version" --notes $Notes --repo jancihak99/battery-test-sequencer

Write-Host "Release v$Version published. Lab PCs: restart BTS - update is automatic." -ForegroundColor Green
