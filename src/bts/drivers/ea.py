from __future__ import annotations

import logging
import re
import socket
import threading
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from bts.models.config import EaChannelConfig, EaConfig
from bts.models.telemetry import EaTelemetry

if TYPE_CHECKING:
    from bts.drivers.bms import MockBmsDriver

log = logging.getLogger(__name__)


def _parse_scpi_float(raw: str) -> float:
    """Parse EA/SCPI numeric replies that may include units, e.g. '1.01 V', '-3.2A'."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty SCPI numeric reply")
    # Prefer first float token (ignores trailing unit / extra words)
    m = re.match(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)", text)
    if not m:
        raise ValueError(f"not a SCPI number: {raw!r}")
    return float(m.group(1))


def _is_auto_serial(port: str | None) -> bool:
    if port is None or not str(port).strip():
        return True
    return str(port).strip().lower() in {"auto", "any", "*"}


def _classify_ea_idn(idn: str) -> str | None:
    """Return 'psi' | 'el' | None from *IDN? text."""
    u = idn.upper()
    # Loads first (EL / electronic load)
    if "EL " in u or "EL-" in u or "EL9" in u or "LOAD" in u:
        return "el"
    if "PSI" in u or "PS 9" in u or "PS9" in u or "SOURCE" in u:
        return "psi"
    return None


def probe_serial_idn(port: str, baudrate: int = 115200, timeout_s: float = 1.2) -> str | None:
    """Open COM briefly and query *IDN?. Returns None if busy/failed."""
    import serial

    try:
        ser = serial.Serial(port, baudrate, timeout=timeout_s)
    except Exception as exc:
        log.info("EA discover %s: open failed (%s)", port, exc)
        return f"__BUSY__:{exc}"
    try:
        ser.reset_input_buffer()
        ser.write(b"*IDN?\n")
        line = ser.readline().decode("ascii", errors="replace").strip()
        return line or None
    except Exception as exc:
        log.debug("IDN on %s failed: %s", port, exc)
        return None
    finally:
        try:
            ser.close()
        except Exception:
            pass


def list_candidate_com_ports() -> list[str]:
    try:
        from serial.tools import list_ports

        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


def discover_ea_serial_map(baudrate: int = 115200) -> dict[str, tuple[str, str]]:
    """Scan COM ports → {'psi': (COMx, idn), 'el': (COMy, idn)}.

    Also sets discover_ea_serial_map.last_scan for richer connect errors.
    """
    found: dict[str, tuple[str, str]] = {}
    busy: list[str] = []
    empty: list[str] = []
    unknown: list[str] = []
    ports = list_candidate_com_ports()
    for port in ports:
        idn = probe_serial_idn(port, baudrate)
        if idn is None:
            empty.append(port)
            continue
        if isinstance(idn, str) and idn.startswith("__BUSY__:"):
            busy.append(port)
            log.info("EA discover %s: BUSY (another app holds COM)", port)
            continue
        role = _classify_ea_idn(idn)
        log.info("EA discover %s: %s → %s", port, idn, role or "unknown")
        if role and role not in found:
            found[role] = (port, idn)
        elif not role:
            unknown.append(f"{port}={idn[:40]}")
        if "psi" in found and "el" in found:
            break
    discover_ea_serial_map.last_scan = {  # type: ignore[attr-defined]
        "ports": ports,
        "busy": busy,
        "empty": empty,
        "unknown": unknown,
        "found": {k: v[0] for k, v in found.items()},
    }
    return found


def resolve_ea_serial_ports(cfg: EaConfig) -> None:
    """Assign COM ports by *IDN? so swapped USB cables still work.

    Uses auto-discovery when serial_port is empty/'auto', or when a fixed
    COM answers as the wrong instrument type.
    """
    need = cfg.psi.transport == "serial" or cfg.el.transport == "serial"
    if not need:
        return

    baud = int(cfg.psi.baudrate or cfg.el.baudrate or 115200)
    mapping = discover_ea_serial_map(baudrate=baud)

    def assign(ch: EaChannelConfig, role: str) -> None:
        if ch.transport != "serial":
            return
        discovered = mapping.get(role)
        configured = ch.serial_port
        if discovered:
            port, idn = discovered
            if _is_auto_serial(configured) or configured != port:
                if configured and not _is_auto_serial(configured) and configured != port:
                    log.warning(
                        "EA %s: config had %s but %s is %s — using discovered port",
                        role,
                        configured,
                        port,
                        idn,
                    )
                ch.serial_port = port
                log.info("EA %s → %s (%s)", role.upper(), port, idn)
            return
        if _is_auto_serial(configured):
            scan = getattr(discover_ea_serial_map, "last_scan", {}) or {}
            found = ", ".join(f"{k}={v[0]}" for k, v in mapping.items()) or "none"
            busy = ", ".join(scan.get("busy") or []) or "none"
            ports = ", ".join(scan.get("ports") or []) or "none"
            hint = ""
            if scan.get("busy"):
                hint = (
                    " COM ports are BUSY — another BTS instance or EA Power Control "
                    "is holding them. Close the other app, then retry."
                )
            elif not scan.get("ports"):
                hint = " No COM ports visible — check USB cables / drivers."
            else:
                hint = " Close EA Power Control (COM exclusive), check USB, then retry."
            raise RuntimeError(
                f"EA {role}: no matching instrument on any COM (*IDN?).{hint} "
                f"Found: {found}; busy: {busy}; ports: {ports}"
            )
        # Keep configured port; verify it is not the other role
        idn = probe_serial_idn(str(configured), baud)
        if idn:
            got = _classify_ea_idn(idn)
            if got and got != role:
                # Swap if the other port is the one we need
                other = mapping.get(role)
                if other:
                    ch.serial_port = other[0]
                    log.warning(
                        "EA %s was on wrong COM (%s=%s); switched to %s",
                        role,
                        configured,
                        idn,
                        other[0],
                    )
                else:
                    raise RuntimeError(
                        f"EA {role}: {configured} identifies as {idn!r} (role={got}), "
                        f"not {role}. USB cables may be swapped — replug or set serial_port: auto"
                    )

    assign(cfg.psi, "psi")
    assign(cfg.el, "el")


class ScpiTransport:
    def __init__(self, cfg: EaChannelConfig, timeout_s: float = 2.0) -> None:
        self.cfg = cfg
        self.timeout_s = timeout_s
        self._sock: socket.socket | None = None
        self._ser = None
        self._lock = threading.RLock()

    def connect(self) -> None:
        if self._sock is not None or self._ser is not None:
            return
        if self.cfg.transport == "tcp":
            sock = socket.create_connection((self.cfg.host, self.cfg.port), timeout=self.timeout_s)
            sock.settimeout(self.timeout_s)
            self._sock = sock
        elif self.cfg.transport == "serial":
            import serial

            self._ser = serial.Serial(self.cfg.serial_port, self.cfg.baudrate, timeout=self.timeout_s)
        else:
            raise ValueError(f"Unknown transport {self.cfg.transport}")

    def close(self) -> None:
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None

    def write(self, cmd: str) -> None:
        payload = (cmd.rstrip() + "\n").encode("ascii")
        with self._lock:
            if self._sock:
                self._sock.sendall(payload)
            elif self._ser:
                self._ser.write(payload)
            else:
                raise RuntimeError("SCPI not connected")

    def query(self, cmd: str) -> str:
        payload = (cmd.rstrip() + "\n").encode("ascii")
        with self._lock:
            if self._sock:
                # Non-blocking drain of any stale bytes, then blocking query
                self._sock.setblocking(False)
                try:
                    while True:
                        chunk = self._sock.recv(65536)
                        if not chunk:
                            break
                except (BlockingIOError, OSError, TimeoutError):
                    pass
                finally:
                    self._sock.setblocking(True)
                    self._sock.settimeout(self.timeout_s)
                self._sock.sendall(payload)
                buf = b""
                while b"\n" not in buf:
                    chunk = self._sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                text = buf.decode("ascii", errors="replace").strip()
            elif self._ser:
                try:
                    self._ser.reset_input_buffer()
                except Exception:
                    pass
                self._ser.write(payload)
                line = self._ser.readline()
                text = line.decode("ascii", errors="replace").strip()
            else:
                raise RuntimeError("SCPI not connected")
            if not text:
                raise TimeoutError(
                    f"SCPI timeout/empty reply to {cmd!r} on "
                    f"{self.cfg.serial_port or self.cfg.host}"
                )
            return text


class EaDevice:
    """Single EA instrument (PSI or EL master) via SCPI."""

    def __init__(self, cfg: EaChannelConfig, is_load: bool = False) -> None:
        self.cfg = cfg
        self.is_load = is_load
        self.transport = ScpiTransport(cfg)
        self._connected = False

    def connect(self) -> None:
        if self._connected:
            return
        self.transport.connect()
        self._connected = True
        # EA Programming Guide (ModBus & SCPI): remote NEVER starts automatically —
        # must send SYST:LOCK ON before setpoints. Reading MEAS is allowed anytime.
        try:
            self.transport.write("SYST:LOCK ON")
        except Exception:
            log.warning("SYST:LOCK ON failed on %s — continuing", self.cfg.name)

    def disconnect(self) -> None:
        try:
            self.output_enable(False)
        except Exception:
            pass
        try:
            self.transport.write("SYST:LOCK OFF")
        except Exception:
            pass
        self.transport.close()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def set_voltage(self, volts: float) -> None:
        self.transport.write(f"VOLT {volts:.4f}")

    def set_current(self, amps: float) -> None:
        self.transport.write(f"CURR {amps:.4f}")

    def set_power(self, watts: float) -> None:
        # PSI/EL 9000: use numeric POW (POW MAX is 10k/20k dialect; can yield -100 Command error)
        self.transport.write(f"POW {float(watts):.4f}")

    def flush_errors(self) -> list[str]:
        """Drain SCPI error queue (errors are not reported until queried)."""
        found: list[str] = []
        for _ in range(8):
            msg = self.query_error()
            if not msg or msg.startswith("0,") or '"No error"' in msg or "No error" in msg:
                break
            found.append(msg)
        return found

    def write_checked(self, cmd: str) -> str | None:
        """Write SCPI command and return first error string if any.

        Caller should flush_errors() once before a command sequence so stale
        queue entries are not attributed to the first write.
        """
        self.transport.write(cmd)
        # Allow device to post error
        time.sleep(0.05)
        errs = self.flush_errors()
        return errs[0] if errs else None

    def output_enable(self, on: bool) -> None:
        # Programming Guide: INP ON for electronic loads, OUTP ON for sources
        if self.is_load:
            self.transport.write(f"INP {'ON' if on else 'OFF'}")
        else:
            self.transport.write(f"OUTP {'ON' if on else 'OFF'}")

    def force_output(self, on: bool, *, settle_s: float = 0.35) -> bool:
        """Explicit OFF↔ON edge, then verify with OUTP?/INP?.

        Some PSI 9000 firmwares engage the power stage more reliably on a
        rising edge after setpoints are already loaded (not only OUTP ON once).
        """
        if on:
            # Ensure a real off→on transition
            self.output_enable(False)
            time.sleep(max(0.15, settle_s * 0.5))
            self.output_enable(True)
            # EA also accepts numeric form; send both dialects if first verify fails
            time.sleep(max(0.15, settle_s))
            if self.query_output_on():
                return True
            alt = "INP 1" if self.is_load else "OUTP 1"
            self.transport.write(alt)
            time.sleep(max(0.15, settle_s))
            return self.query_output_on()
        self.output_enable(False)
        time.sleep(max(0.1, settle_s * 0.4))
        return not self.query_output_on()

    def query_set_voltage(self) -> float:
        return _parse_scpi_float(self.transport.query("VOLT?"))

    def query_set_current(self) -> float:
        return _parse_scpi_float(self.transport.query("CURR?"))

    def query_set_power(self) -> float:
        return _parse_scpi_float(self.transport.query("POW?"))

    def query_output_on(self) -> bool:
        cmd = "INP?" if self.is_load else "OUTP?"
        raw = self.transport.query(cmd).strip().upper().replace(" ", "")
        return raw in {"1", "ON", "OUTPON", "INPON"} or raw.endswith("ON")

    def query_error(self) -> str:
        """SCPI errors are queued — only returned on request (EA Programming Guide)."""
        try:
            return self.transport.query("SYST:ERR?").strip()
        except Exception:
            try:
                return self.transport.query("ERR:NEXT?").strip()
            except Exception as exc:
                return f"(error query failed: {exc})"

    def measure_voltage(self) -> float:
        return _parse_scpi_float(self.transport.query("MEAS:VOLT?"))

    def measure_current(self) -> float:
        return _parse_scpi_float(self.transport.query("MEAS:CURR?"))

    def measure_vi_raw(self) -> tuple[float, float, str, str]:
        """Return (V, I, raw_volt_reply, raw_curr_reply) from MEAS queries."""
        v_raw = self.transport.query("MEAS:VOLT?")
        i_raw = self.transport.query("MEAS:CURR?")
        return (
            _parse_scpi_float(v_raw),
            _parse_scpi_float(i_raw),
            v_raw.strip(),
            i_raw.strip(),
        )

    def sample_vi(
        self,
        duration_s: float = 1.2,
        period_s: float = 0.08,
    ) -> dict[str, float | str | list[str]]:
        """Dense MEAS sampling right after OUTP/INP ON.

        Captures a short front-panel-visible inrush (cable/filter C) that a
        single late sample would miss, and keeps raw SCPI strings for debug.
        """
        rows: list[tuple[float, float, str, str]] = []
        t_end = time.monotonic() + max(0.2, float(duration_s))
        while True:
            v, i, v_raw, i_raw = self.measure_vi_raw()
            rows.append((v, i, v_raw, i_raw))
            if time.monotonic() >= t_end:
                break
            time.sleep(max(0.02, float(period_s)))
        abs_i = [abs(r[1]) for r in rows]
        peak_i = max(abs_i) if abs_i else 0.0
        # Sustained = mean of last ~0.4 s (or last 5 samples)
        n_tail = max(5, int(0.4 / max(0.02, float(period_s))))
        tail = abs_i[-n_tail:] if abs_i else [0.0]
        sustained_i = sum(tail) / len(tail)
        v_last, i_last, v_raw_last, i_raw_last = rows[-1]
        # Compact trail for activity log (time index approx)
        trail = ",".join(f"{r[1]:.2f}" for r in rows[:24])
        if len(rows) > 24:
            trail += ",…"
        return {
            "n": float(len(rows)),
            "peak_i": peak_i,
            "sustained_i": sustained_i,
            "v_last": v_last,
            "i_last": i_last,
            "raw_v": v_raw_last,
            "raw_i": i_raw_last,
            "trail_i": trail,
        }

    def identity(self) -> str:
        return self.transport.query("*IDN?")


class EaRackDriver(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def all_off(self) -> None: ...

    @abstractmethod
    def start_charge(self, voltage_v: float, current_a: float) -> str: ...

    @abstractmethod
    def start_discharge(self, current_a: float, voltage_limit_v: float = 0.0, operating_voltage_v: float | None = None) -> str: ...

    @abstractmethod
    def telemetry(self) -> EaTelemetry: ...

    @abstractmethod
    def is_healthy(self, timeout_s: float) -> bool: ...

    @abstractmethod
    def ensure_exclusive(self) -> None:
        """Hard rule: PSI and EL must never be active together. Raise or force all_off."""
        ...

    def active_mode(self) -> str | None:
        """charge | discharge | None"""
        return None

    def commanded_current_a(self) -> float:
        """Absolute current setpoint of the active path (0 if idle)."""
        return 0.0

    def inject_fault(self, fault: str | None, *, after_s: float = 0.0) -> None:
        """Mock-only fault injection. Live racks ignore this."""
        del fault, after_s


# Dead-time between disabling one path and enabling the other (hardware settle)
_EA_MUTEX_SETTLE_S = 0.25


def _power_setpoint_w(
    voltage_v: float,
    current_a: float,
    ch: EaChannelConfig,
    *,
    is_load: bool = False,
) -> float:
    """EA sources/loads limit by U, I and P. P too low ⇒ current clamped to P/U.

    Prefer enough headroom for U×I. PSI 9000 is often ~10.2 kW; EL master/slave
    pairs are typically much higher — do not clamp loads to the PSI envelope.
    """
    needed = abs(float(voltage_v) * float(current_a)) * 1.25
    envelope = abs(float(ch.max_voltage_v) * float(ch.max_current_a))
    # PSI 9080-xxx ~10.2 kW; EL 9080-510 B MS pair often ≥30 kW combined
    pnom_cap = 30000.0 if is_load else 10200.0
    if envelope > pnom_cap:
        device_guess = pnom_cap
    else:
        device_guess = envelope if envelope > 0 else needed
    return min(max(needed, 100.0), device_guess)


class EaScpiRack(EaRackDriver):
    """1× PSI (charge) + 1× EL master of a hardware master/slave pair (discharge).

    Two EL 9080-510 B units are linked as master/slave on the bench and behave as
    one combined load. Software addresses only the master; setpoints and MEAS
    values are system (combined) values — no software current split.

    Silent loss of the slave typically shows as ~½ of the CURR setpoint while the
    master COM link stays healthy — SequenceEngine detects that via integrity checks.
    """

    def __init__(self, cfg: EaConfig) -> None:
        self.cfg = cfg
        self.psi = EaDevice(cfg.psi, is_load=False)
        self.el = EaDevice(cfg.el, is_load=True)
        self._mode: str | None = None  # charge | discharge | None
        self._set_i = 0.0
        self._lock = threading.RLock()
        self._last_ok = 0.0
        self._last_error: str | None = None
        self._last_arm_sample: dict[str, float | str] | None = None

    def active_mode(self) -> str | None:
        return self._mode

    def commanded_current_a(self) -> float:
        return float(self._set_i)

    def connect(self) -> None:
        # Idempotent: Start must not steal COM ports already opened by Connect HW
        if self.psi.connected and self.el.connected:
            self._last_ok = time.monotonic()
            return
        resolve_ea_serial_ports(self.cfg)
        if not self.psi.connected:
            self.psi = EaDevice(self.cfg.psi, is_load=False)
            self.psi.connect()
        if not self.el.connected:
            self.el = EaDevice(self.cfg.el, is_load=True)
            self.el.connect()
        self._last_ok = time.monotonic()
        log.info(
            "EA rack connected: PSI %s @ %s + EL master %s @ %s (MS pair, max %.0f A)",
            self.cfg.psi.name,
            self.cfg.psi.serial_port or f"{self.cfg.psi.host}:{self.cfg.psi.port}",
            self.cfg.el.name,
            self.cfg.el.serial_port or f"{self.cfg.el.host}:{self.cfg.el.port}",
            self.cfg.el.max_current_a,
        )

    def disconnect(self) -> None:
        self.all_off()
        self.psi.disconnect()
        self.el.disconnect()

    def all_off(self) -> None:
        with self._lock:
            for dev in (self.psi, self.el):
                try:
                    if dev.connected:
                        dev.output_enable(False)
                except Exception:
                    log.exception("Failed to disable %s", dev.cfg.name)
            self._mode = None
            self._set_i = 0.0

    def ensure_exclusive(self) -> None:
        """If both paths look active, kill everything and raise."""
        with self._lock:
            if self._mode == "charge":
                # EL must be off while charging
                try:
                    self.el.output_enable(False)
                except Exception:
                    log.exception("Failed to keep EL off during charge")
            elif self._mode == "discharge":
                try:
                    self.psi.output_enable(False)
                except Exception:
                    log.exception("Failed to keep PSI off during discharge")
            elif self._mode is None:
                # Both should be off
                try:
                    self.psi.output_enable(False)
                    self.el.output_enable(False)
                except Exception:
                    pass

            # Impossible commanded dual-mode (defensive)
            if self._mode not in (None, "charge", "discharge"):
                self.all_off()
                raise RuntimeError(f"EA mutex violation: invalid mode {self._mode!r}")

    def start_charge(self, voltage_v: float, current_a: float) -> str:
        with self._lock:
            # Mutex: load OFF first, settle, then source ON
            self.el.output_enable(False)
            time.sleep(_EA_MUTEX_SETTLE_S)
            self.psi.flush_errors()
            lock_err = self.psi.write_checked("SYST:LOCK ON")
            i = min(current_a, self.cfg.psi.max_current_a)
            v = min(voltage_v, self.cfg.psi.max_voltage_v)
            p = _power_setpoint_w(v, i, self.cfg.psi)
            errs: list[str] = []
            if lock_err:
                errs.append(f"SYST:LOCK ON → {lock_err}")
            # 1) Load setpoints while output still OFF
            self.psi.output_enable(False)
            for label, cmd in (
                ("VOLT", f"VOLT {v:.4f}"),
                ("CURR", f"CURR {i:.4f}"),
                ("POW", f"POW {p:.4f}"),
            ):
                err = self.psi.write_checked(cmd)
                if err:
                    errs.append(f"{label} → {err}")
            v_set = self.psi.query_set_voltage()
            i_set = self.psi.query_set_current()
            p_set = self.psi.query_set_power()
            out_before = self.psi.query_output_on()
            log.info(
                "EA CHARGE setpoints loaded (OUTP still %s): V=%.2f I=%.1f P=%.0fW",
                "ON" if out_before else "OFF",
                v_set,
                i_set,
                p_set,
            )
            # 2) Explicit OFF → ON edge (power stage start)
            out_on = self.psi.force_output(True, settle_s=0.40)
            outp_errs = self.psi.flush_errors()
            if outp_errs:
                errs.append(f"OUTP → {outp_errs[0]}")
            self._mode = "charge"
            self._set_i = float(i_set if i_set > 0 else i)
            log.info("EA CHARGE OUTP off→on verify=%s", "ON" if out_on else "OFF")
            # 3) Sample after power stage is commanded on
            samp = self.psi.sample_vi(duration_s=1.5, period_s=0.08)
            self._last_arm_sample = samp
            # Re-check / re-assert if output dropped during sample
            if not self.psi.query_output_on():
                log.warning("EA CHARGE OUTP dropped during sample — re-assert ON")
                out_on = self.psi.force_output(True, settle_s=0.30)
            else:
                out_on = True
            self._last_ok = time.monotonic()
            self._last_error = None
            err_txt = "; ".join(errs) if errs else "0,\"No error\""
            detail = (
                f"set V={v_set:.2f} I={i_set:.1f} P={p_set:.0f}W "
                f"OUTP={'ON' if out_on else 'OFF'} (was={'ON' if out_before else 'OFF'}→on) "
                f"| meas V={float(samp['v_last']):.2f} I={float(samp['i_last']):.2f}A "
                f"peak={float(samp['peak_i']):.2f}A sust={float(samp['sustained_i']):.2f}A "
                f"raw[V={samp['raw_v']} I={samp['raw_i']}] "
                f"trail_I=[{samp['trail_i']}] | SCPI={err_txt}"
            )
            log.info("EA mode=CHARGE (EL forced off) %s", detail)
            if not out_on:
                raise RuntimeError(f"PSI OUTP did not turn ON (off→on failed) — {detail}")
            if errs:
                raise RuntimeError(f"PSI SCPI command error during arm — {detail}")
            return detail

    def start_discharge(
        self,
        current_a: float,
        voltage_limit_v: float = 0.0,
        operating_voltage_v: float | None = None,
    ) -> str:
        with self._lock:
            # Mutex: source OFF first, settle, then load ON
            self.psi.output_enable(False)
            time.sleep(_EA_MUTEX_SETTLE_S)
            self.el.flush_errors()
            lock_err = self.el.write_checked("SYST:LOCK ON")
            i = min(current_a, self.cfg.el.max_current_a)
            # VOLT on EL = UV floor (stop). POW must use operating pack U, not UV —
            # otherwise I is clamped to P/U_pack (e.g. 17V×60A×1.2=1224W → ~48A @25V).
            uv = float(voltage_limit_v) if voltage_limit_v > 0 else 0.0
            op_v = float(operating_voltage_v) if operating_voltage_v and operating_voltage_v > 0 else 0.0
            if op_v <= 0:
                op_v = float(self.cfg.el.max_voltage_v)
            p_v = max(op_v, uv, 1.0)
            p = _power_setpoint_w(p_v, i, self.cfg.el, is_load=True)
            errs: list[str] = []
            if lock_err:
                errs.append(f"SYST:LOCK ON → {lock_err}")
            cmds: list[tuple[str, str]] = []
            if voltage_limit_v > 0:
                cmds.append(("VOLT", f"VOLT {voltage_limit_v:.4f}"))
            cmds.extend(
                (
                    ("CURR", f"CURR {i:.4f}"),
                    ("POW", f"POW {p:.4f}"),
                )
            )
            for label, cmd in cmds:
                err = self.el.write_checked(cmd)
                if err:
                    errs.append(f"{label} → {err}")
            i_set = self.el.query_set_current()
            p_set = self.el.query_set_power()
            inp_before = self.el.query_output_on()
            log.info(
                "EA DISCHARGE setpoints loaded (INP still %s): I=%.1f P=%.0fW "
                "(POW from Uop=%.2fV, UV=%.2fV)",
                "ON" if inp_before else "OFF",
                i_set,
                p_set,
                p_v,
                uv,
            )
            inp_on = self.el.force_output(True, settle_s=0.40)
            inp_errs = self.el.flush_errors()
            if inp_errs:
                errs.append(f"INP → {inp_errs[0]}")
            self._mode = "discharge"
            self._set_i = float(i_set if i_set > 0 else i)
            log.info("EA DISCHARGE INP off→on verify=%s", "ON" if inp_on else "OFF")
            samp = self.el.sample_vi(duration_s=1.5, period_s=0.08)
            self._last_arm_sample = samp
            if not self.el.query_output_on():
                log.warning("EA DISCHARGE INP dropped during sample — re-assert ON")
                inp_on = self.el.force_output(True, settle_s=0.30)
            else:
                inp_on = True
            self._last_ok = time.monotonic()
            self._last_error = None
            err_txt = "; ".join(errs) if errs else "0,\"No error\""
            detail = (
                f"set I={i_set:.1f} P={p_set:.0f}W UV={uv:.2f} Uop={p_v:.2f} "
                f"INP={'ON' if inp_on else 'OFF'} (was={'ON' if inp_before else 'OFF'}→on) "
                f"| meas V={float(samp['v_last']):.2f} I={abs(float(samp['i_last'])):.2f}A "
                f"peak={float(samp['peak_i']):.2f}A sust={float(samp['sustained_i']):.2f}A "
                f"raw[V={samp['raw_v']} I={samp['raw_i']}] "
                f"trail_I=[{samp['trail_i']}] | SCPI={err_txt}"
            )
            log.info("EA mode=DISCHARGE (PSI forced off) %s", detail)
            if not inp_on:
                raise RuntimeError(f"EL INP did not turn ON (off→on failed) — {detail}")
            if errs:
                raise RuntimeError(f"EL SCPI command error during arm — {detail}")
            return detail

    def telemetry(self) -> EaTelemetry:
        with self._lock:
            tel = EaTelemetry(
                connected=self.psi.connected and self.el.connected,
                active_mode=self._mode,
                set_current_a=float(self._set_i),
            )
            try:
                tel.psi_voltage_v = self.psi.measure_voltage()
                tel.psi_current_a = self.psi.measure_current()
                tel.psi_output_on = self.psi.query_output_on()
                tel.el_voltage_v = self.el.measure_voltage()
                tel.el_current_a = abs(self.el.measure_current())
                tel.el_input_on = self.el.query_output_on()
                # Soft detect: significant current on both paths → emergency off
                if (
                    self._mode is not None
                    and abs(tel.psi_current_a) > 1.0
                    and abs(tel.el_current_a) > 1.0
                ):
                    tel.alarms.append("MUTEX: PSI and EL both drawing current")
                    log.error("EA mutex violation detected in telemetry — forcing all_off")
                    try:
                        self.psi.output_enable(False)
                        self.el.output_enable(False)
                    except Exception:
                        log.exception("all_off during mutex fault")
                    self._mode = None
                    self._set_i = 0.0
                self._last_ok = time.monotonic()
                self._last_error = None
                tel.last_rx_s = self._last_ok
            except Exception as exc:
                self._last_error = str(exc)
                tel.alarms.append(str(exc))
                log.exception("EA measure failed")
            return tel

    def health_detail(self) -> str:
        age = time.monotonic() - self._last_ok if self._last_ok else float("inf")
        ports = (
            f"PSI {self.cfg.psi.serial_port or self.cfg.psi.host} / "
            f"EL-master {self.cfg.el.serial_port or self.cfg.el.host}"
        )
        err = self._last_error or "no reply"
        return f"{ports} · last_ok={age:.1f}s ago · {err}"

    def is_healthy(self, timeout_s: float) -> bool:
        """Live SCPI probe under rack lock. No stale-timestamp fallback."""
        del timeout_s  # probe is authoritative; timeout is on the serial layer
        with self._lock:
            if not (self.psi.connected and self.el.connected):
                self._last_error = "PSI/EL not connected"
                return False
            try:
                _ = self.psi.measure_voltage()
                _ = self.el.measure_voltage()
                self._last_ok = time.monotonic()
                self._last_error = None
                return True
            except Exception as exc:
                self._last_error = str(exc)
                log.warning("EA health probe failed: %s", exc)
                return False


# Known mock fault IDs (also used by DEV YAML mock_inject_fault)
MOCK_FAULT_EL_SLAVE_LOST = "el_slave_lost"
MOCK_FAULT_EL_COM_LOST = "el_com_lost"
MOCK_FAULT_PSI_COM_LOST = "psi_com_lost"
MOCK_FAULT_EL_INPUT_DROP = "el_input_drop"
MOCK_FAULT_PSI_OUTPUT_DROP = "psi_output_drop"
MOCK_FAULT_IDS = (
    MOCK_FAULT_EL_SLAVE_LOST,
    MOCK_FAULT_EL_COM_LOST,
    MOCK_FAULT_PSI_COM_LOST,
    MOCK_FAULT_EL_INPUT_DROP,
    MOCK_FAULT_PSI_OUTPUT_DROP,
)


class MockEaRack(EaRackDriver):
    def __init__(self, cfg: EaConfig | None = None, mock_bms: MockBmsDriver | None = None) -> None:
        self.cfg = cfg
        self.mock_bms = mock_bms
        self._mode: str | None = None
        self._set_v = 0.0
        self._set_i = 0.0
        self._connected = False
        self._last_ok = 0.0
        self._lock = threading.RLock()
        self._fault: str | None = None
        self._pending_fault: str | None = None
        self._pending_fault_at = 0.0

    def active_mode(self) -> str | None:
        return self._mode

    def commanded_current_a(self) -> float:
        return float(self._set_i)

    def inject_fault(self, fault: str | None, *, after_s: float = 0.0) -> None:
        with self._lock:
            if not fault:
                self._fault = None
                self._pending_fault = None
                return
            if fault not in MOCK_FAULT_IDS:
                raise ValueError(f"Unknown mock fault {fault!r}; expected one of {MOCK_FAULT_IDS}")
            if after_s <= 0:
                self._apply_fault_locked(fault)
            else:
                self._pending_fault = fault
                self._pending_fault_at = time.monotonic() + float(after_s)
                log.info("Mock EA: will inject %s in %.1fs", fault, after_s)

    def _tick_pending_fault(self) -> None:
        if self._pending_fault and time.monotonic() >= self._pending_fault_at:
            self._apply_fault_locked(self._pending_fault)
            self._pending_fault = None

    def _apply_fault_locked(self, fault: str) -> None:
        self._fault = fault
        log.warning("Mock EA: injected fault %s (mode=%s set_i=%.1f)", fault, self._mode, self._set_i)
        if fault == MOCK_FAULT_EL_SLAVE_LOST and self._mode == "discharge" and self.mock_bms is not None:
            self.mock_bms.set_external_current(-abs(self._set_i) * 0.5)
        elif fault == MOCK_FAULT_EL_INPUT_DROP and self.mock_bms is not None:
            self.mock_bms.set_external_current(0.0)
        elif fault == MOCK_FAULT_PSI_OUTPUT_DROP and self.mock_bms is not None:
            self.mock_bms.set_external_current(0.0)

    def connect(self) -> None:
        self._connected = True
        self._last_ok = time.monotonic()

    def disconnect(self) -> None:
        self.all_off()
        self._connected = False

    def all_off(self) -> None:
        with self._lock:
            self._mode = None
            self._set_i = 0.0
            if self.mock_bms is not None:
                self.mock_bms.set_external_current(0.0)

    def apply_ui_preview(self, scenario: str, *, current_a: float = 70.0) -> None:
        """Match EA path to BMS Connect-time demo scenario."""
        with self._lock:
            if scenario == "charge":
                self._mode = "charge"
                self._set_v = 27.5
                self._set_i = abs(current_a)
                if self.mock_bms is not None:
                    self.mock_bms.set_external_current(self._set_i)
            elif scenario == "discharge":
                self._mode = "discharge"
                self._set_v = 17.0
                max_i = self.cfg.el.max_current_a if self.cfg else 1020.0
                self._set_i = min(abs(current_a), max_i)
                if self.mock_bms is not None:
                    self.mock_bms.set_external_current(-self._set_i)
            else:
                self._mode = None
                self._set_i = 0.0
                if self.mock_bms is not None and scenario in ("idle", "precharge", "ready"):
                    # Current already set by BMS preview; keep 0 for idle paths
                    self.mock_bms.set_external_current(0.0)
            self._last_ok = time.monotonic()

    def ensure_exclusive(self) -> None:
        with self._lock:
            # Mock: only one mode can be set; nothing else to do
            if self._mode not in (None, "charge", "discharge"):
                self.all_off()
                raise RuntimeError(f"EA mutex: invalid mode {self._mode}")

    def start_charge(self, voltage_v: float, current_a: float) -> str:
        with self._lock:
            # Explicit mutex: leave discharge path
            self._mode = "charge"
            self._set_v = voltage_v
            self._set_i = current_a
            self._last_ok = time.monotonic()
            if self.mock_bms is not None:
                self.mock_bms.set_external_current(current_a)
            return f"mock set V={voltage_v:.2f} I={current_a:.1f} | meas I={current_a:.2f}A"

    def start_discharge(
        self,
        current_a: float,
        voltage_limit_v: float = 0.0,
        operating_voltage_v: float | None = None,
    ) -> str:
        with self._lock:
            # Explicit mutex: leave charge path
            self._mode = "discharge"
            self._set_v = voltage_limit_v
            max_i = self.cfg.el.max_current_a if self.cfg else 1020.0
            self._set_i = min(abs(current_a), max_i)
            self._last_ok = time.monotonic()
            if self.mock_bms is not None:
                self.mock_bms.set_external_current(-self._set_i)
            op = operating_voltage_v if operating_voltage_v else voltage_limit_v
            return (
                f"mock set I={self._set_i:.1f} UV={voltage_limit_v:.2f} Uop={op or 0:.2f} "
                f"| meas I={self._set_i:.2f}A"
            )

    def telemetry(self) -> EaTelemetry:
        with self._lock:
            self._tick_pending_fault()
            if self._fault not in (MOCK_FAULT_EL_COM_LOST, MOCK_FAULT_PSI_COM_LOST):
                self._last_ok = time.monotonic()
            pack_v = 24.0
            if self.mock_bms is not None:
                pack_v = self.mock_bms.telemetry().pack_voltage_v or 24.0

            psi_on = self._mode == "charge"
            el_on = self._mode == "discharge"
            psi_i = self._set_i if psi_on else 0.0
            el_i = self._set_i if el_on else 0.0

            if self._fault == MOCK_FAULT_EL_SLAVE_LOST and el_on:
                el_i = abs(self._set_i) * 0.5
            elif self._fault == MOCK_FAULT_EL_INPUT_DROP and el_on:
                el_on = False
                el_i = 0.0
            elif self._fault == MOCK_FAULT_PSI_OUTPUT_DROP and psi_on:
                psi_on = False
                psi_i = 0.0

            return EaTelemetry(
                psi_voltage_v=pack_v if self._mode == "charge" else 0.0,
                psi_current_a=psi_i,
                psi_output_on=psi_on,
                el_voltage_v=pack_v if self._mode == "discharge" else 0.0,
                el_current_a=el_i,
                el_input_on=el_on,
                connected=self._connected,
                last_rx_s=self._last_ok,
                active_mode=self._mode,
                set_current_a=float(self._set_i),
            )

    def is_healthy(self, timeout_s: float) -> bool:
        with self._lock:
            self._tick_pending_fault()
            if self._fault == MOCK_FAULT_EL_COM_LOST:
                return False
            if self._fault == MOCK_FAULT_PSI_COM_LOST:
                return False
            return self._connected and (time.monotonic() - self._last_ok) <= timeout_s

    def health_detail(self) -> str:
        with self._lock:
            if self._fault in (MOCK_FAULT_EL_COM_LOST, MOCK_FAULT_PSI_COM_LOST):
                return f"mock EA fault={self._fault}"
            return f"mock EA mode={self._mode} set_i={self._set_i:.1f}"


def create_ea_driver(cfg: EaConfig, use_mock: bool, mock_bms=None) -> EaRackDriver:
    if use_mock:
        return MockEaRack(cfg, mock_bms=mock_bms)
    return EaScpiRack(cfg)
