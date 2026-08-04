# Battery Test Sequencer

Windows desktop app for automating LTO module capacity / DCIR tests with:

- **EA-PSI 9080-340** (charge) via SCPI (USB serial, `serial_port: auto`)
- **2× EA-EL 9080-510 B** hardware master/slave as one load (SCPI only to master)
- **Clayton / Altairnano BMU** on External CAN (Kvaser) — app sends READY/IDLE only; BMU owns contactors

Private source + updates: `jancihak99/battery-test-sequencer` (GitHub).

## Install on another PC

**Pošli jeden soubor:** `installer\BTS-Setup.exe` (ikona apky).

Na cílovém PC: Python 3.10+ + Git → dvojklik → PAT / `gh auth` → Nainstalovat.

Znovu sestavit exe: `scripts\setup_exe\build_setup_exe.bat`

## Auto-update

**Na PC zákazníka to běží samo:** při startu apka zeptá GitHub Releases, pozná novější verzi a nabídne „Stáhnout a nainstalovat“. Token z instalace použije sama.

**Ty (vývojář)** jen nahraješ update:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\publish_release.ps1
```

(bump `VERSION` → tag → GitHub Release). Pak stačí na lab PC apku znovu otevřít.
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
