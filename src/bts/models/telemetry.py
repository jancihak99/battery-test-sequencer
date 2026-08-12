from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import Any


class BmuState(IntEnum):
    POWER_UP = 1
    IDLE = 2
    PRE_CHARGE = 3
    READY = 4
    STOPPING = 5
    POWER_DOWN_PREP = 6
    POWER_DOWN_READY = 7
    UNKNOWN = 0


class DesiredState(IntEnum):
    IDLE = 2
    READY = 4
    POWER_DOWN_READY = 7


@dataclass
class ContactorBits:
    """Contactor command/status from Pack Voltage & Current message (0x18FF01xx)."""

    main_pos: bool = False
    main_neg: bool = False
    precharge: bool = False
    mid_pack: bool = False
    aux: bool = False

    @classmethod
    def from_byte(cls, b: int, *, status: bool = False) -> ContactorBits:
        return cls(
            main_pos=bool(b & (1 << 0)),
            main_neg=bool(b & (1 << 1)),
            precharge=bool(b & (1 << 2)),
            mid_pack=bool(b & (1 << 3)),
            aux=bool(b & (1 << (5 if status else 4))),
        )

    @property
    def mains_closed(self) -> bool:
        return self.main_pos or self.main_neg


@dataclass
class BmsTelemetry:
    connected: bool = False  # bus/handle open (not proof of BMU traffic)
    last_rx_s: float = 0.0
    rx_count: int = 0  # extended frames received since connect
    operating_state: BmuState = BmuState.UNKNOWN
    desired_state: DesiredState | None = None
    pack_voltage_v: float | None = None
    pack_current_a: float | None = None
    pack_voltage_switched_v: float | None = None
    soc_pct: float | None = None
    soh_pct: float | None = None
    ah_charge: float | None = None
    ah_discharge: float | None = None
    cell_voltages: list[float] = field(default_factory=list)
    cell_v_min: float | None = None
    cell_v_max: float | None = None
    cell_vmin_id: int | None = None
    cell_vmax_id: int | None = None
    temperatures_c: list[float] = field(default_factory=list)
    cell_balancing: list[bool] = field(default_factory=list)
    t_min_c: float | None = None
    t_max_c: float | None = None
    dtc_level: int = 0
    active_dtcs: list[str] = field(default_factory=list)
    charge_current_limit_a: float | None = None
    discharge_current_limit_a: float | None = None
    contactor_cmd: ContactorBits = field(default_factory=ContactorBits)
    contactor_status: ContactorBits = field(default_factory=ContactorBits)
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def contactors_effective(self) -> ContactorBits:
        """Bits for UI: prefer status feedback; if un-instrumented (all 0), use command.

        BMU External CAN: un-instrumented contactor status bits are forced to 0.
        Lab packs often have no aux feedback — command bitmap still tracks close/open.
        """
        st = self.contactor_status
        if st.main_pos or st.main_neg or st.precharge or st.mid_pack or st.aux:
            return st
        return self.contactor_cmd

    @property
    def mains_energized(self) -> bool:
        """Physical proof the main contactors are closed, independent of the bitmaps.

        The switched (load-side) pack voltage tracking the unswitched pack voltage means
        current can flow through the mains — the same comparison the BMU itself uses for
        its contactor-weld / closed diagnostics (see FC "switched ≠ unswitched"). Robust
        when the command/status bitmaps are unreliable, e.g. a second ACU on the bus
        closes the contactors while our IDLE heartbeat keeps flipping the command bitmap
        back to open on an un-instrumented pack.
        """
        vp = self.pack_voltage_v
        vs = self.pack_voltage_switched_v
        if vp is None or vs is None or vp <= 1.0:
            return False
        return vs >= 0.9 * vp

    @property
    def contactors_for_display(self) -> ContactorBits:
        """UI-only view of contactors_effective, with mains forced closed when the
        switched pack voltage proves they are (see mains_energized).

        Fixes the case where an independent tool (CANking, service tool) closes the
        contactors via the BMU: our idle heartbeat fights it, so the reported command
        bitmap flickers to open and the pack shows OPEN even though it is physically
        connected. Display only — control/engine logic keeps using contactors_effective.
        Caveat: at rest a precharge-only bus also equalises, so this can read the mains
        as closed during the brief precharge window; acceptable for a status indicator.
        """
        eff = self.contactors_effective
        if self.mains_energized and not (eff.main_pos and eff.main_neg):
            return replace(eff, main_pos=True, main_neg=True)
        return eff

    @property
    def external_contactor_override(self) -> bool:
        """We asked the BMU to open the mains (desired ≠ READY) yet the switched bus is
        still live — i.e. another controller on the CAN bus is holding the contactors
        closed against our command. None desired_state = unknown, so we don't claim it.
        Callers should debounce this: a normal power-down keeps the bus live for a few
        seconds while STOPPING waits for current to decay.
        """
        ds = self.desired_state
        if ds is None or ds == DesiredState.READY:
            return False
        return self.mains_energized


@dataclass
class EaTelemetry:
    psi_voltage_v: float = 0.0
    psi_current_a: float = 0.0
    psi_output_on: bool = False
    el_voltage_v: float = 0.0
    el_current_a: float = 0.0
    el_input_on: bool = False
    connected: bool = False
    last_rx_s: float = 0.0
    alarms: list[str] = field(default_factory=list)
    # Commanded path (filled by rack when known)
    active_mode: str | None = None  # charge | discharge | None
    set_current_a: float = 0.0


@dataclass
class RunMeasurements:
    capacity_ah: float | None = None
    dcir_mohm: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)
