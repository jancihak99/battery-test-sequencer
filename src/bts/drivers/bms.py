from __future__ import annotations

import copy
import logging
import random
import struct
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable

from bts.models.config import BmsConfig
from bts.models.telemetry import BmsTelemetry, BmuState, ContactorBits, DesiredState

log = logging.getLogger(__name__)

# UI mock preview scenarios (picked randomly on Connect)
MOCK_PREVIEW_SCENARIOS = (
    "idle",
    "precharge",
    "ready",
    "charge",
    "discharge",
)
# Scaling from BMU v3 External CAN / APP Integration specs
SOC_RES = 0.5  # %/bit, 0xFF = invalid
CURRENT_RES = 1.0 / 8.0  # A/bit (signed)
PACK_VOLTAGE_RES = 1.0 / 16.0  # V/bit (signed)
CELL_RES = 1.0 / 8192.0  # V/bit (unsigned), valid ~0–4 V
TEMP_RES = 1.0 / 64.0  # °C/bit (signed)
SENSOR_ERROR_MIN = 0x7F00  # 0x7F00–0x7FFF = invalid / LMU error codes
SENSOR_ERROR_MAX = 0x7FFF  # inclusive top of the invalid window


def _is_sensor_error(raw_u16: int) -> bool:
    """True only inside the LMU error window (0x7F00–0x7FFF).

    Signed signals (current/voltage/temperature) legitimately use the whole
    negative range 0x8000–0xFFFF, so a bare ``>= 0x7F00`` guard would wrongly
    reject every negative reading (discharge current, sub-zero temperatures).
    """
    return SENSOR_ERROR_MIN <= raw_u16 <= SENSOR_ERROR_MAX


class BmsDriver(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def set_desired_state(self, state: DesiredState) -> None: ...

    @abstractmethod
    def telemetry(self) -> BmsTelemetry: ...

    @abstractmethod
    def is_healthy(self, timeout_s: float) -> bool: ...

    def wait_for_rx(self, timeout_s: float = 3.0) -> bool:
        """Block until at least one BMU frame arrives (or timeout)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.is_healthy(timeout_s):
                return True
            time.sleep(0.05)
        return False

    def health_detail(self) -> str:
        return ""

    def request_dtc_reset(self) -> None:
        """Pulse App Command bit4 (Reset Latched DTCs). No-op if unsupported."""
        return


def _decode_soc(raw: int) -> float | None:
    if raw == 0xFF:
        return None
    return raw * SOC_RES


def _u16_be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _i16_be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">h", data, offset)[0]


def _u32_be(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _cell_voltage_v(raw: int) -> float:
    if raw == 0 or _is_sensor_error(raw):
        # 0 = unused/not reported; 0x7F00–0x7FFF = sensor error codes
        return float("nan")
    return raw * CELL_RES


def _cell_id(raw: int) -> int | None:
    """Cell-group ID from Vmin/Vmax message; reject padding / error."""
    if _is_sensor_error(raw) or raw == 0xFFFF:
        return None
    # Typical packs << 500 cells; 0x00FF often means “no valid min/max yet”
    if raw > 2000:
        return None
    return int(raw)


def _temp_c_u16(raw_u16: int) -> float | None:
    if _is_sensor_error(raw_u16):
        return None
    # Re-interpret as signed for scale (sub-zero temperatures are negative)
    raw_i = struct.unpack(">h", struct.pack(">H", raw_u16 & 0xFFFF))[0]
    return raw_i * TEMP_RES


def _pack_voltage_v(raw_u16: int) -> float | None:
    if _is_sensor_error(raw_u16):
        return None
    raw_i = struct.unpack(">h", struct.pack(">H", raw_u16 & 0xFFFF))[0]
    return raw_i * PACK_VOLTAGE_RES


def _current_a(raw_u16: int) -> float | None:
    if _is_sensor_error(raw_u16):
        return None
    # Signed: charge positive, discharge negative (0x8000–0xFFFF)
    raw_i = struct.unpack(">h", struct.pack(">H", raw_u16 & 0xFFFF))[0]
    return raw_i * CURRENT_RES


class BmsCanDriver(BmsDriver):
    """Clayton/Altairnano BMU External CAN (J1939 proprietary)."""

    def __init__(self, cfg: BmsConfig) -> None:
        self.cfg = cfg
        self._bus = None
        self._lock = threading.RLock()
        self._tel = BmsTelemetry()
        self._desired = DesiredState.IDLE
        self._heartbeat = 0
        self._thread: threading.Thread | None = None
        self._running = False
        self._notifier = None
        self._reset_dtc_pulses = 0  # remaining heartbeats with Reset Latched DTCs bit set

    def connect(self) -> None:
        from bts.can_bus import open_bus

        # Idempotent: Start must not open a second handle on the same Kvaser channel
        if self._bus is not None:
            return
        self._bus = open_bus(
            interface=self.cfg.interface,
            channel=self.cfg.channel,
            bitrate=self.cfg.bitrate,
            receive_own_messages=False,
        )
        with self._lock:
            self._tel.connected = True
            self._tel.last_rx_s = 0.0
            self._tel.rx_count = 0
        log.info("BMS CAN connected %s ch=%s @ %s", self.cfg.interface, self.cfg.channel, self.cfg.bitrate)

    def disconnect(self) -> None:
        self.stop()
        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:
                log.exception("BMS bus shutdown failed")
            self._bus = None
        with self._lock:
            self._tel.connected = False
            self._tel.rx_count = 0
            self._tel.last_rx_s = 0.0

    def start(self) -> None:
        if self._running:
            return
        if self._bus is None:
            self.connect()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="bms-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def set_desired_state(self, state: DesiredState) -> None:
        with self._lock:
            self._desired = state

    def telemetry(self) -> BmsTelemetry:
        with self._lock:
            # Surface what WE last commanded so consumers can spot an external
            # controller holding the contactors against our desired state.
            self._tel.desired_state = self._desired
            # Deep copy: the RX thread keeps mutating list fields
            # (cell_voltages, temperatures_c, …) in place, so a shallow
            # copy would alias them and race with consumers iterating them.
            return copy.deepcopy(self._tel)

    def is_healthy(self, timeout_s: float) -> bool:
        with self._lock:
            if not self._tel.connected or self._tel.rx_count <= 0:
                return False
            return (time.monotonic() - self._tel.last_rx_s) <= timeout_s

    def health_detail(self) -> str:
        with self._lock:
            rx = self._tel.rx_count
            age = (
                f"{time.monotonic() - self._tel.last_rx_s:.1f}s ago"
                if self._tel.last_rx_s > 0
                else "never"
            )
        return (
            f"ch={self.cfg.channel} @ {self.cfg.bitrate}, "
            f"BMU=0x{self.cfg.bmu_address:02X} App=0x{self.cfg.app_address:02X}, "
            f"RX frames={rx} (last {age}). "
            "Check External CAN (not internal), BMU power, bitrate, addresses."
        )

    def request_dtc_reset(self) -> None:
        """Edge 0→1 on Command bit4 clears latched DTCs (BMU External CAN §5.2.12)."""
        with self._lock:
            # Bit4 is normally 0; pulse high for several heartbeats
            self._reset_dtc_pulses = 8
            self._tel.active_dtcs = []
        log.info("BMS: Reset Latched DTCs requested (App Command bit4)")

    def _app_cmd_id(self) -> int:
        # 0x18EFxxyy — xx = BMU SA, yy = App SA
        return 0x18EF0000 | ((self.cfg.bmu_address & 0xFF) << 8) | (self.cfg.app_address & 0xFF)

    def _build_app_command(self) -> bytes:
        # BMU v3 External CAN §5.2.12 — must be sent every ~100 ms or BMU stops
        # streaming cells/temps/balance and may set Lost Comms DTC.
        # Bit1 = cell voltages (0x18FF10xx+), Bit2 = temps (0x18FF90xx+),
        # Bit3 = cell-balance (0x18FFD0xx+), Bit4 = Reset Latched DTCs (edge 0→1)
        cmd_bits = 0
        if self.cfg.request_cell_voltages:
            cmd_bits |= 1 << 1
        if self.cfg.request_temperatures:
            cmd_bits |= 1 << 2
        if self.cfg.request_cell_balance:
            cmd_bits |= 1 << 3
        # Lab default: always request the full stream even if a flag was cleared in YAML
        if (cmd_bits & 0x0E) == 0:
            cmd_bits |= (1 << 1) | (1 << 2) | (1 << 3)
        with self._lock:
            if self._reset_dtc_pulses > 0:
                cmd_bits |= 1 << 4
                self._reset_dtc_pulses -= 1
        desired = int(self._desired) & 0x0F  # bits 0-3; command type bits 4-7 = 0
        # Contactor control = BMU → External Contactor State bit0 must be 1
        external_contactor = 0x01
        self._heartbeat = (self._heartbeat + 1) & 0xFF
        return bytes([desired, external_contactor, cmd_bits, self._heartbeat, 0, 0, 0, 0])

    def _loop(self) -> None:
        import can

        period = max(0.05, self.cfg.heartbeat_period_ms / 1000.0)
        while self._running and self._bus is not None:
            t0 = time.monotonic()
            try:
                msg = can.Message(
                    arbitration_id=self._app_cmd_id(),
                    data=self._build_app_command(),
                    is_extended_id=True,
                )
                self._bus.send(msg)
                deadline = t0 + period
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    rx = self._bus.recv(timeout=min(remaining, 0.02))
                    if rx is None:
                        continue
                    self._handle_rx(rx)
            except Exception:
                log.exception("BMS CAN loop error")
                time.sleep(period)

    def _handle_rx(self, msg) -> None:
        if not msg.is_extended_id:
            return
        can_id = msg.arbitration_id
        data = bytes(msg.data)
        if len(data) < 8:
            data = data + bytes(8 - len(data))

        # 0x18FFppxx — xx = BMU source address
        msg_key = (can_id >> 8) & 0xFFFF

        with self._lock:
            self._tel.last_rx_s = time.monotonic()
            self._tel.connected = True
            self._tel.rx_count += 1

            if msg_key == 0xFF00:  # SOC + limits + state
                self._tel.soc_pct = _decode_soc(data[0])
                self._tel.charge_current_limit_a = _current_a(_u16_be(data, 1))
                self._tel.discharge_current_limit_a = _current_a(_u16_be(data, 3))
                state_fault = data[5]
                st = state_fault & 0x1F
                self._tel.operating_state = (
                    BmuState(st) if st in BmuState._value2member_map_ else BmuState.UNKNOWN
                )
                self._tel.dtc_level = (state_fault >> 5) & 0x07

            elif msg_key == 0xFF01:  # Pack V/I + contactors
                self._tel.pack_voltage_v = _pack_voltage_v(_u16_be(data, 0))
                self._tel.pack_current_a = _current_a(_u16_be(data, 2))
                self._tel.pack_voltage_switched_v = _pack_voltage_v(_u16_be(data, 4))
                self._tel.contactor_cmd = ContactorBits.from_byte(data[6], status=False)
                self._tel.contactor_status = ContactorBits.from_byte(data[7], status=True)

            elif msg_key == 0xFF02:  # Vmin/Vmax cells
                vmin = _cell_voltage_v(_u16_be(data, 0))
                vmax = _cell_voltage_v(_u16_be(data, 2))
                # Prefer BMU-reported extrema when valid
                if vmin == vmin:  # not NaN
                    self._tel.cell_v_min = vmin
                if vmax == vmax:
                    self._tel.cell_v_max = vmax
                vid = _cell_id(_u16_be(data, 4))
                xid = _cell_id(_u16_be(data, 6))
                if vid is not None:
                    self._tel.cell_vmin_id = vid
                if xid is not None:
                    self._tel.cell_vmax_id = xid
                # If BMU sent invalid Vmin but we already have cell stream, derive locally
                self._refresh_cell_extrema_from_array()

            elif msg_key == 0xFF03:  # Tmin/Tmax/Tavg (i16) + IDs
                self._tel.t_min_c = _temp_c_u16(_u16_be(data, 0))
                self._tel.t_max_c = _temp_c_u16(_u16_be(data, 2))

            elif msg_key == 0xFF04:  # DTC — 8× one-byte FC codes
                from bts.dtc_catalog import parse_dtc_message_bytes

                self._tel.active_dtcs = [f"FC{c}" for c in parse_dtc_message_bytes(data)]

            elif msg_key == 0xFF06:  # Amp-hours
                self._tel.ah_charge = float(_u32_be(data, 0))
                self._tel.ah_discharge = float(_u32_be(data, 4))

            elif 0xFF10 <= msg_key <= 0xFF8F:  # Cell voltages — 4× u16 BE
                idx = (msg_key - 0xFF10) * 4
                while len(self._tel.cell_voltages) < idx + 4:
                    self._tel.cell_voltages.append(float("nan"))
                for i in range(4):
                    self._tel.cell_voltages[idx + i] = _cell_voltage_v(_u16_be(data, i * 2))
                self._refresh_cell_extrema_from_array()

            elif 0xFF90 <= msg_key <= 0xFFCF:  # Temperatures — 4× i16 BE @ 1/64 °C
                idx = (msg_key - 0xFF90) * 4
                while len(self._tel.temperatures_c) < idx + 4:
                    self._tel.temperatures_c.append(float("nan"))
                for i in range(4):
                    t = _temp_c_u16(_u16_be(data, i * 2))
                    self._tel.temperatures_c[idx + i] = float("nan") if t is None else t

            elif 0xFFD0 <= msg_key <= 0xFFDF:  # Cell-balance bitmaps — 8 bytes/msg
                base = (msg_key - 0xFFD0) * 64  # 8 bytes × 8 bits
                needed = base + 64
                while len(self._tel.cell_balancing) < needed:
                    self._tel.cell_balancing.append(False)
                for bi, byte in enumerate(data[:8]):
                    for bit in range(8):
                        self._tel.cell_balancing[base + bi * 8 + bit] = bool(byte & (1 << bit))

    def _refresh_cell_extrema_from_array(self) -> None:
        """Fill missing / invalid Vmin/Vmax from the cell stream."""
        valid = [
            (i, v)
            for i, v in enumerate(self._tel.cell_voltages)
            if isinstance(v, float) and v == v and v > 0.05
        ]
        if not valid:
            return
        i_min, v_min = min(valid, key=lambda t: t[1])
        i_max, v_max = max(valid, key=lambda t: t[1])

        def _bad(v: float | None) -> bool:
            return v is None or not (v == v) or v <= 0.05

        if _bad(self._tel.cell_v_min):
            self._tel.cell_v_min = v_min
            self._tel.cell_vmin_id = i_min
        if _bad(self._tel.cell_v_max):
            self._tel.cell_v_max = v_max
            self._tel.cell_vmax_id = i_max
        if self._tel.cell_vmin_id is None or self._tel.cell_vmin_id > 2000:
            self._tel.cell_vmin_id = i_min
        if self._tel.cell_vmax_id is None or self._tel.cell_vmax_id > 2000:
            self._tel.cell_vmax_id = i_max


class MockBmsDriver(BmsDriver):
    """Simulated BMU for offline UI / engine development."""

    def __init__(self, cfg: BmsConfig | None = None) -> None:
        self.cfg = cfg or BmsConfig()
        self._lock = threading.RLock()
        self._tel = BmsTelemetry(
            operating_state=BmuState.IDLE,
            soc_pct=50.0,
            pack_voltage_v=24.0,
            pack_current_a=0.0,
            charge_current_limit_a=500.0,
            discharge_current_limit_a=500.0,
            cell_v_min=2.2,
            cell_v_max=2.3,
            t_min_c=25.0,
            t_max_c=28.0,
            ah_charge=0.0,
            ah_discharge=0.0,
            cell_voltages=[2.25] * 10,
            temperatures_c=[26.0, 27.0, 28.0],
            connected=False,
            last_rx_s=time.monotonic(),
        )
        self._desired = DesiredState.IDLE
        self._running = False
        self._thread: threading.Thread | None = None
        self._charge_rate = 0.0  # A applied by engine via helper
        self._hold_preview = False
        self.on_state_change: Callable[[BmuState], None] | None = None

    def connect(self) -> None:
        with self._lock:
            self._tel.connected = True
            self._tel.last_rx_s = time.monotonic()
            self._tel.rx_count = max(1, self._tel.rx_count)

    def disconnect(self) -> None:
        self.stop()
        self._hold_preview = False
        with self._lock:
            self._tel.connected = False
            self._tel.rx_count = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._sim_loop, name="mock-bms", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def set_desired_state(self, state: DesiredState) -> None:
        with self._lock:
            self._hold_preview = False  # real commands take over
            self._desired = state

    def set_external_current(self, amps: float) -> None:
        """Called by mock EA coupling: positive = charge into pack."""
        with self._lock:
            self._charge_rate = amps

    def clear_ui_preview(self) -> None:
        """Drop Connect-time demo so a real sequence can run."""
        with self._lock:
            self._hold_preview = False
            self._desired = DesiredState.IDLE
            self._charge_rate = 0.0
            self._tel.operating_state = BmuState.IDLE
            self._tel.contactor_cmd = ContactorBits()
            self._tel.contactor_status = ContactorBits()
            self._tel.pack_current_a = 0.0

    def apply_ui_preview(self, scenario: str, *, current_a: float = 70.0) -> str:
        """Freeze a demo bench state for UI checks. Returns scenario name."""
        scenario = scenario if scenario in MOCK_PREVIEW_SCENARIOS else random.choice(MOCK_PREVIEW_SCENARIOS)
        soc = random.uniform(25.0, 85.0)
        with self._lock:
            self._hold_preview = True
            self._tel.soc_pct = soc
            self._tel.pack_voltage_v = 17.0 + (soc / 100.0) * (27.5 - 17.0)
            self._tel.t_min_c = random.uniform(22.0, 28.0)
            self._tel.t_max_c = self._tel.t_min_c + random.uniform(1.0, 4.0)
            self._tel.temperatures_c = [
                self._tel.t_min_c,
                (self._tel.t_min_c + self._tel.t_max_c) / 2,
                self._tel.t_max_c,
            ]
            n = 10
            base = 1.7 + (soc / 100.0) * 0.9
            self._tel.cell_voltages = [base + ((k % 5) - 2) * 0.008 for k in range(n)]
            self._tel.cell_v_min = min(self._tel.cell_voltages)
            self._tel.cell_v_max = max(self._tel.cell_voltages)
            self._tel.cell_vmin_id = self._tel.cell_voltages.index(self._tel.cell_v_min)
            self._tel.cell_vmax_id = self._tel.cell_voltages.index(self._tel.cell_v_max)

            if scenario == "idle":
                self._desired = DesiredState.IDLE
                self._tel.operating_state = BmuState.IDLE
                self._tel.contactor_cmd = ContactorBits()
                self._tel.contactor_status = ContactorBits()
                self._charge_rate = 0.0
                self._tel.pack_current_a = 0.0
            elif scenario == "precharge":
                self._desired = DesiredState.READY
                self._tel.operating_state = BmuState.PRE_CHARGE
                bits = ContactorBits(precharge=True)
                self._tel.contactor_cmd = bits
                self._tel.contactor_status = bits
                self._charge_rate = 0.0
                self._tel.pack_current_a = 0.0
            elif scenario == "ready":
                self._desired = DesiredState.READY
                self._tel.operating_state = BmuState.READY
                bits = ContactorBits(main_pos=True, main_neg=True)
                self._tel.contactor_cmd = bits
                self._tel.contactor_status = bits
                self._charge_rate = 0.0
                self._tel.pack_current_a = 0.0
            elif scenario == "charge":
                self._desired = DesiredState.READY
                self._tel.operating_state = BmuState.READY
                bits = ContactorBits(main_pos=True, main_neg=True)
                self._tel.contactor_cmd = bits
                self._tel.contactor_status = bits
                self._charge_rate = abs(current_a)
                self._tel.pack_current_a = self._charge_rate
            else:  # discharge
                self._desired = DesiredState.READY
                self._tel.operating_state = BmuState.READY
                bits = ContactorBits(main_pos=True, main_neg=True)
                self._tel.contactor_cmd = bits
                self._tel.contactor_status = bits
                self._charge_rate = -abs(current_a)
                self._tel.pack_current_a = self._charge_rate
        return scenario

    def telemetry(self) -> BmsTelemetry:
        with self._lock:
            self._tel.desired_state = self._desired
            # Deep copy: the RX thread keeps mutating list fields
            # (cell_voltages, temperatures_c, …) in place, so a shallow
            # copy would alias them and race with consumers iterating them.
            return copy.deepcopy(self._tel)

    def is_healthy(self, timeout_s: float) -> bool:
        with self._lock:
            if not self._tel.connected or self._tel.rx_count <= 0:
                return False
            return (time.monotonic() - self._tel.last_rx_s) <= timeout_s

    def health_detail(self) -> str:
        return "mock BMU"

    def request_dtc_reset(self) -> None:
        with self._lock:
            self._tel.active_dtcs = []
            self._tel.dtc_level = 0
        log.info("Mock BMS: DTCs cleared")

    def _sim_loop(self) -> None:
        last = time.monotonic()
        while self._running:
            now = time.monotonic()
            dt = now - last
            last = now
            with self._lock:
                self._tel.last_rx_s = now
                self._tel.rx_count += 1
                if not self._hold_preview:
                    # State machine
                    if self._desired == DesiredState.READY:
                        if self._tel.operating_state == BmuState.IDLE:
                            self._tel.operating_state = BmuState.PRE_CHARGE
                            self._tel.contactor_cmd = ContactorBits(precharge=True)
                            self._tel.contactor_status = ContactorBits(precharge=True)
                        elif self._tel.operating_state == BmuState.PRE_CHARGE:
                            self._tel.operating_state = BmuState.READY
                            self._tel.contactor_cmd = ContactorBits(main_pos=True, main_neg=True)
                            self._tel.contactor_status = ContactorBits(main_pos=True, main_neg=True)
                    elif self._desired == DesiredState.IDLE:
                        if self._tel.operating_state == BmuState.READY:
                            self._tel.operating_state = BmuState.STOPPING
                        elif self._tel.operating_state in (BmuState.STOPPING, BmuState.PRE_CHARGE):
                            self._tel.operating_state = BmuState.IDLE
                            self._tel.contactor_cmd = ContactorBits()
                            self._tel.contactor_status = ContactorBits()
                # Integrate Ah / SOC when READY (or preview charge/discharge)
                if self._tel.operating_state == BmuState.READY and abs(self._charge_rate) > 0.01:
                    i = self._charge_rate
                    self._tel.pack_current_a = i
                    dah = i * dt / 3600.0
                    if i > 0:
                        self._tel.ah_charge = (self._tel.ah_charge or 0) + dah
                        self._tel.soc_pct = min(100.0, (self._tel.soc_pct or 0) + dah / 70.0 * 100)
                    else:
                        self._tel.ah_discharge = (self._tel.ah_discharge or 0) + abs(dah)
                        self._tel.soc_pct = max(0.0, (self._tel.soc_pct or 0) + dah / 70.0 * 100)
                    soc = (self._tel.soc_pct or 50) / 100.0
                    self._tel.pack_voltage_v = 17.0 + soc * (27.5 - 17.0) + i * 0.004
                    # Full cell array (requested via App Command bit in real HW)
                    n = max(10, len(self._tel.cell_voltages) or 10)
                    base = 1.7 + soc * 0.9
                    self._tel.cell_voltages = [base + ((k % 5) - 2) * 0.008 for k in range(n)]
                    self._tel.cell_v_min = min(self._tel.cell_voltages)
                    self._tel.cell_v_max = max(self._tel.cell_voltages)
                else:
                    self._tel.pack_current_a = 0.0
                    # Keep broadcasting cells even at idle (as if request bit set)
                    if self._tel.cell_voltages:
                        soc = (self._tel.soc_pct or 50) / 100.0
                        base = 1.7 + soc * 0.9
                        n = len(self._tel.cell_voltages)
                        self._tel.cell_voltages = [base + ((k % 5) - 2) * 0.008 for k in range(n)]
                        self._tel.cell_v_min = min(self._tel.cell_voltages)
                        self._tel.cell_v_max = max(self._tel.cell_voltages)
            time.sleep(0.1)


def create_bms_driver(cfg: BmsConfig, use_mock: bool) -> BmsDriver:
    if use_mock:
        return MockBmsDriver(cfg)
    return BmsCanDriver(cfg)