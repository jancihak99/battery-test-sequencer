# Apply latest GitHub release, show progress GUI, then optionally restart BTS.
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

$pyw = Join-Path $Root "python\pythonw.exe"
$py = Join-Path $Root "python\python.exe"
$venvPyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
$gui = Join-Path $Root "scripts\update_gui.py"

if (Test-Path $pyw) { $launcher = $pyw }
elseif (Test-Path $venvPyw) { $launcher = $venvPyw }
elseif (Test-Path $py) { $launcher = $py }
elseif (Test-Path $venvPy) { $launcher = $venvPy }
else { $launcher = "python" }

$guiArgs = @($gui, $Root, "--wait-exit")
if ($Restart) { $guiArgs += "--restart" }

if (Test-Path $gui) {
    Write-Host "Launching update progress window…" -ForegroundColor Cyan
    & $launcher @guiArgs
    exit $LASTEXITCODE
}

# Legacy path without update_gui.py
$running = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "main\.py" -and $_.Name -match "python" }
)
if ($running.Count -gt 0) {
    Write-Host "Waiting for BTS to exit…" -ForegroundColor Yellow
    $deadline = (Get-Date).AddSeconds(45)
    while (((Get-Date) -lt $deadline) -and ($running.Count -gt 0)) {
        Start-Sleep -Milliseconds 400
        $running = @(
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -match "main\.py" -and $_.Name -match "python" -and $_.CommandLine -notmatch "update_gui" }
        )
    }
    if ($running.Count -gt 0) {
        if (-not $ForceKill) {
            Write-Host "ERROR: BTS still running. Close it, then retry." -ForegroundColor Red
            if (-not $Restart) { pause }
            exit 2
        }
        foreach ($proc in $running) {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
    }
}

& $launcher (Join-Path $Root "scripts\apply_update.py") $Root
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
