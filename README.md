# Battery Test Sequencer

Windows desktop app for automating LTO module capacity / DCIR tests with:

- **EA-PSI 9080-340** (charge) via SCPI (USB serial, `serial_port: auto`)
- **2× EA-EL 9080-510 B** hardware master/slave as one load (SCPI only to master)
- **Clayton / Altairnano BMU** on External CAN (Kvaser) — app sends READY/IDLE only; BMU owns contactors

Private source + updates: `jancihak99/battery-test-sequencer` (GitHub).

## Install on another PC (portable)

1. Install **Python 3.10+** (tick “Add to PATH”) and ideally **Git** + [GitHub CLI](https://cli.github.com/) (`gh auth login`).
2. Copy the folder `installer\` (or the whole repo) and run **`installer\Install-BTS.bat`**.
3. When asked, paste a **fine-grained PAT** with **Contents: Read** on this private repo  
   (or rely on `gh auth login` on that machine).
4. App lands in `%LOCALAPPDATA%\EBZ\BatteryTestSequencer` + Desktop shortcut.
5. Open **Nastavení**, set CAN / EA COM for that lab PC, **Uložit do config**.

No admin rights required. Lab config (`config/default.yaml`) and `runs/` survive updates.

## Auto-update

1. You publish: `powershell -ExecutionPolicy Bypass -File scripts\publish_release.ps1`  
   (bumps `VERSION`, tags `vX.Y.Z`, creates GitHub Release).
2. On the lab PC: **Nastavení → Zkontrolovat aktualizace → Stáhnout a nainstalovat**  
   (or `installer\Update-BTS.ps1`).
3. Token lives in `.github_token` next to the install (hidden; not in git).

## Quick start (dev machine)

Double-click **`Start BTS.bat`** (or `Start BTS.vbs`) in the project folder.

Or from PowerShell:

```powershell
cd C:\Users\janci\Downloads\battery-test-sequencer
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
python main.py
```

**Default lab config** (`config/default.yaml`):

- `use_mock_hardware: false` — live hardware
- EA: `transport: serial`, `serial_port: auto` (*IDN? discovery)
- Kvaser channel / bitrate / BMU + App addresses as in YAML (editable in **Nastavení**)

For offline UI work without instruments: enable **Use mock hardware** in Nastavení.

Smoke / mock-only recipes live in `programs/dev/` and appear as `[DEV/MOCK] …`.

## Safety

- CAN / EA watchdogs force EA off; IDLE only after current ≈ 0
- **Stop (Esc):** EA off → dwell → I≈0 → hold → IDLE (if I stays high, contactors stay CLOSED)
- **External dropout:** COM loss or EL master/slave half-current → `EXT FAIL` + safe path
- **Source/load mutex:** PSI and EL must never be ON together

## Lab checklist

See [docs/LAB_CHECKLIST.md](docs/LAB_CHECKLIST.md).
