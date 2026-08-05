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

## Uživatelský mini-návod

1. Vyber správný **module profile** (soubor v `profiles/`), aby seděly limity `cell_v_min/max` a `pack_v_min/max`.
2. Vyber **program** (YAML v `programs/`).
3. V editoru stepu (UI) u kroků, které jsou „výsledkové“, zaškrtni:
   - `Zaznamenat stopu (CSV + grafy v reportu)` a/nebo
   - `Měřit kapacitu (CC do limitu → report)`
4. Klikni **Connect HW** → **Start program**.
5. Pokud se něco děje, použij **Stop (Esc)** (apka přejde na bezpečný režim).

## Proaktivní bezpečnostní limity (napětí článků)

Engine se snaží zabránit tomu, aby BMU musela sahat na `Disconnect` (krajní nouze):
- Pro `charge/discharge` automaticky přidává stop podmínky pro **napětí článků** (z profilu, s malou rezervou), aby step skončil dřív.
- Navíc `_effective_abort` přidává **hard abort** na **cell_v_max/min** jako poslední pojistku.
- Charge voltage setpoint (`voltage_v`) se clampuje na `profile.pack_v_max`.
- Při `dtc_level=2` (Derate): u běžných kroků appka proud sníží a **run pokračuje**; u **měření kapacity / DCIR** (kde C-rate / pulse I patří k výsledku) se run **zastaví**, aby nebyl výsledek zkreslený.
- `dtc_level>=3` (Disconnect) je fail-fast (default `abort_dtc_level=3`).

## Data a reporty

Výstupy (CSV + HTML/JSON report) se ukládají do `runs/` a generují se jen pro kroky, kde máš v UI zapnuté `record`/`measure`.

Na záložce **Běh** je sbalitelná **Historie V / I** (klik na ▸) — live křivka pack napětí a proudu (~10 min), defaultně sbalená.

## Safety

- CAN / EA watchdogs force EA off; IDLE only after current ≈ 0
- **Stop (Esc):** EA off → dwell → I≈0 → hold → IDLE
- **External dropout:** COM loss nebo EL master/slave poloviční proud → `EXT FAIL` + bezpečná cesta

## Lab checklist

See [docs/LAB_CHECKLIST.md](docs/LAB_CHECKLIST.md).
