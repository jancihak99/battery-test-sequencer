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

        self.ed_voltage = QDoubleSpinBox()
        self.ed_voltage.setRange(0.1, 100)
        self.ed_voltage.setDecimals(2)
        self.ed_voltage.setSuffix(" V")
        self.ed_voltage.setValue(27.5)

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

        self.ed_message = QLineEdit()
        self.ed_measure_cap = QCheckBox("Measure capacity_ah")
        self.chk_rep_cap = QCheckBox("capacity_ah")
        self.chk_rep_dcir = QCheckBox("dcir_mohm")
        self.chk_rep_cells = QCheckBox("cells (all)")
        self.chk_rep_temps = QCheckBox("temps")
        self.chk_rep_dtc = QCheckBox("dtc")
        for c in (self.chk_rep_cap, self.chk_rep_dcir, self.chk_rep_cells, self.chk_rep_temps, self.chk_rep_dtc):
            c.setChecked(True)

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
        add_row("current", "Current / pulse", self.ed_current)
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
        add_row("stop_ah", "", self.chk_stop_ah)
        add_row("ah", "Ah target", self.ed_ah)
        add_row("abort_tmax", "", self.chk_abort_tmax)
        add_row("abort_tmax_v", "Abort Tmax", self.ed_abort_tmax)
        add_row("abort_dtc", "", self.chk_abort_dtc)
        add_row("abort_dtc_v", "DTC level ≥", self.ed_abort_dtc)
        add_row("soc_ref_chk", "", self.chk_soc_ref)
        add_row("soc_ref", "SOC reference", self.ed_soc_ref)
        add_row("measure", "", self.ed_measure_cap)
        add_row("message", "Message", self.ed_message)
        add_row("rep_cap", "", self.chk_rep_cap)
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
            "wait_time": "Set Duration in seconds. Apply before Validate/Save.",
            "wait_temp": "Wait until temperature is within limits (all sensors via BMS).",
            "charge": "At least one stop condition required. Cell limits use ALL cells.",
            "discharge": "At least one stop condition required. Cell limits use ALL cells.",
            "bms_ready": "Request BMU READY (contactors under BMU).",
            "bms_idle": "Open contactors / IDLE.",
            "dcir": "Pulse na EL. Volitelné soc_ref_pct čeká na SOC pásmo před pulzem.",
            "notify": "Jen status zpráva (bez dialogu).",
            "report": "Zápis HTML/JSON reportu z naměřených dat.",
        }
        self._hint.setText(hints.get(stype, ""))
        if stype == "wait_time":
            self._show("seconds")
        elif stype in ("bms_ready", "bms_idle"):
            self._show("timeout")
        elif stype == "wait_temp":
            self._show("use_tmax", "tmax", "use_tmin", "tmin", "timeout")
        elif stype == "charge":
            self._show(
                "current",
                "voltage",
                "timeout",
                "stop_pack_vmax",
                "pack_vmax",
                "stop_cell_vmax",
                "cell_vmax",
                "stop_soc",
                "soc",
                "stop_ah",
                "ah",
                "abort_tmax",
                "abort_tmax_v",
                "abort_dtc",
                "abort_dtc_v",
            )
        elif stype == "discharge":
            self._show(
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
                "measure",
                "abort_tmax",
                "abort_tmax_v",
                "abort_dtc",
                "abort_dtc_v",
            )
        elif stype == "dcir":
            self._show("current", "pulse_s", "soc_ref_chk", "soc_ref")
        elif stype == "notify":
            self._show("message")
        elif stype == "report":
            self._show("rep_cap", "rep_dcir", "rep_cells", "rep_temps", "rep_dtc")
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
        self.chk_stop_soc.setChecked("soc_pct" in stop)
        self.chk_stop_ah.setChecked("ah_target" in stop)
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
        if "ah_target" in stop:
            self.ed_ah.setValue(float(stop["ah_target"]))

        self.chk_abort_tmax.setChecked("t_max_c" in abort)
        self.chk_abort_dtc.setChecked("dtc_level_max" in abort)
        if "t_max_c" in abort:
            self.ed_abort_tmax.setValue(float(abort["t_max_c"]))
        if "dtc_level_max" in abort:
            self.ed_abort_dtc.setValue(int(abort["dtc_level_max"]))

        self.chk_soc_ref.setChecked("soc_ref_pct" in p)
        if "soc_ref_pct" in p:
            self.ed_soc_ref.setValue(float(p["soc_ref_pct"]))

        self.ed_message.setText(str(p.get("message") or ""))
        self.ed_measure_cap.setChecked(p.get("measure") == "capacity_ah")

        include = set(p.get("include") or [])
        self.chk_rep_cap.setChecked("capacity_ah" in include or not include)
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
        elif stype == "charge":
            p["current_a"] = float(self.ed_current.value())
            p["voltage_v"] = float(self.ed_voltage.value())
            p["timeout_s"] = int(self.ed_timeout.value())
            stop: dict[str, Any] = {}
            if self.chk_stop_pack_vmax.isChecked():
                stop["pack_v_max"] = float(self.ed_pack_vmax.value())
            if self.chk_stop_cell_vmax.isChecked():
                stop["cell_v_max"] = float(self.ed_cell_vmax.value())
            if self.chk_stop_soc.isChecked():
                stop["soc_pct"] = float(self.ed_soc.value())
            if self.chk_stop_ah.isChecked():
                stop["ah_target"] = float(self.ed_ah.value())
            p["stop"] = stop
            abort: dict[str, Any] = {}
            if self.chk_abort_tmax.isChecked():
                abort["t_max_c"] = float(self.ed_abort_tmax.value())
            if self.chk_abort_dtc.isChecked():
                abort["dtc_level_max"] = int(self.ed_abort_dtc.value())
            if abort:
                p["abort"] = abort
        elif stype == "discharge":
            p["current_a"] = float(self.ed_current.value())
            p["timeout_s"] = int(self.ed_timeout.value())
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
                p["measure"] = "capacity_ah"
        elif stype == "dcir":
            p["pulse_a"] = float(self.ed_current.value())
            p["pulse_s"] = float(self.ed_pulse_s.value())
            if self.chk_soc_ref.isChecked():
                p["soc_ref_pct"] = float(self.ed_soc_ref.value())
        elif stype == "notify":
            p["message"] = self.ed_message.text().strip()
        elif stype == "report":
            include = []
            if self.chk_rep_cap.isChecked():
                include.append("capacity_ah")
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

    def _emit_apply(self) -> None:
        self.applied.emit(self.build_step())
