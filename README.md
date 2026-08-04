# Battery Test Sequencer

Windows desktop app for automating LTO module capacity / DCIR tests with:

- **EA-PSI 9080-340** (charge) via SCPI (USB serial, `serial_port: auto`)
- **2× EA-EL 9080-510 B** hardware master/slave as one load (SCPI only to master)
- **Clayton / Altairnano BMU** on External CAN (Kvaser) — app sends READY/IDLE only; BMU owns contactors

Private source + updates: `jancihak99/battery-test-sequencer` (GitHub).

## Install on another PC

**Pošli jeden soubor:** `installer\BTS-Setup.exe` (~260 MB, ikona apky, klasický průvodce).

- První instalace je **offline** — aktuální verze je uvnitř instalátoru (žádný GitHub).
- Pozdější aktualizace apka stáhne z GitHub Releases.
- Windows SmartScreen může poprvé varovat (nepodepsaný exe) — Další informace → Přesto spustit.

Znovu sestavit: `scripts\setup_exe\build_setup_exe.bat`

## Auto-update

**Na PC zákazníka:** při startu apka zeptá GitHub Releases a nabídne instalaci nové verze.

**Ty (vývojář)** nahraješ update:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\publish_release.ps1
```

Pak znovu sestav a nahraj `BTS-Setup.exe` k release (nebo jen tag — už nainstalované PC berou update z GitHubu).

## Quick start (dev machine)

Double-click **`Start BTS.bat`** in the project folder.

## Safety

- CAN / EA watchdogs force EA off; IDLE only after current ≈ 0
- **Stop (Esc):** EA off → dwell → I≈0 → hold → IDLE
- **External dropout:** COM loss or EL master/slave half-current → `EXT FAIL` + safe path

## Lab checklist

See [docs/LAB_CHECKLIST.md](docs/LAB_CHECKLIST.md).
