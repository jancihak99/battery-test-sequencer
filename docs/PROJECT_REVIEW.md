# Battery Test Sequencer — kritika projektu a návrh dalšího směřování

> Datum: 2026-08-11 · Verze aplikace v době review: **0.3.11** · Rozsah: ~13 200 řádků Pythonu
>
> Toto je konzultační review celého projektu (architektura, bezpečnost,
> kvalita kódu, distribuce, dokumentace) s prioritizovanou roadmapou.
> Dokument nic v kódu nemění — je to podklad pro rozhodování.

---

## 1. Shrnutí (TL;DR)

Battery Test Sequencer je **funkčně vyspělá aplikace s mimořádně dobře
promyšlenou bezpečnostní vrstvou** pro řízení vysokoproudého hardwaru. Vidět
je, že autor rozumí doméně (LTO moduly, BMU, kontaktory, DTC) a bezpečnost je
jasná priorita. To je největší přednost projektu.

Zároveň nese znaky rychle rostoucího jednovývojářského nástroje: **dva
god-objekty**, **nulové automatizované testy/CI**, a — nejzávažnější —
**dvě kritická bezpečnostně-právní zjištění na veřejném repu**, která je nutné
řešit okamžitě a nezávisle na kvalitě kódu.

| Priorita | Téma | Riziko |
|---|---|---|
| **P0** | GitHub token v git historii (veřejný repo) | Únik přístupových údajů |
| **P0** | Důvěrné vendor specifikace v `docs/_*.txt` (veřejný repo) | Porušení NDA / IP |
| **P1** | Auto-update spouští nepodepsaný kód bez ověření | RCE na HW-řídícím PC |
| **P1** | Race condition — mělká kopie telemetrie mezi vlákny | Torn reads / pády za běhu |
| **P1** | Nulové testy / CI / lint / lockfile | Regrese, nereprodukovatelné buildy |
| **P1** | Nesoulad pinu `python-can` (dokumentovaně rozbitá verze projde) | Nefunkční instalace |
| **P2** | God-objekty (`main_window.py`, `sequence.py`) | Udržovatelnost |
| **P2** | Blokující I/O na GUI vlákně | Mrznutí UI |
| **P2** | Reporty nejsou certifikační kvality | Použitelnost výstupu |

---

## 2. Co je na projektu dobré

Aby kritika nezastínila silné stránky — tohle je nadprůměrně zvládnuté a
při refaktorech je nutné to **zachovat**:

- **Vrstvená safety-first architektura.** Centrální invariant „nikdy neotevřít
  Main+/Main− pod proudem" je vynucen důsledně: `_safe_shutdown` vždy dělá
  EA-off → čekání na I≈0 → BMS IDLE (`src/bts/engine/sequence.py:427`), a
  `_wait_current_near_zero` při timeoutu **raději nechá pack v READY, než by
  otevřel kontaktory pod proudem** (`sequence.py:419`).
- **Trojnásobně vynucený source/load mutex** (engine watchdog + `ensure_exclusive`
  + defenzivní kill v `telemetry()`), živý watchdog nad CAN i SCPI
  (`sequence.py:668`), dvouúrovňové aborty: okamžité pro tvrdé napěťové limity,
  debounced pro teplotu/DTC (`sequence.py:461`, `:500`).
- **Detekce tichého výpadku** EL master/slave (měřený proud ≈ ½ setpointu při
  zdravém CAN) — `_check_power_path_integrity` (`sequence.py:754`).
- **Čisté driver ABC + plné mocky + fault-injection framework.** Engine je
  plně provozovatelný bez HW (`MockBmsDriver`, `MockEaRack`, `inject_fault`,
  `sequence.py:843`). Skvělý základ pro testy.
- **Správné marshalování engine→UI přes Qt signály** (`main_window.py:1969`) —
  v komentáři je vidět, že to autor vyřešil po dřívějším bugu.
- **Robustní validace programů** vůči profilu s doménovými hláškami typu
  „BMU WILL disconnect" (`src/bts/engine/validation.py`).
- **Offline instalátor** pro izolovaná lab PC a **self-contained HTML report**
  bez JS (ruční SVG grafy) — dobrá volba pro prostředí bez internetu.

---

## 3. Kritická zjištění (P0 — řešit ihned)

### 3.1 GitHub token v git historii veřejného repozitáře

**Stav (ověřeno):** aktuální soubor `src/bts/_embedded_token.py:3` je prázdný
(`UPDATE_TOKEN = ""`), ale token byl v minulosti commitnut a **je stále čitelný
z historie**:

- commit `8b532ba` („release: v0.3.2 — embedded GitHub token for lab
  auto-update") obsahuje `UPDATE_TOKEN = 'gho_…'` (GitHub OAuth token);
- commit `e928ad5` soubor vyprázdnil, ale vyprázdnění souboru **historii
  neodstraní** — kdokoli spustí `git show 8b532ba:src/bts/_embedded_token.py`.

Repo je explicitně veřejný (`config/update.yaml:1`).

**Doporučený postup (akci provede vlastník repa):**
1. **Ihned revokovat token** v GitHub → Settings → Developer settings
   (i kdyby už byl neplatný — nespoléhat na to).
2. **Přepsat historii** — `git filter-repo` (doporučeno) nebo BFG Repo-Cleaner
   odstraní soubor ze všech commitů; poté force-push a informovat případné
   forky/klony.
3. **Zrušit celou koncepci embedded tokenu** — repo je veřejný, PAT není
   potřeba. Smazat token-resolution žebřík v `src/bts/update.py` (`read_token`
   / `token_source`) i soubor `_embedded_token.py`. Vyprázdnění je jen náplast
   nad špatným vzorem (tajemství ve zdrojáku, který se šíří na každé lab PC).

### 3.2 Důvěrné vendor specifikace na veřejném repu

**Stav (ověřeno):** v `docs/` jsou committnuté raw textové extrakty z vendor
PDF specifikací:

- `docs/_extract_app_int.txt` (App Integration spec, 2061 řádků)
- `docs/_dtc_extract.txt` (DTC list, 1404 řádků)
- `docs/_extract_ext_can.txt` (External CAN spec, 1914 řádků)
- `docs/_vmin_page.txt` (70 řádků)

Soubory obsahují desítky značek **„CONFIDENTIAL / Business Confidential /
Restricted Distribution / proprietary"** (ověřeno: 63 + 27 + 2 výskytů).
Na veřejném GitHubu to je pravděpodobné **porušení NDA / IP** vůči dodavateli
(Altairnano / nano-power).

**Doporučený postup:**
1. Ověřit s právním/dodavatelem, zda smí být tyto materiály vůbec uloženy
   mimo interní úložiště.
2. Odstranit z pracovního stromu **i z historie** (stejný `filter-repo`/BFG
   běh jako u 3.1).
3. V repu ponechat jen **vlastní** derivované artefakty (např. `dtc_catalog.py`,
   pokud neobsahuje doslovné důvěrné texty) a odkaz na interní zdroj.

> Poznámka: obě P0 věci se řeší **jedním** přepisem historie — vyřešit
> společně.

### 3.3 Strategické rozhodnutí: má být repo veřejný?

Kombinace 3.1 + 3.2 naznačuje, že veřejnost repa nebyla zcela zvážená. Doporučuji
explicitně rozhodnout:
- **Pokud veřejný záměrně** (open-source nástroj) → důsledně vyčistit veškerá
  tajemství i cizí IP, přidat LICENSI a `SECURITY.md`.
- **Pokud ne** → repozitář zprivátnit; auto-update se pak vrátí k modelu s
  autentizací (a s ověřením podpisu, viz 4.1).

---

## 4. Vysoká priorita (P1)

### 4.1 Auto-update spouští nepodepsaný kód bez ověření

`src/bts/update.py` stáhne buď GitHub Release **zipball**, rozbalí ho přes
instalační adresář a spustí `pip install -r requirements.txt` + `pip install -e .`,
případně udělá `git pull --ff-only`. **Nikde není checksum, podpis ani pinning
na konkrétní tag/commit.** Na PC, které řídí vysokoproudý hardware, je to
spuštění libovolného kódu z internetu.

Riziko: kdokoli s MITM pozicí nebo s přístupem k release procesu (viz únik
tokenu 3.1) může doručit libovolný Python, který se spustí na lab PC.

**Návrh:**
- Podepisovat release artefakt (min. SHA-256 manifest publikovaný odděleně,
  lépe podpis GPG/Sigstore) a **ověřit před rozbalením/spuštěním**.
- Pinovat update na konkrétní tag/commit, ne na „latest branch HEAD".
- Atomická výměna (rozbalit do temp → přepnout), aby přerušený update
  nenechal instalaci v půli (dnes se zálohuje jen `config/` a token, ne
  strom kódu).
- Zvážit, zda auto-prompt-install nemá být default **vypnutý**.

### 4.2 Race condition — mělká kopie telemetrie mezi vlákny

`BmsCanDriver.telemetry()` vrací `BmsTelemetry(**self._tel.__dict__)` pod zámkem
(`src/bts/drivers/bms.py:196`, stejně mock na `:542`). `BmsTelemetry` má ale
mutable pole (`cell_voltages`, `temperatures_c`, `active_dtcs`, …) a `**__dict__`
je **mělká** kopie — vrácený objekt sdílí tytéž listy s živým `_tel`. Heartbeat
vlákno je in-place mutuje, zatímco sekvenční vlákno je čte
(`_cell_extremes` / `_finite_cells`). Výsledek: možné `list changed size during
iteration` nebo torn reads — a zámek tím ztrácí smysl.

**Návrh:** v `telemetry()` dělat deep copy mutable polí (např. `copy.deepcopy`
nebo explicitní `list(...)`/`dict(...)` per pole). Pro srovnání: `status()`
v enginu (`sequence.py:170`) deep-copy dělá správně — driver ne.

### 4.3 Nulové automatizované testy / CI / lint / lockfile

Přestože je mock-scaffolding výborný, **není žádná `tests/` složka, žádný pytest
v závislostech, žádné `.github/workflows`, žádný ruff/black/mypy, žádný
lockfile.** „Testy" jsou tři ruční skripty v `scripts/` (`smoke_test.py`,
`test_el_slave_fail.py`, `test_validate.py`), které nikdo automaticky nespouští.

**Návrh (nízké úsilí, vysoká návratnost):**
- `pytest` suite nad enginem + mocky: pokrýt každou abort úroveň, CV taper,
  DCIR SOC gate, BMU derate, `_wait_current_near_zero`, EXT FAIL cestu.
- GitHub Actions: lint + type-check + testy na push/PR.
- `ruff` (lint+format) + `mypy`, konfigurace v `pyproject.toml`.
- Lockfile (`uv.lock` / `requirements.lock`) pro reprodukovatelné buildy.

### 4.4 Nesoulad pinu `python-can`

`requirements.txt` pinuje `python-can==4.5.0`, ale `pyproject.toml:14` povoluje
`>=4.3.0`. Kód přitom dokumentuje, že **4.6+ je rozbité** (Kvaser `canIoCtl`
bug, viz `diagnostics.py`). `pip install .` tedy může stáhnout verzi, o které
tým ví, že nefunguje.

**Návrh:** sjednotit v obou souborech na `>=4.3.0,<4.6` (nebo `==4.5.0`).

### 4.5 Nepodepsaný instalátor → SmartScreen

`BTS-Setup.exe` (~260 MB) je nepodepsaný a vyžaduje admin práva; Windows
SmartScreen uživatele varuje (`README.md:17`).

**Návrh:** OV/EV code-signing certifikát odstraní varování a je standardním
řešením pro distribuci mimo Store.

---

## 5. Střední priorita (P2 — udržovatelnost a čitelnost)

### 5.1 God-objekty

- **`src/bts/ui/main_window.py` (2249 řádků)** míchá minimálně 5 rolí:
  stavba widgetů, editace modelu programu, řízení HW (přímé volání driverů),
  **safety shutdown v UI** (`_safe_power_down_for_quit`), orchestrace update a
  persistence configu. QMainWindow je zároveň controller, editor i správce
  driverů.
- **`src/bts/engine/sequence.py` (1764 řádků)** = jedna třída s ~500řádkovým
  `if/elif` dispatchem přes 10 typů kroků + **duplicitní charge/discharge
  větve** (`sequence.py:1235` vs `:1350`), které se rozjíždějí nezávisle
  (CV `i_min_a` logika je jen na charge straně).

**Návrh:**
- UI rozdělit na `HardwareController` (vlastní bms/ea + connect/disconnect +
  safe-shutdown), `ProgramEditorController`, `UpdateController` a čisté view
  třídy per tab — přesně vzor, který **ostatní taby už dodržují**
  (`dashboard.py`, `simulate_tab.py`, `diagnostics_tab.py`).
- Step dispatch → registry/handler třídy (`StepHandler` per typ), sdílený
  charge/discharge helper. Report/CSV I/O a operátorské diagnostické texty
  vytáhnout z enginu (nepatří do state machine).

### 5.2 Duplicitní safety-shutdown logika v UI i enginu

`_safe_power_down_for_quit` v UI reimplementuje contactor-safe shutdown, který
už je v enginu. Dva zdroje pravdy pro bezpečnostně kritickou logiku = riziko
rozjetí. **Sjednotit na engine**, UI jen volá.

### 5.3 Blokující I/O na GUI vlákně

Několik operací běží synchronně na GUI vlákně s ručním `processEvents()`:
`_connect_hw` (blokující `.connect()` + 3s smyčka), `_refresh_live` v idle
stavu volá `self._ea.telemetry()` (SCPI-over-serial round-trip) každý tick,
`_safe_power_down_for_quit` běží až 20 s v `closeEvent` s `time.sleep` +
`processEvents()` (re-entrantní hazard). Jen Diagnostics scan používá správně
`QThread` worker.

**Návrh:** přesunout blokující serial/CAN/shutdown I/O na `QThread` workery
(vzor `diagnostics_tab.py:30`).

### 5.4 Netypované step parametry a nevalidované profily

`Step.params: dict[str, Any]` (`models/program.py:88`) — všechna klíčová
per-step pole jsou netypovaná; překlep v klíči tiše projde (validace některé
klíče jako `soc_ref_pct`, `soc_band_pct` vůbec nekontroluje). Profily
(`ModuleProfile.from_dict`) navíc **nemají žádnou validaci** — chybějící
`nominal_capacity_ah` selže až později matematikou.

**Návrh:** schéma per step-type (dataclass/pydantic/jsonschema) + validace
profilů obdobná `validate_program`.

### 5.5 Drobnosti se sečtou

- **Mock-awareness prosakuje do produkčního step kódu** — `isinstance(...,
  MockBmsDriver)` a sahání na privátní `_last_arm_sample` uvnitř reálné step
  logiky (`sequence.py`). Nahradit injektovaným hookem.
- **Dvojjazyčný string-soup** (CZ/EN promíchané v jednom widgetu) bez i18n /
  `tr()` vrstvy — obtížně udržovatelné a nelokalizovatelné.
- **Pervasivní `except Exception: pass`** i na měřicích cestách — může
  maskovat reálné poruchy (např. `_pack_and_ea_current_a` při selhání driveru
  tiše hlásí 0 A, což je vstup do rozhodnutí o otevření kontaktorů).
- **Opakované inline CSS** místo existujících konstant v `ui/theme.py`.
- **Tři zdroje pravdy pro verzi** (`VERSION`, `pyproject.toml`, `version.py`) —
  dnes v souladu, ale ruční aktualizace na každý release je náchylná k chybě.
- **Magické konstanty** roztroušené (`estimate.py`, `sim_preview.py`) — část
  patří do configu.

---

## 6. Reporty — nejsou certifikační kvality

`report_builder.py` produkuje pěkný self-contained HTML + JSON. Pro **provozní
summary** je to dobré, pro **lab/certifikační výstup** chybí traceability:

- žádný **verdikt pass/fail** vůči `min_capacity_ah` (pole v profilu existuje,
  report ho nepoužívá);
- žádná **identifikace přístrojů** (`*IDN?` / firmware PSI/EL/BMU), operátor,
  verze aplikace, ambient podmínky, nejistota měření;
- **žádný PDF export** (certifikace typicky chce podepsané PDF);
- **labely natvrdo česky** bez i18n.

**Návrh dle záměru:** rozhodnout, zda má report být jen provozní (pak stačí
drobnosti), nebo certifikační — pak doplnit verdikt, traceability a PDF.

---

## 7. Návrh dalšího směřování (roadmapa)

Pořadí je zvolené tak, aby nejdřív padla rizika, pak se postavila záchranná síť
(testy/CI), a teprve potom se sáhlo na velké refaktory.

**Sprint 0 (dny) — hašení požárů**
- P0: revokovat token, odstranit důvěrné docs, jeden `filter-repo`/BFG přepis
  historie (§3.1, §3.2).
- Rozhodnout veřejnost repa (§3.3).
- Sjednotit `python-can` pin (§4.4).

**Sprint 1 — záchranná síť**
- Oprava telemetry race (§4.2) — malý diff, velký dopad.
- pytest suite nad mocky + GitHub Actions CI + ruff/mypy + lockfile (§4.3).
- Ověření podpisu/hashe v auto-update (§4.1).

**Sprint 2 — udržovatelnost**
- Rozbití `main_window.py` na controllery + view (§5.1), sjednocení
  safety-shutdown na engine (§5.2).
- Přesun blokujícího I/O z GUI vlákna (§5.3).
- Extrakce charge/discharge helperu a step handlerů ze `sequence.py`.

**Sprint 3 — kvalita výstupu a produkt (dle záměru)**
- Schéma step params + validace profilů (§5.4).
- Certifikační report: verdikt / traceability / PDF (§6).
- i18n vrstva, pokud se cílí mimo CZ kontext.
- Code-signing instalátoru (§4.5).

---

## 8. Závěr

Jádro je zdravé a bezpečnostní inženýrství je nadprůměrné — to je nejtěžší část
a je zvládnutá. Největší hodnotu teď přinese **ne psaní nových funkcí, ale
uzavření rizik** (§3, §4) a **postavení testovací/CI sítě** (§4.3), která
umožní bezpečně provést refaktory god-objektů. Doporučuji začít Sprintem 0
ještě dnes — dvě P0 věci jsou otevřené na veřejném repu.
