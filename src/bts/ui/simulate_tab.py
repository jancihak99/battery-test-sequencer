"""Simulate tab — offline stepfile U/I preview (full trajectory at once)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
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
from bts.ui.theme import ACCENT, BORDER, CHART_BG, OK, TEXT, TEXT_DIM

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
    """Single-trace chart with phase background bands."""

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
        self.setMinimumHeight(200)
        self.setMaximumHeight(320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_y_getter(self, fn: Callable[[SimSample], float]) -> None:
        self._y_getter = fn

    def set_y_range(self, lo: float | None, hi: float | None) -> None:
        self._y_lo, self._y_hi = lo, hi

    def set_samples(self, samples: list[SimSample]) -> None:
        self._samples = samples
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(CHART_BG))

        pad_l, pad_r, pad_t, pad_b = 48, 12, 22, 22
        plot = QRectF(pad_l, pad_t, max(10, w - pad_l - pad_r), max(10, h - pad_t - pad_b))

        p.setPen(QColor(TEXT_DIM))
        p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
        p.drawText(8, 14, f"{self._title} ({self._unit})")

        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(plot, 4, 4)

        if len(self._samples) < 2:
            p.setPen(QColor(TEXT_DIM))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(plot, Qt.AlignCenter, "Simulate → full stepfile curve…")
            return

        xs = [s.t_s for s in self._samples]
        ys = [self._y_getter(s) for s in self._samples]
        t0, t1 = xs[0], xs[-1]
        if t1 <= t0:
            t1 = t0 + 1.0

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

        band_start = 0
        for i in range(1, len(self._samples) + 1):
            end = i == len(self._samples)
            if end or self._samples[i].phase != self._samples[band_start].phase:
                phase = self._samples[band_start].phase
                c = QColor(_PHASE_COLOR.get(phase, QColor("#ccc")))
                c.setAlpha(28)
                x0 = map_x(self._samples[band_start].t_s)
                x1 = map_x(self._samples[i - 1].t_s)
                p.fillRect(QRectF(x0, plot.top(), max(1.0, x1 - x0), plot.height()), c)
                band_start = i

        if y_lo < 0 < y_hi:
            p.setPen(QPen(QColor("#c5ced8"), 1, Qt.DashLine))
            p.drawLine(QPointF(plot.left(), map_y(0)), QPointF(plot.right(), map_y(0)))

        poly = QPolygonF()
        for s, y in zip(self._samples, ys):
            poly.append(QPointF(map_x(s.t_s), map_y(y)))
        pen = QPen(self._color, 2.0)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(poly)

        p.setPen(QColor(TEXT_DIM))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRectF(0, map_y(y_hi) - 6, pad_l - 4, 12), Qt.AlignRight, f"{y_hi:.1f}")
        p.drawText(QRectF(0, map_y(y_lo) - 6, pad_l - 4, 12), Qt.AlignRight, f"{y_lo:.1f}")
        p.drawText(
            QRectF(plot.left(), plot.bottom() + 4, plot.width(), 14),
            Qt.AlignLeft,
            self._fmt_time(t0),
        )
        p.drawText(
            QRectF(plot.left(), plot.bottom() + 4, plot.width(), 14),
            Qt.AlignRight,
            self._fmt_time(t1),
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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._get_program: Callable[[], tuple[Program | None, ModuleProfile | None]] | None = None
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
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(10)

        bar = QFrame()
        bar.setObjectName("simBar")
        bar.setStyleSheet(
            f"#simBar {{ background:#ffffff; border:1px solid {BORDER}; border-radius:8px; }}"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)

        self.btn_run = QPushButton("Simulate")
        self.btn_run.setObjectName("btnPrimary")
        self.btn_run.setToolTip(
            "Celá křivka najednou. Start SOC je náhodný — po goto_soc / nabití na limit "
            "by měl zbytek programu vypadat stejně (jen začátek je kratší/delší)."
        )
        self.btn_run.clicked.connect(self._run_full)

        self.lbl_step = QLabel("Simulate → full voltage / current curve from the Editor program.")
        self.lbl_step.setTextFormat(Qt.RichText)
        self.lbl_step.setWordWrap(True)
        self.lbl_step.setStyleSheet(f"color:{TEXT_DIM};")
        self.lbl_vals = QLabel("—")
        self.lbl_vals.setStyleSheet(f"color:{TEXT};font-weight:600;")

        row.addWidget(self.btn_run)
        row.addSpacing(12)
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
            f' &nbsp;&nbsp;·&nbsp;&nbsp; náhodný start · po <b>goto_soc</b> / full charge se křivka srovná'
        )
        legend.setStyleSheet("background: transparent; font-size:11px;")
        root.addWidget(legend)

        self.chart_v = TraceChart("Pack voltage", "V", color=ACCENT)
        self.chart_v.set_y_getter(lambda s: s.voltage_v)
        self.chart_i = TraceChart("Pack current", "A", color=OK)
        self.chart_i.set_y_getter(lambda s: s.current_a)
        root.addWidget(self.chart_v)
        root.addWidget(self.chart_i)
        root.addStretch(1)

    def set_program_provider(
        self, fn: Callable[[], tuple[Program | None, ModuleProfile | None]]
    ) -> None:
        self._get_program = fn

    def _run_full(self) -> None:
        if not self._get_program:
            return
        program, profile = self._get_program()
        if program is None or profile is None:
            self.lbl_step.setText("No program / profile — set Module profile in Editor.")
            return
        if not program.steps:
            self.lbl_step.setText("Program has no steps.")
            return

        self.btn_run.setEnabled(False)
        self.lbl_step.setText("Computing…")
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
                f"Start SOC {start_soc:.0f}% → end {st.soc_pct:.0f}% &nbsp;·&nbsp; "
                f"{len(program.steps)} steps &nbsp;·&nbsp; "
                f"duration {TraceChart._fmt_time(st.t_s)}"
            )
            self.lbl_vals.setText(
                f"start U {start_v:.2f} V → {st.voltage_v:.2f} V   ·   "
                f"{len(st.samples)} pts"
            )
        finally:
            self.btn_run.setEnabled(True)

    def _refresh_view(self) -> None:
        if self._sim is None:
            return
        st = self._sim.state
        samples = st.samples
        self.chart_v.set_samples(samples)
        self.chart_i.set_samples(samples)

        if samples:
            ys = [s.current_a for s in samples]
            lo, hi = min(ys), max(ys)
            pad = max(10.0, (hi - lo) * 0.15)
            if abs(hi - lo) < 1:
                pad = 20.0
            self.chart_i.set_y_range(lo - pad, hi + pad)
