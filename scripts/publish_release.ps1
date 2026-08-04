# Publish a new private GitHub release so lab PCs can auto-update.
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File scripts\publish_release.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\publish_release.ps1 -Version 0.2.0

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
    Set-Content -Path .\VERSION -Value "$v`n" -Encoding UTF8 -NoNewline
    $py = Get-Content .\pyproject.toml -Raw
    $py2 = [regex]::Replace($py, 'version\s*=\s*"[^"]+"', "version = `"$v`"")
    Set-Content -Path .\pyproject.toml -Value $py2 -Encoding UTF8 -NoNewline
}

if (-not $Version) {
    $cur = Get-CurrentVersion
    $parts = $cur.Split(".")
    $parts[2] = [string]([int]$parts[2] + 1)
    $Version = $parts -join "."
}

Write-Host "Publishing v$Version" -ForegroundColor Cyan
Set-Version $Version

git add VERSION pyproject.toml
git status -sb
git commit -m "release: v$Version"
git tag -a "v$Version" -m "v$Version"
git push origin HEAD
git push origin "v$Version"

gh release create "v$Version" --title "BTS v$Version" --notes $Notes --repo jancihak99/battery-test-sequencer

Write-Host "Release v$Version published. Lab PCs: Nastaveni → Aktualizace." -ForegroundColor Green
