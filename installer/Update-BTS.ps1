# Apply latest GitHub release / git pull, then optionally restart BTS.
[CmdletBinding()]
param(
    [string]$Root = "",
    [switch]$Restart
)

$ErrorActionPreference = "Stop"

if (-not $Root) {
    $Root = Split-Path $PSScriptRoot -Parent
}
if (-not (Test-Path (Join-Path $Root "main.py"))) {
    Write-Host "ERROR: Cannot find BTS root (main.py). Pass -Root." -ForegroundColor Red
    exit 1
}

Write-Host "Updating BTS in $Root" -ForegroundColor Cyan

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "main\.py" -and $_.Name -match "python" } |
    ForEach-Object {
        Write-Host "Stopping PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 1

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

& $py (Join-Path $Root "scripts\apply_update.py") $Root
if ($LASTEXITCODE -ne 0) {
    Write-Host "Update failed." -ForegroundColor Red
    if (-not $Restart) { pause }
    exit 1
}

Write-Host "Update finished." -ForegroundColor Green

if ($Restart) {
    $bat = Join-Path $Root "Start BTS.bat"
    if (Test-Path $bat) {
        Start-Process -FilePath $bat -WorkingDirectory $Root
    }
}
