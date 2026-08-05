# Apply latest GitHub release / git pull, then optionally restart BTS.
[CmdletBinding()]
param(
    [string]$Root = "",
    [switch]$Restart,
    [switch]$ForceKill
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

$running = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "main\.py" -and $_.Name -match "python" }
)
if ($running.Count -gt 0) {
    if (-not $ForceKill) {
        Write-Host "ERROR: BTS is running (PID(s): $($running.ProcessId -join ', '))." -ForegroundColor Red
        Write-Host "Close the app with safe shutdown first, then update." -ForegroundColor Yellow
        Write-Host "(Refusing force-kill — would leave EA/contactors unsafe.)" -ForegroundColor Yellow
        if (-not $Restart) { pause }
        exit 2
    }
    foreach ($proc in $running) {
        Write-Host "Force-stopping PID $($proc.ProcessId) (-ForceKill)" -ForegroundColor Yellow
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

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
