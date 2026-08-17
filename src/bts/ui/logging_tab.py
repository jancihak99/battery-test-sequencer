"""Logování tab — manual CSV logger of live BMS telemetry (Service-Tool style).

Pick signals (checkboxes; whole cell-voltage / temperature arrays are single picks),
set the write rate and format, choose the log file, Start/Stop. A background logger
appends rows until stopped.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bts.csv_logger import (
    FORMAT_SERVICE,
    FORMAT_STANDARD,
    CsvTelemetryLogger,
    Parameter,
    available_parameters,
)
from bts.models.telemetry import BmsTelemetry
from bts.ui.theme import BG_PANEL, BORDER, TEXT_DIM, card_style

log = logging.getLogger(__name__)

# Literal token in the Log File template, substituted with the real timestamp at Start
# (like the Service Tool's "...year_month_day_hhmmss.csv").
TS_TOKEN = "YYYY_MM_DD_HHMMSS"

# Checked on first load — a sensible default set (user tweaks from there).
_DEFAULT_CHECKED = {
    "pack_volt", "pack_curr", "pack_power_kw", "soc_pct",
    "cell_v_min", "cell_v_max", "t_min_c", "t_max_c", "bms_state",
    "temp_all", "cell_v_all",
}

_RATES = [(0.5, "0,5 s"), (1.0, "1 s"), (2.0, "2 s"), (5.0, "5 s"),
          (10.0, "10 s"), (30.0, "30 s"), (60.0, "60 s")]


class LoggingTab(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._bms_provider: Callable[[], object] | None = None
        self._name_provider: Callable[[], str] | None = None
        self._params: list[Parameter] = []
        self._checks: dict[str, QCheckBox] = {}
        self._logger: CsvTelemetryLogger | None = None
        docs = Path.home() / "Documents"
        self._default_dir = docs if docs.exists() else Path.home()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ===== Parameters =====
        head = QHBoxLayout()
        title = QLabel("Parametry")
        title.setStyleSheet(f"color:{TEXT_DIM};font-weight:600;font-size:11px;")
        self.lbl_count = QLabel("—")
        self.lbl_count.setStyleSheet(f"color:{TEXT_DIM};")
        self.btn_refresh = QPushButton("Načíst signály")
        self.btn_refresh.setToolTip("Načte seznam parametrů z živé telemetrie (potřebuje připojené BMS).")
        self.btn_refresh.clicked.connect(self.refresh_signals)
        btn_all = QPushButton("Vybrat vše")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none = QPushButton("Zrušit vše")
        btn_none.clicked.connect(lambda: self._set_all(False))
        head.addWidget(title)
        head.addSpacing(10)
        head.addWidget(self.lbl_count)
        head.addStretch(1)
        head.addWidget(self.btn_refresh)
        head.addWidget(btn_all)
        head.addWidget(btn_none)
        root.addLayout(head)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet(
            f"QScrollArea {{ border:1px solid {BORDER}; border-radius:6px; background:{BG_PANEL}; }}"
        )
        self._sig_host = QWidget()
        self._sig_layout = QVBoxLayout(self._sig_host)
        self._sig_layout.setContentsMargins(14, 12, 14, 12)
        self._sig_layout.setSpacing(10)
        self.scroll.setWidget(self._sig_host)
        root.addWidget(self.scroll, 1)

        # ===== Options =====
        opt = QFrame()
        opt.setObjectName("logOpt")
        opt.setStyleSheet(card_style("logOpt"))
        ol = QVBoxLayout(opt)
        ol.setContentsMargins(16, 12, 16, 12)
        ol.setSpacing(10)

        r1 = QHBoxLayout()
        r1.setSpacing(10)
        r1.addWidget(QLabel("Interval zápisu"))
        self.cmb_rate = QComboBox()
        for secs, lbl in _RATES:
            self.cmb_rate.addItem(lbl, secs)
        self.cmb_rate.setCurrentIndex(1)  # 1 s
        r1.addWidget(self.cmb_rate)
        r1.addSpacing(12)
        r1.addWidget(QLabel("Formát"))
        self.cmb_format = QComboBox()
        self.cmb_format.addItem("Service tool (; , metadata)", FORMAT_SERVICE)
        self.cmb_format.addItem("Standardní (, . ISO)", FORMAT_STANDARD)
        r1.addWidget(self.cmb_format)
        r1.addStretch(1)
        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("btnPrimary")
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("btnDanger")
        self.btn_stop.clicked.connect(self._stop)
        self.btn_stop.setEnabled(False)
        r1.addWidget(self.btn_start)
        r1.addWidget(self.btn_stop)
        ol.addLayout(r1)

        r2 = QHBoxLayout()
        r2.setSpacing(10)
        r2.addWidget(QLabel("Log soubor"))
        self.ed_logfile = QLineEdit()
        self.ed_logfile.setToolTip(
            f"Cesta k CSV. „{TS_TOKEN}“ se při Startu nahradí datem a časem."
        )
        self.ed_logfile.setText(self._suggest_logfile())
        r2.addWidget(self.ed_logfile, 1)
        btn_browse = QPushButton("Procházet…")
        btn_browse.clicked.connect(self._browse)
        r2.addWidget(btn_browse)
        ol.addLayout(r2)

        self.lbl_status = QLabel("Připoj BMS a klikni „Načíst signály“.")
        self.lbl_status.setStyleSheet(f"color:{TEXT_DIM};")
        ol.addWidget(self.lbl_status)
        root.addWidget(opt)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._update_status)

    # ---- wiring -------------------------------------------------------------

    def set_bms_provider(self, fn: Callable[[], object]) -> None:
        self._bms_provider = fn

    def set_name_provider(self, fn: Callable[[], str]) -> None:
        self._name_provider = fn

    # ---- log file -----------------------------------------------------------

    def _base_name(self) -> str:
        base = "bts-log"
        if self._name_provider:
            try:
                base = (self._name_provider() or base).strip() or base
            except Exception:
                pass
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in base)

    def _suggest_logfile(self) -> str:
        return str(self._default_dir / f"{self._base_name()}-{TS_TOKEN}.csv")

    def _browse(self) -> None:
        cur = self.ed_logfile.text().strip() or self._suggest_logfile()
        start = cur.replace(TS_TOKEN, datetime.now().strftime("%Y_%m_%d_%H%M%S"))
        path, _ = QFileDialog.getSaveFileName(self, "Log soubor", start, "CSV (*.csv)")
        if path:
            self.ed_logfile.setText(path)

    # ---- signal list --------------------------------------------------------

    def _get_sample(self) -> BmsTelemetry:
        drv = self._bms_provider() if self._bms_provider else None
        if drv is None:
            return BmsTelemetry()
        try:
            return drv.telemetry()
        except Exception:
            log.exception("Logging: telemetry() failed")
            return BmsTelemetry()

    def _checked_keys(self) -> set[str]:
        return {k for k, cb in self._checks.items() if cb.isChecked()}

    def refresh_signals(self) -> None:
        if self._logger and self._logger.is_running:
            return
        sample = self._get_sample()
        first_time = not self._params
        prev = self._checked_keys()
        self._params = available_parameters(sample)

        while self._sig_layout.count():
            item = self._sig_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())
        self._checks.clear()

        groups: list[str] = []
        for p in self._params:
            if p.group not in groups:
                groups.append(p.group)

        for g in groups:
            params_g = [p for p in self._params if p.group == g]
            grp_cb = QCheckBox(f"{g}  ({len(params_g)})")
            grp_cb.setStyleSheet("font-weight:600;")
            grp_cb.stateChanged.connect(
                lambda st, keys=[p.key for p in params_g]: self._toggle_group(keys, st)
            )
            self._sig_layout.addWidget(grp_cb)

            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(2)
            ncols = 4 if len(params_g) > 8 else 2
            n_on = 0
            for i, p in enumerate(params_g):
                cb = QCheckBox(p.label)
                cb.setToolTip(p.key)
                want = (p.key in prev) if not first_time else (p.key in _DEFAULT_CHECKED)
                cb.setChecked(want)
                n_on += 1 if want else 0
                cb.stateChanged.connect(self._update_count)
                self._checks[p.key] = cb
                grid.addWidget(cb, i // ncols, i % ncols)
            grp_cb.setChecked(n_on == len(params_g))
            self._sig_layout.addLayout(grid)

        self._sig_layout.addStretch(1)
        # keep the Log File template current with the program name
        if TS_TOKEN in self.ed_logfile.text():
            self.ed_logfile.setText(self._suggest_logfile())
        self._update_count()
        if not sample.cell_voltages and not sample.temperatures_c:
            self.lbl_status.setText("Načteno jen základ — pro články/teploty připoj BMS a načti znovu.")
        else:
            self.lbl_status.setText(f"Načteno {len(self._params)} parametrů.")

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _toggle_group(self, keys: list[str], state: int) -> None:
        on = bool(state)  # plain checkbox: 0 = unchecked, 2 = checked
        for k in keys:
            cb = self._checks.get(k)
            if cb is not None:
                cb.setChecked(on)

    def _set_all(self, on: bool) -> None:
        for cb in self._checks.values():
            cb.setChecked(on)

    def _update_count(self) -> None:
        n = len(self._checked_keys())
        cols = sum(len(p.specs) for p in self._params if p.key in self._checked_keys())
        self.lbl_count.setText(f"{n} / {len(self._checks)} parametrů · {cols} sloupců")

    # ---- start / stop -------------------------------------------------------

    def _selected_specs(self):
        keys = self._checked_keys()
        specs = []
        for p in self._params:
            if p.key in keys:
                specs.extend(p.specs)
        return specs

    def _start(self) -> None:
        if self._logger and self._logger.is_running:
            return
        specs = self._selected_specs()
        if not specs:
            QMessageBox.warning(self, "Logování", "Vyber aspoň jeden parametr.")
            return
        drv = self._bms_provider() if self._bms_provider else None
        if drv is None:
            QMessageBox.warning(self, "Logování", "Není připojené BMS (ani mock). Nejdřív Připojit HW.")
            return
        raw = self.ed_logfile.text().strip()
        if not raw:
            QMessageBox.warning(self, "Logování", "Zadej cestu k log souboru.")
            return
        path = Path(raw.replace(TS_TOKEN, datetime.now().strftime("%Y_%m_%d_%H%M%S")))
        fmt = self.cmb_format.currentData()
        try:
            import getpass

            user = getpass.getuser()
        except Exception:
            user = ""
        self._logger = CsvTelemetryLogger(
            self._bms_provider, specs, float(self.cmb_rate.currentData()), fmt, user=user
        )
        try:
            self._logger.start(path)
        except Exception as exc:
            QMessageBox.critical(self, "Logování", f"Nepodařilo se spustit: {exc}")
            self._logger = None
            return
        self._set_running(True)
        self._timer.start()
        self._update_status()

    def _stop(self) -> None:
        if self._logger is not None:
            self._logger.stop()
        self._timer.stop()
        self._set_running(False)
        self._update_status(final=True)
        # refresh the Log File template so the next run gets a fresh timestamp/name
        if not self.ed_logfile.text().strip() or TS_TOKEN not in self.ed_logfile.text():
            self.ed_logfile.setText(self._suggest_logfile())

    def stop_if_running(self) -> None:
        """Called by the main window on HW disconnect / app close."""
        if self._logger is not None and self._logger.is_running:
            self._stop()

    def _set_running(self, running: bool) -> None:
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_refresh.setEnabled(not running)
        self.cmb_rate.setEnabled(not running)
        self.cmb_format.setEnabled(not running)
        self.ed_logfile.setEnabled(not running)
        for cb in self._checks.values():
            cb.setEnabled(not running)

    def _update_status(self, *, final: bool = False) -> None:
        lg = self._logger
        if lg is None:
            return
        if lg.error:
            self.lbl_status.setText(f"CHYBA: {lg.error}")
            return
        state = "Hotovo" if final else "Loguji"
        self.lbl_status.setText(
            f"{state} → {lg.path.name if lg.path else '?'} · "
            f"{lg.rows_written} řádků · {lg.elapsed_s:.0f} s · interval {lg.interval_s:g}s"
        )
