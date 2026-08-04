"""Altairnano / Clayton BMU DTC catalog (3343572 R2).

On External CAN, each active fault in 0x18FF04xx is a **1-byte** code equal to
the FC number (e.g. 0x68 = 104 = FC104). Older UI wrongly paired two bytes into
one hex word (68A0 → FC104 + FC160).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DtcInfo:
    fc: int
    name: str
    title: str  # short UI label
    detail: str  # longer explanation


# fc number → info (from BMS DTC List 3343572 R2)
_DTC: dict[int, DtcInfo] = {}


def _add(fc: int, name: str, title: str, detail: str) -> None:
    _DTC[fc] = DtcInfo(fc=fc, name=name, title=title, detail=detail)


_add(101, "EXTERNAL_ESTOP_FAULT", "Externí E-Stop", "Aplikace požadovala nouzové zastavení (App Command E-Stop).")
_add(103, "COMM_TIMEOUT_APP_BUS", "Ztráta komunikace s aplikací", "BMU nedostává Application Command (External CAN / timeout).")
_add(104, "LMU_NOT_REPORTING", "LMU neodpovídá", "Žádná data z LMU na interní CAN po dobu limitu.")
_add(105, "PACK_VOLTAGE_MISMATCH", "Nesoulad napětí packu", "Nespínané napětí packu se výrazně liší od souču článků.")
_add(106, "CELL_TEMPERATURE_ABOVE_NORMAL_RANGE", "Teplota článku vysoká", "Jedna nebo více teplot nad normálním rozsahem.")
_add(107, "CELL_TEMPERATURE_BELOW_NORMAL_RANGE", "Teplota článku nízká", "Jedna nebo více teplot pod normálním rozsahem.")
_add(108, "CELL_TEMPERATURE_FAR_ABOVE_NORMAL_RANGE", "Teplota článku velmi vysoká", "Teplota výrazně nad normálním rozsahem.")
_add(109, "CELL_TEMPERATURE_FAR_BELOW_NORMAL_RANGE", "Teplota článku velmi nízká", "Teplota výrazně pod normálním rozsahem.")
_add(110, "CELL_VOLTAGE_ABOVE_NORMAL_RANGE", "Napětí článku vysoké", "Napětí cell-group nad normálním rozsahem.")
_add(111, "CELL_VOLTAGE_BELOW_NORMAL_RANGE", "Napětí článku nízké", "Napětí cell-group pod normálním rozsahem.")
_add(112, "CELL_VOLTAGE_FAR_ABOVE_NORMAL_RANGE", "Napětí článku velmi vysoké", "Napětí cell-group výrazně nad normálním rozsahem.")
_add(113, "CELL_VOLTAGE_FAR_BELOW_NORMAL_RANGE", "Napětí článku velmi nízké", "Napětí cell-group výrazně pod normálním rozsahem.")
_add(114, "MCU_POWER_SUPPLY_FAULT", "Porucha napájení BMU→LMU", "Interní zdroj BMU pro LMU má problém.")
_add(115, "CELL_VOLTAGE_SENSOR_OOR", "Senzor napětí článku mimo rozsah", "Typicky špatné zapojení / sense wiring.")
_add(116, "CHARGE_CURRENT_ABOVE_CMD_LIMIT", "Nabíjecí proud nad limitem", "Proud nabíjení nad povoleným command limitem (~103 %).")
_add(117, "CHARGE_CURRENT_FAR_ABOVE_CMD_LIMIT", "Nabíjecí proud daleko nad limitem", "Proud nabíjení výrazně nad limitem (~150 %).")
_add(122, "DISCONNECT_TOO_SLOW", "Odpojení příliš pomalé", "Po disconnect příkazu proud neklesl k nule včas.")
_add(123, "MAIN_CONTACTOR_FAULT", "Porucha hlavního stykače", "Po sepnutí main není spínané napětí = nespínané.")
_add(128, "CURRENT_SENSOR_LARGE_OFFSET", "Velký offset proudového senzoru", "Offset proudového senzoru mimo normu.")
_add(130, "MULTI_STRING_ADDRESS_MISMATCH", "Multi-string: špatná adresa", "Nesoulad CAN adresy stringu v multi-string konfiguraci.")
_add(131, "MULTI_STRING_COMM_TIMEOUT", "Multi-string: ztráta komunikace", "Koordinátor nedostává data od string BMU.")
_add(133, "DISCHARGE_CURRENT_ABOVE_CMD_LIMIT", "Vybíjecí proud nad limitem", "Proud vybíjení nad povoleným limitem (~103 %).")
_add(134, "DISCHARGE_CURRENT_FAR_ABOVE_CMD_LIMIT", "Vybíjecí proud daleko nad limitem", "Proud vybíjení výrazně nad limitem (~150 %).")
_add(137, "MCU_TEMP_BELOW_NORMAL_RANGE", "Teplota BMU nízká", "Interní teplota MCU pod normou.")
_add(138, "MCU_TEMP_FAR_BELOW_NORMAL_RANGE", "Teplota BMU velmi nízká", "Interní teplota MCU výrazně pod normou.")
_add(139, "MCU_TEMP_ABOVE_NORMAL_RANGE", "Teplota BMU vysoká", "Interní teplota MCU nad normou.")
_add(140, "MCU_TEMP_FAR_ABOVE_NORMAL_RANGE", "Teplota BMU velmi vysoká", "Interní teplota MCU výrazně nad normou.")
_add(142, "LMU_HW_OVERTEMP", "Přehřátí LMU", "Interní teplota LMU nad normou.")
_add(143, "PACK_OUT_OF_BALANCE", "Pack nevyvážený", "Příliš velký rozdíl SOC mezi cell-groups.")
_add(144, "PRECHARGE_RESISTOR_PROTECTION", "Ochrana předřadného odporu", "Precharge dočasně zakázán (ochrana odporu před přehřátím).")
_add(146, "PRECHARGE_FAILURE", "Precharge selhal", "Nepodařilo se předbíjet zátěž.")
_add(147, "HVIL_FAULT", "HVIL přerušen", "High Voltage Interlock Loop je otevřený.")
_add(148, "CONTACTOR_STATUS_MISMATCH", "Nesoulad stavu stykače", "Snímaný stav stykače ≠ požadovaný.")
_add(149, "CELL_VOLTAGE_FAR_BELOW_NORMAL_RANGE_DERATE", "Článek velmi nízko (derate)", "Napětí cell-group daleko pod normou — derating.")
_add(150, "PACK_CURRENT_SENSOR_OOR", "Proudový senzor mimo rozsah", "Senzor pack proudu OOR (často odpojený).")
_add(151, "PACK_CURRENT_SENSOR_PARTIAL_OOR", "Proudový senzor částečně OOR", "U dual-pickup jeden kanál mimo rozsah.")
_add(152, "PACK_VOLTAGE_SENSOR_OOR", "Napěťový senzor packu OOR", "Sense napětí packu mimo rozsah (často wiring).")
_add(153, "CURRENT_SENSOR_MISMATCH", "Nesoulad proudových senzorů", "Dual senzory hlásí odlišný proud.")
_add(154, "MCU_HW_FAILURE", "Hardwarová chyba BMU", "Interní chyba MCU / BMU hardware.")
_add(155, "MCU_SUPPLY_VOLTAGE_ABOVE_NORMAL_RANGE", "Napájení BMU vysoké", "Napájecí napětí BMU nad normou.")
_add(156, "MCU_SUPPLY_VOLTAGE_FAR_ABOVE_NORMAL_RANGE", "Napájení BMU velmi vysoké", "Napájecí napětí BMU výrazně nad normou.")
_add(157, "MCU_SUPPLY_VOLTAGE_BELOW_NORMAL_RANGE", "Napájení BMU nízké", "Napájecí napětí BMU pod normou.")
_add(158, "MCU_SUPPLY_VOLTAGE_FAR_BELOW_NORMAL_RANGE", "Napájení BMU velmi nízké", "Napájecí napětí BMU výrazně pod normou.")
_add(159, "TEMPERATURE_SENSOR_OOR", "Teplotní senzor OOR", "Teplotní vstup mimo rozsah (často odpojený NTC).")
_add(160, "CONFIGURATION_ERROR", "Chyba konfigurace BMS", "BMS není správně nakonfigurována (parametr určuje typ).")
_add(161, "EXTERNAL_ISOLATION_DETECTOR", "Izolační chyba", "Externí izolace hlásí nízký odpor / fault.")
_add(162, "LMU_HW_FAILURE", "Hardwarová chyba LMU", "Porucha hardware LMU (parametr = LMU ID).")
_add(163, "AUX_WARN", "Aux varování", "Externí auxiliary vstup — varování.")
_add(164, "AUX_DISCONNECT", "Aux disconnect", "Externí auxiliary vstup — požadavek odpojení.")
_add(165, "AUX_ESTOP", "Aux E-Stop", "Externí auxiliary vstup — nouzové zastavení.")
_add(166, "CHANGE_IN_TEMPERATURE_FOR_CELL_ABOVE_ALLOWED_LIMIT", "Rychlý nárůst teploty článku", "Změna teploty článku nad povolený limit.")
_add(167, "CELL_VOLTAGE_FAR_OUTSIDE_NORMAL_RANGE", "Napětí článku mimo extrémní meze", "Napětí mimo extreme upper/lower limity.")


_LEVEL_LABEL = {
    0: "OK",
    1: "Info",
    2: "Derate",
    3: "Disconnect",
    4: "E-Stop",
}


def lookup_dtc(code: int) -> DtcInfo | None:
    return _DTC.get(int(code) & 0xFF)


def level_label(level: int) -> str:
    return _LEVEL_LABEL.get(int(level), f"L{level}")


def format_dtc_code(code: int) -> str:
    """Single-line label for UI: 'FC104 — LMU neodpovídá'."""
    code = int(code) & 0xFF
    if code == 0:
        return ""
    info = lookup_dtc(code)
    if info:
        return f"FC{info.fc} — {info.title}"
    return f"FC{code} — neznámý DTC (0x{code:02X})"


def format_dtc_detail(code: int) -> str:
    info = lookup_dtc(int(code) & 0xFF)
    if info:
        return f"{info.detail} [{info.name}]"
    return f"Neznámý kód 0x{int(code) & 0xFF:02X} (není v katalogu 3343572)."


def parse_dtc_message_bytes(data: bytes) -> list[int]:
    """Decode 0x18FF04xx payload: up to 8 one-byte FC codes (0 = empty)."""
    out: list[int] = []
    for b in data[:8]:
        if b:
            out.append(int(b) & 0xFF)
    return out


def format_active_dtcs(codes: list[int] | list[str], *, level: int | None = None) -> str:
    """Human summary for sidebar / logs."""
    nums: list[int] = []
    for c in codes:
        if isinstance(c, int):
            if c:
                nums.append(c & 0xFF)
        else:
            s = str(c).strip()
            if not s:
                continue
            # Legacy "68A0" (two packed bytes) or "68" / "FC104"
            if s.upper().startswith("FC"):
                try:
                    nums.append(int(s[2:]) & 0xFF)
                    continue
                except ValueError:
                    pass
            try:
                raw = int(s, 16)
            except ValueError:
                continue
            if raw > 0xFF:
                # Split legacy 16-bit pairs into two FC bytes
                nums.append((raw >> 8) & 0xFF)
                nums.append(raw & 0xFF)
            else:
                nums.append(raw & 0xFF)
    nums = [n for n in nums if n]
    if not nums:
        prefix = f"{level_label(level)} · " if level is not None and level > 0 else ""
        return f"{prefix}žádné" if level else "—"
    parts = [format_dtc_code(n) for n in nums]
    if level is not None:
        return f"{level_label(level)} (L{level}): " + "; ".join(parts)
    return "; ".join(parts)
