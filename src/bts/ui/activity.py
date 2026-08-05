"""Subtle run activity animation + program progress bar."""
from __future__ import annotations

import math
import time
from enum import Enum

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from bts.engine.estimate import ProgramEstimate, format_duration
from bts.engine.sequence import RunState
from bts.ui.theme import ACCENT, BORDER, CHART_BG, OK, TEXT, TEXT_DIM

# Full ring/power animation speed at 6C (matches schematic particle max).
_MAX_C_RATE = 6.0


class ActivityMode(str, Enum):
    IDLE = "idle"
    CHARGE = "charge"
    DISCHARGE = "discharge"
    WAIT = "wait"
    READY = "ready"
    DONE = "done"
    ERROR = "error"
    ABORTED = "aborted"


_MODE_LABEL = {
    ActivityMode.IDLE: "Nečinný",
    ActivityMode.CHARGE: "Nabíjení",
    ActivityMode.DISCHARGE: "Vybíjení",
    ActivityMode.WAIT: "Čekání",
    ActivityMode.READY: "READY",
    ActivityMode.DONE: "Hotovo",
    ActivityMode.ERROR: "Chyba",
    ActivityMode.ABORTED: "Zastaveno",
}


class ActivityRing(QWidget):
    """Status ring — charge/discharge speed scales with C-rate (max = 6C)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(64, 64)
        self._mode = ActivityMode.IDLE
        self._phase = 0.0
        self._crate_frac = 0.0  # 0…1 vs 6C
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_mode(self, mode: ActivityMode) -> None:
        if mode != self._mode:
            self._mode = mode
            self.update()

    def set_crate_frac(self, frac: float) -> None:
        """0 = no current-driven motion; 1.0 = 6C (previous full speed)."""
        self._crate_frac = max(0.0, min(1.0, float(frac)))

    def mode(self) -> ActivityMode:
        return self._mode

    def _tick(self) -> None:
        mode = self._mode
        if mode in (ActivityMode.CHARGE, ActivityMode.DISCHARGE):
            # 6C → 2.4 (old full speed); 3C → 1.2; 0A → soft breathe only
            speed = 2.4 * self._crate_frac
            if speed < 0.02:
                speed = 0.55
        elif mode == ActivityMode.WAIT:
            speed = 1.35
        elif mode == ActivityMode.READY:
            speed = 0.95
        elif mode == ActivityMode.IDLE:
            speed = 0.7
        elif mode == ActivityMode.ERROR:
            speed = 1.9
        else:
            speed = 0.0
        if speed > 0:
            self._phase = (self._phase + speed * 0.033) % (math.pi * 2)
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        r = 22.0
        track = QRectF(cx - r, cy - r, 2 * r, 2 * r)

        p.setPen(QPen(QColor("#dce3ea"), 4.0))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(track)

        mode = self._mode
        frac = self._crate_frac

        if mode == ActivityMode.IDLE:
            alpha = int(80 + 70 * (0.5 + 0.5 * math.sin(self._phase)))
            pen = QPen(QColor(ACCENT))
            pen.setWidthF(3.0)
            pen.setCapStyle(Qt.RoundCap)
            c = QColor(ACCENT)
            c.setAlpha(alpha)
            pen.setColor(c)
            p.setPen(pen)
            start = int((-90 + math.degrees(self._phase * 0.25)) * 16)
            p.drawArc(track, start, 55 * 16)
            self._draw_dot(p, cx, cy, QColor("#9aabba"))

        elif mode == ActivityMode.CHARGE:
            pen = QPen(QColor(OK))
            pen.setWidthF(3.6 + 1.2 * frac)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            start = int((-90 + math.degrees(self._phase)) * 16)
            span = int((100 + 50 * frac + 30 * math.sin(self._phase * 1.4)) * 16)
            p.drawArc(track, -start, -span)
            fill = 0.32 + 0.28 * frac + 0.12 * (0.5 + 0.5 * math.sin(self._phase * 0.7))
            self._draw_battery(p, cx, cy, fill, QColor(OK), charging=True)

        elif mode == ActivityMode.DISCHARGE:
            pen = QPen(QColor("#c47a3a"))
            pen.setWidthF(3.6 + 1.2 * frac)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            start = int((-90 - math.degrees(self._phase)) * 16)
            span = int((100 + 50 * frac + 30 * math.sin(self._phase * 1.4)) * 16)
            p.drawArc(track, start, -span)
            fill = 0.72 - 0.28 * frac - 0.12 * (0.5 + 0.5 * math.sin(self._phase * 0.7))
            self._draw_battery(p, cx, cy, max(0.12, fill), QColor("#c47a3a"), charging=False)

        elif mode == ActivityMode.WAIT:
            pen = QPen(QColor(ACCENT))
            pen.setWidthF(3.4)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            for k in range(3):
                ang = self._phase + k * (math.pi * 2 / 3)
                start = int((-90 + math.degrees(ang)) * 16)
                p.drawArc(track, -start, -42 * 16)
            pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(self._phase * 1.2))
            c = QColor(ACCENT)
            c.setAlpha(int(90 + 100 * pulse))
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(cx, cy), 4.5, 4.5)

        elif mode == ActivityMode.READY:
            breath = 0.5 + 0.5 * math.sin(self._phase)
            pen = QPen(QColor(OK))
            pen.setWidthF(3.2 + breath)
            pen.setCapStyle(Qt.RoundCap)
            c = QColor(OK)
            c.setAlpha(int(120 + 100 * breath))
            pen.setColor(c)
            p.setPen(pen)
            p.drawEllipse(track)
            self._draw_dot(p, cx, cy, QColor(OK))

        elif mode == ActivityMode.DONE:
            pen = QPen(QColor(OK))
            pen.setWidthF(3.5)
            p.setPen(pen)
            p.drawEllipse(track)
            p.setPen(QPen(QColor(OK), 2.4))
            p.drawLine(int(cx - 7), int(cy + 1), int(cx - 1), int(cy + 7))
            p.drawLine(int(cx - 1), int(cy + 7), int(cx + 9), int(cy - 6))

        elif mode == ActivityMode.ERROR:
            pulse = 0.55 + 0.45 * abs(math.sin(self._phase * 2))
            c = QColor("#c0392b")
            c.setAlphaF(pulse)
            pen = QPen(c)
            pen.setWidthF(3.5)
            p.setPen(pen)
            p.drawEllipse(track)
            p.setPen(QPen(QColor("#c0392b"), 2.2))
            p.drawLine(int(cx - 5), int(cy - 5), int(cx + 5), int(cy + 5))
            p.drawLine(int(cx + 5), int(cy - 5), int(cx - 5), int(cy + 5))

        else:  # ABORTED
            pen = QPen(QColor("#8a6a20"))
            pen.setWidthF(3.2)
            p.setPen(pen)
            p.drawEllipse(track)
            p.setPen(QPen(QColor("#8a6a20"), 2.4))
            p.drawLine(int(cx - 6), int(cy), int(cx + 6), int(cy))

    def _draw_dot(self, p: QPainter, cx: float, cy: float, color: QColor) -> None:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QRectF(cx - 3, cy - 3, 6, 6))

    def _draw_battery(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        fill: float,
        color: QColor,
        *,
        charging: bool,
    ) -> None:
        bw, bh = 12.0, 8.0
        body = QRectF(cx - bw / 2, cy - bh / 2, bw, bh)
        p.setPen(QPen(color, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(body, 1.5, 1.5)
        # nub
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(cx + bw / 2, cy - 2, 2.2, 4), 0.6, 0.6)
        # fill
        fill = max(0.08, min(1.0, fill))
        inner = QRectF(body.x() + 1.5, body.y() + 1.5, (bw - 3) * fill, bh - 3)
        c = QColor(color)
        c.setAlpha(160)
        p.setBrush(c)
        p.drawRoundedRect(inner, 1.0, 1.0)
        # tiny arrow
        p.setPen(QPen(color, 1.3))
        if charging:
            p.drawLine(int(cx), int(cy + 1), int(cx), int(cy - 3))
            p.drawLine(int(cx), int(cy - 3), int(cx - 2.5), int(cy - 1))
            p.drawLine(int(cx), int(cy - 3), int(cx + 2.5), int(cy - 1))
        else:
            p.drawLine(int(cx), int(cy - 1), int(cx), int(cy + 3))
            p.drawLine(int(cx), int(cy + 3), int(cx - 2.5), int(cy + 1))
            p.drawLine(int(cx), int(cy + 3), int(cx + 2.5), int(cy + 1))


class RunProgressPanel(QFrame):
    """Activity ring + estimated program progress."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("runProgress")
        self.setStyleSheet(
            f"#runProgress {{ background:{CHART_BG}; border:1px solid {BORDER};"
            f" border-radius:8px; }}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(86)

        self._estimate: ProgramEstimate | None = None
        self._run_state = RunState.IDLE
        self._step_index = -1
        self._step_id: str | None = None
        self._step_type: str | None = None
        self._step_started_mono: float | None = None
        self._run_started_mono: float | None = None
        self._frozen_elapsed: float | None = None
        self._ea_charging = False
        self._ea_discharging = False
        self._capacity_ah = 70.0
        self._current_a = 0.0
        self._bmu_ready = False

        # Keep elapsed / progress moving even if live poll stalls briefly
        self._tick = QTimer(self)
        self._tick.setInterval(250)
        self._tick.timeout.connect(self._paint_progress)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 10, 14, 10)
        root.setSpacing(14)

        self.ring = ActivityRing()
        root.addWidget(self.ring, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.lbl_mode = QLabel("Nečinný")
        self.lbl_mode.setStyleSheet(
            f"color:{TEXT};font-size:14px;font-weight:700;"
        )
        self.lbl_step = QLabel("Žádný běžící program")
        self.lbl_step.setStyleSheet(f"color:{TEXT_DIM};font-size:12px;")
        top.addWidget(self.lbl_mode, 0)
        top.addWidget(self.lbl_step, 1)
        self.lbl_pct = QLabel("")
        self.lbl_pct.setStyleSheet(
            f"color:{ACCENT};font-size:13px;font-weight:700;"
        )
        self.lbl_pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(self.lbl_pct, 0)
        col.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background:#ffffff; border:1px solid {BORDER};"
            f" border-radius:4px; }}"
            f"QProgressBar::chunk {{ background:{ACCENT}; border-radius:3px; }}"
        )
        col.addWidget(self.bar)

        times = QHBoxLayout()
        times.setSpacing(8)
        self.lbl_elapsed = QLabel("0:00")
        self.lbl_total = QLabel("Estimate —")
        self.lbl_remain = QLabel("")
        for lab in (self.lbl_elapsed, self.lbl_total, self.lbl_remain):
            lab.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        times.addWidget(self.lbl_elapsed)
        times.addStretch(1)
        times.addWidget(self.lbl_total)
        times.addStretch(1)
        times.addWidget(self.lbl_remain)
        col.addLayout(times)

        root.addLayout(col, 1)

    def set_estimate(self, estimate: ProgramEstimate | None) -> None:
        self._estimate = estimate
        self._refresh_static_estimate()
        self._paint_progress()

    def set_capacity_ah(self, ah: float) -> None:
        self._capacity_ah = max(1.0, float(ah))

    def set_live_current(self, amps: float, *, bmu_ready: bool = False) -> None:
        """Drive animation intensity from measured |I| (Ah → C-rate vs 6C max)."""
        self._current_a = abs(float(amps))
        self._bmu_ready = bool(bmu_ready)
        cap = self._capacity_ah
        frac = 0.0 if cap <= 0 else min(1.0, self._current_a / cap / _MAX_C_RATE)
        self.ring.set_crate_frac(frac)
        # Refresh labels when current changes during a run
        if self._run_state == RunState.RUNNING:
            self._update_activity()

    def set_preview_activity(
        self,
        *,
        charging: bool = False,
        discharging: bool = False,
        waiting: bool = False,
        ready: bool = False,
        current_a: float = 0.0,
        label: str | None = None,
    ) -> None:
        """Drive the ring when HW is connected but no program is running."""
        if self._run_state == RunState.RUNNING:
            return
        self._current_a = abs(float(current_a))
        cap = self._capacity_ah
        frac = 0.0 if cap <= 0 else min(1.0, self._current_a / cap / _MAX_C_RATE)
        self.ring.set_crate_frac(frac)
        if charging:
            mode = ActivityMode.CHARGE
        elif discharging:
            mode = ActivityMode.DISCHARGE
        elif ready:
            mode = ActivityMode.READY
        elif waiting:
            mode = ActivityMode.WAIT
        else:
            mode = ActivityMode.IDLE
        self.ring.set_mode(mode)
        mode_txt = _MODE_LABEL[mode]
        if mode in (ActivityMode.CHARGE, ActivityMode.DISCHARGE) and self._current_a > 0.5:
            crate = self._current_a / cap
            mode_txt = f"{mode_txt} · {crate:.2f}C"
        self.lbl_mode.setText(mode_txt)
        self.lbl_step.setText(label or "HW připojeno · čekám na Start")
        chunk = {
            ActivityMode.CHARGE: OK,
            ActivityMode.DISCHARGE: "#c47a3a",
            ActivityMode.READY: OK,
        }.get(mode, ACCENT)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background:#ffffff; border:1px solid {BORDER};"
            f" border-radius:4px; }}"
            f"QProgressBar::chunk {{ background:{chunk}; border-radius:3px; }}"
        )

    def reset_idle(self) -> None:
        self._run_state = RunState.IDLE
        self._step_index = -1
        self._step_id = None
        self._step_type = None
        self._step_started_mono = None
        self._run_started_mono = None
        self._frozen_elapsed = None
        self._ea_charging = False
        self._ea_discharging = False
        self._tick.stop()
        self.ring.set_mode(ActivityMode.IDLE)
        self.ring.set_crate_frac(0.0)
        self.lbl_mode.setText("Nečinný")
        self.lbl_step.setText("Žádný běžící program")
        self.lbl_pct.setText("")
        self.bar.setValue(0)
        self.lbl_elapsed.setText("0:00")
        self.lbl_remain.setText("")
        self._refresh_static_estimate()

    def _refresh_static_estimate(self) -> None:
        if self._run_state == RunState.RUNNING:
            return
        if self._estimate is None:
            self.lbl_total.setText("Estimate —")
            return
        approx = self._estimate.any_uncertain
        self.lbl_total.setText(
            f"Plan {format_duration(self._estimate.total_seconds, approx=approx)}"
            f"  ·  {len(self._estimate.steps)} steps"
        )

    def update_run(
        self,
        *,
        run_state: RunState,
        step_index: int,
        step_id: str | None,
        step_type: str | None,
        step_started_mono: float | None,
        run_started_mono: float | None,
        ea_charging: bool = False,
        ea_discharging: bool = False,
    ) -> None:
        self._run_state = run_state
        self._step_index = step_index
        self._step_id = step_id
        self._step_type = step_type
        self._step_started_mono = step_started_mono
        self._run_started_mono = run_started_mono
        self._ea_charging = ea_charging
        self._ea_discharging = ea_discharging
        if run_state == RunState.RUNNING:
            self._frozen_elapsed = None
            if not self._tick.isActive():
                self._tick.start()
        else:
            self._tick.stop()
            if (
                run_state
                in (RunState.COMPLETED, RunState.FAILED, RunState.ABORTED)
                and self._frozen_elapsed is None
                and run_started_mono is not None
            ):
                self._frozen_elapsed = max(0.0, time.monotonic() - run_started_mono)
        self._update_activity()
        self._paint_progress()

    def _update_activity(self) -> None:
        st = self._run_state
        if st == RunState.COMPLETED:
            mode = ActivityMode.DONE
        elif st == RunState.FAILED:
            mode = ActivityMode.ERROR
        elif st == RunState.ABORTED:
            mode = ActivityMode.ABORTED
        elif st != RunState.RUNNING:
            mode = ActivityMode.IDLE
        elif self._ea_charging:
            mode = ActivityMode.CHARGE
        elif self._ea_discharging:
            mode = ActivityMode.DISCHARGE
        elif self._step_type == "charge":
            mode = ActivityMode.CHARGE
        elif self._step_type == "discharge":
            mode = ActivityMode.DISCHARGE
        elif self._step_type == "bms_ready":
            mode = ActivityMode.READY
        elif self._step_type in ("wait_temp", "wait_time", "bms_idle", "dcir", "goto_soc"):
            mode = ActivityMode.WAIT
        else:
            mode = ActivityMode.WAIT if self._step_type else ActivityMode.IDLE

        self.ring.set_mode(mode)
        mode_txt = _MODE_LABEL[mode]
        if mode in (ActivityMode.CHARGE, ActivityMode.DISCHARGE) and self._current_a > 0.5:
            crate = self._current_a / self._capacity_ah
            mode_txt = f"{mode_txt} · {crate:.2f}C"
        self.lbl_mode.setText(mode_txt)

        if st == RunState.RUNNING and self._step_id:
            n = len(self._estimate.steps) if self._estimate else "?"
            idx = self._step_index + 1 if self._step_index >= 0 else "?"
            typ = self._step_type or ""
            self.lbl_step.setText(f"{self._step_id}  ·  {typ}  ·  krok {idx}/{n}")
        elif st == RunState.COMPLETED:
            self.lbl_step.setText("Všechny kroky dokončeny")
        elif st in (RunState.FAILED, RunState.ABORTED):
            self.lbl_step.setText(self._step_id or "Zastaveno")
        else:
            self.lbl_step.setText("Žádný běžící program")

        chunk = {
            ActivityMode.CHARGE: OK,
            ActivityMode.DISCHARGE: "#c47a3a",
            ActivityMode.READY: OK,
            ActivityMode.DONE: OK,
            ActivityMode.ERROR: "#c0392b",
            ActivityMode.ABORTED: "#c47a3a",
        }.get(mode, ACCENT)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background:#ffffff; border:1px solid {BORDER};"
            f" border-radius:4px; }}"
            f"QProgressBar::chunk {{ background:{chunk}; border-radius:3px; }}"
        )

    def _paint_progress(self) -> None:
        est = self._estimate
        now = time.monotonic()

        if self._frozen_elapsed is not None:
            elapsed = self._frozen_elapsed
            self.lbl_elapsed.setText(f"Elapsed {format_duration(elapsed)}")
        elif self._run_started_mono is not None and self._run_state == RunState.RUNNING:
            elapsed = max(0.0, now - self._run_started_mono)
            self.lbl_elapsed.setText(f"Elapsed {format_duration(elapsed)}")
        else:
            elapsed = 0.0
            self.lbl_elapsed.setText("Elapsed 0:00")

        if est is None or not est.steps:
            self.bar.setValue(0)
            self.lbl_pct.setText("")
            self._refresh_static_estimate()
            return

        approx = est.any_uncertain
        total = max(0.1, est.total_seconds)

        if self._run_state == RunState.COMPLETED:
            self.bar.setValue(1000)
            self.lbl_pct.setText("100%")
            self.lbl_total.setText(
                f"Total {format_duration(elapsed)}  ·  plan {format_duration(total, approx=approx)}"
            )
            self.lbl_remain.setText("Done")
            return

        if self._run_state in (RunState.FAILED, RunState.ABORTED):
            # Partial progress at stop
            done = est.completed_seconds(max(0, self._step_index))
            frac = min(1.0, done / total)
            self.bar.setValue(int(frac * 1000))
            self.lbl_pct.setText(f"{int(frac * 100)}%")
            self.lbl_total.setText(
                f"Plan {format_duration(total, approx=approx)}"
            )
            self.lbl_remain.setText("Stopped")
            return

        if self._run_state != RunState.RUNNING:
            self.bar.setValue(0)
            self.lbl_pct.setText("")
            self._refresh_static_estimate()
            self.lbl_remain.setText("")
            return

        idx = max(0, self._step_index)
        done = est.completed_seconds(idx)
        step_est = est.steps[idx].seconds if idx < len(est.steps) else 1.0
        if self._step_started_mono is not None and step_est > 0:
            # Cap at end of this step's share while overrunning estimate —
            # elapsed / Left still move; bar must not look frozen mid-step.
            in_step = (now - self._step_started_mono) / step_est
            in_step = max(0.0, min(1.0, in_step))
        else:
            in_step = 0.0
        progress_s = done + in_step * step_est
        frac = max(0.0, min(1.0, progress_s / total))
        self.bar.setValue(int(frac * 1000))
        self.lbl_pct.setText(f"{int(frac * 100)}%")

        remain = max(0.0, total - progress_s)
        # If wall clock already exceeded plan, show elapsed-based remaining soft floor
        if elapsed > progress_s:
            remain = max(0.0, total - elapsed)

        self.lbl_total.setText(
            f"Plan {format_duration(total, approx=approx)}"
        )
        self.lbl_remain.setText(
            f"Left {format_duration(remain, approx=approx)}"
        )
