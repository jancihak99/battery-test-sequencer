"""Live one-line schematic: PSI / EL ↔ control-box contactors ↔ pack."""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from bts.models.telemetry import BmsTelemetry, BmuState, EaTelemetry
from bts.ui.theme import ACCENT, BORDER, CHART_BG, OK, TEXT, TEXT_DIM

# Animation reference: today's full particle speed == 6C. 0 A = frozen.
_MAX_C_RATE = 6.0
_BASE_PHASE_STEP = 0.015  # phase advance per ~33 ms tick at 6C


@dataclass
class _FlowPath:
    points: list[QPointF]
    color: QColor
    active: bool
    speed: float = 1.0  # multiplier on shared phase


def _lerp(a: QPointF, b: QPointF, t: float) -> QPointF:
    return QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)


def _path_length(pts: list[QPointF]) -> float:
    total = 0.0
    for i in range(1, len(pts)):
        dx = pts[i].x() - pts[i - 1].x()
        dy = pts[i].y() - pts[i - 1].y()
        total += math.hypot(dx, dy)
    return max(1.0, total)


def _point_on_path(pts: list[QPointF], t: float) -> QPointF:
    """t in [0,1] along polyline."""
    t = max(0.0, min(1.0, t))
    target = t * _path_length(pts)
    walked = 0.0
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        seg = math.hypot(b.x() - a.x(), b.y() - a.y())
        if walked + seg >= target or i == len(pts) - 1:
            local = 0.0 if seg < 1e-6 else (target - walked) / seg
            return _lerp(a, b, max(0.0, min(1.0, local)))
        walked += seg
    return pts[-1]


def _crate_frac(amps: float, capacity_ah: float) -> float:
    """0 at 0 A … 1.0 at ≥ 6C (relative to pack nominal Ah)."""
    if capacity_ah <= 0.05 or amps <= 0.05:
        return 0.0
    return max(0.0, min(1.0, abs(amps) / capacity_ah / _MAX_C_RATE))


class WiringSchematic(QWidget):
    """Animated bench schematic for live current + contactor state."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(188)
        self.setMaximumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._bms: BmsTelemetry | None = None
        self._ea: EaTelemetry | None = None
        self._capacity_ah = 70.0
        self._phase = 0.0
        self._glow_phase = 0.0
        self._flow_frac = 0.0  # smoothed 0…1 for visuals
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_capacity_ah(self, ah: float) -> None:
        self._capacity_ah = max(1.0, float(ah))

    def _measured_amps(self) -> float:
        ea = self._ea
        bms = self._bms
        amps = 0.0
        if ea and ea.connected:
            if ea.psi_output_on or abs(ea.psi_current_a) > 0.3:
                amps = max(amps, abs(float(ea.psi_current_a)))
            if ea.el_input_on or abs(ea.el_current_a) > 0.3:
                amps = max(amps, abs(float(ea.el_current_a)))
        if bms and bms.pack_current_a is not None:
            amps = max(amps, abs(float(bms.pack_current_a)))
        return amps

    def _tick(self) -> None:
        amps = self._measured_amps()
        frac = _crate_frac(amps, self._capacity_ah)
        # Smooth so animation doesn't stutter on noisy MEAS
        self._flow_frac += (frac - self._flow_frac) * 0.22
        if self._flow_frac < 0.008:
            self._flow_frac = 0.0

        # Soft ambient for contactors / wait (always slow breathe)
        self._glow_phase = (self._glow_phase + 0.045) % (math.pi * 2)

        if self._flow_frac > 0:
            # 6C → full _BASE_PHASE_STEP; 3C → half; 0 → freeze
            self._phase = (self._phase + _BASE_PHASE_STEP * self._flow_frac) % 1.0
            self.update()
        else:
            # Still refresh for contactor / idle breathe
            self.update()

    def set_telemetry(self, bms: BmsTelemetry | None, ea: EaTelemetry | None) -> None:
        self._bms = bms
        self._ea = ea
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(CHART_BG))

        # Soft top wash when power is flowing
        if self._flow_frac > 0.02:
            wash = QColor(OK if self._is_charging() else "#c47a3a")
            wash.setAlpha(int(10 + 22 * self._flow_frac))
            grad = QRadialGradient(w * 0.55, h * 0.5, w * 0.55)
            grad.setColorAt(0.0, wash)
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(self.rect(), grad)

        pack_x = 18
        box_x = w * 0.30
        right_x = w - 18 - 86
        mid_y = h * 0.50
        psi_y = h * 0.26
        el_y = h * 0.74

        device_w, device_h = 86, 38
        box_w, box_h = 168, 100
        pack_w, pack_h = 112, 76

        pack_rect = QRectF(pack_x, mid_y - pack_h / 2, pack_w, pack_h)
        box_rect = QRectF(box_x, mid_y - box_h / 2, box_w, box_h)
        psi_rect = QRectF(right_x, psi_y - device_h / 2, device_w, device_h)
        el_rect = QRectF(right_x, el_y - device_h / 2, device_w, device_h)

        bms = self._bms
        ea = self._ea
        st = bms.contactors_effective if bms else None
        main_pos = bool(st and st.main_pos)
        main_neg = bool(st and st.main_neg)
        precharge = bool(st and st.precharge)
        connected_bms = bool(bms and bms.connected)
        connected_ea = bool(ea and ea.connected)

        pack_i = float(bms.pack_current_a) if bms and bms.pack_current_a is not None else 0.0
        charging = self._is_charging()
        discharging = self._is_discharging()
        if charging and discharging:
            if ea and abs(ea.el_current_a) >= abs(ea.psi_current_a):
                charging = False
            else:
                discharging = False
        bus_live = main_pos and main_neg and (charging or discharging)

        charge_color = QColor(OK)
        discharge_color = QColor("#c47a3a")
        idle_wire = QColor("#b0bac4")
        live_wire = charge_color if charging else (discharge_color if discharging else idle_wire)

        pack_port = QPointF(pack_rect.right(), mid_y)
        box_left = QPointF(box_rect.left(), mid_y)
        box_right = QPointF(box_rect.right(), mid_y)
        bus_junction = QPointF((box_rect.right() + psi_rect.left()) * 0.5, mid_y)
        psi_in = QPointF(psi_rect.left(), psi_rect.center().y())
        el_in = QPointF(el_rect.left(), el_rect.center().y())
        box_mid = QPointF(box_rect.center().x(), mid_y)

        to_box = [pack_port, box_left]
        out_of_box = [box_right, bus_junction]
        psi_drop = [bus_junction, QPointF(bus_junction.x(), psi_in.y()), psi_in]
        el_drop = [bus_junction, QPointF(bus_junction.x(), el_in.y()), el_in]

        def draw_wire(pts: list[QPointF], color: QColor, width: float = 2.4, active: bool = False) -> None:
            if active and self._flow_frac > 0.05:
                glow = QColor(color)
                glow.setAlpha(int(35 + 55 * self._flow_frac))
                pen_g = QPen(glow)
                pen_g.setWidthF(width + 3.5 + 2.5 * self._flow_frac)
                pen_g.setCapStyle(Qt.RoundCap)
                pen_g.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen_g)
                for i in range(1, len(pts)):
                    p.drawLine(pts[i - 1], pts[i])
            pen = QPen(color)
            pen.setWidthF(width)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            for i in range(1, len(pts)):
                p.drawLine(pts[i - 1], pts[i])

        draw_wire(to_box, live_wire if bus_live else idle_wire, 2.8 if bus_live else 2.0, bus_live)
        draw_wire(out_of_box, live_wire if bus_live else idle_wire, 2.8 if bus_live else 2.0, bus_live)
        draw_wire(psi_drop, charge_color if charging else idle_wire, 2.6 if charging else 2.0, charging)
        draw_wire(el_drop, discharge_color if discharging else idle_wire, 2.6 if discharging else 2.0, discharging)

        # Junction
        j_r = 4.0 + (2.5 * self._flow_frac if bus_live else 0.0)
        p.setPen(Qt.NoPen)
        if bus_live and self._flow_frac > 0:
            jg = QColor(live_wire)
            jg.setAlpha(int(60 + 80 * self._flow_frac))
            p.setBrush(jg)
            p.drawEllipse(bus_junction, j_r + 4, j_r + 4)
        p.setBrush(live_wire if bus_live else QColor("#8a96a3"))
        p.drawEllipse(bus_junction, j_r, j_r)

        flows: list[_FlowPath] = []
        if charging and main_pos and main_neg:
            flows.append(
                _FlowPath(
                    [
                        psi_in,
                        QPointF(bus_junction.x(), psi_in.y()),
                        bus_junction,
                        box_right,
                        box_mid,
                        box_left,
                        pack_port,
                    ],
                    charge_color,
                    True,
                )
            )
        elif discharging and main_pos and main_neg:
            flows.append(
                _FlowPath(
                    [
                        pack_port,
                        box_left,
                        box_mid,
                        box_right,
                        bus_junction,
                        QPointF(bus_junction.x(), el_in.y()),
                        el_in,
                    ],
                    discharge_color,
                    True,
                )
            )
        elif precharge and not main_pos:
            flows.append(
                _FlowPath(
                    [
                        QPointF(box_rect.center().x(), box_rect.top() + 28),
                        QPointF(box_rect.left() + 8, box_rect.top() + 28),
                        box_left,
                        pack_port,
                    ],
                    QColor("#d4a017"),
                    True,
                    speed=0.45,
                )
            )

        for flow in flows:
            self._draw_flow(p, flow)

        amps = self._measured_amps()
        crate = amps / self._capacity_ah if self._capacity_ah > 0 else 0.0

        self._draw_pack(
            p,
            pack_rect,
            connected=connected_bms,
            soc=bms.soc_pct if bms else None,
            volts=bms.pack_voltage_v if bms else None,
            amps=bms.pack_current_a if bms else None,
            charging=charging,
            discharging=discharging,
            flow_frac=self._flow_frac,
        )
        self._draw_control_box(
            p,
            box_rect,
            main_pos=main_pos,
            main_neg=main_neg,
            precharge=precharge,
            connected=connected_bms,
            state=bms.operating_state if bms and connected_bms else None,
            pulse=self._glow_phase,
        )
        self._draw_device(
            p,
            psi_rect,
            "Zdroj",
            self._ea_detail(ea, source=True, active=charging, pack_i=pack_i),
            active=charging,
            accent=charge_color,
            flow_frac=self._flow_frac if charging else 0.0,
        )
        self._draw_device(
            p,
            el_rect,
            "Zátěž",
            self._ea_detail(ea, source=False, active=discharging, pack_i=pack_i),
            active=discharging,
            accent=discharge_color,
            flow_frac=self._flow_frac if discharging else 0.0,
        )

        # Status chip + caption
        p.setFont(QFont("Segoe UI", 8))
        caption = self._caption(
            charging, discharging, main_pos, main_neg, precharge, connected_bms, amps, crate
        )
        p.setPen(QColor(TEXT_DIM))
        p.drawText(QRectF(8, h - 20, w - 16, 16), Qt.AlignLeft | Qt.AlignVCenter, caption)

    def _is_charging(self) -> bool:
        ea = self._ea
        bms = self._bms
        pack_i = float(bms.pack_current_a) if bms and bms.pack_current_a is not None else 0.0
        st = bms.contactors_effective if bms else None
        main_ok = bool(st and st.main_pos and st.main_neg)
        if ea and ea.connected and (ea.psi_output_on or abs(ea.psi_current_a) > 0.5):
            return True
        return bool(
            bms
            and bms.connected
            and main_ok
            and pack_i > 2.0
            and not (ea and ea.connected and (ea.el_input_on or abs(ea.el_current_a) > 0.5))
        )

    def _is_discharging(self) -> bool:
        ea = self._ea
        bms = self._bms
        pack_i = float(bms.pack_current_a) if bms and bms.pack_current_a is not None else 0.0
        st = bms.contactors_effective if bms else None
        main_ok = bool(st and st.main_pos and st.main_neg)
        if ea and ea.connected and (ea.el_input_on or abs(ea.el_current_a) > 0.5):
            return True
        return bool(bms and bms.connected and main_ok and pack_i < -2.0)

    def _caption(
        self,
        charging: bool,
        discharging: bool,
        main_pos: bool,
        main_neg: bool,
        precharge: bool,
        connected: bool,
        amps: float,
        crate: float,
    ) -> str:
        if not connected:
            return "Zapojení: modul ↔ control box (Main+ / Main− / Precharge) ↔ zdroj + zátěž"
        rate = f"  ·  {amps:.0f} A ({crate:.2f}C)" if amps > 0.5 else ""
        if charging:
            return f"Nabíjení — tok zdroj → stykače → pack{rate}"
        if discharging:
            return f"Vybíjení — tok pack → stykače → zátěž{rate}"
        if precharge and not main_pos:
            return "Precharge aktivní — Main+ ještě otevřený"
        if main_pos and main_neg:
            return "Stykače CLOSED · EA idle (žádný tok)"
        return "Stykače OPEN · žádný výkonový tok"

    @staticmethod
    def _ea_detail(
        ea: EaTelemetry | None,
        *,
        source: bool,
        active: bool = False,
        pack_i: float = 0.0,
    ) -> str:
        if ea is not None and ea.connected:
            if source:
                v, i = ea.psi_voltage_v, ea.psi_current_a
                if ea.psi_output_on or abs(i) > 0.5 or active:
                    return f"{v:.1f} V · {i:.0f} A"
                return f"{v:.1f} V · OFF"
            v, i = ea.el_voltage_v, ea.el_current_a
            if ea.el_input_on or abs(i) > 0.5 or active:
                return f"{v:.1f} V · {abs(i):.0f} A"
            return f"{v:.1f} V · OFF"
        if active and source and pack_i > 2.0:
            return f"ON · ~{pack_i:.0f} A"
        if active and not source and pack_i < -2.0:
            return f"ON · ~{abs(pack_i):.0f} A"
        return "—"

    def _draw_flow(self, p: QPainter, flow: _FlowPath) -> None:
        """Comet-style particles; speed already in shared _phase (scaled by C-rate)."""
        if not flow.active or len(flow.points) < 2:
            return
        # Precharge without load: slow crawl even at flow_frac 0
        frac = self._flow_frac
        if frac <= 0 and flow.speed < 1.0:
            # Static dashed hint for precharge path
            frac = 0.12
        if frac <= 0:
            return

        n = 4 + int(4 * frac)  # 4…8 particles
        head = QColor(flow.color)
        for i in range(n):
            t = (self._phase * flow.speed + i / n) % 1.0
            pt = _point_on_path(flow.points, t)
            # Soft halo
            halo = QColor(flow.color)
            halo.setAlpha(int(40 + 50 * frac))
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(pt, 5.5 + 2.0 * frac, 5.5 + 2.0 * frac)
            # Core
            head.setAlpha(int(160 + 70 * frac))
            p.setBrush(head)
            r = 2.2 + 1.4 * frac
            p.drawEllipse(pt, r, r)

    def _draw_device(
        self,
        p: QPainter,
        rect: QRectF,
        title: str,
        subtitle: str,
        *,
        active: bool,
        accent: QColor,
        flow_frac: float = 0.0,
    ) -> None:
        bg = QColor("#ffffff")
        border = accent if active else QColor(BORDER)
        p.setPen(QPen(border, 1.8 if active else 1.2))
        p.setBrush(bg)
        p.drawRoundedRect(rect, 7, 7)
        if active:
            pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(self._glow_phase * (1.0 + flow_frac)))
            glow = QColor(accent)
            glow.setAlpha(int((22 + 40 * flow_frac) * pulse))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 5, 5)

        p.setPen(QColor(TEXT))
        p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        p.drawText(rect.adjusted(8, 4, -8, -14), Qt.AlignLeft | Qt.AlignTop, title)
        p.setPen(QColor(TEXT_DIM))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(rect.adjusted(8, 18, -8, -2), Qt.AlignLeft | Qt.AlignTop, subtitle)

        led = QColor(accent) if active else QColor("#c5ced8")
        if active and flow_frac > 0:
            breath = 0.65 + 0.35 * (0.5 + 0.5 * math.sin(self._glow_phase * 2.2))
            led.setAlpha(int(120 + 135 * breath))
        p.setPen(Qt.NoPen)
        p.setBrush(led)
        p.drawEllipse(QPointF(rect.right() - 12, rect.top() + 12), 4.5 if active else 3.5, 4.5 if active else 3.5)

    def _draw_control_box(
        self,
        p: QPainter,
        rect: QRectF,
        *,
        main_pos: bool,
        main_neg: bool,
        precharge: bool,
        connected: bool,
        state: BmuState | None,
        pulse: float,
    ) -> None:
        p.setPen(QPen(QColor(BORDER), 1.3))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(rect, 8, 8)

        if connected and state == BmuState.READY and main_pos and main_neg:
            ready_glow = QColor(OK)
            ready_glow.setAlpha(int(18 + 14 * (0.5 + 0.5 * math.sin(pulse))))
            p.setPen(Qt.NoPen)
            p.setBrush(ready_glow)
            p.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 6, 6)
        elif connected and state == BmuState.PRE_CHARGE:
            pc = QColor("#d4a017")
            pc.setAlpha(int(20 + 16 * (0.5 + 0.5 * math.sin(pulse * 1.4))))
            p.setPen(Qt.NoPen)
            p.setBrush(pc)
            p.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 6, 6)

        p.setPen(QColor(TEXT_DIM))
        p.setFont(QFont("Segoe UI", 8, QFont.DemiBold))
        header = "Control box · stykače"
        if state is not None:
            header = f"Control box · BMU {state.name}"
        p.drawText(rect.adjusted(10, 6, -10, 0), Qt.AlignLeft | Qt.AlignTop, header)

        y = rect.center().y() + 4
        n = 3
        margin = 22.0
        usable = rect.width() - 2 * margin
        xs = [rect.left() + margin + usable * (i + 0.5) / n for i in range(n)]
        self._draw_contactor(p, QPointF(xs[0], y), "Main+", main_pos, connected, pulse=pulse)
        self._draw_contactor(p, QPointF(xs[1], y), "Main−", main_neg, connected, pulse=pulse)
        self._draw_contactor(
            p, QPointF(xs[2], y), "Prech.", precharge, connected, warn=True, pulse=pulse
        )

    def _draw_contactor(
        self,
        p: QPainter,
        origin: QPointF,
        label: str,
        closed: bool,
        connected: bool,
        *,
        warn: bool = False,
        pulse: float = 0.0,
    ) -> None:
        if not connected:
            color = QColor("#b0bac4")
        elif closed:
            color = QColor(OK) if not warn else QColor("#d4a017")
        else:
            color = QColor("#c0392b") if not warn else QColor("#b0bac4")

        cx, cy = origin.x(), origin.y()
        if connected and closed:
            halo = QColor(color)
            halo.setAlpha(int(50 + 40 * (0.5 + 0.5 * math.sin(pulse * 1.6))))
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(QPointF(cx, cy), 11, 11)

        p.setPen(QPen(color, 2.1))
        p.drawLine(QPointF(cx, cy - 10), QPointF(cx, cy - 2))
        p.drawLine(QPointF(cx, cy + 10), QPointF(cx, cy + 2))
        if closed:
            p.drawLine(QPointF(cx, cy - 2), QPointF(cx, cy + 2))
        else:
            p.drawLine(QPointF(cx, cy - 2), QPointF(cx + 9, cy + 4))

        p.setPen(QColor(TEXT if connected else TEXT_DIM))
        p.setFont(QFont("Segoe UI", 8, QFont.DemiBold))
        p.drawText(QRectF(cx - 22, cy + 14, 44, 14), Qt.AlignCenter, label)
        p.setFont(QFont("Segoe UI", 7))
        p.setPen(color)
        state = "—" if not connected else ("CLOSED" if closed else "OPEN")
        p.drawText(QRectF(cx - 26, cy + 26, 52, 12), Qt.AlignCenter, state)

    def _draw_pack(
        self,
        p: QPainter,
        rect: QRectF,
        *,
        connected: bool,
        soc: float | None,
        volts: float | None,
        amps: float | None,
        charging: bool,
        discharging: bool,
        flow_frac: float,
    ) -> None:
        accent = QColor(OK) if charging else (QColor("#c47a3a") if discharging else QColor(ACCENT))
        p.setPen(QPen(accent if (charging or discharging) else QColor(BORDER), 1.6))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(rect, 8, 8)

        if (charging or discharging) and flow_frac > 0:
            g = QColor(accent)
            g.setAlpha(int(18 + 36 * flow_frac * (0.5 + 0.5 * math.sin(self._glow_phase))))
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)

        bx = rect.left() + 14
        by = rect.center().y() - 10
        p.setPen(QPen(accent, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(bx, by, 22, 16), 2, 2)
        p.setBrush(accent)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(bx + 22, by + 4, 3, 8), 1, 1)
        fill = 0.5 if soc is None else max(0.05, min(1.0, soc / 100.0))
        # Gentle fill wave while power flows
        if flow_frac > 0:
            fill = max(0.05, min(1.0, fill + 0.04 * math.sin(self._glow_phase) * flow_frac))
        c = QColor(accent)
        c.setAlpha(150)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(bx + 2, by + 2, 18 * fill, 12), 1, 1)

        p.setPen(QColor(TEXT))
        p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        p.drawText(rect.adjusted(44, 10, -8, 0), Qt.AlignLeft | Qt.AlignTop, "Pack")

        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor(TEXT_DIM))
        if not connected:
            lines = "BMS offline"
        else:
            soc_s = f"{soc:.0f}%" if soc is not None else "—"
            v_s = f"{volts:.1f} V" if volts is not None else "—"
            i_s = f"{amps:.0f} A" if amps is not None else "—"
            lines = f"{soc_s}  ·  {v_s}\n{i_s}"
        p.drawText(rect.adjusted(44, 28, -8, -8), Qt.AlignLeft | Qt.AlignTop, lines)
