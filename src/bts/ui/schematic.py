"""Live one-line schematic: PSI / EL ↔ control-box contactors ↔ pack."""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from bts.models.telemetry import BmsTelemetry, BmuState, EaTelemetry
from bts.ui.theme import ACCENT, BORDER, CHART_BG, OK, TEXT, TEXT_DIM


@dataclass
class _FlowPath:
    points: list[QPointF]
    color: QColor
    active: bool
    speed: float = 1.0  # particles/sec along path


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


class WiringSchematic(QWidget):
    """Animated bench schematic for live current + contactor state."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(168)
        self.setMaximumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self._bms: BmsTelemetry | None = None
        self._ea: EaTelemetry | None = None
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        ea = self._ea
        bms = self._bms
        pack_i = abs(bms.pack_current_a) if bms and bms.pack_current_a is not None else 0.0
        flowing = bool(
            (ea and ea.connected and (ea.psi_output_on or ea.el_input_on or abs(ea.psi_current_a) > 0.5 or abs(ea.el_current_a) > 0.5))
            or pack_i > 1.0
        )
        if flowing:
            # ~0.45 path lengths / second — readable one-way march
            self._phase = (self._phase + 0.015) % 1.0
            self.update()
        # No idle shimmer — only animate when current actually flows

    def set_telemetry(self, bms: BmsTelemetry | None, ea: EaTelemetry | None) -> None:
        self._bms = bms
        self._ea = ea
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(CHART_BG))

        # Layout: pack (left) → control box → PSI / EL (right)
        pack_x = 18
        box_x = w * 0.30
        right_x = w - 18 - 86
        mid_y = h * 0.52
        psi_y = h * 0.28
        el_y = h * 0.76

        device_w, device_h = 86, 36
        box_w, box_h = 168, 96
        pack_w, pack_h = 108, 72

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
        # EA flags + measured current; BMS current as fallback while run caches EA
        charging = bool(
            ea
            and ea.connected
            and (ea.psi_output_on or abs(ea.psi_current_a) > 0.5)
        ) or (connected_bms and main_pos and main_neg and pack_i > 2.0 and not (
            ea and ea.connected and (ea.el_input_on or abs(ea.el_current_a) > 0.5)
        ))
        discharging = bool(
            ea
            and ea.connected
            and (ea.el_input_on or abs(ea.el_current_a) > 0.5)
        ) or (connected_bms and main_pos and main_neg and pack_i < -2.0)
        # Prefer EA mode if both somehow true
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

        # --- wires (bus) ---
        pack_port = QPointF(pack_rect.right(), mid_y)
        box_left = QPointF(box_rect.left(), mid_y)
        box_right = QPointF(box_rect.right(), mid_y)
        # Junction centered in the gap between control box and PSI/EL
        bus_junction = QPointF((box_rect.right() + psi_rect.left()) * 0.5, mid_y)
        psi_in = QPointF(psi_rect.left(), psi_rect.center().y())
        el_in = QPointF(el_rect.left(), el_rect.center().y())
        box_mid = QPointF(box_rect.center().x(), mid_y)

        to_box = [pack_port, box_left]
        out_of_box = [box_right, bus_junction]
        psi_drop = [bus_junction, QPointF(bus_junction.x(), psi_in.y()), psi_in]
        el_drop = [bus_junction, QPointF(bus_junction.x(), el_in.y()), el_in]

        def draw_wire(pts: list[QPointF], color: QColor, width: float = 2.4, active: bool = False) -> None:
            pen = QPen(color)
            pen.setWidthF(width)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            if active:
                pen.setStyle(Qt.SolidLine)
            p.setPen(pen)
            for i in range(1, len(pts)):
                p.drawLine(pts[i - 1], pts[i])

        # Always draw structure
        draw_wire(to_box, live_wire if bus_live else idle_wire, 2.8 if bus_live else 2.0, bus_live)
        draw_wire(out_of_box, live_wire if bus_live else idle_wire, 2.8 if bus_live else 2.0, bus_live)
        draw_wire(psi_drop, charge_color if charging else idle_wire, 2.6 if charging else 2.0, charging)
        draw_wire(el_drop, discharge_color if discharging else idle_wire, 2.6 if discharging else 2.0, discharging)

        # Junction dot
        p.setPen(Qt.NoPen)
        p.setBrush(live_wire if bus_live else QColor("#8a96a3"))
        p.drawEllipse(bus_junction, 4, 4)

        # One continuous flow path → seamless particle loop
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
                    speed=0.6,
                )
            )

        for flow in flows:
            self._draw_flow(p, flow)

        # --- devices ---
        self._draw_pack(
            p,
            pack_rect,
            connected=connected_bms,
            soc=bms.soc_pct if bms else None,
            volts=bms.pack_voltage_v if bms else None,
            amps=bms.pack_current_a if bms else None,
            charging=charging,
            discharging=discharging,
        )
        self._draw_control_box(
            p,
            box_rect,
            main_pos=main_pos,
            main_neg=main_neg,
            precharge=precharge,
            connected=connected_bms,
            state=bms.operating_state if bms and connected_bms else None,
        )
        self._draw_device(
            p,
            psi_rect,
            "Zdroj",
            self._ea_detail(ea, source=True, active=charging, pack_i=pack_i),
            active=charging,
            accent=charge_color,
        )
        self._draw_device(
            p,
            el_rect,
            "Zátěž",
            self._ea_detail(ea, source=False, active=discharging, pack_i=pack_i),
            active=discharging,
            accent=discharge_color,
        )

        # Caption
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor(TEXT_DIM))
        caption = self._caption(charging, discharging, main_pos, main_neg, precharge, connected_bms)
        p.drawText(QRectF(8, h - 18, w - 16, 14), Qt.AlignLeft | Qt.AlignVCenter, caption)

    def _caption(
        self,
        charging: bool,
        discharging: bool,
        main_pos: bool,
        main_neg: bool,
        precharge: bool,
        connected: bool,
    ) -> str:
        if not connected:
            return "Zapojení: modul ↔ control box (Main+ / Main− / Precharge) ↔ zdroj + zátěž"
        if charging:
            return "Tok: zdroj → stykače → pack  (nabíjení)"
        if discharging:
            return "Tok: pack → stykače → zátěž  (vybíjení)"
        if precharge and not main_pos:
            return "Precharge aktivní — Main+ ještě otevřený"
        if main_pos and main_neg:
            return "Stykače CLOSED · EA idle"
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
        # No EA sample (e.g. brief gap) — show inferred from pack current
        if active and source and pack_i > 2.0:
            return f"ON · ~{pack_i:.0f} A"
        if active and not source and pack_i < -2.0:
            return f"ON · ~{abs(pack_i):.0f} A"
        return "—"

    def _draw_flow(self, p: QPainter, flow: _FlowPath) -> None:
        """Evenly spaced dots — constant alpha so wrap-around stays seamless."""
        if not flow.active or len(flow.points) < 2:
            return
        n = 6
        c = QColor(flow.color)
        c.setAlpha(175)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        for i in range(n):
            t = (self._phase * flow.speed + i / n) % 1.0
            pt = _point_on_path(flow.points, t)
            p.drawEllipse(pt, 2.5, 2.5)

    def _draw_device(
        self,
        p: QPainter,
        rect: QRectF,
        title: str,
        subtitle: str,
        *,
        active: bool,
        accent: QColor,
    ) -> None:
        bg = QColor("#ffffff")
        border = accent if active else QColor(BORDER)
        p.setPen(QPen(border, 1.6 if active else 1.2))
        p.setBrush(bg)
        p.drawRoundedRect(rect, 6, 6)
        if active:
            glow = QColor(accent)
            glow.setAlpha(28)
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 4, 4)

        p.setPen(QColor(TEXT))
        p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        p.drawText(rect.adjusted(8, 4, -8, -14), Qt.AlignLeft | Qt.AlignTop, title)
        p.setPen(QColor(TEXT_DIM))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(rect.adjusted(8, 18, -8, -2), Qt.AlignLeft | Qt.AlignTop, subtitle)

        # Active LED
        led = QColor(accent) if active else QColor("#c5ced8")
        p.setPen(Qt.NoPen)
        p.setBrush(led)
        p.drawEllipse(QPointF(rect.right() - 12, rect.top() + 12), 4, 4)

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
    ) -> None:
        p.setPen(QPen(QColor(BORDER), 1.3))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(rect, 8, 8)

        # Header
        p.setPen(QColor(TEXT_DIM))
        p.setFont(QFont("Segoe UI", 8, QFont.DemiBold))
        header = "Control box · stykače"
        if state is not None:
            header = f"Control box · BMU {state.name}"
        p.drawText(rect.adjusted(10, 6, -10, 0), Qt.AlignLeft | Qt.AlignTop, header)

        # Contactors centered: Main+ · Main− · Precharge
        y = rect.center().y() + 4
        n = 3
        margin = 22.0
        usable = rect.width() - 2 * margin
        xs = [rect.left() + margin + usable * (i + 0.5) / n for i in range(n)]
        self._draw_contactor(p, QPointF(xs[0], y), "Main+", main_pos, connected)
        self._draw_contactor(p, QPointF(xs[1], y), "Main−", main_neg, connected)
        self._draw_contactor(p, QPointF(xs[2], y), "Prech.", precharge, connected, warn=True)

    def _draw_contactor(
        self,
        p: QPainter,
        origin: QPointF,
        label: str,
        closed: bool,
        connected: bool,
        *,
        warn: bool = False,
    ) -> None:
        # Switch symbol
        if not connected:
            color = QColor("#b0bac4")
        elif closed:
            color = QColor(OK) if not warn else QColor("#d4a017")
        else:
            color = QColor("#c0392b") if not warn else QColor("#b0bac4")

        cx, cy = origin.x(), origin.y()
        p.setPen(QPen(color, 2.0))
        # poles
        p.drawLine(QPointF(cx, cy - 10), QPointF(cx, cy - 2))
        p.drawLine(QPointF(cx, cy + 10), QPointF(cx, cy + 2))
        if closed:
            p.drawLine(QPointF(cx, cy - 2), QPointF(cx, cy + 2))
        else:
            # open blade
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
    ) -> None:
        accent = QColor(OK) if charging else (QColor("#c47a3a") if discharging else QColor(ACCENT))
        p.setPen(QPen(accent if (charging or discharging) else QColor(BORDER), 1.5))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(rect, 8, 8)

        # Battery glyph
        bx = rect.left() + 14
        by = rect.center().y() - 10
        p.setPen(QPen(accent, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(bx, by, 22, 16), 2, 2)
        p.setBrush(accent)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(bx + 22, by + 4, 3, 8), 1, 1)
        fill = 0.5 if soc is None else max(0.05, min(1.0, soc / 100.0))
        c = QColor(accent)
        c.setAlpha(140)
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
