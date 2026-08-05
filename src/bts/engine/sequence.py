from __future__ import annotations

import csv
import logging
import threading
import time
from copy import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from bts.drivers.bms import BmsDriver, MockBmsDriver
from bts.drivers.ea import EaRackDriver
from bts.engine.report_builder import (
    build_report_payload,
    traces_from_csv_rows,
    write_report_bundle,
)
from bts.models.current import format_amps_with_crate, resolve_amps
from bts.models.program import ModuleProfile, Program, Step
from bts.models.telemetry import BmuState, DesiredState, EaTelemetry, RunMeasurements

log = logging.getLogger(__name__)

# Soft checks: EA/BMS values are not instant — confirm before failing the run.
_SOFT_ABORT_CONFIRM_S = 3.0
_SOFT_ABORT_CONFIRM_S_DTC = 1.0
_BMU_CURRENT_MARGIN = 0.97  # keep small headroom below BMU command limits

# Cell voltage safety margins (applied to profile limits).
# Stop triggers first (bigger margin); abort is the hard safety net.
_CELL_V_STOP_MARGIN  = 0.030  # V — stop step 30 mV inside profile limit
_CELL_V_ABORT_MARGIN = 0.010  # V — hard abort 10 mV inside profile limit
_CURRENT_GATE_WAIT_S = 8.0
_CURRENT_GATE_OK_A = 0.5
_I_MIN_HOLD_S = 5.0  # CV end-current must stay ≤ i_min_a this long

# Never open contactors under load — wait for current to decay after EA off.
# Timing is current-driven; fixed dwells are short (only to confirm I stays low).
_CONTACTOR_OPEN_I_MAX_A = 1.5
_CONTACTOR_OPEN_WAIT_S = 20.0       # ceiling while waiting for I→0
_CONTACTOR_OPEN_MIN_DWELL_S = 1.5   # brief confirm after EA off before accepting I≈0
_CONTACTOR_OPEN_HOLD_S = 1.5        # I must stay ≤ limit this long, then IDLE
# UI join / quit must wait at least this long for safe_shutdown thread work.
CONTACTOR_SAFE_JOIN_S = _CONTACTOR_OPEN_WAIT_S + _CONTACTOR_OPEN_HOLD_S + 10.0
# After Main+/Main− close (READY): always dwell before charge/discharge/dcir.
# Stepfiles must not rely on inserting wait_time after bms_ready.
_CONTACTOR_SETTLE_S = 10.0


class RunState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


@dataclass
class EngineStatus:
    run_state: RunState = RunState.IDLE
    current_step_id: str | None = None
    current_step_index: int = -1
    current_step_type: str | None = None
    message: str = ""
    measurements: RunMeasurements = field(default_factory=RunMeasurements)
    started_at: str | None = None
    finished_at: str | None = None
    log_path: str | None = None
    report_path: str | None = None
    activity_path: str | None = None
    run_started_mono: float | None = None
    step_started_mono: float | None = None
    activity_tail: list[str] = field(default_factory=list)


class SequenceEngine:
    def __init__(
        self,
        bms: BmsDriver,
        ea: EaRackDriver,
        profile: ModuleProfile,
        program: Program,
        runs_dir: Path,
        poll_period_s: float = 0.2,
        can_timeout_s: float = 1.0,
        ea_timeout_s: float = 2.0,
        abort_dtc_level: int = 3,
        serial_number: str = "",
        ea_i_mismatch_min_set_a: float = 40.0,
        ea_i_slave_ratio_lo: float = 0.35,
        ea_i_slave_ratio_hi: float = 0.65,
        ea_i_collapse_ratio: float = 0.15,
        ea_i_mismatch_confirm_s: float = 2.0,
    ) -> None:
        self.bms = bms
        self.ea = ea
        self.profile = profile
        self.program = program
        self.runs_dir = runs_dir
        self.poll_period_s = poll_period_s
        self.can_timeout_s = can_timeout_s
        self.ea_timeout_s = ea_timeout_s
        self.abort_dtc_level = abort_dtc_level
        self.serial_number = serial_number
        self.ea_i_mismatch_min_set_a = ea_i_mismatch_min_set_a
        self.ea_i_slave_ratio_lo = ea_i_slave_ratio_lo
        self.ea_i_slave_ratio_hi = ea_i_slave_ratio_hi
        self.ea_i_collapse_ratio = ea_i_collapse_ratio
        self.ea_i_mismatch_confirm_s = ea_i_mismatch_confirm_s

        self._status = EngineStatus()
        self._lock = threading.RLock()
        self._abort = threading.Event()
        self._thread: threading.Thread | None = None
        self._listeners: list[Callable[[EngineStatus], None]] = []
        self._activity_listeners: list[Callable[[str], None]] = []
        self._csv_rows: list[dict[str, Any]] = []
        self._ah_start: float | None = None
        self._step_ah_start: float | None = None
        self._activity: list[str] = []
        self._activity_path: Path | None = None
        # Soft-abort debounce: (reason_key, since_mono)
        self._soft_abort: tuple[str, float] | None = None
        # External device integrity debounce: (reason, since_mono)
        self._ext_fail: tuple[str, float] | None = None
        # True after we once saw healthy current on the active path (≥70% setpoint)
        self._power_seen_ok = False
        # Last EA sample from the run thread (UI must not poll SCPI while RUNNING)
        self._last_ea: EaTelemetry | None = None
        # Opt-in per-step recording (CSV + report charts)
        self._record_step = False
        # Per-step: whether we obey BMU current limits (live derate)
        self._bmu_respect_active: bool = False
        self._bmu_mode_active: str | None = None  # charge | discharge
        # True when commanded I must stay for a valid result (capacity / DCIR).
        # On BMU derate that would cut current → fail the run instead of continuing.
        self._bmu_measure_critical: bool = False
        self._bmu_last_derate_a: float | None = None
        self._bmu_last_dtc_level: int | None = None
        self._run_mono0: float | None = None
        self._step_types: dict[str, str] = {}
        self._step_results: dict[str, dict[str, Any]] = {}  # capacity/dcir per step_id
        self._integrated_ah_step = 0.0
        self._run_stamp: str = ""
        self._csv_path: Path | None = None
        self._last_sample_mono = 0.0

    def last_ea_telemetry(self) -> EaTelemetry | None:
        """Thread-safe copy of the latest EA sample taken by the sequence thread."""
        with self._lock:
            return copy(self._last_ea) if self._last_ea is not None else None

    def _cache_ea(self, tel: EaTelemetry | None = None) -> EaTelemetry:
        e = tel if tel is not None else self.ea.telemetry()
        with self._lock:
            self._last_ea = e
        return e

    def add_listener(self, cb: Callable[[EngineStatus], None]) -> None:
        self._listeners.append(cb)

    def add_activity_listener(self, cb: Callable[[str], None]) -> None:
        self._activity_listeners.append(cb)

    def status(self) -> EngineStatus:
        with self._lock:
            return EngineStatus(
                run_state=self._status.run_state,
                current_step_id=self._status.current_step_id,
                current_step_index=self._status.current_step_index,
                current_step_type=self._status.current_step_type,
                message=self._status.message,
                measurements=RunMeasurements(
                    capacity_ah=self._status.measurements.capacity_ah,
                    dcir_mohm=self._status.measurements.dcir_mohm,
                    extras=dict(self._status.measurements.extras),
                ),
                started_at=self._status.started_at,
                finished_at=self._status.finished_at,
                log_path=self._status.log_path,
                report_path=self._status.report_path,
                activity_path=self._status.activity_path,
                run_started_mono=self._status.run_started_mono,
                step_started_mono=self._status.step_started_mono,
                activity_tail=list(self._activity[-80:]),
            )

    def _emit(self) -> None:
        st = self.status()
        for cb in list(self._listeners):
            try:
                cb(st)
            except Exception:
                log.exception("Listener error")

    def _event(self, msg: str, *, to_status: bool = False) -> None:
        """Timestamped activity line for console + file analysis."""
        line = f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]}  {msg}"
        with self._lock:
            self._activity.append(line)
            if to_status:
                self._status.message = msg
        log.info(msg)
        if self._activity_path is not None:
            try:
                with self._activity_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
        for cb in list(self._activity_listeners):
            try:
                cb(line)
            except Exception:
                log.exception("Activity listener error")
        self._emit()

    def _set_msg(self, msg: str) -> None:
        self._event(msg, to_status=True)

    def _snapshot_bms(self, prefix: str = "BMU") -> str:
        b = self.bms.telemetry()
        cmd = b.contactor_cmd
        st = b.contactor_status
        eff = b.contactors_effective
        dtc = ",".join(b.active_dtcs) if b.active_dtcs else "none"
        pack = f"{b.pack_voltage_v:.3f}" if b.pack_voltage_v is not None else "—"
        sw = (
            f"{b.pack_voltage_switched_v:.3f}"
            if b.pack_voltage_switched_v is not None
            else "—"
        )
        i = f"{b.pack_current_a:.2f}" if b.pack_current_a is not None else "—"
        lim_ch = (
            f"{b.charge_current_limit_a:.0f}"
            if b.charge_current_limit_a is not None
            else "—"
        )
        lim_dch = (
            f"{b.discharge_current_limit_a:.0f}"
            if b.discharge_current_limit_a is not None
            else "—"
        )
        return (
            f"{prefix} state={b.operating_state.name} "
            f"pack={pack}V switched={sw}V I={i}A "
            f"Ilim[chg={lim_ch}A dch={lim_dch}A] "
            f"cmd[M+={int(cmd.main_pos)} M-={int(cmd.main_neg)} Pre={int(cmd.precharge)}] "
            f"sts[M+={int(st.main_pos)} M-={int(st.main_neg)} Pre={int(st.precharge)}] "
            f"eff[M+={int(eff.main_pos)} M-={int(eff.main_neg)} Pre={int(eff.precharge)}] "
            f"DTC_L{b.dtc_level}[{dtc}]"
        )

    def _diagnose_zero_charge_current(self, detail: str, et) -> str:
        """PSI@setV / 0A while switched bus sits near pack ⇒ PSI not on that DC node."""
        b = self.bms.telemetry()
        pack = b.pack_voltage_v
        sw = b.pack_voltage_switched_v
        psi_v = et.psi_voltage_v
        samp = getattr(self.ea, "_last_arm_sample", None) or {}
        peak = float(samp.get("peak_i", 0.0) or 0.0)
        sust = float(samp.get("sustained_i", abs(et.psi_current_a)) or 0.0)
        lines = [
            "Charge started but PSI sustained current ≈ 0 A.",
            f"PSI: {detail}",
            self._snapshot_bms("BMU"),
        ]
        if peak >= 0.5 and sust < 0.5:
            lines.append(
                f"NOTE: SCPI saw a short peak {peak:.2f}A then I→0 (sust={sust:.2f}A). "
                "That matches front-panel blip from output/cable capacitance at OUTP ON — "
                "not continuous charge into the pack. Raw MEAS replies are in the PSI line above."
            )
        if pack is not None and psi_v is not None and sust < 0.5:
            if sw is not None and sw > 5.0 and (psi_v - sw) > 2.0:
                lines.append(
                    f"ROOT CAUSE: PSI meas {psi_v:.2f}V @ ~0A while BMU switched bus is {sw:.2f}V "
                    f"(pack {pack:.2f}V). These cannot be the same DC node — "
                    "PSI power leads are NOT connected to the switched pack bus "
                    "(or fuse/connector open on the PSI branch only). "
                    "IR drop cannot explain this: at I=0, drop≈0; a connected PSI would read ~switched V, not its free setpoint."
                )
            elif psi_v > pack + 1.0:
                lines.append(
                    f"DIAG: PSI {psi_v:.2f}V ≈ setpoint, pack {pack:.2f}V, I≈0 → PSI output looks unloaded."
                )
            lim = b.charge_current_limit_a
            if lim is not None and lim < 1.0:
                lines.append(f"NOTE: BMU charge current limit is {lim:.1f}A.")
        lines.append(
            "Action: multimeter between PSI DC+/DC− and control-box switched B+/B−; "
            "check PSI branch fuse, polarity, and that cables land on switched (not only sense)."
        )
        return " ".join(lines)

    def start(self) -> None:
        with self._lock:
            if self._status.run_state == RunState.RUNNING:
                return
            self._abort.clear()
            self._status = EngineStatus(
                run_state=RunState.RUNNING,
                started_at=_utc_now(),
                run_started_mono=time.monotonic(),
            )
            self._csv_rows = []
            self._activity = []
            self._record_step = False
            self._run_mono0 = time.monotonic()
            self._step_types = {}
            self._step_results = {}
            self._integrated_ah_step = 0.0
            self._csv_path = None
            self._last_sample_mono = 0.0
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._run_stamp = stamp
        self._activity_path = self.runs_dir / f"{stamp}_{self.program.meta.name}_activity.log"
        self._activity_path.write_text(
            f"# BTS activity log  {self._activity_path.name}\n"
            f"# program={self.program.meta.name}  serial={self.serial_number}\n",
            encoding="utf-8",
        )
        with self._lock:
            self._status.activity_path = str(self._activity_path)
        self._thread = threading.Thread(target=self._run, name="seq-engine", daemon=True)
        self._thread.start()

    def abort(self) -> None:
        """Request stop — sequence thread will EA-off, wait I≈0, then open contactors."""
        self._abort.set()
        self._set_msg("Stop requested — EA off, then contactors after I≈0")

    def join(self, timeout_s: float | None = 60.0) -> None:
        """Wait for the sequence thread to finish (incl. safe contactor open)."""
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout_s)

    def _pack_and_ea_current_a(self) -> float:
        """Largest |I| seen on pack or EA (A)."""
        pack_i = 0.0
        ea_i = 0.0
        try:
            b = self.bms.telemetry()
            if b.pack_current_a is not None:
                pack_i = abs(float(b.pack_current_a))
        except Exception:
            pass
        try:
            e = self.ea.telemetry()
            ea_i = max(abs(float(e.psi_current_a)), abs(float(e.el_current_a)))
        except Exception:
            pass
        return max(pack_i, ea_i)

    def _wait_current_near_zero(
        self,
        *,
        label: str,
        timeout_s: float = _CONTACTOR_OPEN_WAIT_S,
        i_max_a: float = _CONTACTOR_OPEN_I_MAX_A,
        min_dwell_s: float = _CONTACTOR_OPEN_MIN_DWELL_S,
    ) -> bool:
        """After EA off: wait until current is low enough to open contactors safely.

        Always dwells at least min_dwell_s so Stop is visible even when I was already ~0.
        Returns True if I dropped in time, False on timeout (do NOT open contactors).
        """
        self._event(
            f"{label}: EA off — waiting ≥{min_dwell_s:.0f}s then I≤{i_max_a:.1f}A "
            f"(timeout {timeout_s:.0f}s, contactors stay CLOSED)"
        )
        t0 = time.monotonic()
        last_log = -1.0
        while True:
            try:
                self.ea.all_off()
            except Exception:
                pass
            i_now = self._pack_and_ea_current_a()
            elapsed = time.monotonic() - t0
            dwell_ok = elapsed >= min_dwell_s
            if dwell_ok and i_now <= i_max_a:
                self._event(
                    f"{label}: I={i_now:.2f}A ≤ {i_max_a:.1f}A after {elapsed:.1f}s — "
                    f"confirm {_CONTACTOR_OPEN_HOLD_S:.1f}s (I must stay low)"
                )
                hold_t0 = time.monotonic()
                hold_ok = True
                while time.monotonic() - hold_t0 < _CONTACTOR_OPEN_HOLD_S:
                    try:
                        self.ea.all_off()
                    except Exception:
                        pass
                    i_hold = self._pack_and_ea_current_a()
                    if i_hold > i_max_a:
                        self._event(
                            f"{label}: during confirm I rose to {i_hold:.2f}A — "
                            "stykače NEOTEVÍRÁME, čekám dál"
                        )
                        hold_ok = False
                        break
                    time.sleep(0.2)
                if hold_ok:
                    self._event(f"{label}: I still ≤{i_max_a:.1f}A — OK to open contactors")
                    return True
                # I rose during hold — keep waiting in outer loop
                continue
            if elapsed - last_log >= 0.5:
                last_log = elapsed
                phase = "min dwell" if not dwell_ok else "I decay"
                self._event(
                    f"{label}: {phase} {elapsed:.1f}/{timeout_s:.0f}s  I={i_now:.2f}A  "
                    f"(stykače CLOSED)"
                )
            if elapsed >= timeout_s:
                self._event(
                    f"{label}: TIMEOUT {timeout_s:.0f}s with I={i_now:.1f}A — "
                    "NOT opening contactors (leave READY). Check EA off / fuse / cabling."
                )
                return False
            time.sleep(0.25)

    def _safe_shutdown(self, *, open_contactors: bool = True) -> None:
        """EA off first; only then BMS IDLE once current is near zero.

        Opening Main+/Main− under load arcs and damages contactors.
        If I never decays, contactors stay CLOSED (READY) — safer than arcing.
        """
        self._event("safe_shutdown: 1/3 EA all_off (stykače zůstávají CLOSED)")
        try:
            self.ea.all_off()
        except Exception:
            log.exception("EA all_off failed")
        if not open_contactors:
            return
        ok = self._wait_current_near_zero(label="safe_shutdown")
        if not ok:
            self._event(
                "safe_shutdown: 2/3 SKIP IDLE — proud stále teče, stykače NEOTEVÍRÁME"
            )
            return
        self._event("safe_shutdown: 3/3 BMS IDLE (otevírání stykačů)")
        try:
            self.bms.set_desired_state(DesiredState.IDLE)
        except Exception:
            log.exception("BMS idle failed")

    def _trip_voltage(self, reason: str) -> None:
        """Explicit abort.* voltage: EA off → wait I≈0 → IDLE."""
        self._event(f"HARD voltage abort — {reason} → EA off, then IDLE after I≈0")
        self._safe_shutdown(open_contactors=True)
        raise RuntimeError(f"Abort: {reason}")

    def _reset_soft_abort(self) -> None:
        self._soft_abort = None

    def _voltage_abort_reason(
        self,
        abort: dict[str, Any] | None,
        *,
        mode: str | None = None,
    ) -> str | None:
        """Immediate trip only for voltage keys in step abort{} — never profile/stop.

        Profile / stop pack·cell limits end the charge/discharge step normally
        (EA off, contactors stay READY for the next program steps). BMU already
        enforces its own hard limits. Treating profile Vmax as abort was opening
        contactors and FAIL-ing the run at the intended end of charge.
        """
        del mode  # reserved for future direction-specific abort keys
        if not abort:
            return None
        b = self.bms.telemetry()
        cell_min, cell_max = _cell_extremes(b)
        checks: list[tuple[str, float | None, float, str]] = []
        if "cell_v_max" in abort:
            checks.append(("cell_v_max", cell_max, float(abort["cell_v_max"]), "abort cell Vmax"))
        if "cell_v_min" in abort:
            checks.append(("cell_v_min", cell_min, float(abort["cell_v_min"]), "abort cell Vmin"))
        if "pack_v_max" in abort:
            checks.append(("pack_v_max", b.pack_voltage_v, float(abort["pack_v_max"]), "abort pack Vmax"))
        if "pack_v_min" in abort:
            checks.append(("pack_v_min", b.pack_voltage_v, float(abort["pack_v_min"]), "abort pack Vmin"))

        for key, measured, limit, label in checks:
            if measured is None:
                continue
            if "max" in key:
                if measured > limit:
                    return f"{label} {measured:.4f} V > {limit:.4f} V"
            elif "min" in key:
                if measured < limit:
                    return f"{label} {measured:.4f} V < {limit:.4f} V"
        return None

    def _soft_abort_reason(self, abort: dict[str, Any] | None) -> str | None:
        if not abort:
            return None
        b = self.bms.telemetry()
        if "dtc_level_max" in abort and b.dtc_level >= float(abort["dtc_level_max"]):
            return f"DTC level {b.dtc_level} ≥ {abort['dtc_level_max']}"
        if "t_max_c" in abort and b.t_max_c is not None and b.t_max_c > float(abort["t_max_c"]):
            return f"Tmax {b.t_max_c:.1f} C > {abort['t_max_c']}"
        if "t_min_c" in abort and b.t_min_c is not None and b.t_min_c < float(abort["t_min_c"]):
            return f"Tmin {b.t_min_c:.1f} C < {abort['t_min_c']}"
        return None

    def _effective_abort(self, step_abort: dict[str, Any] | None) -> dict[str, Any]:
        """Merge program-level t_max_c and profile cell V hard limits."""
        abort = dict(step_abort or {})
        prog_tmax = getattr(self.program.meta, "t_max_c", None)
        if prog_tmax is not None and "t_max_c" not in abort:
            abort["t_max_c"] = float(prog_tmax)
        # Hard safety net: cell voltage abort from profile (with tiny margin).
        # User-defined values are respected if tighter; profile limit is the ceiling.
        prof_cell_max = self.profile.cell_v_max - _CELL_V_ABORT_MARGIN
        prof_cell_min = self.profile.cell_v_min + _CELL_V_ABORT_MARGIN
        if "cell_v_max" not in abort:
            abort["cell_v_max"] = prof_cell_max
        else:
            abort["cell_v_max"] = min(float(abort["cell_v_max"]), prof_cell_max)
        if "cell_v_min" not in abort:
            abort["cell_v_min"] = prof_cell_min
        else:
            abort["cell_v_min"] = max(float(abort["cell_v_min"]), prof_cell_min)
        return abort

    def _check_abort_conditions(
        self,
        abort: dict[str, Any] | None,
        *,
        mode: str | None = None,
    ) -> None:
        """Voltage = immediate trip. Temp/DTC = confirm for _SOFT_ABORT_CONFIRM_S."""
        hard = self._voltage_abort_reason(abort, mode=mode)
        if hard:
            self._reset_soft_abort()
            self._trip_voltage(hard)

        soft = self._soft_abort_reason(abort)
        if not soft:
            if self._soft_abort is not None:
                self._event(f"soft abort cleared (was: {self._soft_abort[0]})")
            self._reset_soft_abort()
            return

        now = time.monotonic()
        confirm_s = _SOFT_ABORT_CONFIRM_S
        if soft and str(soft).startswith("DTC level"):
            confirm_s = _SOFT_ABORT_CONFIRM_S_DTC
        if self._soft_abort is None or self._soft_abort[0] != soft:
            self._soft_abort = (soft, now)
            self._event(
                f"soft abort pending: {soft} — confirming {confirm_s:.0f}s "
                "(measurements not instant)"
            )
            return
        elapsed = now - self._soft_abort[1]
        if elapsed >= confirm_s:
            self._reset_soft_abort()
            raise RuntimeError(f"Abort (confirmed {elapsed:.1f}s): {soft}")

    def _wait_for_ea_current(
        self,
        *,
        mode: str,
        detail: str,
        abort: dict[str, Any] | None,
    ) -> None:
        """Allow EA current to rise; fail only if still ~0 after settle window."""
        label = "PSI" if mode == "charge" else "EL"
        self._event(
            f"{mode}: waiting up to {_CURRENT_GATE_WAIT_S:.0f}s for {label} current "
            f">= {_CURRENT_GATE_OK_A}A (settle)"
        )
        t0 = time.monotonic()
        last_log = 0.0
        while True:
            if self._abort.is_set():
                raise RuntimeError("Aborted")
            # Hard voltage always immediate during settle
            hard = self._voltage_abort_reason(abort, mode=mode)
            if hard:
                self._trip_voltage(hard)
            self._check_watchdog(require_ea=True, mode=mode)

            et = self.ea.telemetry()
            if mode == "charge":
                # Keep power stage asserted while waiting for current
                if not et.psi_output_on:
                    self._event(f"{mode}: PSI OUTP was OFF during settle — re-assert ON")
                    try:
                        self.ea.psi.force_output(True, settle_s=0.25)
                    except Exception as exc:
                        self._event(f"{mode}: re-assert OUTP failed: {exc}")
                    et = self.ea.telemetry()
                i_now = abs(et.psi_current_a)
                v_now = et.psi_voltage_v
            else:
                if not et.el_input_on:
                    self._event(f"{mode}: EL INP was OFF during settle — re-assert ON")
                    try:
                        self.ea.el.force_output(True, settle_s=0.25)
                    except Exception as exc:
                        self._event(f"{mode}: re-assert INP failed: {exc}")
                    et = self.ea.telemetry()
                i_now = abs(et.el_current_a)
                v_now = et.el_voltage_v
            self._cache_ea(et)

            if i_now >= _CURRENT_GATE_OK_A:
                self._event(
                    f"{mode}: current OK {label} I={i_now:.2f}A V={v_now:.2f} "
                    f"after {time.monotonic() - t0:.1f}s"
                )
                return

            elapsed = time.monotonic() - t0
            if elapsed - last_log >= 1.0:
                last_log = elapsed
                self._event(
                    f"{mode}: settle {elapsed:.1f}/{_CURRENT_GATE_WAIT_S:.0f}s "
                    f"{label} I={i_now:.2f}A V={v_now:.2f} "
                    f"{'OUTP' if mode == 'charge' else 'INP'}="
                    f"{'ON' if (et.psi_output_on if mode == 'charge' else et.el_input_on) else 'OFF'}"
                )
                self._event(self._snapshot_bms(f"{mode} settle"))

            if elapsed >= _CURRENT_GATE_WAIT_S:
                if mode == "charge":
                    raise RuntimeError(self._diagnose_zero_charge_current(detail, et))
                raise RuntimeError(
                    f"Discharge started but EL current still ≈ 0 A after {_CURRENT_GATE_WAIT_S:.0f}s settle. "
                    "Check: POW setpoint, EL remote/Local, DC cabling, BMU discharge limit. "
                    f"Detail: {detail}"
                )
            self._sample_log()
            time.sleep(self.poll_period_s)

    def _check_watchdog(self, *, require_ea: bool = True, mode: str | None = None) -> None:
        if not self.bms.is_healthy(self.can_timeout_s):
            detail = ""
            try:
                detail = self.bms.health_detail()
            except Exception:
                pass
            raise RuntimeError(
                "EXT FAIL - BMS CAN: lost BMU link. "
                "EA off, contactors open only after I~0. "
                + (detail or "check Kvaser / External CAN / App heartbeat")
            )
        tel = self.bms.telemetry()
        # Operator-facing: react faster on BMU Derate before it reaches Disconnect.
        if self._bmu_last_dtc_level is None or tel.dtc_level != self._bmu_last_dtc_level:
            if tel.dtc_level in (1, 2, 3, 4):
                msg = (
                    f"BMU DTC level changed: L{tel.dtc_level} — "
                    f"{tel.active_dtcs[0] if tel.active_dtcs else '—'}"
                )
                if tel.dtc_level in (2, 3):
                    self._set_msg(msg)
                else:
                    self._event(msg)
            self._bmu_last_dtc_level = tel.dtc_level
        if (
            require_ea
            and tel.dtc_level == 2
            and mode in ("charge", "discharge")
            and self._bmu_respect_active
        ):
            try:
                desired = abs(float(self.ea.commanded_current_a()))
                self._maybe_live_bmu_derate_current(mode=mode, desired_a=desired)
            except Exception:
                log.exception("Live BMU derate handling failed")
        if tel.dtc_level >= self.abort_dtc_level:
            from bts.dtc_catalog import format_active_dtcs

            raise RuntimeError(
                f"BMS DTC level {tel.dtc_level} >= abort threshold — "
                + format_active_dtcs(tel.active_dtcs, level=tel.dtc_level)
            )
        if not require_ea:
            return
        if not self.ea.is_healthy(self.ea_timeout_s):
            detail = ""
            try:
                detail = self.ea.health_detail()
            except Exception:
                pass
            raise RuntimeError(
                "EXT FAIL - EA SCPI: lost PSI or EL-master link. "
                "EA off -> wait I~0 -> IDLE. "
                "Check USB COM / close Power Control. "
                + (detail or "")
            )
        # Hard rule: PSI (source) and EL (load) never run together
        self.ea.ensure_exclusive()
        et = self.ea.telemetry()
        if any("MUTEX" in a for a in et.alarms):
            raise RuntimeError("EA mutex violation: source and load must not run together")
        if et.psi_output_on and et.el_input_on:
            self.ea.all_off()
            raise RuntimeError("EA mutex violation: PSI and EL both reported ON")
        self._check_power_path_integrity(et, tel)

    def _reset_ext_fail(self) -> None:
        self._ext_fail = None

    def _confirm_ext_fail(self, reason: str) -> None:
        """Debounce transient glitches, then raise with a clear operator message."""
        now = time.monotonic()
        if self._ext_fail is None or self._ext_fail[0] != reason:
            self._ext_fail = (reason, now)
            self._event(f"EXT WARN (confirming {self.ea_i_mismatch_confirm_s:.0f}s): {reason}")
            return
        elapsed = now - self._ext_fail[1]
        if elapsed >= self.ea_i_mismatch_confirm_s:
            self._reset_ext_fail()
            raise RuntimeError(
                f"{reason} "
                f"(confirmed {elapsed:.1f}s) - safe stop: EA off -> I~0 -> IDLE."
            )

    def _check_power_path_integrity(self, et: EaTelemetry, bms_tel) -> None:
        """Detect silent hardware dropouts while COM still answers.

        Primary case: one of two EL units in master/slave drops → measured I ≈ ½ setpoint
        while EL master SCPI stays healthy.

        Ramp-up is ignored for collapse (need prior healthy current). The half-current
        MS-slave signature is checked even if current never reached full setpoint.
        """
        mode = et.active_mode or self.ea.active_mode()
        set_i = float(et.set_current_a or self.ea.commanded_current_a() or 0.0)
        if mode not in ("charge", "discharge") or set_i < self.ea_i_mismatch_min_set_a:
            self._reset_ext_fail()
            return

        pack_i = abs(float(bms_tel.pack_current_a)) if bms_tel.pack_current_a is not None else None

        if mode == "discharge":
            i_meas = abs(float(et.el_current_a))
            path_on = bool(et.el_input_on)
            path_label = "EL"
        else:
            i_meas = abs(float(et.psi_current_a))
            path_on = bool(et.psi_output_on)
            path_label = "PSI"

        ratio = i_meas / set_i if set_i > 0 else 1.0
        pack_ratio = (pack_i / set_i) if (pack_i is not None and set_i > 0) else None

        # Healthy band — clear warnings
        if path_on and ratio >= 0.70:
            self._power_seen_ok = True
            self._reset_ext_fail()
            return

        if not path_on:
            if not self._power_seen_ok:
                return  # still arming / settle — current gate handles zero-I
            self._confirm_ext_fail(
                f"EXT FAIL - {path_label} "
                f"{'INP' if mode == 'discharge' else 'OUTP'} dropped during {mode} "
                f"(I_set={set_i:.0f}A, I_{path_label}={i_meas:.1f}A"
                + (f", I_pack={pack_i:.1f}A" if pack_i is not None else "")
                + "). Check MS load / DC path."
            )
            return

        # Classic MS slave loss (~half combined MEAS) — EL pair only.
        # Never apply on charge: CV taper / BMU derate legitimately sits in the 35–65% band.
        if mode == "discharge" and self.ea_i_slave_ratio_lo <= ratio <= self.ea_i_slave_ratio_hi:
            pack_note = ""
            if pack_ratio is not None and self.ea_i_slave_ratio_lo <= pack_ratio <= self.ea_i_slave_ratio_hi:
                pack_note = f", I_pack={pack_i:.1f}A (~1/2, BMU confirms)"
            elif pack_ratio is not None:
                pack_note = f", I_pack={pack_i:.1f}A"
            self._confirm_ext_fail(
                f"EXT FAIL - EL master/slave: current ~ half of setpoint "
                f"(I_set={set_i:.0f}A, I_EL={i_meas:.1f}A = {ratio*100:.0f}%"
                f"{pack_note}). "
                f"One of two EL loads likely dropped (MS pair)."
            )
            return

        # Total collapse only after we had healthy current — discharge only.
        # Charge CV taper / BMU derate looks the same as a collapse on PSI.
        if self._power_seen_ok and mode == "discharge" and ratio <= self.ea_i_collapse_ratio:
            self._confirm_ext_fail(
                f"EXT FAIL - {path_label}: current collapsed "
                f"(I_set={set_i:.0f}A, I={i_meas:.1f}A = {ratio*100:.0f}%). "
                f"Check DC cabling / fuse / remote enable."
            )
            return

        if (
            self._power_seen_ok
            and mode == "discharge"
            and pack_ratio is not None
            and self.ea_i_slave_ratio_lo <= pack_ratio <= self.ea_i_slave_ratio_hi
            and ratio > self.ea_i_slave_ratio_hi
        ):
            self._confirm_ext_fail(
                f"EXT FAIL - BMU pack current ~ half setpoint during discharge "
                f"(I_set={set_i:.0f}A, I_pack={pack_i:.1f}A, I_EL={i_meas:.1f}A). "
                f"Possible partial load / sense fault."
            )
            return

        self._reset_ext_fail()

    def _maybe_schedule_mock_fault(self, params: dict[str, Any]) -> None:
        """DEV/mock only: schedule inject_fault from step YAML."""
        fault = params.get("mock_inject_fault")
        if not fault:
            return
        after = float(params.get("mock_inject_after_s", 0) or 0)
        try:
            self.ea.inject_fault(str(fault), after_s=after)
            self._event(
                f"mock: scheduled fault {fault!r}"
                + (f" after {after:.1f}s" if after > 0 else " now")
            )
        except Exception as exc:
            self._event(f"mock: inject_fault ignored ({exc})")

    def _wait_until(
        self,
        predicate: Callable[[], bool],
        timeout_s: float,
        label: str,
        *,
        require_ea: bool = True,
        on_poll: Callable[[], None] | None = None,
    ) -> None:
        t0 = time.monotonic()
        while not predicate():
            if self._abort.is_set():
                raise RuntimeError("Aborted")
            self._check_watchdog(require_ea=require_ea)
            if on_poll:
                on_poll()
            if time.monotonic() - t0 > timeout_s:
                raise TimeoutError(f"Timeout waiting for {label} — {self._snapshot_bms()}")
            self._sample_log()
            time.sleep(self.poll_period_s)

    def _should_record(self, params: dict[str, Any]) -> bool:
        """CSV/trace only when step opts in — or when measuring capacity."""
        if params.get("record") is False:
            return False
        if params.get("record") is True or params.get("record") in ("true", "1", 1, "yes"):
            return True
        return bool(params.get("measure"))

    def _sample_log(self) -> None:
        if not self._record_step:
            return
        b = self.bms.telemetry()
        e = self._cache_ea()
        mono = time.monotonic()
        t0 = self._run_mono0 if self._run_mono0 is not None else mono
        t_s = mono - t0
        psi_i = abs(float(e.psi_current_a or 0.0))
        el_i = abs(float(e.el_current_a or 0.0))
        if psi_i >= el_i and psi_i > 0.05:
            ea_v, ea_i = e.psi_voltage_v, psi_i
        elif el_i > 0.05:
            ea_v, ea_i = e.el_voltage_v, el_i
        else:
            ea_v = e.psi_voltage_v or e.el_voltage_v or b.pack_voltage_v
            ea_i = psi_i if psi_i >= el_i else el_i
        with self._lock:
            ah = self._status.measurements.extras.get("step_ah")
        self._csv_rows.append(
            {
                "t": _utc_now(),
                "t_s": round(t_s, 3),
                "step": self._status.current_step_id,
                "bms_state": int(b.operating_state),
                "soc": b.soc_pct,
                "pack_v": b.pack_voltage_v,
                "pack_i": b.pack_current_a,
                "cell_vmin": b.cell_v_min,
                "cell_vmax": b.cell_v_max,
                "t_min": b.t_min_c,
                "t_max": b.t_max_c,
                "dtc_level": b.dtc_level,
                "psi_v": e.psi_voltage_v,
                "psi_i": e.psi_current_a,
                "el_v": e.el_voltage_v,
                "el_i": e.el_current_a,
                "ea_v": ea_v,
                "ea_i": ea_i,
                "ah": ah,
            }
        )
        self._last_sample_mono = mono

    def _stop_reached(self, stop: dict[str, Any] | None, mode: str) -> bool:
        if not stop:
            return False
        b = self.bms.telemetry()
        cell_min, cell_max = _cell_extremes(b)
        if "soc_pct" in stop and b.soc_pct is not None:
            if mode == "charge" and b.soc_pct >= float(stop["soc_pct"]):
                return True
            if mode == "discharge" and b.soc_pct <= float(stop["soc_pct"]):
                return True
        # CV end-current (i_min_a): pack/cell Vmax only enter CV — do not end the step here.
        cv_hold = mode == "charge" and "i_min_a" in stop
        if not cv_hold:
            if "pack_v_max" in stop and b.pack_voltage_v is not None and b.pack_voltage_v >= float(stop["pack_v_max"]):
                return True
            if "cell_v_max" in stop and cell_max is not None and cell_max >= float(stop["cell_v_max"]):
                return True
        if "pack_v_min" in stop and b.pack_voltage_v is not None and b.pack_voltage_v <= float(stop["pack_v_min"]):
            return True
        if "cell_v_min" in stop and cell_min is not None and cell_min <= float(stop["cell_v_min"]):
            return True
        if "ah_target" in stop and self._step_ah_start is not None:
            transferred = self._ah_transferred()
            if transferred >= float(stop["ah_target"]):
                return True
        return False

    def _stop_reason(self, stop: dict[str, Any] | None, mode: str) -> str:
        """Human-readable which stop condition fired (for activity log)."""
        if not stop:
            return "stop"
        b = self.bms.telemetry()
        cell_min, cell_max = _cell_extremes(b)
        if "soc_pct" in stop and b.soc_pct is not None:
            if mode == "charge" and b.soc_pct >= float(stop["soc_pct"]):
                return f"soc {b.soc_pct:.1f}% >= {stop['soc_pct']}"
            if mode == "discharge" and b.soc_pct <= float(stop["soc_pct"]):
                return f"soc {b.soc_pct:.1f}% <= {stop['soc_pct']}"
        cv_hold = mode == "charge" and "i_min_a" in stop
        if not cv_hold:
            if "pack_v_max" in stop and b.pack_voltage_v is not None and b.pack_voltage_v >= float(stop["pack_v_max"]):
                return f"pack Vmax {b.pack_voltage_v:.3f} >= {stop['pack_v_max']}"
            if "cell_v_max" in stop and cell_max is not None and cell_max >= float(stop["cell_v_max"]):
                return f"cell Vmax {cell_max:.4f} >= {stop['cell_v_max']}"
        if "pack_v_min" in stop and b.pack_voltage_v is not None and b.pack_voltage_v <= float(stop["pack_v_min"]):
            return f"pack Vmin {b.pack_voltage_v:.3f} <= {stop['pack_v_min']}"
        if "cell_v_min" in stop and cell_min is not None and cell_min <= float(stop["cell_v_min"]):
            return f"cell Vmin {cell_min:.4f} <= {stop['cell_v_min']}"
        if "ah_target" in stop and self._step_ah_start is not None:
            transferred = self._ah_transferred()
            if transferred >= float(stop["ah_target"]):
                return f"ah_target {transferred:.3f} >= {stop['ah_target']}"
        return "stop"

    def _ah_transferred(self) -> float:
        b = self.bms.telemetry()
        # Prefer coulomb from EA integration if BMS Ah counters are coarse
        if self._step_ah_start is None:
            return 0.0
        # Use discharge counter delta for discharge, charge for charge — store as absolute
        return abs((b.ah_discharge or 0) + (b.ah_charge or 0) - self._step_ah_start)

    def _mark_ah_start(self) -> None:
        b = self.bms.telemetry()
        self._step_ah_start = (b.ah_charge or 0) + (b.ah_discharge or 0)

    def _require_ready(self) -> None:
        b = self.bms.telemetry()
        if b.operating_state != BmuState.READY:
            raise RuntimeError(f"BMS not READY (state={b.operating_state.name})")

    def _want_respect_bmu_limits(self, params: dict[str, Any], *, mode: str) -> bool:
        """Step flag respect_bmu_limits.

        Default ON:
        - discharge / charge (sníží risk BMU disconnect — zejména při SOC / derate)
        U capacity/DCIR měření: limity stále respektujeme, ale pokud by BMU
        snížila proud pod setpoint, run se ukončí (zkreslený výsledek).
        Default OFF pouze když krok explicitně vypne `respect_bmu_limits`.
        """
        raw = params.get("respect_bmu_limits")
        if raw is not None:
            if isinstance(raw, str):
                return raw.strip().lower() in ("1", "true", "yes", "on")
            return bool(raw)
        return mode in ("charge", "discharge")

    @staticmethod
    def _is_measure_current_critical(params: dict[str, Any], step_type: str) -> bool:
        """Capacity / DCIR results depend on keeping the commanded C-rate / pulse I."""
        if step_type == "dcir":
            return True
        meas = params.get("measure")
        if not meas:
            return False
        return True

    def _cap_current_to_bmu(self, current_a: float, params: dict[str, Any], *, mode: str) -> float:
        if not self._want_respect_bmu_limits(params, mode=mode):
            return current_a
        b = self.bms.telemetry()
        lim: float | None = None
        if mode == "charge" and b.charge_current_limit_a is not None:
            lim = float(b.charge_current_limit_a)
        elif mode == "discharge" and b.discharge_current_limit_a is not None:
            lim = float(b.discharge_current_limit_a)
        if lim is None or lim <= 0:
            return current_a
        safe_lim = lim * _BMU_CURRENT_MARGIN
        if current_a > safe_lim + 0.05:
            if self._bmu_measure_critical:
                raise RuntimeError(
                    f"BMU current limit would change measurement current "
                    f"({mode} {current_a:.1f}A → {safe_lim:.1f}A, BMU {lim:.1f}A) — "
                    "aborting instead of running at reduced C-rate / pulse"
                )
            self._event(
                f"respect_bmu_limits: {mode} I {current_a:.1f}A → {safe_lim:.1f}A "
                f"(BMU limit {lim:.1f}A × headroom {_BMU_CURRENT_MARGIN:.2f})"
            )
            return safe_lim
        return current_a

    def _maybe_live_bmu_derate_current(self, *, mode: str, desired_a: float) -> None:
        """On BMU DTC level 2 (Derate) reduce EA current — or abort if it would spoil a measure.

        Non-measure steps: lower current to stay under BMU limit and continue
        (avoid path to DTC=3 disconnect / contactors).

        Capacity / DCIR: commanded I is part of the result → stop the run instead.
        """
        if not self._bmu_respect_active:
            return
        if self._bmu_mode_active != mode:
            return
        b = self.bms.telemetry()
        lim_a = (
            float(b.charge_current_limit_a)
            if mode == "charge"
            else float(b.discharge_current_limit_a)
        ) if (mode in ("charge", "discharge")) else None
        if lim_a is None or lim_a <= 0:
            return
        safe_lim_a = lim_a * _BMU_CURRENT_MARGIN
        if desired_a <= safe_lim_a + 0.05:
            return
        if self._bmu_last_derate_a is not None and abs(self._bmu_last_derate_a - safe_lim_a) < 1.0:
            return
        if self._bmu_measure_critical:
            raise RuntimeError(
                f"BMU DTC derate during measurement: commanded {desired_a:.1f}A would drop to "
                f"{safe_lim_a:.1f}A (BMU {lim_a:.1f}A) — aborting to keep result valid"
            )
        self._bmu_last_derate_a = safe_lim_a
        self._event(
            f"BMU DTC derate: lowering EA current {desired_a:.1f}A → {safe_lim_a:.1f}A "
            f"(BMU {lim_a:.1f}A × {_BMU_CURRENT_MARGIN:.2f})"
        )
        self._set_msg(f"BMU derate — current reduced to {safe_lim_a:.0f}A")
        try:
            if mode == "charge" and hasattr(self.ea, "psi"):
                self.ea.psi.set_current(safe_lim_a)
            elif mode == "discharge" and hasattr(self.ea, "el"):
                self.ea.el.set_current(safe_lim_a)
        except Exception:
            # If live set_current fails, next watchdog/abort will still stop EA safely.
            log.exception("Live BMU derate set_current failed")

    def _run_step(self, step: Step) -> None:
        t = step.type
        p = step.params
        self._set_msg(f"Step {step.id}: {t}")
        # Reset per-step BMU limit tracking.
        self._bmu_respect_active = False
        self._bmu_mode_active = None
        self._bmu_last_derate_a = None
        self._bmu_measure_critical = self._is_measure_current_critical(p, t)
        self._step_types[step.id] = t
        self._record_step = self._should_record(p) if t in (
            "charge",
            "discharge",
            "dcir",
            "goto_soc",
            "wait_time",
            "wait_temp",
        ) else False
        if self._record_step:
            self._event(f"{step.id}: recording CSV/trace")

        if t == "bms_ready":
            timeout = float(p.get("timeout_s", 60))
            self._event(
                "bms_ready: requesting READY (EA not required for contactors — BMU owns them)"
            )
            self._event(self._snapshot_bms("before"))
            self.bms.set_desired_state(DesiredState.READY)
            last_key = ""

            def _progress() -> None:
                nonlocal last_key
                b = self.bms.telemetry()
                eff = b.contactors_effective
                key = (
                    f"{b.operating_state.value}:"
                    f"{int(eff.main_pos)}{int(eff.main_neg)}"
                    f"{int(eff.precharge)}:{b.dtc_level}:"
                    f"{','.join(b.active_dtcs)}"
                )
                if key != last_key:
                    last_key = key
                    self._event(self._snapshot_bms("progress"))

            self._wait_until(
                lambda: self.bms.telemetry().operating_state == BmuState.READY,
                timeout,
                "BMS READY",
                require_ea=False,
                on_poll=_progress,
            )
            self._event(self._snapshot_bms("READY ok"))
            settle_s = float(p.get("settle_s", _CONTACTOR_SETTLE_S))
            if settle_s > 0:
                self._event(
                    f"bms_ready: stykače READY — odstup {settle_s:.0f}s před dalším krokem"
                )
                self._set_msg(f"Po stykačích: čekám {settle_s:.0f}s…")
                t0 = time.monotonic()
                while time.monotonic() - t0 < settle_s:
                    if self._abort.is_set():
                        raise RuntimeError("Aborted")
                    self._check_watchdog(require_ea=False)
                    self._sample_log()
                    left = settle_s - (time.monotonic() - t0)
                    self._set_msg(f"Po stykačích: čekám {max(0.0, left):.0f}s…")
                    time.sleep(self.poll_period_s)
                self._event("bms_ready: settle done — pokračuji")

        elif t == "bms_idle":
            timeout = float(p.get("timeout_s", 30))
            self._event("bms_idle: EA all_off, wait I≈0, then IDLE (protect contactors)")
            try:
                self.ea.all_off()
            except Exception:
                log.exception("EA all_off in bms_idle")
            ok = self._wait_current_near_zero(
                label="bms_idle",
                timeout_s=max(timeout, _CONTACTOR_OPEN_WAIT_S),
            )
            if not ok:
                raise RuntimeError(
                    "bms_idle: current still high after EA off — refusing to open contactors"
                )
            self.bms.set_desired_state(DesiredState.IDLE)
            self._wait_until(
                lambda: self.bms.telemetry().operating_state == BmuState.IDLE,
                timeout,
                "BMS IDLE",
                require_ea=False,
            )
            self._event(self._snapshot_bms("IDLE ok"))

        elif t == "goto_soc":
            # Charge or discharge to target SOC from unknown starting SOC
            target = float(p.get("soc_pct", p.get("stop", {}).get("soc_pct", 50)))
            band = float(p.get("band_pct", 0.5))
            soc = self.bms.telemetry().soc_pct
            if soc is None:
                raise RuntimeError("goto_soc: BMS SOC not available")
            if abs(float(soc) - target) <= band:
                self._event(f"goto_soc: already at {soc:.1f}% (target {target:.0f}% ±{band})")
            else:
                sub_p = dict(p)
                sub_p.pop("soc_pct", None)
                sub_p.pop("band_pct", None)
                if self._should_record(p):
                    sub_p["record"] = True
                stop = dict(sub_p.get("stop") or {})
                stop["soc_pct"] = target
                sub_p["stop"] = stop
                if float(soc) < target:
                    sub_p.setdefault("voltage_v", self.profile.pack_v_max)
                    self._event(
                        f"goto_soc: SOC {soc:.1f}% < {target:.0f}% → charge"
                    )
                    self._run_step(Step(id=f"{step.id}_chg", type="charge", params=sub_p))
                else:
                    self._event(
                        f"goto_soc: SOC {soc:.1f}% > {target:.0f}% → discharge"
                    )
                    self._run_step(Step(id=f"{step.id}_dch", type="discharge", params=sub_p))

        elif t == "charge":
            self._require_ready()
            self._reset_soft_abort()
            current = resolve_amps(
                p,
                self.profile,
                amp_key="current_a",
                default=self.profile.default_test_current_a,
            )
            self._bmu_respect_active = self._want_respect_bmu_limits(p, mode="charge")
            self._bmu_mode_active = "charge"
            current = self._cap_current_to_bmu(current, p, mode="charge")
            voltage = float(p.get("voltage_v", self.profile.pack_v_max))
            if voltage > self.profile.pack_v_max:
                self._event(
                    f"charge: clamping voltage_v {voltage:.2f}V → profile pack_v_max "
                    f"{self.profile.pack_v_max:.2f}V"
                )
                voltage = self.profile.pack_v_max
            stop = dict(p.get("stop") or {})
            abort = self._effective_abort(p.get("abort"))
            # Fill missing pack stop from temperature-aware profile cutoffs.
            # With i_min_a (CV), pack_v_max is informational for logs / near-V — step ends on i_min.
            t_now = self.bms.telemetry().t_max_c
            if "pack_v_max" not in stop:
                stop["pack_v_max"] = self.profile.charge_pack_vmax(t_now)
                self._event(
                    f"charge: stop.pack_v_max from profile cutoff = {stop['pack_v_max']:.2f}V "
                    f"(Tmax={t_now if t_now is not None else '—'}°C)"
                    + (" — CV via i_min_a, Vmax alone does not end step" if "i_min_a" in stop else "")
                )
            # Cell voltage stop — safety net from profile, stops before BMU disconnect.
            if "cell_v_max" not in stop:
                stop["cell_v_max"] = self.profile.cell_v_max - _CELL_V_STOP_MARGIN
                self._event(
                    f"charge: auto cell_v_max stop = {stop['cell_v_max']:.3f}V "
                    f"(profile {self.profile.cell_v_max:.3f}V − {_CELL_V_STOP_MARGIN*1000:.0f}mV margin)"
                )
            timeout = float(stop.get("timeout_s", p.get("timeout_s", 8 * 3600)))
            self._mark_ah_start()
            integrated_ah = 0.0
            i_min_since: float | None = None
            self._event(
                f"charge: start PSI V={voltage:.2f} I={format_amps_with_crate(current, self.profile)} "
                f"(EL off)"
            )
            self._event("charge: setpoints first, then PSI OUTP OFF→ON")
            self._power_seen_ok = False
            self._reset_ext_fail()
            detail = self.ea.start_charge(voltage, current)
            self._event(f"charge: PSI armed — {detail}")
            self._maybe_schedule_mock_fault(p)
            et = self._cache_ea()
            samp = getattr(self.ea, "_last_arm_sample", None) or {}
            sust = float(samp.get("sustained_i", abs(et.psi_current_a)) or 0.0)
            peak = float(samp.get("peak_i", 0.0) or 0.0)
            self._event(
                f"charge: arm sample peak={peak:.2f}A sust={sust:.2f}A "
                f"last V={et.psi_voltage_v:.2f} I={et.psi_current_a:.2f}A"
            )
            self._event(self._snapshot_bms("charge path"))
            self._wait_for_ea_current(mode="charge", detail=detail, abort=abort)
            t0 = time.monotonic()
            last = t0
            while True:
                if self._abort.is_set():
                    raise RuntimeError("Aborted")
                self._check_watchdog(require_ea=True, mode="charge")
                now = time.monotonic()
                et = self._cache_ea()
                integrated_ah += abs(et.psi_current_a) * (now - last) / 3600.0
                last = now
                with self._lock:
                    self._status.measurements.extras["step_ah"] = integrated_ah
                # Stop (pack/cell Vmax) before abort — normal end of charge, keep contactors
                if "ah_target" in stop and integrated_ah >= float(stop["ah_target"]):
                    self._event(f"charge: stop ah_target reached ({integrated_ah:.3f} Ah)")
                    break
                if self._stop_reached(stop, "charge"):
                    why = self._stop_reason(stop, "charge")
                    self._event(f"charge: stop reached — {why} (EA off, contactors stay READY)")
                    self.ea.all_off()
                    self._cache_ea()
                    break
                # CV end-current cutoff (i_min_a): hold ≤ limit for _I_MIN_HOLD_S
                if "i_min_a" in stop:
                    i_now = abs(et.psi_current_a)
                    near_v = et.psi_voltage_v >= (voltage - 0.15)
                    if near_v and i_now <= float(stop["i_min_a"]):
                        if i_min_since is None:
                            i_min_since = now
                            self._event(
                                f"charge: I={i_now:.2f}A ≤ i_min {stop['i_min_a']}A — "
                                f"holding {_I_MIN_HOLD_S:.0f}s for CV cutoff"
                            )
                        elif now - i_min_since >= _I_MIN_HOLD_S:
                            self._event(
                                f"charge: stop i_min_a — I={i_now:.2f}A ≤ {stop['i_min_a']}A "
                                f"for {_I_MIN_HOLD_S:.0f}s (CV done)"
                            )
                            self.ea.all_off()
                            self._cache_ea()
                            break
                    else:
                        i_min_since = None
                self._check_abort_conditions(abort, mode="charge")
                if time.monotonic() - t0 > timeout:
                    raise TimeoutError("Charge timeout")
                self._sample_log()
                time.sleep(self.poll_period_s)
            self.ea.all_off()
            if isinstance(self.bms, MockBmsDriver):
                self.bms.set_external_current(0.0)

        elif t == "discharge":
            self._require_ready()
            self._reset_soft_abort()
            current = resolve_amps(
                p,
                self.profile,
                amp_key="current_a",
                default=self.profile.default_test_current_a,
            )
            self._bmu_respect_active = self._want_respect_bmu_limits(p, mode="discharge")
            self._bmu_mode_active = "discharge"
            current = self._cap_current_to_bmu(current, p, mode="discharge")
            stop = dict(p.get("stop") or {})
            abort = self._effective_abort(p.get("abort"))
            timeout = float(stop.get("timeout_s", p.get("timeout_s", 8 * 3600)))
            t_now = self.bms.telemetry().t_max_c
            if "pack_v_min" not in stop:
                stop["pack_v_min"] = self.profile.discharge_pack_vmin(t_now)
                self._event(
                    f"discharge: stop.pack_v_min from profile cutoff = {stop['pack_v_min']:.2f}V "
                    f"(Tmax={t_now if t_now is not None else '—'}°C)"
                )
            if "cell_v_min" not in stop:
                stop["cell_v_min"] = self.profile.cell_v_min + _CELL_V_STOP_MARGIN
                self._event(
                    f"discharge: auto cell_v_min stop = {stop['cell_v_min']:.3f}V "
                    f"(profile {self.profile.cell_v_min:.3f}V + {_CELL_V_STOP_MARGIN*1000:.0f}mV margin)"
                )
            vlim = float(stop.get("pack_v_min", self.profile.pack_v_min))
            pack_v = self.bms.telemetry().pack_voltage_v
            op_v = float(pack_v) if pack_v and pack_v > 0 else float(self.profile.pack_v_max)
            self._mark_ah_start()
            integrated_ah = 0.0
            self._event(
                f"discharge: start EL I={format_amps_with_crate(current, self.profile)} (PSI off)"
            )
            self._event(
                f"discharge: setpoints first, then EL INP OFF→ON "
                f"(POW from Uop≈{op_v:.2f}V, UV={vlim:.2f}V)"
            )
            self._power_seen_ok = False
            self._reset_ext_fail()
            detail = self.ea.start_discharge(
                current,
                voltage_limit_v=vlim,
                operating_voltage_v=op_v,
            )
            self._event(f"discharge: EL armed — {detail}")
            self._maybe_schedule_mock_fault(p)
            self._wait_for_ea_current(mode="discharge", detail=detail, abort=abort)
            t0 = time.monotonic()
            last = t0
            while True:
                if self._abort.is_set():
                    raise RuntimeError("Aborted")
                self._check_watchdog(require_ea=True, mode="discharge")
                now = time.monotonic()
                et = self._cache_ea()
                integrated_ah += abs(et.el_current_a) * (now - last) / 3600.0
                last = now
                with self._lock:
                    self._status.measurements.extras["step_ah"] = integrated_ah
                if "ah_target" in stop and integrated_ah >= float(stop["ah_target"]):
                    self._event(f"discharge: stop ah_target reached ({integrated_ah:.3f} Ah)")
                    break
                if self._stop_reached(stop, "discharge"):
                    why = self._stop_reason(stop, "discharge")
                    self._event(f"discharge: stop reached — {why} (EA off, contactors stay READY)")
                    self.ea.all_off()
                    self._cache_ea()
                    break
                self._check_abort_conditions(abort, mode="discharge")
                if time.monotonic() - t0 > timeout:
                    raise TimeoutError("Discharge timeout")
                self._sample_log()
                time.sleep(self.poll_period_s)
            self.ea.all_off()
            if isinstance(self.bms, MockBmsDriver):
                self.bms.set_external_current(0.0)
            # Capacity from EA (EL) coulomb count — BMU Ah is not always accurate.
            capacity = integrated_ah
            meas = p.get("measure")
            if meas:
                if capacity < 0.01:
                    raise RuntimeError(
                        f"capacity measure: EA integrated Ah≈{capacity:.4f} — check EL current"
                    )
                key = str(meas).strip() or "capacity_ah"
                with self._lock:
                    if key == "capacity_ah":
                        self._status.measurements.capacity_ah = capacity
                    self._status.measurements.extras[key] = capacity
                self._step_results[step.id] = {
                    "capacity_ah": capacity,
                    "capacity_key": key,
                }
                self._set_msg(f"Measured {key}: {capacity:.3f} Ah (EA/EL)")

        elif t == "wait_temp":
            t_max = float(p["t_max_c"]) if "t_max_c" in p else None
            t_min = float(p["t_min_c"]) if "t_min_c" in p else None
            timeout = float(p.get("timeout_s", 7200))

            def ok() -> bool:
                b = self.bms.telemetry()
                if t_max is not None and (b.t_max_c is None or b.t_max_c > t_max):
                    return False
                if t_min is not None and (b.t_min_c is None or b.t_min_c < t_min):
                    return False
                return True

            self._wait_until(ok, timeout, "temperature window", require_ea=False)

        elif t == "wait_time":
            seconds = float(p.get("seconds", p.get("timeout_s", 1)))
            t0 = time.monotonic()
            while time.monotonic() - t0 < seconds:
                if self._abort.is_set():
                    raise RuntimeError("Aborted")
                self._check_watchdog(require_ea=False)
                self._sample_log()
                time.sleep(self.poll_period_s)

        elif t == "dcir":
            self._require_ready()
            pulse_a = resolve_amps(
                p,
                self.profile,
                amp_key="pulse_a",
                default=self.profile.dcir_pulse_a,
            )
            self._bmu_respect_active = self._want_respect_bmu_limits(p, mode="discharge")
            self._bmu_mode_active = "discharge"
            pulse_a = self._cap_current_to_bmu(pulse_a, p, mode="discharge")
            pulse_s = float(p.get("pulse_s", self.profile.dcir_pulse_s))
            abort = self._effective_abort(p.get("abort"))
            soc_ref = p.get("soc_ref_pct")
            if soc_ref is not None:
                target = float(soc_ref)
                band = float(p.get("soc_band_pct", 5.0))
                wait_s = float(p.get("soc_wait_s", 600.0))
                self._event(
                    f"dcir: waiting for SOC ≈ {target:.0f}% ±{band:.0f}% (timeout {wait_s:.0f}s)"
                )
                t_soc = time.monotonic()
                while True:
                    if self._abort.is_set():
                        raise RuntimeError("Aborted")
                    self._check_watchdog(require_ea=False)
                    self._check_abort_conditions(abort, mode=None)
                    b = self.bms.telemetry()
                    soc = b.soc_pct
                    if soc is not None and abs(soc - target) <= band:
                        self._event(f"dcir: SOC {soc:.1f}% within band of {target:.0f}%")
                        break
                    if time.monotonic() - t_soc > wait_s:
                        soc_s = f"{soc:.1f}%" if soc is not None else "—"
                        self._event(
                            f"dcir: SOC wait timeout (SOC={soc_s}, target={target:.0f}%) — "
                            "continuing with pulse anyway"
                        )
                        break
                    time.sleep(self.poll_period_s)
            # Rest voltage from EA (EL sense), fallback BMS pack
            self.ea.all_off()
            time.sleep(1.0)
            e_rest = self._cache_ea()
            v0 = e_rest.el_voltage_v or self.bms.telemetry().pack_voltage_v
            self._power_seen_ok = False
            self._reset_ext_fail()
            self._event(
                f"dcir: pulse {format_amps_with_crate(pulse_a, self.profile)} × {pulse_s:.1f}s"
            )
            self.ea.start_discharge(
                pulse_a,
                voltage_limit_v=self.profile.pack_v_min,
                operating_voltage_v=v0 if v0 and v0 > 0 else self.profile.pack_v_max,
            )
            self._maybe_schedule_mock_fault(p)
            # Pulse with abort/watchdog (no blind sleep)
            t_pulse = time.monotonic()
            while time.monotonic() - t_pulse < pulse_s:
                if self._abort.is_set():
                    self.ea.all_off()
                    raise RuntimeError("Aborted")
                self._check_watchdog(require_ea=True, mode="discharge")
                self._sample_log()
                time.sleep(min(self.poll_period_s, 0.2))
            b = self.bms.telemetry()
            e = self._cache_ea()
            # dV + I from EA (EL); BMU pack V/I is not always accurate enough for R.
            v1 = float(e.el_voltage_v or 0.0)
            if v1 <= 0 and b.pack_voltage_v is not None:
                v1 = float(b.pack_voltage_v)
            i = abs(float(e.el_current_a))
            self.ea.all_off()
            if isinstance(self.bms, MockBmsDriver):
                self.bms.set_external_current(0.0)
            min_i = max(1.0, 0.2 * abs(pulse_a))
            if i < min_i:
                raise RuntimeError(
                    f"DCIR: measured EL I={i:.2f}A too low (need ≥{min_i:.1f}A for "
                    f"set {pulse_a:.0f}A) — refusing setpoint-based fake R"
                )
            dV = abs(float(v0 or 0) - v1)
            dcir_mohm = dV / i * 1000.0
            with self._lock:
                self._status.measurements.dcir_mohm = dcir_mohm
                if soc_ref is not None:
                    self._status.measurements.extras["dcir_soc_ref_pct"] = float(soc_ref)
            self._step_results[step.id] = {"dcir_mohm": dcir_mohm}
            self._set_msg(
                f"DCIR: {dcir_mohm:.3f} mOhm (dV={dV:.4f} V @ EL {i:.1f} A)"
            )

        elif t == "report":
            include = list(p.get("include") or [])
            b = self.bms.telemetry()
            path = self._write_report(include=include, telemetry=b)
            with self._lock:
                self._status.report_path = str(path)
            self._set_msg(f"Report written: {path}")

        elif t == "notify":
            self._set_msg(str(p.get("message", "Notify")))

        else:
            raise ValueError(f"Unknown step type: {t}")

    def _write_report(self, *, include: list[str], telemetry: Any) -> Path:
        """Classic capacity/DCIR report: results cards + V/I charts from recorded steps."""
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        stamp = self._run_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{stamp}_{self.program.meta.name}"

        csv_name: str | None = None
        if self._csv_rows:
            csv_path = self._write_csv()
            csv_name = csv_path.name

        traces = traces_from_csv_rows(self._csv_rows, self._step_types)
        for tr in traces:
            res = self._step_results.get(tr.step_id) or {}
            if res.get("capacity_ah") is not None:
                tr.capacity_ah = float(res["capacity_ah"])
                tr.capacity_key = str(res.get("capacity_key") or "capacity_ah")
            if res.get("dcir_mohm") is not None:
                tr.dcir_mohm = float(res["dcir_mohm"])

        with self._lock:
            meas = self._status.measurements
            extras = dict(meas.extras)
            capacity_ah = meas.capacity_ah
            dcir_mohm = meas.dcir_mohm
            started = self._status.started_at
            activity = self._status.activity_path

        cells = list(telemetry.cell_voltages) if "cells" in include else None
        temps = list(telemetry.temperatures_c) if "temps" in include else None
        dtc_level = int(telemetry.dtc_level) if "dtc" in include else None
        active_dtcs = list(telemetry.active_dtcs) if "dtc" in include else None

        payload = build_report_payload(
            program=self.program.meta.name,
            profile=self.profile.id,
            serial=self.serial_number or "",
            started_at=started,
            finished_at=_utc_now(),
            measurements={"capacity_ah": capacity_ah, "dcir_mohm": dcir_mohm},
            extras=extras,
            include=include,
            traces=traces,
            csv_name=csv_name,
            activity_name=Path(activity).name if activity else None,
            cells=cells,
            temps=temps,
            dtc_level=dtc_level,
            active_dtcs=active_dtcs,
        )
        html_path, _json_path = write_report_bundle(self.runs_dir, base_name, payload)
        return html_path

    def _write_csv(self) -> Path:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        stamp = self._run_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.runs_dir / f"{stamp}_{self.program.meta.name}.csv"
        if not self._csv_rows:
            path.write_text("t,t_s,step\n", encoding="utf-8")
            self._csv_path = path
            return path
        fields = list(self._csv_rows[0].keys())
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(self._csv_rows)
        self._csv_path = path
        return path

    def _run(self) -> None:
        try:
            self._event("Run begin — connecting drivers")
            self.bms.connect()
            self.bms.start()
            self._event(self._snapshot_bms("link"))
            self.ea.connect()
            self._event("EA connected (SCPI)")
            for idx, step in enumerate(self.program.steps):
                with self._lock:
                    self._status.current_step_index = idx
                    self._status.current_step_id = step.id
                    self._status.current_step_type = step.type
                    self._status.step_started_mono = time.monotonic()
                self._event(f"=== step {idx + 1}/{len(self.program.steps)} id={step.id} type={step.type} ===")
                self._run_step(step)
                self._event(f"step {step.id} completed")
            csv_path: Path | None = None
            if self._csv_rows:
                csv_path = self._write_csv()
                self._event(
                    f"All steps completed — csv={csv_path.name} "
                    f"({len(self._csv_rows)} samples from recorded steps)"
                )
            else:
                self._event(
                    "All steps completed — no CSV "
                    "(zapni „Zaznamenat stopu“ / measure u vybraných kroků)"
                )
            # Always leave the bench safe: if still READY, open contactors.
            try:
                st = self.bms.telemetry().operating_state
                if st in (BmuState.READY, BmuState.PRE_CHARGE):
                    self._event(
                        f"post-run: BMS still {st.name} — safe_shutdown (open contactors)"
                    )
                    self._safe_shutdown(open_contactors=True)
            except Exception:
                log.exception("post-run safe_shutdown failed")
            with self._lock:
                if csv_path is not None:
                    self._status.log_path = str(csv_path)
                self._status.run_state = RunState.COMPLETED
                self._status.finished_at = _utc_now()
                self._status.message = "All steps completed"
            self._emit()
        except Exception as exc:
            log.exception("Sequence failed")
            self._event(f"FAIL: {exc}")
            try:
                self._event(self._snapshot_bms("at failure"))
            except Exception:
                pass
            self._event("safe_shutdown: EA off → wait I≈0 → BMS IDLE")
            self._safe_shutdown()
            csv_path = self._write_csv()
            aborted = self._abort.is_set()
            with self._lock:
                self._status.log_path = str(csv_path)
                self._status.activity_path = str(self._activity_path) if self._activity_path else None
                self._status.run_state = RunState.ABORTED if aborted else RunState.FAILED
                self._status.finished_at = _utc_now()
                self._status.message = str(exc)
            self._emit()
        finally:
            try:
                self.ea.all_off()
            except Exception:
                pass
            if self._activity_path is not None:
                self._event(f"Activity log saved: {self._activity_path}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite_cells(cells: list[float]) -> list[float]:
    return [c for c in cells if c == c and abs(c) < 1e6]  # filter NaN/inf


def _cell_extremes(b) -> tuple[float | None, float | None]:
    cells = _finite_cells(b.cell_voltages or [])
    if cells:
        return min(cells), max(cells)
    return b.cell_v_min, b.cell_v_max
