"""Step parameter form with fields shown/hidden by step type."""
from __future__ import annotations

from typing import Any

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
from bts.ui.theme import BORDER, TEXT_DIM


class StepForm(QWidget):
    """Type-aware step editor. Emits applied(Step) when Apply is pressed."""

    applied = Signal(object)  # Step
    type_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._capacity_ah = 70.0
        self._syncing_current = False
        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(
            f"color:{TEXT_DIM}; padding:8px 10px; background:#f2f6fa;"
            f"border:1px solid {BORDER}; border-radius:4px;"
        )

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

        self.ed_settle = QDoubleSpinBox()
        self.ed_settle.setRange(0, 600)
        self.ed_settle.setDecimals(0)
        self.ed_settle.setSuffix(" s")
        self.ed_settle.setValue(10)
        self.ed_settle.setToolTip(
            "Po sepnutí stykačů engine počká tuto dobu, než pustí další krok "
            "(default 10 s — ochrana stykačů)."
        )

        self.ed_current = QDoubleSpinBox()
        self.ed_current.setRange(0.1, 2000)
        self.ed_current.setDecimals(1)
        self.ed_current.setSuffix(" A")
        self.ed_current.setValue(70)
        self.ed_current.setToolTip("Proud v ampérech — vždy svázaný s C-rate přes Ah profilu.")

        self.chk_use_crate = QCheckBox("V YAML uložit jako C-rate (ne absolutní A)")
        self.chk_use_crate.setToolTip(
            "C-rate a A jsou vždy propojené (I = C × Ah). "
            "Zaškrtnuto = do souboru jde c_rate; vypnuto = current_a / pulse_a."
        )
        self.chk_use_crate.setChecked(True)
        self.ed_crate = QDoubleSpinBox()
        self.ed_crate.setRange(0.01, 20)
        self.ed_crate.setDecimals(2)
        self.ed_crate.setSuffix(" C")
        self.ed_crate.setValue(1.0)
        self.ed_crate.setToolTip("C-rate — vždy svázaný s proudem (I = C × Ah profilu).")
        self.ed_crate.valueChanged.connect(self._on_crate_changed)
        self.ed_current.valueChanged.connect(self._on_current_changed)
        self.lbl_crate_link = QLabel("")
        self.lbl_crate_link.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        self._refresh_crate_link_label()

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

        # goto_soc: how close to target counts as "reached"
        self.ed_band_pct = QDoubleSpinBox()
        self.ed_band_pct.setRange(0.1, 20)
        self.ed_band_pct.setDecimals(1)
        self.ed_band_pct.setSuffix(" %")
        self.ed_band_pct.setValue(0.5)
        self.ed_band_pct.setToolTip(
            "Tolerance kolem cílového SOC — když je pack v tomto pásmu, goto_soc je splněný "
            "(default ±0,5 %)."
        )

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

        self.chk_soc_ref = QCheckBox("DCIR: vyžadovat SOC (SOC brána)")
        self.chk_soc_ref.setToolTip(
            "Pulse se spustí jen když BMS SOC je v pásmu. "
            "Chování při timeoutu řídí „Mimo pásmo = FAIL“ níže."
        )
        self.ed_soc_ref = QDoubleSpinBox()
        self.ed_soc_ref.setRange(0, 100)
        self.ed_soc_ref.setDecimals(0)
        self.ed_soc_ref.setSuffix(" %")
        self.ed_soc_ref.setValue(50)
        self.ed_dcir_band = QDoubleSpinBox()
        self.ed_dcir_band.setRange(0.1, 20)
        self.ed_dcir_band.setDecimals(0)
        self.ed_dcir_band.setSuffix(" %")
        self.ed_dcir_band.setValue(2)
        self.ed_dcir_band.setToolTip("Šířka pásma kolem reference SOC, ve které se pulz smí spustit.")
        self.ed_dcir_wait = QDoubleSpinBox()
        self.ed_dcir_wait.setRange(1, 7200)
        self.ed_dcir_wait.setDecimals(0)
        self.ed_dcir_wait.setSuffix(" s")
        self.ed_dcir_wait.setValue(600)
        self.ed_dcir_wait.setToolTip("Jak dlouho čekat na SOC v pásmu, než se rozhodne.")
        self.chk_require_soc = QCheckBox("Mimo pásmo = FAIL (jinak pulzuj dál)")
        self.chk_require_soc.setChecked(True)
        self.chk_require_soc.setToolTip(
            "Zaškrtnuto: po timeoutu run SELŽE (výsledek mimo SOC by byl neplatný). "
            "Vypnuto: pulz proběhne i mimo pásmo."
        )

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

        self.btn_apply = QPushButton("Použít změny kroku")
        self.btn_apply.setToolTip(
            "Změny se ukládají i automaticky (při přepnutí kroku / Validovat / Uložit) — "
            "tohle je jen okamžité potvrzení."
        )
        self.btn_apply.clicked.connect(self._emit_apply)

        form = QFormLayout()
        # Whole-row show/hide keyed by name. Section headers store (label, None).
        self._row_widgets: dict[str, tuple[QLabel, QWidget | None]] = {}

        def add_row(key: str, label: str, widget: QWidget) -> None:
            lab = QLabel(label)
            form.addRow(lab, widget)
            self._row_widgets[key] = (lab, widget)

        def add_section(key: str, title: str) -> None:
            lab = QLabel(title.upper())
            lab.setStyleSheet(
                f"color:{TEXT_DIM}; font-size:10px; font-weight:700; letter-spacing:1px;"
                f"margin-top:12px; padding-bottom:3px; border-bottom:1px solid {BORDER};"
            )
            form.addRow(lab)
            self._row_widgets[key] = (lab, None)

        add_row("id", "Step id", self.ed_step_id)
        add_row("type", "Type", self.ed_step_type)

        add_section("sec_timing", "Časování")
        add_row("seconds", "Duration (wait_time)", self.ed_seconds)
        add_row("timeout", "Timeout", self.ed_timeout)
        add_row("settle", "Odstup po stykačích", self.ed_settle)

        add_section("sec_temp", "Teplota")
        add_row("use_tmax", "", self.chk_use_tmax)
        add_row("tmax", "T max", self.ed_tmax)
        add_row("use_tmin", "", self.chk_use_tmin)
        add_row("tmin", "T min", self.ed_tmin)

        add_section("sec_current", "Proud")
        add_row("use_crate", "", self.chk_use_crate)
        add_row("crate", "C-rate", self.ed_crate)
        add_row("current", "Current / pulse (A)", self.ed_current)
        add_row("crate_link", "", self.lbl_crate_link)
        add_row("voltage", "Charge voltage", self.ed_voltage)
        add_row("pulse_s", "Pulse duration", self.ed_pulse_s)

        add_section("sec_stop", "Stop podmínky")
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

        add_section("sec_soc", "SOC brána")
        add_row("band_pct", "SOC tolerance (±)", self.ed_band_pct)
        add_row("soc_ref_chk", "", self.chk_soc_ref)
        add_row("soc_ref", "SOC reference", self.ed_soc_ref)
        add_row("dcir_band", "SOC pásmo (±)", self.ed_dcir_band)
        add_row("dcir_wait", "Čekat na SOC", self.ed_dcir_wait)
        add_row("require_soc", "", self.chk_require_soc)

        add_section("sec_safety", "Bezpečnost")
        add_row("abort_tmax", "", self.chk_abort_tmax)
        add_row("abort_tmax_v", "Abort Tmax", self.ed_abort_tmax)
        add_row("abort_dtc", "", self.chk_abort_dtc)
        add_row("abort_dtc_v", "DTC level ≥", self.ed_abort_dtc)
        add_row("respect_bmu", "", self.chk_respect_bmu)

        add_section("sec_record", "Záznam & report")
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

        # Grey out a value field until its enabling checkbox is on — the form
        # then shows at a glance which limits are actually active.
        self._link(self.chk_use_tmax, self.ed_tmax)
        self._link(self.chk_use_tmin, self.ed_tmin)
        self._link(self.chk_stop_pack_vmax, self.ed_pack_vmax)
        self._link(self.chk_stop_pack_vmin, self.ed_pack_vmin)
        self._link(self.chk_stop_cell_vmax, self.ed_cell_vmax)
        self._link(self.chk_stop_cell_vmin, self.ed_cell_vmin)
        self._link(self.chk_stop_soc, self.ed_soc)
        self._link(self.chk_stop_imin, self.ed_imin)
        self._link(self.chk_stop_ah, self.ed_ah)
        self._link(self.chk_abort_tmax, self.ed_abort_tmax)
        self._link(self.chk_abort_dtc, self.ed_abort_dtc)
        self._link(self.chk_soc_ref, self.ed_soc_ref, self.ed_dcir_band, self.ed_dcir_wait, self.chk_require_soc)
        self._link(self.ed_measure_cap, self.ed_measure_key)

        layout = QVBoxLayout(self)
        layout.addWidget(self._hint)
        layout.addLayout(form)
        layout.addWidget(self.btn_apply)
        layout.addStretch()
        self._on_type_changed(self.ed_step_type.currentText())

    def _link(self, chk: QCheckBox, *fields: QWidget) -> None:
        """Enable the given fields only while ``chk`` is checked."""
        def _apply() -> None:
            on = chk.isChecked()
            for f in fields:
                f.setEnabled(on)

        chk.toggled.connect(lambda _=False: _apply())
        _apply()

    def set_capacity_ah(self, ah: float) -> None:
        """Nominal Ah from active module profile — locks C-rate ↔ amps."""
        ah = max(0.1, float(ah))
        if abs(ah - self._capacity_ah) < 1e-9:
            return
        self._capacity_ah = ah
        # Keep C as source of truth when capacity changes (YAML programs use c_rate).
        self._syncing_current = True
        try:
            self.ed_current.setValue(round(self.ed_crate.value() * self._capacity_ah, 1))
        finally:
            self._syncing_current = False
        self._refresh_crate_link_label()

    def _refresh_crate_link_label(self) -> None:
        ah = self._capacity_ah
        self.lbl_crate_link.setText(
            f"Propojeno: I = C × {ah:.0f} Ah  ·  "
            f"{self.ed_crate.value():.2f}C = {self.ed_current.value():.1f} A"
        )

    def _on_crate_changed(self, value: float) -> None:
        if self._syncing_current:
            return
        self._syncing_current = True
        try:
            self.ed_current.setValue(round(float(value) * self._capacity_ah, 1))
        finally:
            self._syncing_current = False
        self._refresh_crate_link_label()

    def _on_current_changed(self, value: float) -> None:
        if self._syncing_current:
            return
        self._syncing_current = True
        try:
            self.ed_crate.setValue(round(float(value) / self._capacity_ah, 2))
        finally:
            self._syncing_current = False
        self._refresh_crate_link_label()

    def _set_linked_current(self, *, crate: float | None = None, amps: float | None = None) -> None:
        """Load both fields from one known value without feedback loops."""
        self._syncing_current = True
        try:
            if crate is not None:
                c = max(0.01, float(crate))
                self.ed_crate.setValue(c)
                self.ed_current.setValue(round(c * self._capacity_ah, 1))
            elif amps is not None:
                a = max(0.1, float(amps))
                self.ed_current.setValue(a)
                self.ed_crate.setValue(round(a / self._capacity_ah, 2))
        finally:
            self._syncing_current = False
        self._refresh_crate_link_label()

    def _put_current_params(self, p: dict[str, Any], *, amp_key: str = "current_a") -> None:
        """Write either c_rate or absolute amps (UI fields stay linked either way)."""
        if self.chk_use_crate.isChecked():
            p["c_rate"] = float(self.ed_crate.value())
        else:
            p[amp_key] = float(self.ed_current.value())

    def _show(self, *keys: str) -> None:
        show = set(keys) | {"id", "type"}
        for key, (lab, wid) in self._row_widgets.items():
            vis = key in show
            lab.setVisible(vis)
            if wid is not None:
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
            "bms_ready": "BMU READY (stykače). Po sepnutí engine čeká „Odstup po stykačích“ před dalším krokem.",
            "bms_idle": "Open contactors / IDLE.",
            "dcir": "Pulse na EL. C-rate nebo A. Zapni „vyžadovat SOC“ (typicky 50 %) — mimo pásmo = FAIL.",
            "notify": "Jen status zpráva (bez dialogu).",
            "report": "HTML/JSON report: kapacity, DCIR, grafy V/I/SOC ze stop s „Zaznamenat stopu“.",
        }
        self._hint.setText(hints.get(stype, ""))
        if stype == "wait_time":
            self._show("sec_timing", "seconds")
        elif stype == "bms_ready":
            self._show("sec_timing", "timeout", "settle")
        elif stype == "bms_idle":
            self._show("sec_timing", "timeout")
        elif stype == "wait_temp":
            self._show("sec_temp", "use_tmax", "tmax", "use_tmin", "tmin", "sec_timing", "timeout")
        elif stype == "goto_soc":
            self._show(
                "sec_current",
                "use_crate",
                "crate",
                "current",
                "crate_link",
                "voltage",
                "sec_timing",
                "timeout",
                "sec_stop",
                "stop_soc",
                "soc",
                "sec_soc",
                "band_pct",
                "sec_safety",
                "respect_bmu",
                "abort_tmax",
                "abort_tmax_v",
                "abort_dtc",
                "abort_dtc_v",
                "sec_record",
                "record",
            )
            self.chk_stop_soc.setChecked(True)
        elif stype == "charge":
            self._show(
                "sec_current",
                "use_crate",
                "crate",
                "current",
                "crate_link",
                "voltage",
                "sec_timing",
                "timeout",
                "sec_stop",
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
                "sec_safety",
                "respect_bmu",
                "abort_tmax",
                "abort_tmax_v",
                "abort_dtc",
                "abort_dtc_v",
                "sec_record",
                "record",
            )
        elif stype == "discharge":
            self._show(
                "sec_current",
                "use_crate",
                "crate",
                "current",
                "crate_link",
                "sec_timing",
                "timeout",
                "sec_stop",
                "stop_pack_vmin",
                "pack_vmin",
                "stop_cell_vmin",
                "cell_vmin",
                "stop_soc",
                "soc",
                "stop_ah",
                "ah",
                "sec_safety",
                "respect_bmu",
                "abort_tmax",
                "abort_tmax_v",
                "abort_dtc",
                "abort_dtc_v",
                "sec_record",
                "record",
                "measure",
                "measure_key",
            )
        elif stype == "dcir":
            self._show(
                "sec_current",
                "use_crate",
                "crate",
                "current",
                "crate_link",
                "pulse_s",
                "sec_soc",
                "soc_ref_chk",
                "soc_ref",
                "dcir_band",
                "dcir_wait",
                "require_soc",
                "sec_safety",
                "respect_bmu",
                "sec_record",
                "record",
            )
        elif stype == "notify":
            self._show("sec_record", "message")
        elif stype == "report":
            self._show(
                "sec_record",
                "rep_cap",
                "rep_cap_1c",
                "rep_cap_3c",
                "rep_dcir",
                "rep_cells",
                "rep_temps",
                "rep_dtc",
            )
        else:
            self._show("sec_timing", "timeout")
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
        self.ed_settle.setValue(float(p.get("settle_s", 10)))
        has_crate = "c_rate" in p and p.get("c_rate") is not None and str(p.get("c_rate")).strip() != ""
        self.chk_use_crate.setChecked(has_crate or ("current_a" not in p and "pulse_a" not in p))
        if has_crate:
            self._set_linked_current(crate=float(p["c_rate"]))
        else:
            amps = p.get("current_a") if p.get("current_a") is not None else p.get("pulse_a")
            self._set_linked_current(amps=float(amps) if amps is not None else self._capacity_ah)
        self.ed_voltage.setValue(float(p.get("voltage_v") or 27.5))
        self.ed_pulse_s.setValue(float(p.get("pulse_s") or 10))
        self.ed_band_pct.setValue(float(p.get("band_pct", 0.5)))

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
        self.ed_dcir_band.setValue(float(p.get("soc_band_pct", 2)))
        self.ed_dcir_wait.setValue(float(p.get("soc_wait_s", 600)))
        self.chk_require_soc.setChecked(_as_bool(p.get("require_soc"), default=True))

        # Tri-state-ish: explicit false unchecks; missing uses engine defaults (shown checked for CV/dch)
        if "respect_bmu_limits" in p:
            self.chk_respect_bmu.setChecked(_as_bool(p.get("respect_bmu_limits"), default=False))
        else:
            # Visual default matches engine: ON u nabíjení/vybíjení (a dcir/goto_soc).
            self.chk_respect_bmu.setChecked(
                step.type in ("charge", "discharge", "dcir", "goto_soc")
            )

        self.ed_message.setText(str(p.get("message") or ""))
        rec = _as_bool(p.get("record"), default=False)
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
        elif stype == "bms_ready":
            p["timeout_s"] = int(self.ed_timeout.value())
            p["settle_s"] = float(self.ed_settle.value())
        elif stype == "bms_idle":
            p["timeout_s"] = int(self.ed_timeout.value())
        elif stype == "wait_temp":
            if self.chk_use_tmax.isChecked():
                p["t_max_c"] = float(self.ed_tmax.value())
            if self.chk_use_tmin.isChecked():
                p["t_min_c"] = float(self.ed_tmin.value())
            p["timeout_s"] = int(self.ed_timeout.value())
        elif stype == "goto_soc":
            self._put_current_params(p, amp_key="current_a")
            p["voltage_v"] = float(self.ed_voltage.value())
            p["timeout_s"] = int(self.ed_timeout.value())
            p["soc_pct"] = float(self.ed_soc.value())
            p["band_pct"] = float(self.ed_band_pct.value())
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
            self._put_current_params(p, amp_key="current_a")
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
            self._put_current_params(p, amp_key="current_a")
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
            self._put_current_params(p, amp_key="pulse_a")
            p["pulse_s"] = float(self.ed_pulse_s.value())
            p["respect_bmu_limits"] = bool(self.chk_respect_bmu.isChecked())
            if self.chk_record.isChecked():
                p["record"] = True
            if self.chk_soc_ref.isChecked():
                p["soc_ref_pct"] = float(self.ed_soc_ref.value())
                p["soc_band_pct"] = float(self.ed_dcir_band.value())
                p["soc_wait_s"] = float(self.ed_dcir_wait.value())
                p["require_soc"] = bool(self.chk_require_soc.isChecked())
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


def _as_bool(raw: Any, *, default: bool) -> bool:
    """Coerce YAML truthy values (bool / int / '1'/'true'/'yes'/'on')."""
    if raw is None:
        return default
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(raw)
