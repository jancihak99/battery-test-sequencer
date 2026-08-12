"""Simulate tab — offline stepfile U/I preview (full trajectory at once)."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bts.engine.sim_preview import SimSample, StepfileSimulator
from bts.ui.theme import ACCENT, BG, BG_PANEL, BORDER, OK, TEXT, TEXT_DIM

if TYPE_CHECKING:
    from bts.models.program import ModuleProfile, Program


_PHASE_COLOR = {
    "idle": QColor("#8a96a3"),
    "wait": QColor("#8a96a3"),
    "charge": QColor(OK),
    "discharge": QColor("#c47a3a"),
    "pulse": QColor("#c47a3a"),
    "done": QColor(ACCENT),
}


class TraceChart(QWidget):
    """Single-trace chart with phase bands + wheel-zoom / drag-pan on the time axis."""

    _PAD_L, _PAD_R, _PAD_T, _PAD_B = 48, 12, 22, 24

    def __init__(
        self,
        title: str,
        unit: str,
        *,
        color: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._unit = unit
        self._color = QColor(color)
        self._samples: list[SimSample] = []
        self._y_getter: Callable[[SimSample], float] = lambda s: s.voltage_v
        self._y_lo: float | None = None
        self._y_hi: float | None = None
        self.setMinimumHeight(210)
        self.setMaximumHeight(340)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # Time-axis view window (None → full range). Zoom/pan operate on this.
        self._view_t0: float | None = None
        self._view_t1: float | None = None
        self._drag_x: float | None = None
        self._drag_view: tuple[float, float] | None = None
        self._on_view_change: Callable[[float | None, float | None], None] | None = None
        self.setMouseTracking(True)

    def set_y_getter(self, fn: Callable[[SimSample], float]) -> None:
        self._y_getter = fn

    def set_y_range(self, lo: float | None, hi: float | None) -> None:
        self._y_lo, self._y_hi = lo, hi

    def set_samples(self, samples: list[SimSample]) -> None:
        self._samples = samples
        # New data → show everything
        self._view_t0 = self._view_t1 = None
        self.update()

    def set_view_change_cb(self, fn: Callable[[float | None, float | None], None]) -> None:
        self._on_view_change = fn

    def set_view(self, t0: float | None, t1: float | None) -> None:
        """Apply a time window (used to keep the V and I charts in sync)."""
        self._view_t0, self._view_t1 = t0, t1
        self.update()

    # ---- geometry / view helpers ---------------------------------------

    def _plot_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        return QRectF(
            self._PAD_L,
            self._PAD_T,
            max(10, w - self._PAD_L - self._PAD_R),
            max(10, h - self._PAD_T - self._PAD_B),
        )

    def _win(self) -> tuple[float, float, float, float] | None:
        if len(self._samples) < 2:
            return None
        f0, f1 = self._samples[0].t_s, self._samples[-1].t_s
        if f1 <= f0:
            f1 = f0 + 1.0
        t0 = self._view_t0 if self._view_t0 is not None else f0
        t1 = self._view_t1 if self._view_t1 is not None else f1
        return t0, t1, f0, f1

    def _set_view_and_sync(self, t0: float | None, t1: float | None) -> None:
        self._view_t0, self._view_t1 = t0, t1
        self.update()
        if self._on_view_change is not None:
            self._on_view_change(t0, t1)

    # ---- interaction ---------------------------------------------------

    def wheelEvent(self, e) -> None:  # noqa: N802
        win = self._win()
        if win is None:
            return
        t0, t1, f0, f1 = win
        span = t1 - t0
        plot = self._plot_rect()
        x = e.position().x()
        frac = min(1.0, max(0.0, (x - plot.left()) / plot.width())) if plot.width() > 0 else 0.5
        tc = t0 + frac * span
        factor = 0.82 if e.angleDelta().y() > 0 else 1.22
        new_span = max(1.0, min(span * factor, f1 - f0))
        nt0 = tc - frac * new_span
        nt1 = nt0 + new_span
        if nt0 < f0:
            nt0, nt1 = f0, f0 + new_span
        if nt1 > f1:
            nt1, nt0 = f1, f1 - new_span
        if nt0 <= f0 + 1e-6 and nt1 >= f1 - 1e-6:
            self._set_view_and_sync(None, None)
        else:
            self._set_view_and_sync(nt0, nt1)
        e.accept()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.LeftButton and len(self._samples) >= 2:
            win = self._win()
            self._drag_x = e.position().x()
            self._drag_view = (win[0], win[1]) if win else None
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag_x is None or self._drag_view is None:
            return
        win = self._win()
        if win is None:
            return
        _, _, f0, f1 = win
        span = self._drag_view[1] - self._drag_view[0]
        plot = self._plot_rect()
        if plot.width() <= 0:
            return
        dt = (e.position().x() - self._drag_x) / plot.width() * span
        nt0 = self._drag_view[0] - dt
        nt1 = self._drag_view[1] - dt
        if nt0 < f0:
            nt0, nt1 = f0, f0 + span
        if nt1 > f1:
            nt1, nt0 = f1, f1 - span
        if nt0 <= f0 + 1e-6 and nt1 >= f1 - 1e-6:
            self._set_view_and_sync(None, None)
        else:
            self._set_view_and_sync(nt0, nt1)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._drag_x = None
        self._drag_view = None
        self.setCursor(Qt.ArrowCursor)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self._set_view_and_sync(None, None)

    # ---- paint ---------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(BG))            # page gray behind the card

        # White card covering the widget
        card = QRectF(0.5, 0.5, w - 1, h - 1)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor(BG_PANEL))
        p.drawRoundedRect(card, 6, 6)

        plot = self._plot_rect()

        p.setPen(QColor(TEXT_DIM))
        p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        p.drawText(10, 16, f"{self._title} ({self._unit})")

        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(plot, 3, 3)

        if len(self._samples) < 2:
            p.setPen(QColor(TEXT_DIM))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(plot, Qt.AlignCenter, "Simulace → celá křivka stepfile…")
            return

        win = self._win()
        t0, t1, f0, f1 = win
        if t1 <= t0:
            t1 = t0 + 1.0
        visible = [s for s in self._samples if t0 - 1e-9 <= s.t_s <= t1 + 1e-9]
        if len(visible) < 2:
            visible = self._samples[-2:]

        ys = [self._y_getter(s) for s in visible]
        if self._y_lo is not None and self._y_hi is not None:
            y_lo, y_hi = self._y_lo, self._y_hi
        else:
            y_lo, y_hi = min(ys), max(ys)
            if abs(y_hi - y_lo) < 1e-6:
                y_lo -= 1.0
                y_hi += 1.0
            margin = (y_hi - y_lo) * 0.08
            y_lo -= margin
            y_hi += margin

        def map_x(t: float) -> float:
            return plot.left() + (t - t0) / (t1 - t0) * plot.width()

        def map_y(v: float) -> float:
            return plot.bottom() - (v - y_lo) / (y_hi - y_lo) * plot.height()

        p.setClipRect(plot)
        band_start = 0
        for i in range(1, len(visible) + 1):
            end = i == len(visible)
            if end or visible[i].phase != visible[band_start].phase:
                phase = visible[band_start].phase
                c = QColor(_PHASE_COLOR.get(phase, QColor("#ccc")))
                c.setAlpha(28)
                x0 = map_x(visible[band_start].t_s)
                x1 = map_x(visible[i - 1].t_s)
                p.fillRect(QRectF(x0, plot.top(), max(1.0, x1 - x0), plot.height()), c)
                band_start = i

        if y_lo < 0 < y_hi:
            p.setPen(QPen(QColor("#c5ced8"), 1, Qt.DashLine))
            p.drawLine(QPointF(plot.left(), map_y(0)), QPointF(plot.right(), map_y(0)))

        poly = QPolygonF()
        for s, y in zip(visible, ys):
            poly.append(QPointF(map_x(s.t_s), map_y(y)))
        pen = QPen(self._color, 2.0)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(poly)
        p.setClipping(False)

        p.setPen(QColor(TEXT_DIM))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRectF(0, map_y(y_hi) - 6, self._PAD_L - 4, 12), Qt.AlignRight, f"{y_hi:.1f}")
        p.drawText(QRectF(0, map_y(y_lo) - 6, self._PAD_L - 4, 12), Qt.AlignRight, f"{y_lo:.1f}")
        p.drawText(
            QRectF(plot.left(), plot.bottom() + 5, plot.width(), 14),
            Qt.AlignLeft,
            self._fmt_time(t0),
        )
        p.drawText(
            QRectF(plot.left(), plot.bottom() + 5, plot.width(), 14),
            Qt.AlignRight,
            self._fmt_time(t1),
        )
        zoomed = self._view_t0 is not None or self._view_t1 is not None
        if zoomed:
            p.setPen(QColor(ACCENT))
            p.setFont(QFont("Segoe UI", 8, QFont.DemiBold))
            p.drawText(
                QRectF(plot.left(), plot.bottom() + 5, plot.width(), 14),
                Qt.AlignHCenter,
                f"zoom {self._fmt_time(t1 - t0)}  ·  dvojklik = celé",
            )

    @staticmethod
    def _fmt_time(t: float) -> str:
        s = int(max(0, t))
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{sec:02d}"
        return f"{m}:{sec:02d}"


class SimulateTab(QWidget):
    """Show full stepfile U/I curves in one shot (random start → converges after goto_soc)."""

    _EDITOR_LABEL = "◆ Program z Editoru"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._get_program: Callable[[], tuple[Program | None, ModuleProfile | None]] | None = None
        self._list_provider: Callable[[], list[tuple[str, Path]]] | None = None
        self._file_loader: Callable[[Path], tuple[Program | None, ModuleProfile | None]] | None = None
        self._sim: StepfileSimulator | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(16)

        bar = QFrame()
        bar.setObjectName("simBar")
        bar.setStyleSheet(
            f"#simBar {{ background:{BG_PANEL}; border:1px solid {BORDER}; border-radius:6px; }}"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(10)

        row.addWidget(QLabel("Program"))
        self.program_combo = QComboBox()
        self.program_combo.setMinimumWidth(240)
        self.program_combo.setToolTip("Vyber stepfile k simulaci, nebo program z Editoru.")
        row.addWidget(self.program_combo, 0)

        self.btn_run = QPushButton("Simulovat")
        self.btn_run.setObjectName("btnPrimary")
        self.btn_run.setToolTip(
            "Celá křivka najednou. Start SOC je náhodný — po goto_soc / nabití na limit "
            "by měl zbytek programu vypadat stejně (jen začátek je kratší/delší)."
        )
        self.btn_run.clicked.connect(self._run_full)
        row.addWidget(self.btn_run)

        row.addSpacing(12)
        self.lbl_step = QLabel("Vyber program a klikni Simulovat.")
        self.lbl_step.setTextFormat(Qt.RichText)
        self.lbl_step.setWordWrap(True)
        self.lbl_step.setStyleSheet(f"color:{TEXT_DIM};")
        self.lbl_vals = QLabel("—")
        self.lbl_vals.setStyleSheet(f"color:{TEXT};font-weight:600;")
        row.addWidget(self.lbl_step, 1)
        row.addWidget(self.lbl_vals)
        root.addWidget(bar)

        legend = QLabel()
        legend.setTextFormat(Qt.RichText)
        legend.setWordWrap(True)
        legend.setText(
            f'<span style="color:{OK}">■ charge</span> &nbsp; '
            f'<span style="color:#c47a3a">■ discharge / pulse</span> &nbsp; '
            f'<span style="color:#8a96a3">■ wait / idle</span>'
            f' &nbsp;&nbsp;·&nbsp;&nbsp; kolečko = zoom · táhni = posun · dvojklik = celé'
        )
        legend.setStyleSheet("background: transparent; font-size:11px;")
        root.addWidget(legend)

        self.chart_v = TraceChart("Napětí packu", "V", color=ACCENT)
        self.chart_v.set_y_getter(lambda s: s.voltage_v)
        self.chart_i = TraceChart("Proud packu", "A", color=OK)
        self.chart_i.set_y_getter(lambda s: s.current_a)
        # Keep both charts on the same time window
        self.chart_v.set_view_change_cb(self._sync_views)
        self.chart_i.set_view_change_cb(self._sync_views)
        root.addWidget(self.chart_v)
        root.addWidget(self.chart_i)
        root.addStretch(1)

    # ---- wiring --------------------------------------------------------

    def set_program_provider(
        self, fn: Callable[[], tuple[Program | None, ModuleProfile | None]]
    ) -> None:
        self._get_program = fn

    def set_program_list_provider(self, fn: Callable[[], list[tuple[str, Path]]]) -> None:
        self._list_provider = fn

    def set_file_loader(
        self, fn: Callable[[Path], tuple[Program | None, ModuleProfile | None]]
    ) -> None:
        self._file_loader = fn

    def refresh_programs(self) -> None:
        """Repopulate the program picker (kept current with the stepfile folder)."""
        if self._list_provider is None:
            return
        prev = self.program_combo.currentData()
        self.program_combo.blockSignals(True)
        self.program_combo.clear()
        self.program_combo.addItem(self._EDITOR_LABEL, None)
        for label, path in self._list_provider():
            self.program_combo.addItem(label, str(path))
        # Restore previous selection if still present
        if prev is not None:
            idx = self.program_combo.findData(prev)
            if idx >= 0:
                self.program_combo.setCurrentIndex(idx)
        self.program_combo.blockSignals(False)

    # ---- run -----------------------------------------------------------

    def _resolve_program(self):
        """(program, profile) from the picker: a stepfile, or the editor program."""
        data = self.program_combo.currentData()
        if data is None:
            if self._get_program is not None:
                return self._get_program()
            return None, None
        if self._file_loader is not None:
            return self._file_loader(Path(data))
        return None, None

    def _run_full(self) -> None:
        program, profile = self._resolve_program()
        if program is None:
            self.lbl_step.setText("Nelze načíst program.")
            return
        if profile is None:
            prof_name = getattr(program.meta, "module_profile", "?")
            self.lbl_step.setText(
                f"Chybí profil '{prof_name}' — zkontroluj Module profile."
            )
            return
        if not program.steps:
            self.lbl_step.setText("Program nemá žádné kroky.")
            return

        self.btn_run.setEnabled(False)
        self.lbl_step.setText("Počítám…")
        try:
            self._sim = StepfileSimulator(program, profile, speed=1.0)
            self._sim.reset(randomize=True)
            start_soc = self._sim.state.soc_pct
            start_v = self._sim.state.voltage_v
            sample_every = 1.0
            n_power = sum(
                1 for s in program.steps if s.type in ("charge", "discharge", "goto_soc")
            )
            if n_power >= 2:
                sample_every = 5.0
            self._sim.run_all(sample_every=sample_every)
            self.chart_v.set_y_range(profile.pack_v_min - 1.0, profile.pack_v_max + 1.0)
            self._refresh_view()
            st = self._sim.state
            self.lbl_step.setText(
                f"Start SOC {start_soc:.0f}% → konec {st.soc_pct:.0f}% &nbsp;·&nbsp; "
                f"{len(program.steps)} kroků &nbsp;·&nbsp; "
                f"trvání {TraceChart._fmt_time(st.t_s)}"
            )
            self.lbl_vals.setText(
                f"U {start_v:.2f} V → {st.voltage_v:.2f} V   ·   {len(st.samples)} pts"
            )
        finally:
            self.btn_run.setEnabled(True)

    def _sync_views(self, t0: float | None, t1: float | None) -> None:
        """Mirror one chart's zoom/pan window onto the other (time axis shared)."""
        self.chart_v.set_view(t0, t1)
        self.chart_i.set_view(t0, t1)

    def _refresh_view(self) -> None:
        if self._sim is None:
            return
        st = self._sim.state
        samples = st.samples
        self.chart_v.set_samples(samples)
        self.chart_i.set_samples(samples)

        if samples:
            ys = [s.current_a for s in samples]
            # Zero-centred current axis so charge (+) and discharge (−) read
            # symmetrically and the zero line sits in the middle.
            m = max(abs(min(ys)), abs(max(ys)), 1.0)
            m *= 1.15
            self.chart_i.set_y_range(-m, m)
