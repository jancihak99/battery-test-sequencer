"""Step parameter form with fields shown/hidden by step type."""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from bts.models.program import STEP_TYPES, Step


class StepForm(QWidget):
    """Type-aware step editor. Emits applied(Step) when Apply is pressed."""

    applied = Signal(object)  # Step
    type_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color:#555;")

        self.ed_step_id = QLineEdit()
        self.ed_step_type = QComboBox()
        self.ed_step_type.addItems(list(STEP_TYPES))
        self.ed_step_type.currentTextChanged.connect(self._on_type_changed)

        self.ed_seconds = QSpinBox()
        self.ed_seconds.setRange(1, 86400)
        self.ed_seconds.setSuffix(" s")
        self.ed_seconds.setValue(60)

        self.ed_timeout = QSpinBox()
        self.ed_timeout.setRange(1, 86400)
        self.ed_timeout.setSuffix(" s")
        self.ed_timeout.setValue(60)

        self.ed_current = QDoubleSpinBox()
        self.ed_current.setRange(0.1, 2000)
        self.ed_current.setDecimals(1)
        self.ed_current.setSuffix(" A")
        self.ed_current.setValue(70)

        self.chk_use_crate = QCheckBox("Zadat jako C-rate (× Ah profilu)")
        self.ed_crate = QDoubleSpinBox()
        self.ed_crate.setRange(0.01, 20)
        self.ed_crate.setDecimals(2)
        self.ed_crate.setSuffix(" C")
        self.ed_crate.setValue(1.0)

        self.ed_voltage = QDoubleSpinBox()
        self.ed_voltage.setRange(0.1, 100)
        self.ed_voltage.setDecimals(2)
        self.ed_voltage.setSuffix(" V")
        self.ed_voltage.setValue(27.5)

        self.ed_imin = QDoubleSpinBox()
        self.ed_imin.setRange(0.05, 200)
        self.ed_imin.setDecimals(2)
        self.ed_imin.setSuffix(" A")
        self.ed_imin.setValue(3.5)
        self.chk_stop_imin = QCheckBox("Stop on CV end-current (i_min)")
        self.chk_stop_imin.setToolTip(
            "Jen u nabíjení bez měření kapacity (plné CV). "
            "Kapacita se měří na výbíjení CC až do Vmin — tam i_min nepatří."
        )

        self.ed_pulse_s = QDoubleSpinBox()
        self.ed_pulse_s.setRange(0.1, 60)
        self.ed_pulse_s.setDecimals(1)
        self.ed_pulse_s.setSuffix(" s")
        self.ed_pulse_s.setValue(10)

        self.ed_tmax = QDoubleSpinBox()
        self.ed_tmax.setRange(-60, 100)
        self.ed_tmax.setSuffix(" °C")
        self.ed_tmax.setValue(35)

        self.ed_tmin = QDoubleSpinBox()
        self.ed_tmin.setRange(-60, 100)
        self.ed_tmin.setSuffix(" °C")
        self.ed_tmin.setValue(-40)

        self.chk_use_tmin = QCheckBox("Use Tmin limit")
        self.chk_use_tmax = QCheckBox("Use Tmax limit")
        self.chk_use_tmax.setChecked(True)

        self.ed_pack_vmin = QDoubleSpinBox()
        self.ed_pack_vmin.setRange(0, 100)
        self.ed_pack_vmin.setDecimals(2)
        self.ed_pack_vmin.setSuffix(" V")
        self.ed_pack_vmax = QDoubleSpinBox()
        self.ed_pack_vmax.setRange(0, 100)
        self.ed_pack_vmax.setDecimals(2)
        self.ed_pack_vmax.setSuffix(" V")
        self.ed_cell_vmin = QDoubleSpinBox()
        self.ed_cell_vmin.setRange(0, 5)
        self.ed_cell_vmin.setDecimals(3)
        self.ed_cell_vmin.setSuffix(" V")
        self.ed_cell_vmax = QDoubleSpinBox()
        self.ed_cell_vmax.setRange(0, 5)
        self.ed_cell_vmax.setDecimals(3)
        self.ed_cell_vmax.setSuffix(" V")
        self.ed_soc = QDoubleSpinBox()
        self.ed_soc.setRange(0, 100)
        self.ed_soc.setSuffix(" %")
        self.ed_ah = QDoubleSpinBox()
        self.ed_ah.setRange(0, 500)
        self.ed_ah.setDecimals(2)
        self.ed_ah.setSuffix(" Ah")

        self.chk_stop_pack_vmin = QCheckBox("Stop on pack Vmin")
        self.chk_stop_pack_vmax = QCheckBox("Stop on pack Vmax")
        self.chk_stop_cell_vmin = QCheckBox("Stop on any cell Vmin")
        self.chk_stop_cell_vmax = QCheckBox("Stop on any cell Vmax")
        self.chk_stop_soc = QCheckBox("Stop on SOC")
        self.chk_stop_ah = QCheckBox("Stop on Ah target")

        self.ed_abort_tmax = QDoubleSpinBox()
        self.ed_abort_tmax.setRange(-60, 100)
        self.ed_abort_tmax.setSuffix(" °C")
        self.ed_abort_tmax.setValue(55)
        self.chk_abort_tmax = QCheckBox("Abort on Tmax")
        self.chk_abort_tmax.setChecked(True)
        self.ed_abort_dtc = QSpinBox()
        self.ed_abort_dtc.setRange(0, 4)
        self.ed_abort_dtc.setValue(2)
        self.chk_abort_dtc = QCheckBox("Abort při DTC level ≥")
        self.chk_abort_dtc.setChecked(True)

        self.chk_soc_ref = QCheckBox("DCIR: čekat na SOC")
        self.ed_soc_ref = QDoubleSpinBox()
        self.ed_soc_ref.setRange(0, 100)
        self.ed_soc_ref.setDecimals(0)
        self.ed_soc_ref.setSuffix(" %")
        self.ed_soc_ref.setValue(50)

        self.chk_respect_bmu = QCheckBox("Omezit I podle BMU limit (charge/discharge)")
        self.chk_respect_bmu.setToolTip(
            "Default ON: appka omezí proud podle BMU command limit, aby se snížilo riziko Derate/Disconnect "
            "(DTC=2/3) a rozepnutí stykačů. U měření kapacity/DCIR: pokud by BMU snížila proud pod setpoint, "
            "run se ukončí (zkreslený výsledek)."
        )
        self.ed_message = QLineEdit()
        self.chk_record = QCheckBox("Zaznamenat stopu (CSV + grafy v reportu)")
        self.chk_record.setToolTip(
            "Vzorkuje V/I/SOC během tohoto kroku. Bez zaškrtnutí se do CSV nic neukládá. "
            "U discharge s „Měřit kapacitu“ se zapne automaticky."
        )
        self.ed_measure_cap = QCheckBox("Měřit kapacitu (CC do limitu → report)")
        self.ed_measure_cap.setToolTip(
            "Jen u výbíjení: konstantní proud až do stop Vmin/článek/SOC/Ah. "
            "Ne CV (i_min) — to patří jen u plného nabíjení."
        )
        self.ed_measure_key = QLineEdit("capacity_ah")
        self.ed_measure_key.setPlaceholderText("capacity_ah / capacity_1c_ah / …")
        self.ed_measure_cap.toggled.connect(self._on_measure_toggled)
        self.chk_rep_cap = QCheckBox("capacity_ah")
        self.chk_rep_cap_1c = QCheckBox("capacity_1c_ah")
        self.chk_rep_cap_3c = QCheckBox("capacity_3c_ah")
        self.chk_rep_dcir = QCheckBox("dcir_mohm")
        self.chk_rep_cells = QCheckBox("cells (all)")
        self.chk_rep_temps = QCheckBox("temps")
        self.chk_rep_dtc = QCheckBox("dtc")
        for c in (
            self.chk_rep_cap,
            self.chk_rep_cap_1c,
            self.chk_rep_cap_3c,
            self.chk_rep_dcir,
            self.chk_rep_cells,
            self.chk_rep_temps,
            self.chk_rep_dtc,
        ):
            c.setChecked(True)
        self.chk_rep_cap_1c.setChecked(False)
        self.chk_rep_cap_3c.setChecked(False)

        self.btn_apply = QPushButton("Apply step changes")
        self.btn_apply.clicked.connect(self._emit_apply)

        form = QFormLayout()
        self._rows: dict[str, list] = {}

        def add(key: str, label: str, widget) -> None:
            form.addRow(label, widget)
            # store the label widget from form — use wrapper
            self._rows[key] = (label, widget)

        # We need show/hide of whole rows — use labels as keys via QWidget wrappers
        self._row_widgets: dict[str, tuple[QLabel, QWidget]] = {}

        def add_row(key: str, label: str, widget: QWidget) -> None:
            lab = QLabel(label)
            form.addRow(lab, widget)
            self._row_widgets[key] = (lab, widget)

        add_row("id", "Step id", self.ed_step_id)
        add_row("type", "Type", self.ed_step_type)
        add_row("seconds", "Duration (wait_time)", self.ed_seconds)
        add_row("timeout", "Timeout", self.ed_timeout)
        add_row("use_crate", "", self.chk_use_crate)
        add_row("crate", "C-rate", self.ed_crate)
        add_row("current", "Current / pulse (A)", self.ed_current)
        add_row("voltage", "Charge voltage", self.ed_voltage)
        add_row("pulse_s", "Pulse duration", self.ed_pulse_s)
        add_row("use_tmax", "", self.chk_use_tmax)
        add_row("tmax", "T max", self.ed_tmax)
        add_row("use_tmin", "", self.chk_use_tmin)
        add_row("tmin", "T min", self.ed_tmin)
        add_row("stop_pack_vmax", "", self.chk_stop_pack_vmax)
        add_row("pack_vmax", "Pack Vmax", self.ed_pack_vmax)
        add_row("stop_pack_vmin", "", self.chk_stop_pack_vmin)
        add_row("pack_vmin", "Pack Vmin", self.ed_pack_vmin)
        add_row("stop_cell_vmax", "", self.chk_stop_cell_vmax)
        add_row("cell_vmax", "Any cell Vmax", self.ed_cell_vmax)
        add_row("stop_cell_vmin", "", self.chk_stop_cell_vmin)
        add_row("cell_vmin", "Any cell Vmin", self.ed_cell_vmin)
        add_row("stop_soc", "", self.chk_stop_soc)
        add_row("soc", "SOC", self.ed_soc)
        add_row("stop_imin", "", self.chk_stop_imin)
        add_row("imin", "i_min (CV cutoff)", self.ed_imin)
        add_row("stop_ah", "", self.chk_stop_ah)
        add_row("ah", "Ah target", self.ed_ah)
        add_row("abort_tmax", "", self.chk_abort_tmax)
        add_row("abort_tmax_v", "Abort Tmax", self.ed_abort_tmax)
        add_row("abort_dtc", "", self.chk_abort_dtc)
        add_row("abort_dtc_v", "DTC level ≥", self.ed_abort_dtc)
        add_row("soc_ref_chk", "", self.chk_soc_ref)
        add_row("soc_ref", "SOC reference", self.ed_soc_ref)
        add_row("respect_bmu", "", self.chk_respect_bmu)
        add_row("record", "", self.chk_record)
        add_row("measure", "", self.ed_measure_cap)
        add_row("measure_key", "Klíč kapacity", self.ed_measure_key)
        add_row("message", "Message", self.ed_message)
        add_row("rep_cap", "", self.chk_rep_cap)
        add_row("rep_cap_1c", "", self.chk_rep_cap_1c)
        add_row("rep_cap_3c", "", self.chk_rep_cap_3c)
        add_row("rep_dcir", "", self.chk_rep_dcir)
        add_row("rep_cells", "", self.chk_rep_cells)
        add_row("rep_temps", "", self.chk_rep_temps)
        add_row("rep_dtc", "", self.chk_rep_dtc)

        layout = QVBoxLayout(self)
        layout.addWidget(self._hint)
        layout.addLayout(form)
        layout.addWidget(self.btn_apply)
        layout.addStretch()
        self._on_type_changed(self.ed_step_type.currentText())

    def _show(self, *keys: str) -> None:
        show = set(keys) | {"id", "type"}
        for key, (lab, wid) in self._row_widgets.items():
            vis = key in show
            lab.setVisible(vis)
            wid.setVisible(vis)

    def _on_type_changed(self, stype: str) -> None:
        hints = {
            "wait_time": "Délka ve vteřinách. Změny se berou automaticky při změně řádku / Validate / Save.",
            "wait_temp": "Wait until temperature is within limits (all sensors via BMS).",
            "charge": "CC→CV na PSI. CV cutoff (i_min) jen u plného nabití — ne u měření kapacity. "
            "„Zaznamenat stopu“ = CSV + V/I grafy.",
            "discharge": "CC na EL až do Vmin/článek/SOC/Ah (kapacita = measure). Ne CV. "
            "„Zaznamenat stopu“ = CSV + V/I grafy.",
            "goto_soc": "Z neznámého SOC nabije nebo vybije na cíl (auto směr). Ideální precondition.",
            "bms_ready": "BMU READY (stykače). Po sepnutí engine vždy čeká 10 s před dalším krokem.",
            "bms_idle": "Open contactors / IDLE.",
            "dcir": "Pulse na EL. C-rate nebo A. Zapni „Zaznamenat stopu“ pro V/I během pulsu.",
            "notify": "Jen status zpráva (bez dialogu).",
            "report": "HTML/JSON report: kapacity, DCIR, grafy V/I/SOC ze stop s „Zaznamenat stopu“.",
        }
        self._hint.setText(hints.get(stype, ""))
        if stype == "wait_time":
            self._show("seconds")
        elif stype in ("bms_ready", "bms_idle"):
            self._show("timeout")
        elif stype == "wait_temp":
            self._show("use_tmax", "tmax", "use_tmin", "tmin", "timeout")
        elif stype == "goto_soc":
            self._show(
                "use_crate",
                "crate",
                "current",
                "voltage",
                "timeout",
                "stop_soc",
                "soc",
                "respect_bmu",
                "record",
                "abort_tmax",
                "abort_tmax_v",
                "abort_dtc",
                "abort_dtc_v",
            )
            self.chk_stop_soc.setChecked(True)
        elif stype == "charge":
            self._show(
                "use_crate",
                "crate",
                "current",
                "voltage",
                "timeout",
                "stop_pack_vmax",
                "pack_vmax",
                "stop_cell_vmax",
                "cell_vmax",
                "stop_soc",
                "soc",
                "stop_imin",
                "imin",
                "stop_ah",
                "ah",
                "respect_bmu",
                "record",
                "abort_tmax",
                "abort_tmax_v",
                "abort_dtc",
                "abort_dtc_v",
            )
        elif stype == "discharge":
            self._show(
                "use_crate",
                "crate",
                "current",
                "timeout",
                "stop_pack_vmin",
                "pack_vmin",
                "stop_cell_vmin",
                "cell_vmin",
                "stop_soc",
                "soc",
                "stop_ah",
                "ah",
                "record",
                "measure",
                "measure_key",
                "respect_bmu",
                "abort_tmax",
                "abort_tmax_v",
                "abort_dtc",
                "abort_dtc_v",
            )
        elif stype == "dcir":
            self._show(
                "use_crate",
                "crate",
                "current",
                "pulse_s",
                "soc_ref_chk",
                "soc_ref",
                "respect_bmu",
                "record",
            )
        elif stype == "notify":
            self._show("message")
        elif stype == "report":
            self._show(
                "rep_cap",
                "rep_cap_1c",
                "rep_cap_3c",
                "rep_dcir",
                "rep_cells",
                "rep_temps",
                "rep_dtc",
            )
        else:
            self._show("timeout")
        self.type_changed.emit(stype)

    def load_step(self, step: Step) -> None:
        self.ed_step_id.setText(step.id)
        i = self.ed_step_type.findText(step.type)
        if i >= 0:
            self.ed_step_type.blockSignals(True)
            self.ed_step_type.setCurrentIndex(i)
            self.ed_step_type.blockSignals(False)
        p = step.params
        stop = p.get("stop") or {}
        abort = p.get("abort") or {}

        self.ed_seconds.setValue(int(p.get("seconds") or p.get("timeout_s") or 60))
        self.ed_timeout.setValue(int(p.get("timeout_s") or stop.get("timeout_s") or 60))
        self.chk_use_crate.setChecked("c_rate" in p)
        if "c_rate" in p:
            self.ed_crate.setValue(float(p["c_rate"]))
        self.ed_current.setValue(float(p.get("current_a") or p.get("pulse_a") or 70))
        self.ed_voltage.setValue(float(p.get("voltage_v") or 27.5))
        self.ed_pulse_s.setValue(float(p.get("pulse_s") or 10))

        self.chk_use_tmax.setChecked("t_max_c" in p)
        self.chk_use_tmin.setChecked("t_min_c" in p)
        if "t_max_c" in p:
            self.ed_tmax.setValue(float(p["t_max_c"]))
        if "t_min_c" in p:
            self.ed_tmin.setValue(float(p["t_min_c"]))

        self.chk_stop_pack_vmax.setChecked("pack_v_max" in stop)
        self.chk_stop_pack_vmin.setChecked("pack_v_min" in stop)
        self.chk_stop_cell_vmax.setChecked("cell_v_max" in stop)
        self.chk_stop_cell_vmin.setChecked("cell_v_min" in stop)
        self.chk_stop_soc.setChecked("soc_pct" in stop or step.type == "goto_soc")
        self.chk_stop_ah.setChecked("ah_target" in stop)
        self.chk_stop_imin.setChecked("i_min_a" in stop)
        if "pack_v_max" in stop:
            self.ed_pack_vmax.setValue(float(stop["pack_v_max"]))
        if "pack_v_min" in stop:
            self.ed_pack_vmin.setValue(float(stop["pack_v_min"]))
        if "cell_v_max" in stop:
            self.ed_cell_vmax.setValue(float(stop["cell_v_max"]))
        if "cell_v_min" in stop:
            self.ed_cell_vmin.setValue(float(stop["cell_v_min"]))
        if "soc_pct" in stop:
            self.ed_soc.setValue(float(stop["soc_pct"]))
        elif "soc_pct" in p:
            self.ed_soc.setValue(float(p["soc_pct"]))
        if "ah_target" in stop:
            self.ed_ah.setValue(float(stop["ah_target"]))
        if "i_min_a" in stop:
            self.ed_imin.setValue(float(stop["i_min_a"]))

        self.chk_abort_tmax.setChecked("t_max_c" in abort)
        self.chk_abort_dtc.setChecked("dtc_level_max" in abort)
        if "t_max_c" in abort:
            self.ed_abort_tmax.setValue(float(abort["t_max_c"]))
        if "dtc_level_max" in abort:
            self.ed_abort_dtc.setValue(int(abort["dtc_level_max"]))

        self.chk_soc_ref.setChecked("soc_ref_pct" in p)
        if "soc_ref_pct" in p:
            self.ed_soc_ref.setValue(float(p["soc_ref_pct"]))

        # Tri-state-ish: explicit false unchecks; missing uses engine defaults (shown checked for CV/dch)
        if "respect_bmu_limits" in p:
            raw = p.get("respect_bmu_limits")
            if isinstance(raw, str):
                self.chk_respect_bmu.setChecked(raw.strip().lower() in ("1", "true", "yes", "on"))
            else:
                self.chk_respect_bmu.setChecked(bool(raw))
        else:
            # Visual default matches engine: ON u nabíjení/vybíjení (a dcir/goto_soc).
            stype = step.type
            stop = p.get("stop") or {}
            self.chk_respect_bmu.setChecked(
                stype in ("charge", "discharge", "dcir", "goto_soc")
            )

        self.ed_message.setText(str(p.get("message") or ""))
        raw_rec = p.get("record")
        if isinstance(raw_rec, str):
            rec = raw_rec.strip().lower() in ("1", "true", "yes", "on")
        else:
            rec = bool(raw_rec)
        meas = p.get("measure")
        self.ed_measure_cap.setChecked(bool(meas))
        self.ed_measure_key.setText(str(meas or "capacity_ah"))
        # measure implies recording in engine; show checkbox checked when either is set
        self.chk_record.setChecked(rec or bool(meas))

        include = set(p.get("include") or [])
        self.chk_rep_cap.setChecked("capacity_ah" in include or not include)
        self.chk_rep_cap_1c.setChecked("capacity_1c_ah" in include)
        self.chk_rep_cap_3c.setChecked("capacity_3c_ah" in include)
        self.chk_rep_dcir.setChecked("dcir_mohm" in include or not include)
        self.chk_rep_cells.setChecked("cells" in include or not include)
        self.chk_rep_temps.setChecked("temps" in include or not include)
        self.chk_rep_dtc.setChecked("dtc" in include or not include)

        self._on_type_changed(step.type)

    def build_step(self) -> Step:
        stype = self.ed_step_type.currentText()
        sid = self.ed_step_id.text().strip() or "step"
        p: dict[str, Any] = {}

        if stype == "wait_time":
            p["seconds"] = int(self.ed_seconds.value())
        elif stype in ("bms_ready", "bms_idle"):
            p["timeout_s"] = int(self.ed_timeout.value())
        elif stype == "wait_temp":
            if self.chk_use_tmax.isChecked():
                p["t_max_c"] = float(self.ed_tmax.value())
            if self.chk_use_tmin.isChecked():
                p["t_min_c"] = float(self.ed_tmin.value())
            p["timeout_s"] = int(self.ed_timeout.value())
        elif stype == "goto_soc":
            if self.chk_use_crate.isChecked():
                p["c_rate"] = float(self.ed_crate.value())
            else:
                p["current_a"] = float(self.ed_current.value())
            p["voltage_v"] = float(self.ed_voltage.value())
            p["timeout_s"] = int(self.ed_timeout.value())
            p["soc_pct"] = float(self.ed_soc.value())
            p["respect_bmu_limits"] = bool(self.chk_respect_bmu.isChecked())
            if self.chk_record.isChecked():
                p["record"] = True
            abort = {}
            if self.chk_abort_tmax.isChecked():
                abort["t_max_c"] = float(self.ed_abort_tmax.value())
            if self.chk_abort_dtc.isChecked():
                abort["dtc_level_max"] = int(self.ed_abort_dtc.value())
            if abort:
                p["abort"] = abort
        elif stype == "charge":
            if self.chk_use_crate.isChecked():
                p["c_rate"] = float(self.ed_crate.value())
            else:
                p["current_a"] = float(self.ed_current.value())
            p["voltage_v"] = float(self.ed_voltage.value())
            p["timeout_s"] = int(self.ed_timeout.value())
            p["respect_bmu_limits"] = bool(self.chk_respect_bmu.isChecked())
            if self.chk_record.isChecked():
                p["record"] = True
            stop: dict[str, Any] = {}
            if self.chk_stop_pack_vmax.isChecked():
                stop["pack_v_max"] = float(self.ed_pack_vmax.value())
            if self.chk_stop_cell_vmax.isChecked():
                stop["cell_v_max"] = float(self.ed_cell_vmax.value())
            if self.chk_stop_soc.isChecked():
                stop["soc_pct"] = float(self.ed_soc.value())
            if self.chk_stop_ah.isChecked():
                stop["ah_target"] = float(self.ed_ah.value())
            if self.chk_stop_imin.isChecked():
                stop["i_min_a"] = float(self.ed_imin.value())
            p["stop"] = stop
            abort = {}
            if self.chk_abort_tmax.isChecked():
                abort["t_max_c"] = float(self.ed_abort_tmax.value())
            if self.chk_abort_dtc.isChecked():
                abort["dtc_level_max"] = int(self.ed_abort_dtc.value())
            if abort:
                p["abort"] = abort
        elif stype == "discharge":
            if self.chk_use_crate.isChecked():
                p["c_rate"] = float(self.ed_crate.value())
            else:
                p["current_a"] = float(self.ed_current.value())
            p["timeout_s"] = int(self.ed_timeout.value())
            p["respect_bmu_limits"] = bool(self.chk_respect_bmu.isChecked())
            if self.chk_record.isChecked() or self.ed_measure_cap.isChecked():
                p["record"] = True
            stop = {}
            if self.chk_stop_pack_vmin.isChecked():
                stop["pack_v_min"] = float(self.ed_pack_vmin.value())
            if self.chk_stop_cell_vmin.isChecked():
                stop["cell_v_min"] = float(self.ed_cell_vmin.value())
            if self.chk_stop_soc.isChecked():
                stop["soc_pct"] = float(self.ed_soc.value())
            if self.chk_stop_ah.isChecked():
                stop["ah_target"] = float(self.ed_ah.value())
            p["stop"] = stop
            abort = {}
            if self.chk_abort_tmax.isChecked():
                abort["t_max_c"] = float(self.ed_abort_tmax.value())
            if self.chk_abort_dtc.isChecked():
                abort["dtc_level_max"] = int(self.ed_abort_dtc.value())
            if abort:
                p["abort"] = abort
            if self.ed_measure_cap.isChecked():
                key = self.ed_measure_key.text().strip() or "capacity_ah"
                p["measure"] = key
        elif stype == "dcir":
            if self.chk_use_crate.isChecked():
                p["c_rate"] = float(self.ed_crate.value())
            else:
                p["pulse_a"] = float(self.ed_current.value())
            p["pulse_s"] = float(self.ed_pulse_s.value())
            p["respect_bmu_limits"] = bool(self.chk_respect_bmu.isChecked())
            if self.chk_record.isChecked():
                p["record"] = True
            if self.chk_soc_ref.isChecked():
                p["soc_ref_pct"] = float(self.ed_soc_ref.value())
        elif stype == "notify":
            p["message"] = self.ed_message.text().strip()
        elif stype == "report":
            include = []
            if self.chk_rep_cap.isChecked():
                include.append("capacity_ah")
            if self.chk_rep_cap_1c.isChecked():
                include.append("capacity_1c_ah")
            if self.chk_rep_cap_3c.isChecked():
                include.append("capacity_3c_ah")
            if self.chk_rep_dcir.isChecked():
                include.append("dcir_mohm")
            if self.chk_rep_cells.isChecked():
                include.append("cells")
            if self.chk_rep_temps.isChecked():
                include.append("temps")
            if self.chk_rep_dtc.isChecked():
                include.append("dtc")
            p["include"] = include

        return Step(id=sid, type=stype, params=p)

    def _on_measure_toggled(self, checked: bool) -> None:
        if checked:
            self.chk_record.setChecked(True)

    def _emit_apply(self) -> None:
        self.applied.emit(self.build_step())
