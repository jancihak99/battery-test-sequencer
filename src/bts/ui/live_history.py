"""Collapsible live pack V / I history chart for the Běh dashboard."""
from __future__ import annotations

import math
import time
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bts.models.telemetry import BmsTelemetry, EaTelemetry
from bts.ui.theme import ACCENT, BG_PANEL, BORDER, CHART_BG, TEXT_DIM

_LIVE_HISTORY_S = 10 * 60.0
_LIVE_HISTORY_MAX_PTS = 2400
_COLOR_V = ACCENT
_COLOR_I = "#e67e22"


def signed_current_a(bms: BmsTelemetry | None, ea: EaTelemetry | None) -> float | None:
    """Charge = +, discharge = −. Prefer EA when connected; else BMS pack I."""
    if ea is not None and ea.connected:
        psi = abs(float(ea.psi_current_a or 0.0))
        el = abs(float(ea.el_current_a or 0.0))
        if psi >= el and psi > 0.05:
            return psi
        if el > 0.05:
            return -el
    if bms is not None and bms.pack_current_a is not None and math.isfinite(bms.pack_current_a):
        return float(bms.pack_current_a)
    return None


class LiveViHistory(QWidget):
    """Rolling live pack voltage + signed current (QPainter, no extra deps)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._samples: deque[tuple[float, float | None, float | None]] = deque()
        self._v_lo: float | None = None
        self._v_hi: float | None = None
        self.setMinimumHeight(150)
        self.setMaximumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(400, 170)

    def clear(self) -> None:
        self._samples.clear()
        self.update()

    def set_v_range(self, lo: float | None, hi: float | None) -> None:
        self._v_lo, self._v_hi = lo, hi

    def append(self, voltage_v: float | None, current_a: float | None) -> None:
        if voltage_v is None and current_a is None:
            return
        now = time.monotonic()
        v = float(voltage_v) if voltage_v is not None and math.isfinite(voltage_v) else None
        i = float(current_a) if current_a is not None and math.isfinite(current_a) else None
        if v is not None and v <= 0.05:
            v = None
        self._samples.append((now, v, i))
        cutoff = now - _LIVE_HISTORY_S
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        while len(self._samples) > _LIVE_HISTORY_MAX_PTS:
            self._samples.popleft()
        if self.isVisible():
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(CHART_BG))

        pad_l, pad_r, pad_t, pad_b = 44, 44, 18, 18
        plot = QRectF(pad_l, pad_t, max(10, w - pad_l - pad_r), max(10, h - pad_t - pad_b))

        p.setPen(QColor(TEXT_DIM))
        p.setFont(QFont("Segoe UI", 8, QFont.DemiBold))
        p.drawText(8, 12, "U pack (V)")
        p.setPen(QColor(_COLOR_I))
        p.drawText(QRectF(w - 90, 2, 82, 14), Qt.AlignRight | Qt.AlignVCenter, "I (A)")

        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(plot, 4, 4)

        if len(self._samples) < 2:
            p.setPen(QColor(TEXT_DIM))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(plot, Qt.AlignCenter, "Připoj HW — historie V / I se naplní…")
            return

        t0 = self._samples[0][0]
        t1 = self._samples[-1][0]
        if t1 <= t0:
            t1 = t0 + 1.0

        vs = [s[1] for s in self._samples if s[1] is not None]
        is_ = [s[2] for s in self._samples if s[2] is not None]

        if self._v_lo is not None and self._v_hi is not None:
            yv_lo, yv_hi = float(self._v_lo), float(self._v_hi)
            if vs:
                yv_lo = min(yv_lo, min(vs))
                yv_hi = max(yv_hi, max(vs))
        elif vs:
            yv_lo, yv_hi = min(vs), max(vs)
        else:
            yv_lo, yv_hi = 0.0, 1.0
        if abs(yv_hi - yv_lo) < 0.2:
            mid = 0.5 * (yv_lo + yv_hi)
            yv_lo, yv_hi = mid - 0.5, mid + 0.5
        vm = (yv_hi - yv_lo) * 0.06
        yv_lo -= vm
        yv_hi += vm

        if is_:
            yi_lo, yi_hi = min(is_), max(is_)
        else:
            yi_lo, yi_hi = -1.0, 1.0
        if abs(yi_hi - yi_lo) < 1.0:
            mid = 0.5 * (yi_lo + yi_hi)
            yi_lo, yi_hi = mid - 5.0, mid + 5.0
        if yi_lo >= 0:
            yi_lo = 0.0
        elif yi_hi <= 0:
            yi_hi = 0.0
        im = (yi_hi - yi_lo) * 0.08
        yi_lo -= im
        yi_hi += im

        def map_x(t: float) -> float:
            return plot.left() + (t - t0) / (t1 - t0) * plot.width()

        def map_yv(v: float) -> float:
            return plot.bottom() - (v - yv_lo) / (yv_hi - yv_lo) * plot.height()

        def map_yi(a: float) -> float:
            return plot.bottom() - (a - yi_lo) / (yi_hi - yi_lo) * plot.height()

        p.setPen(QPen(QColor("#e3e8ee"), 1, Qt.DotLine))
        for frac in (0.25, 0.5, 0.75):
            y = plot.top() + frac * plot.height()
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        if yi_lo < 0 < yi_hi:
            p.setPen(QPen(QColor("#c5ced8"), 1, Qt.DashLine))
            p.drawLine(QPointF(plot.left(), map_yi(0)), QPointF(plot.right(), map_yi(0)))

        step = max(1, len(self._samples) // 500)
        poly_v = QPolygonF()
        poly_i = QPolygonF()
        for idx in range(0, len(self._samples), step):
            t, v, i = self._samples[idx]
            x = map_x(t)
            if v is not None:
                poly_v.append(QPointF(x, map_yv(v)))
            if i is not None:
                poly_i.append(QPointF(x, map_yi(i)))
        t, v, i = self._samples[-1]
        if v is not None:
            poly_v.append(QPointF(map_x(t), map_yv(v)))
        if i is not None:
            poly_i.append(QPointF(map_x(t), map_yi(i)))

        if poly_i.count() >= 2:
            pen_i = QPen(QColor(_COLOR_I), 1.6)
            pen_i.setJoinStyle(Qt.RoundJoin)
            pen_i.setCapStyle(Qt.RoundCap)
            p.setPen(pen_i)
            p.drawPolyline(poly_i)
        if poly_v.count() >= 2:
            pen_v = QPen(QColor(_COLOR_V), 2.0)
            pen_v.setJoinStyle(Qt.RoundJoin)
            pen_v.setCapStyle(Qt.RoundCap)
            p.setPen(pen_v)
            p.drawPolyline(poly_v)

        p.setFont(QFont("Segoe UI", 8))
        p.setPen(QColor(_COLOR_V))
        p.drawText(2, int(plot.top()) + 10, f"{yv_hi:.1f}")
        p.drawText(2, int(plot.bottom()), f"{yv_lo:.1f}")
        p.setPen(QColor(_COLOR_I))
        p.drawText(QRectF(w - 42, plot.top(), 40, 12), Qt.AlignRight, f"{yi_hi:.0f}")
        p.drawText(QRectF(w - 42, plot.bottom() - 12, 40, 12), Qt.AlignRight, f"{yi_lo:.0f}")

        span_s = t1 - t0
        span_txt = f"{span_s / 60:.1f} min" if span_s >= 60 else f"{span_s:.0f} s"
        footer = span_txt
        if v is not None:
            footer += f"   ·   {v:.2f} V"
        if i is not None:
            footer += f"   ·   {i:+.1f} A"
        p.setPen(QColor(TEXT_DIM))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRectF(plot.left(), h - 16, plot.width(), 14), Qt.AlignCenter, footer)


class CollapsibleLiveHistory(QFrame):
    """Dashboard panel: header toggles the live V/I chart (collapsed by default)."""

    def __init__(self, *, expanded: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dashPanel")
        self.setStyleSheet(
            f"#dashPanel {{ background: {BG_PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._expanded = bool(expanded)
        self.chart = LiveViHistory()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 8)
        lay.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self._btn = QToolButton()
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setAutoRaise(True)
        self._btn.setStyleSheet(
            f"QToolButton {{ color: {TEXT_DIM}; font-weight: 600; font-size: 11px; border: none; padding: 2px 0; }}"
            f"QToolButton:hover {{ color: {ACCENT}; }}"
        )
        self._btn.clicked.connect(self.toggle)
        header.addWidget(self._btn, 1)
        hint = QLabel("V · I  ·  posledních ~10 min")
        hint.setStyleSheet(f"color:{TEXT_DIM};font-size:10px;")
        header.addWidget(hint, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addLayout(header)
        lay.addWidget(self.chart)
        self._apply_expanded()

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._apply_expanded()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._apply_expanded()

    def _apply_expanded(self) -> None:
        arrow = "▾" if self._expanded else "▸"
        self._btn.setText(f"{arrow}  Historie V / I")
        self.chart.setVisible(self._expanded)
        if self._expanded:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            self.chart.update()
        else:
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def clear(self) -> None:
        self.chart.clear()

    def set_v_range(self, lo: float | None, hi: float | None) -> None:
        self.chart.set_v_range(lo, hi)

    def push(self, bms: BmsTelemetry | None, ea: EaTelemetry | None) -> None:
        v = bms.pack_voltage_v if bms is not None else None
        if v is None and ea is not None and ea.connected:
            v = ea.psi_voltage_v or ea.el_voltage_v
        self.chart.append(v, signed_current_a(bms, ea))
