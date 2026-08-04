# Install Battery Test Sequencer on a lab PC (portable, no admin required).
# Usage:
#   Right-click → Run with PowerShell
#   or: powershell -ExecutionPolicy Bypass -File Install-BTS.ps1
#
# Prerequisites: Python 3.10+ on PATH. GitHub access via `gh auth login`
# or a fine-grained PAT (Contents: Read) pasted when asked.

[CmdletBinding()]
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\EBZ\BatteryTestSequencer",
    [string]$Repo = "jancihak99/battery-test-sequencer",
    [string]$Token = "",
    [switch]$SkipShortcut
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Test-Python {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { return $null }
    $ver = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    return $ver
}

Write-Host "Battery Test Sequencer — installer" -ForegroundColor Green
Write-Host "Target: $InstallDir"
Write-Host "Repo:   $Repo (private)"

$pyVer = Test-Python
if (-not $pyVer) {
    Write-Host "ERROR: Python not found on PATH. Install Python 3.10+ from python.org (check 'Add to PATH')." -ForegroundColor Red
    exit 1
}
Write-Host "Python: $pyVer"

# Resolve token
if (-not $Token) {
    $Token = $env:BTS_GITHUB_TOKEN
}
if (-not $Token) {
    $Token = $env:GITHUB_TOKEN
}

$useGh = $false
$gh = Get-Command gh -ErrorAction SilentlyContinue
if ($gh) {
    $status = & gh auth status 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        $useGh = $true
        Write-Host "Using authenticated gh CLI."
    }
}

if (-not $useGh -and -not $Token) {
    Write-Host ""
    Write-Host "Private repo needs access. Options:" -ForegroundColor Yellow
    Write-Host "  1) Run: gh auth login"
    Write-Host "  2) Paste a GitHub fine-grained PAT (Contents: Read on $Repo)"
    $Token = Read-Host "PAT (leave empty if you just ran gh auth login)"
    if ($Token) {
        $useGh = $false
    } else {
        $gh2 = Get-Command gh -ErrorAction SilentlyContinue
        if ($gh2) {
            $status2 = & gh auth status 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0) { $useGh = $true }
        }
        if (-not $useGh) {
            Write-Host "ERROR: No GitHub auth." -ForegroundColor Red
            exit 1
        }
    }
}

Write-Step "Preparing install folder"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$parent = Split-Path $InstallDir -Parent
New-Item -ItemType Directory -Force -Path $parent | Out-Null

$gitDir = Join-Path $InstallDir ".git"
if (Test-Path $gitDir) {
    Write-Step "Existing install — git pull"
    Push-Location $InstallDir
    try {
        if ($useGh) {
            & git pull --ff-only
        } else {
            $env:GIT_ASKPASS = "echo"
            & git -c "http.extraheader=AUTHORIZATION: bearer $Token" pull --ff-only
        }
        if ($LASTEXITCODE -ne 0) { throw "git pull failed" }
    } finally {
        Pop-Location
    }
} else {
    Write-Step "Cloning private repository"
    if (Test-Path $InstallDir) {
        Get-ChildItem $InstallDir -Force | Where-Object { $_.Name -ne '.github_token' } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

    if ($useGh) {
        & gh repo clone $Repo $InstallDir
        if ($LASTEXITCODE -ne 0) { throw "gh repo clone failed" }
    } else {
        $url = "https://x-access-token:${Token}@github.com/${Repo}.git"
        & git clone $url $InstallDir
        if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
        # Rewrite remote without embedded token
        Push-Location $InstallDir
        & git remote set-url origin "https://github.com/${Repo}.git"
        Pop-Location
    }
}

if ($Token) {
    Write-Step "Saving update token (hidden)"
    $tokPath = Join-Path $InstallDir ".github_token"
    Set-Content -Path $tokPath -Value $Token.Trim() -Encoding UTF8
    attrib +H $tokPath 2>$null
}

Write-Step "Creating Python venv + dependencies"
Push-Location $InstallDir
try {
    if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
        & python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "venv failed" }
    }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    & .\.venv\Scripts\python.exe -m pip install -e .
} finally {
    Pop-Location
}

# Ensure update.yaml points at repo
$upd = Join-Path $InstallDir "config\update.yaml"
@"
github_repo: $Repo
check_on_startup: false
channel: latest
"@ | Set-Content -Path $upd -Encoding UTF8

if (-not $SkipShortcut) {
    Write-Step "Desktop shortcut"
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnkPath = Join-Path $desktop "Battery Test Sequencer.lnk"
    $wsh = New-Object -ComObject WScript.Shell
    $lnk = $wsh.CreateShortcut($lnkPath)
    $lnk.TargetPath = Join-Path $InstallDir "Start BTS.bat"
    $lnk.WorkingDirectory = $InstallDir
    $lnk.Description = "EBZ Battery Test Sequencer"
    $icon = Join-Path $InstallDir "assets\ebz_nanopower_logo_ui.png"
    if (Test-Path $icon) { $lnk.IconLocation = "%SystemRoot%\System32\shell32.dll,13" }
    $lnk.Save()

    $startDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\EBZ"
    New-Item -ItemType Directory -Force -Path $startDir | Out-Null
    $lnk2 = $wsh.CreateShortcut((Join-Path $startDir "Battery Test Sequencer.lnk"))
    $lnk2.TargetPath = Join-Path $InstallDir "Start BTS.bat"
    $lnk2.WorkingDirectory = $InstallDir
    $lnk2.Save()
}

Write-Host ""
Write-Host "INSTALL OK" -ForegroundColor Green
Write-Host "Location: $InstallDir"
Write-Host "Start:    Start BTS.bat  (or Desktop shortcut)"
Write-Host "Updates:  in app Nastaveni → Aktualizace, or installer\Update-BTS.ps1"
Write-Host ""
Write-Host "On this PC set hardware in Nastaveni (CAN / EA COM) and Save."
