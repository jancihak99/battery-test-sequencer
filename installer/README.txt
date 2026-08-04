Battery Test Sequencer — instalace na další PC
==============================================

1) Nainstaluj Python 3.10+ (python.org) — zaškrtni "Add python.exe to PATH"
2) Nainstaluj Git for Windows (git-scm.com)
3) Volitelně: GitHub CLI →  gh auth login   (nebo připrav fine-grained PAT
   s oprávněním Contents: Read na repo jancihak99/battery-test-sequencer)

4) Spusť Install-BTS.bat (v této složce installer\)
   - instalace do:  %LOCALAPPDATA%\EBZ\BatteryTestSequencer
   - zástupce na Plochu

5) Spusť "Battery Test Sequencer" → Nastavení → CAN/EA COM → Uložit

Aktualizace později:
  - v aplikaci: Nastavení → Zkontrolovat aktualizace → Stáhnout a nainstalovat
  - nebo: Update-BTS.ps1

Token (.github_token) se uloží při instalaci; nesdílej ho.
