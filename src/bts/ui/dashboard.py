"""Live test dashboard — compact Service Tool–style glance layout."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRectF, Qt, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygon
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bts.models.telemetry import BmsTelemetry, BmuState, EaTelemetry
from bts.ui.activity import RunProgressPanel
from bts.ui.live_history import CollapsibleLiveHistory
from bts.ui.schematic import WiringSchematic
from bts.ui.theme import ACCENT, BG_PANEL, BORDER, CHART_BG, OK, TEXT, TEXT_DIM

if TYPE_CHECKING:
    from bts.models.program import ModuleProfile


def _finite(vals: list[float]) -> list[tuple[int, float]]:
    out = []
    for i, v in enumerate(vals):
        # Ignore NaN and near-zero placeholders (unused cell slots)
        if isinstance(v, (int, float)) and math.isfinite(v) and float(v) > 0.05:
            out.append((i, float(v)))
    return out


class Panel(QFrame):
    def __init__(self, title: str, body: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dashPanel")
        self.setStyleSheet(
            f"#dashPanel {{ background: {BG_PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(10)
        lab = QLabel(title)
        lab.setStyleSheet(
            f"color: {TEXT_DIM}; font-weight: 700; font-size: 11px;"
        )
        lay.addWidget(lab)
        lay.addWidget(body)


class CellVoltageScale(QWidget):
    """Compact horizontal voltage scale."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(56)
        self.setMinimumWidth(320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._cells: list[tuple[int, float]] = []
        self._v_lo = 1.5
        self._v_hi = 2.75
        self._vmin_id: int | None = None
        self._vmax_id: int | None = None
        self._vmin_v: float | None = None
        self._vmax_v: float | None = None

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(400, 56)

    def set_data(
        self,
        voltages: list[float],
        v_lo: float,
        v_hi: float,
        vmin_id: int | None = None,
        vmax_id: int | None = None,
        vmin_v: float | None = None,
        vmax_v: float | None = None,
    ) -> None:
        self._cells = _finite(voltages)
        self._v_lo = v_lo
        self._v_hi = v_hi
        self._vmin_id = vmin_id if vmin_id is not None and vmin_id <= 2000 else None
        self._vmax_id = vmax_id if vmax_id is not None and vmax_id <= 2000 else None
        self._vmin_v = vmin_v if vmin_v is not None and math.isfinite(vmin_v) and vmin_v > 0.05 else None
        self._vmax_v = vmax_v if vmax_v is not None and math.isfinite(vmax_v) and vmax_v > 0.05 else None
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        p.fillRect(self.rect(), QColor(BG_PANEL))   # blend into the white card

        pad_l, pad_r = 44, 44
        y_line = 26
        x0, x1 = pad_l, w - pad_r
        if x1 <= x0 + 20:
            return

        def x_of(v: float) -> float:
            span = max(0.2, self._v_hi - self._v_lo)
            margin = span * 0.06
            lo = self._v_lo - margin
            hi = self._v_hi + margin
            t = (v - lo) / (hi - lo) if hi > lo else 0.5
            t = max(0.0, min(1.0, t))
            return x0 + t * (x1 - x0)

        p.setPen(QPen(QColor("#8a96a3"), 2))
        p.drawLine(x0, y_line, x1, y_line)

        x_lo = x_of(self._v_lo)
        x_hi = x_of(self._v_hi)
        p.fillRect(QRectF(x_lo, y_line - 4, max(2.0, x_hi - x_lo), 8), QColor(0, 143, 193, 45))

        p.setPen(QPen(QColor("#c47a3a"), 2))
        for x in (x_lo, x_hi):
            p.drawLine(int(x), y_line - 9, int(x), y_line + 9)
        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor("#9a6a40"))
        p.drawText(int(x_lo) - 18, y_line + 22, f"{self._v_lo:.2f}")
        p.drawText(int(x_hi) - 18, y_line + 22, f"{self._v_hi:.2f}")

        if not self._cells:
            p.setPen(QColor(TEXT_DIM))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(QRectF(x0, 4, x1 - x0, 14), Qt.AlignCenter, "Waiting for cells…")
            return

        vals = [v for _, v in self._cells]
        vmin = self._vmin_v if self._vmin_v is not None else min(vals)
        vmax = self._vmax_v if self._vmax_v is not None else max(vals)
        vmin_id = self._vmin_id
        vmax_id = self._vmax_id
        if vmin_id is None:
            vmin_id = next((i for i, v in self._cells if abs(v - vmin) < 1e-6), None)
        if vmax_id is None:
            vmax_id = next((i for i, v in self._cells if abs(v - vmax) < 1e-6), None)

        p.setPen(QPen(QColor("#b0bac4"), 1))
        for idx, v in self._cells:
            if idx in (vmin_id, vmax_id):
                continue
            x = int(x_of(v))
            p.drawLine(x, y_line - 3, x, y_line + 3)

        def draw_arrow(x: float, color: QColor, label: str, above: bool) -> None:
            xi = int(x)
            p.setPen(QPen(color, 1.5))
            p.setBrush(color)
            if above:
                pts = [(xi, y_line - 1), (xi - 5, y_line - 11), (xi + 5, y_line - 11)]
                ty = 2
            else:
                pts = [(xi, y_line + 1), (xi - 5, y_line + 11), (xi + 5, y_line + 11)]
                ty = y_line + 12
            p.drawPolygon(QPolygon([QPoint(a, b) for a, b in pts]))
            p.setFont(QFont("Segoe UI", 8, QFont.DemiBold))
            p.drawText(xi - 52, ty, 104, 12, Qt.AlignCenter, label)

        draw_arrow(x_of(vmin), QColor("#2a7fd4"), f"min #{vmin_id} {vmin:.3f}V", True)
        if abs(vmax - vmin) < 1e-4:
            draw_arrow(x_of(vmax) + 8, QColor("#d45a2a"), f"max #{vmax_id} {vmax:.3f}V", False)
        else:
            draw_arrow(x_of(vmax), QColor("#d45a2a"), f"max #{vmax_id} {vmax:.3f}V", False)


class MetricTile(QFrame):
    """Readable value tile — number first, optional thin bar."""

    def __init__(self, title: str, unit: str = "", *, emphasize: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._lo = 0.0
        self._hi = 100.0
        self._value: float | None = None
        self._unit = unit
        self._emphasize = emphasize
        self.setObjectName("metricTile")
        # Transparent: tiles read as stat columns on the white card, not gray
        # boxes nested inside it.
        self.setStyleSheet("#metricTile { background: transparent; border: none; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.title_lab = QLabel(title)
        self.title_lab.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        self.value_lab = QLabel("—")
        if emphasize:
            self.value_lab.setStyleSheet(
                f"color:{ACCENT};font-size:32px;font-weight:700;"
            )
            self.setMinimumHeight(92)
        else:
            self.value_lab.setStyleSheet(
                f"color:{TEXT};font-size:17px;font-weight:600;"
            )
            self.setMinimumHeight(66)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background:{CHART_BG}; border:none; border-radius:3px; }}"
            f"QProgressBar::chunk {{ background:{ACCENT}; border-radius:3px; }}"
        )
        lay.addWidget(self.title_lab)
        lay.addWidget(self.value_lab)
        lay.addWidget(self.bar)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_range(self, lo: float, hi: float) -> None:
        self._lo, self._hi = lo, hi

    def set_value(self, v: float | None) -> None:
        self._value = v
        if v is None:
            self.value_lab.setText("—")
            self.bar.setValue(0)
            return
        if self._unit == "%":
            self.value_lab.setText(f"{v:.0f} %")
        elif self._unit == "V":
            self.value_lab.setText(f"{v:.2f} V")
        elif self._unit == "°C":
            self.value_lab.setText(f"{v:.1f} °C")
        else:
            self.value_lab.setText(f"{v:.2f} {self._unit}".strip())
        if self._hi > self._lo:
            t = max(0.0, min(1.0, (v - self._lo) / (self._hi - self._lo)))
            self.bar.setValue(int(t * 1000))
            # Warn colors near edges
            if t <= 0.05 or t >= 0.95:
                chunk = "#d94040"
            elif t < 0.12 or t > 0.88:
                chunk = "#d4a017"
            else:
                chunk = ACCENT
            self.bar.setStyleSheet(
                f"QProgressBar {{ background:{CHART_BG}; border:none; border-radius:3px; }}"
                f"QProgressBar::chunk {{ background:{chunk}; border-radius:3px; }}"
            )
        else:
            self.bar.setValue(0)


class StatusPill(QLabel):
    def __init__(self, text: str = "—", parent=None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(34)
        self.setMinimumWidth(88)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_idle()

    def set_idle(self) -> None:
        self.setStyleSheet(
            f"background:#f0f3f6;color:{TEXT_DIM};border:1px solid {BORDER};"
            f"border-radius:6px;padding:6px 8px;font-weight:600;font-size:12px;"
        )

    def set_closed(self) -> None:
        self.setStyleSheet(
            f"background:#e6f5ee;color:{OK};border:1px solid #9dceb4;"
            f"border-radius:6px;padding:6px 8px;font-weight:600;font-size:12px;"
        )

    def set_open(self) -> None:
        self.setStyleSheet(
            "background:#f8eaea;color:#a33a3a;border:1px solid #e0b0b0;"
            "border-radius:6px;padding:6px 8px;font-weight:600;font-size:12px;"
        )

    def set_warn(self) -> None:
        self.setStyleSheet(
            "background:#f8f1de;color:#8a6a20;border:1px solid #e0d0a0;"
            "border-radius:6px;padding:6px 8px;font-weight:600;font-size:12px;"
        )

    def set_active(self) -> None:
        self.setStyleSheet(
            f"background:#e5f5fb;color:{ACCENT};border:1px solid #9fd4ea;"
            f"border-radius:6px;padding:6px 8px;font-weight:600;font-size:12px;"
        )


class ContactorPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._pills: dict[str, StatusPill] = {}
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(4)
        for col, (key, title) in enumerate(
            [
                ("main_pos", "Main +"),
                ("main_neg", "Main −"),
                ("precharge", "Precharge"),
                ("state", "BMU"),
            ]
        ):
            name = QLabel(title)
            name.setAlignment(Qt.AlignCenter)
            name.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
            pill = StatusPill()
            layout.addWidget(name, 0, col)
            layout.addWidget(pill, 1, col)
            self._pills[key] = pill

    def update_contactors(self, bms: BmsTelemetry) -> None:
        st = bms.contactors_for_display
        for key, closed in [
            ("main_pos", st.main_pos),
            ("main_neg", st.main_neg),
            ("precharge", st.precharge),
        ]:
            pill = self._pills[key]
            if not bms.connected:
                pill.setText("—")
                pill.set_idle()
            else:
                pill.setText("CLOSED" if closed else "OPEN")
                if closed:
                    pill.set_closed()
                else:
                    pill.set_open()

        state_pill = self._pills["state"]
        if not bms.connected:
            state_pill.setText("—")
            state_pill.set_idle()
            return
        state = bms.operating_state
        state_pill.setText(state.name)
        if state == BmuState.READY:
            state_pill.set_closed()
        elif state in (BmuState.PRE_CHARGE, BmuState.STOPPING):
            state_pill.set_warn()
        elif state == BmuState.IDLE:
            state_pill.set_open()
        else:
            state_pill.set_idle()


class EaPathPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.psi = StatusPill("PSI OFF")
        self.el = StatusPill("EL OFF")
        layout.addWidget(self.psi)
        layout.addWidget(self.el)

    def update_ea(self, ea: EaTelemetry) -> None:
        if not ea.connected:
            self.psi.setText("PSI —")
            self.el.setText("EL —")
            self.psi.set_idle()
            self.el.set_idle()
            return
        if ea.psi_output_on:
            self.psi.setText(f"PSI ON  {ea.psi_current_a:.0f}A")
            self.psi.set_active()
        else:
            self.psi.setText("PSI OFF")
            self.psi.set_idle()
        if ea.el_input_on:
            self.el.setText(f"EL ON  {ea.el_current_a:.0f}A")
            self.el.set_active()
        else:
            self.el.setText("EL OFF")
            self.el.set_idle()


class LiveDashboard(QWidget):
    """Stable vertical stack — no collapsing side-by-side charts."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._profile: ModuleProfile | None = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        self.progress = RunProgressPanel()
        root.addWidget(self.progress)

        self.schematic = WiringSchematic()
        root.addWidget(Panel("Zapojení · live tok", self.schematic))

        # Pack glance: big SOC + pack V + temperature
        metrics = QHBoxLayout()
        metrics.setSpacing(14)
        self.tile_soc = MetricTile("State of charge (SOC)", "%", emphasize=True)
        self.tile_pack = MetricTile("Pack voltage", "V")
        self.tile_temp = MetricTile("Cell temperature (max)", "°C")
        self.tile_soc.set_range(0, 100)
        metrics.addWidget(self.tile_soc, 2)
        metrics.addWidget(self.tile_pack, 1)
        metrics.addWidget(self.tile_temp, 1)
        root.addWidget(Panel("Pack summary", self._wrap(metrics)))

        # Live V/I history — collapsed by default so it doesn't crowd the glance layout
        self.history = CollapsibleLiveHistory(expanded=False)
        root.addWidget(self.history)

        self.cells = CellVoltageScale()
        root.addWidget(Panel("Cells · Vmin / Vmax vs limits", self.cells))

        self.hint = QLabel(
            "Sleduj: stykače CLOSED v READY  ·  tok zdroj→pack / pack→zátěž  ·  DTC"
        )
        self.hint.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        root.addWidget(self.hint)
        root.addStretch(1)

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        return w

    def set_profile(self, profile: ModuleProfile | None) -> None:
        self._profile = profile
        if profile:
            self.tile_pack.set_range(profile.pack_v_min, profile.pack_v_max)
            self.tile_temp.set_range(profile.t_min_c, profile.t_max_c)
            self.cells.set_data([], profile.cell_v_min, profile.cell_v_max)
            self.history.set_v_range(profile.pack_v_min, profile.pack_v_max)
            ah = float(profile.nominal_capacity_ah or 70.0)
            self.schematic.set_capacity_ah(ah)
            self.progress.set_capacity_ah(ah)

    def clear_history(self) -> None:
        self.history.clear()

    def update_live(self, bms: BmsTelemetry | None, ea: EaTelemetry | None) -> None:
        self.schematic.set_telemetry(bms, ea)
        self.history.push(bms, ea)
        amps = 0.0
        if ea is not None and ea.connected:
            amps = max(abs(float(ea.psi_current_a)), abs(float(ea.el_current_a)))
        if bms is not None and bms.pack_current_a is not None:
            amps = max(amps, abs(float(bms.pack_current_a)))
        ready = bool(
            bms is not None
            and bms.operating_state == BmuState.READY
        )
        self.progress.set_live_current(amps, bmu_ready=ready)
        if bms is not None:
            self.tile_soc.set_value(bms.soc_pct)
            self.tile_pack.set_value(bms.pack_voltage_v)
            self.tile_temp.set_value(bms.t_max_c)
            lo = self._profile.cell_v_min if self._profile else 1.5
            hi = self._profile.cell_v_max if self._profile else 2.75
            self.cells.set_data(
                bms.cell_voltages,
                lo,
                hi,
                vmin_id=bms.cell_vmin_id,
                vmax_id=bms.cell_vmax_id,
                vmin_v=bms.cell_v_min,
                vmax_v=bms.cell_v_max,
            )
