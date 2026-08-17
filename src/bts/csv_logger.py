"""Manual CSV logger for live BMS telemetry — an in-app analogue of the Altairnano
Service Tool logger.

The user picks which BMS signals to record, the write interval, and where to save;
Start/Stop drive a background thread that samples ``driver.telemetry()`` and appends
rows. Two output flavours:

* ``service`` — like the Service Tool: ``;`` delimiter, decimal comma (``2,1609``),
  metadata header (User / Date Created / Update Rate), ``Current Time`` +
  ``Test Time(sec)`` columns.
* ``standard`` — ``,`` delimiter, decimal dot, ISO timestamp + ``t_s`` seconds.

Read-only w.r.t. the pack: the logger never commands anything, only samples telemetry.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from bts.models.telemetry import BmsTelemetry

log = logging.getLogger(__name__)

FORMAT_SERVICE = "service"
FORMAT_STANDARD = "standard"

# Signal groups (also used as UI section headings)
G_CORE = "Core"
G_CONTACTOR = "Stykače"
G_CELL = "Články (napětí)"
G_TEMP = "Teploty"


@dataclass
class SignalSpec:
    """One loggable column: stable key, UI label, group, and a telemetry getter."""

    key: str
    label: str
    group: str
    getter: Callable[[BmsTelemetry], object]
    digits: int = 3  # float decimals; 0 = integer


def _bit(x: object) -> int:
    return 1 if x else 0


def available_signals(tel: BmsTelemetry) -> list[SignalSpec]:
    """Build the full signal list from a live sample (array lengths come from ``tel``)."""
    specs: list[SignalSpec] = []

    def add(key: str, label: str, group: str, getter, digits: int = 3) -> None:
        specs.append(SignalSpec(key, label, group, getter, digits))

    add("pack_volt", "Napětí packu [V]", G_CORE, lambda t: t.pack_voltage_v, 2)
    add("pack_curr", "Proud packu [A]", G_CORE, lambda t: t.pack_current_a, 2)
    add(
        "pack_power_kw",
        "Výkon [kW]",
        G_CORE,
        lambda t: (t.pack_voltage_v * t.pack_current_a / 1000.0)
        if (t.pack_voltage_v is not None and t.pack_current_a is not None)
        else None,
        3,
    )
    add("pack_volt_switched", "Spínané napětí [V]", G_CORE, lambda t: t.pack_voltage_switched_v, 2)
    add("soc_pct", "SOC [%]", G_CORE, lambda t: t.soc_pct, 1)
    add("soh_pct", "SOH [%]", G_CORE, lambda t: t.soh_pct, 1)
    add("ah_charge", "Ah nabito", G_CORE, lambda t: t.ah_charge, 3)
    add("ah_discharge", "Ah vybito", G_CORE, lambda t: t.ah_discharge, 3)
    add("cell_v_min", "Umin článek [V]", G_CORE, lambda t: t.cell_v_min, 4)
    add("cell_v_max", "Umax článek [V]", G_CORE, lambda t: t.cell_v_max, 4)
    add("cell_vmin_id", "Umin ID", G_CORE, lambda t: t.cell_vmin_id, 0)
    add("cell_vmax_id", "Umax ID", G_CORE, lambda t: t.cell_vmax_id, 0)
    add("t_min_c", "Tmin [°C]", G_CORE, lambda t: t.t_min_c, 2)
    add("t_max_c", "Tmax [°C]", G_CORE, lambda t: t.t_max_c, 2)
    add("dtc_level", "DTC level", G_CORE, lambda t: t.dtc_level, 0)
    add("chg_limit_a", "Limit nabíjení [A]", G_CORE, lambda t: t.charge_current_limit_a, 1)
    add("dch_limit_a", "Limit vybíjení [A]", G_CORE, lambda t: t.discharge_current_limit_a, 1)
    add(
        "bms_state",
        "Stav BMU",
        G_CORE,
        lambda t: int(t.operating_state) if t.operating_state is not None else None,
        0,
    )

    add("main_pos", "Main+ (0/1)", G_CONTACTOR, lambda t: _bit(t.contactors_effective.main_pos), 0)
    add("main_neg", "Main− (0/1)", G_CONTACTOR, lambda t: _bit(t.contactors_effective.main_neg), 0)
    add("precharge", "Precharge (0/1)", G_CONTACTOR, lambda t: _bit(t.contactors_effective.precharge), 0)
    add("mains_closed", "Stykače sepnuté (0/1)", G_CONTACTOR, lambda t: _bit(t.contactors_effective.mains_closed), 0)

    for i in range(len(tel.cell_voltages or [])):
        add(
            f"cell_v[{i + 1}]",
            f"Článek {i + 1} [V]",
            G_CELL,
            lambda t, idx=i: t.cell_voltages[idx] if idx < len(t.cell_voltages or []) else None,
            4,
        )
    for i in range(len(tel.temperatures_c or [])):
        add(
            f"temp[{i + 1}]",
            f"Teplota {i + 1} [°C]",
            G_TEMP,
            lambda t, idx=i: t.temperatures_c[idx] if idx < len(t.temperatures_c or []) else None,
            2,
        )
    return specs


@dataclass
class Parameter:
    """A user-selectable parameter (one checkbox in the UI).

    Scalars map to a single column; array signals (cell voltages, temperatures) are
    one parameter that expands to many columns — mirroring the Service Tool, where
    ``LMU_Volt_cg`` / ``LMU_Temp_meas`` are single picks that fan out per channel.
    """

    key: str
    label: str
    group: str
    specs: list[SignalSpec]


def available_parameters(tel: BmsTelemetry) -> list[Parameter]:
    """Group :func:`available_signals` into checkbox parameters (arrays aggregated).

    Column order follows the Service Tool: core, then all temperatures, then all cells.
    """
    sigs = available_signals(tel)
    cell_specs = [s for s in sigs if s.group == G_CELL]
    temp_specs = [s for s in sigs if s.group == G_TEMP]
    params: list[Parameter] = [
        Parameter(s.key, s.label, s.group, [s]) for s in sigs if s.group in (G_CORE, G_CONTACTOR)
    ]
    if temp_specs:
        params.append(Parameter("temp_all", f"Všechny teploty ({len(temp_specs)})", G_TEMP, temp_specs))
    if cell_specs:
        params.append(Parameter("cell_v_all", f"Všechna napětí článků ({len(cell_specs)})", G_CELL, cell_specs))
    return params


def _fmt_value(v: object, digits: int, decimal_comma: bool) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        s = str(int(round(v))) if digits == 0 else f"{v:.{digits}f}"
    elif isinstance(v, bool):
        s = "1" if v else "0"
    elif isinstance(v, int):
        s = str(v)
    else:
        s = str(v)
    return s.replace(".", ",") if decimal_comma else s


class CsvTelemetryLogger:
    """Background sampler that appends telemetry rows to a CSV until stopped."""

    def __init__(
        self,
        driver,
        specs: list[SignalSpec],
        interval_s: float,
        fmt: str = FORMAT_SERVICE,
        *,
        user: str = "",
    ) -> None:
        self.driver = driver
        self.specs = list(specs)
        self.interval_s = max(0.1, float(interval_s))
        self.fmt = fmt if fmt in (FORMAT_SERVICE, FORMAT_STANDARD) else FORMAT_SERVICE
        self.user = user
        self.path: Path | None = None
        self.rows_written = 0
        self.error: str | None = None
        self._fh = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._t0 = 0.0

    # ---- lifecycle ----------------------------------------------------------

    def start(self, path: Path) -> None:
        if self._thread is not None:
            raise RuntimeError("Logger already running")
        if not self.specs:
            raise ValueError("Vyber aspoň jeden signál k logování.")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig so Excel opens °C / Czech headers correctly
        self._fh = self.path.open("w", encoding="utf-8-sig", newline="")
        self._write_header()
        self.rows_written = 0
        self.error = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="bts-csv-logger", daemon=True)
        self._thread.start()
        log.info("CSV logger started: %s (%s, %.2fs)", self.path, self.fmt, self.interval_s)

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                log.exception("CSV logger close failed")
            self._fh = None
        log.info("CSV logger stopped: %s rows -> %s", self.rows_written, self.path)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def elapsed_s(self) -> float:
        return (time.monotonic() - self._t0) if self._t0 else 0.0

    # ---- internals ----------------------------------------------------------

    @property
    def _delimiter(self) -> str:
        return ";" if self.fmt == FORMAT_SERVICE else ","

    @property
    def _decimal_comma(self) -> bool:
        return self.fmt == FORMAT_SERVICE

    def _columns(self) -> list[str]:
        if self.fmt == FORMAT_SERVICE:
            lead = ["Current Time", "Test Time(sec)"]
        else:
            lead = ["time_iso", "t_s"]
        return lead + [s.key for s in self.specs]

    def _write_header(self) -> None:
        d = self._delimiter
        if self.fmt == FORMAT_SERVICE:
            sample = self._sample()
            n_cells = len(sample.cell_voltages or [])
            meta = [
                ("User:", self.user or ""),
                ("Date Created:", datetime.now().strftime("%a %b %d %H:%M:%S %Z %Y")),
                ("Update Rate:", f"{self.interval_s:g} sec"),
                ("Cell Groups:", str(n_cells)),
            ]
            for k, v in meta:
                self._fh.write(f"{k}{d}{v}\n")
            self._fh.write("\n")
        self._fh.write(d.join(self._columns()) + "\n")
        self._fh.flush()

    def _sample(self) -> BmsTelemetry:
        d = self.driver() if callable(self.driver) else self.driver
        if d is None:
            return BmsTelemetry()
        try:
            return d.telemetry()
        except Exception:
            log.exception("CSV logger telemetry() failed")
            return BmsTelemetry()

    def _row(self, tel: BmsTelemetry, t_s: float) -> str:
        comma = self._decimal_comma
        if self.fmt == FORMAT_SERVICE:
            ts = datetime.now().strftime("%m/%d/%Y %H:%M:%S")
            lead = [ts, _fmt_value(t_s, 3, comma)]
        else:
            lead = [datetime.now().isoformat(timespec="milliseconds"), f"{t_s:.3f}"]
        vals = [_fmt_value(s.getter(tel), s.digits, comma) for s in self.specs]
        return self._delimiter.join(lead + vals)

    def _run(self) -> None:
        self._t0 = time.monotonic()
        n = 0
        while not self._stop.is_set():
            try:
                tel = self._sample()
                self._fh.write(self._row(tel, time.monotonic() - self._t0) + "\n")
                self._fh.flush()
            except Exception as exc:  # disk full, handle closed, …
                self.error = str(exc)
                log.exception("CSV logger write failed")
                break
            n += 1
            self.rows_written = n
            target = self._t0 + n * self.interval_s
            wait = target - time.monotonic()
            if wait > 0:
                self._stop.wait(wait)
