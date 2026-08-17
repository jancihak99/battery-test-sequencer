from __future__ import annotations

import copy
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bts.drivers import create_bms_driver, create_ea_driver
from bts.dtc_catalog import format_active_dtcs, format_dtc_detail
from bts.engine import SequenceEngine, estimate_program, validate_program
from bts.engine.sequence import CONTACTOR_SAFE_JOIN_S
from bts.models.config import AppConfig, load_config, save_bms_settings
from bts.models.current import format_amps_with_crate, resolve_amps
from bts.models.program import (
    Program,
    ProgramMeta,
    Step,
    archive_file,
    clone_profile,
    list_profiles,
    list_programs_by_module,
    load_profile,
    load_program,
    save_program,
)
from bts.models.telemetry import BmuState, DesiredState
from bts.ui.dashboard import LiveDashboard
from bts.ui.diagnostics_tab import DiagnosticsTab
from bts.ui.logging_tab import LoggingTab
from bts.ui.module_step_picker import ModuleStepPicker
from bts.ui.simulate_tab import SimulateTab
from bts.ui.step_form import StepForm
from bts.ui.theme import (
    APP_STYLESHEET,
    TEXT_DIM,
    card_style,
)

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    status_tick = Signal()
    # Cross-thread marshal from sequence engine → GUI thread
    engine_status = Signal(object)
    activity_line = Signal(str)
    # Update check result always delivered on the GUI thread
    _update_check_done = Signal(object)

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self._app_version = "0.0.0"
        self._update_busy = False
        self._update_check_interactive = False
        self._update_watchdog: QTimer | None = None
        self._pending_release = None
        self.setWindowTitle("Battery Test Sequencer | EBZ nano power")
        self.resize(1400, 860)
        self._apply_window_icon()

        # BMU frame-rate estimate (shown instead of an ever-growing frame count)
        self._rx_rate = 0.0
        self._rx_rate_count: int | None = None
        self._rx_rate_mono: float | None = None

        self.program: Program | None = None
        self.program_path: Path | None = None
        self.engine: SequenceEngine | None = None
        self._bms = None
        self._ea = None
        self._active_profile = None
        self._contactor_conflict_since: float | None = None
        self._hw_busy = False
        self._editor_dirty = False
        self._editor_row = -1

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._build_run_tab()
        self._build_editor_tab()
        self._build_simulate_tab()
        self._build_logging_tab()
        self._build_history_tab()
        self._build_settings_tab()
        self._build_diagnostics_tab()
        self._wrap_tabs_scrollable()
        self._build_branding_bar()
        self._refresh_version_ui()
        # Content minimums (schematic/dashboard/…) otherwise pin the window
        # height so it can only shrink sideways. The scroll wrappers above let
        # it shrink vertically; keep a small floor just for usability.
        self.setMinimumSize(720, 420)

        self.engine_status.connect(self._on_engine_status)
        self.activity_line.connect(self._on_activity_line)
        self._update_check_done.connect(self._on_update_check_signal)

        self._reload_lists()
        QShortcut(QKeySequence("Esc"), self, activated=self._stop_run)

        self.timer = QTimer(self)
        self.timer.setInterval(cfg.safety.poll_period_ms)
        self.timer.timeout.connect(self._refresh_live)
        self.timer.start()

        self._sync_run_buttons()
        QTimer.singleShot(2500, self._maybe_check_updates_on_startup)

    def _apply_window_icon(self) -> None:
        icon = _load_app_icon(self.cfg.root)
        if icon is not None:
            self.setWindowIcon(icon)

    def _wrap_tabs_scrollable(self) -> None:
        """Put each tab page inside a QScrollArea.

        The tab pages carry large content minimum heights (schematic, live
        dashboard, history…), which pin the window's minimum height so it can
        only be resized sideways. Wrapping each page lets the window shrink
        vertically, showing a scrollbar instead of blocking the resize.
        """
        pages = []
        while self.tabs.count():
            page = self.tabs.widget(0)
            pages.append((page, self.tabs.tabIcon(0), self.tabs.tabText(0)))
            self.tabs.removeTab(0)
        for page, icon, text in pages:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.NoFrame)
            area.setWidget(page)
            self.tabs.addTab(area, icon, text)

    def _build_branding_bar(self) -> None:
        """Subtle footer: company logo + version + internal-tool notice."""
        bar = QStatusBar()
        bar.setSizeGripEnabled(False)
        bar.setFixedHeight(36)
        self.setStatusBar(bar)

        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(10)

        logo = QLabel()
        logo.setToolTip("EBZ nano power")
        logo_pix = self._load_ebz_logo_pixmap(height=26)
        if logo_pix is not None:
            logo.setPixmap(logo_pix)
            logo.setStyleSheet("background: transparent;")
        else:
            logo.setText("EBZ nano power")
            logo.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")

        self.lbl_status_version = QLabel("")
        self.lbl_status_version.setStyleSheet("color:#8a96a3;font-size:11px;")
        self.lbl_status_version.setToolTip("Verze aplikace (VERSION) — po update se změní")
        self.lbl_status_version.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        notice = QLabel("Internal tool  ·  not for distribution")
        notice.setStyleSheet("color:#8a96a3;font-size:11px;")
        notice.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        row.addWidget(logo, 0, Qt.AlignVCenter)
        row.addStretch(1)
        row.addWidget(self.lbl_status_version, 0, Qt.AlignVCenter)
        row.addWidget(notice, 0, Qt.AlignVCenter)
        wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(wrap, 1)

    def _refresh_version_ui(self, version: str | None = None) -> None:
        """Keep title / status bar / Settings in sync (for update checks)."""
        from bts.version import read_version

        ver = (version or read_version(self.cfg.root) or "0.0.0").strip()
        self._app_version = ver
        hw = " · HW ON" if getattr(self, "_hw_live", False) else ""
        self.setWindowTitle(f"Battery Test Sequencer | EBZ nano power · v{ver}{hw}")
        if hasattr(self, "lbl_status_version"):
            self.lbl_status_version.setText(f"v{ver}")
            self.lbl_status_version.setToolTip(
                f"Battery Test Sequencer {ver}\nSoubor VERSION v instalaci — ověření update."
            )
        if hasattr(self, "lbl_app_version"):
            self.lbl_app_version.setText(f"Verze: {ver}")

    def _load_ebz_logo_pixmap(self, height: int = 22) -> QPixmap | None:
        """Load the official EBZ nano power logo (HiDPI-aware). Never substitute a redraw."""
        assets = self.cfg.root / "assets"
        app = QApplication.instance()
        dpr = max(1.0, float(app.devicePixelRatio()) if app is not None else 1.0)
        target_h = max(1, int(round(height * dpr)))

        # Official brand files only (light-grey master suits the light UI chrome).
        for name in (
            "ebz_nanopower_logo_light_master.png",
            "ebz_nanopower_logo_ui.png",
            "ebz_nanopower_logo_light.png",
            "ebz_nanopower_logo.png",
        ):
            path = assets / name
            if not path.exists():
                continue
            pix = QPixmap(str(path))
            if pix.isNull() or pix.height() < 1:
                continue
            scaled = pix.scaledToHeight(target_h, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            return scaled
        return None

    def _build_run_tab(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(16)

        # Toolbar card
        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        toolbar.setStyleSheet(card_style("toolbar"))
        top = QHBoxLayout(toolbar)
        top.setContentsMargins(16, 12, 16, 12)
        top.setSpacing(10)
        self.program_picker = ModuleStepPicker(allow_delete=True)
        self.program_picker.selection_changed.connect(self._on_picker_selection)
        self.program_picker.delete_requested.connect(self._delete_stepfile)
        self.program_picker.delete_category_requested.connect(self._delete_module_type)
        self.profile_label = QLabel("Profile: —")
        self.profile_label.setStyleSheet(f"color:{TEXT_DIM};")
        self.serial_edit = QLineEdit()
        self.serial_edit.setPlaceholderText("Module serial / claim ID")
        self.serial_edit.setMinimumWidth(140)
        self.btn_connect = QPushButton("Připojit HW")
        self.btn_connect.setMinimumWidth(120)
        self.btn_connect.setToolTip("Připojí / odpojí Kvaser + EA. Stav je v pruhu nad toolbarem.")
        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("btnPrimary")
        self.btn_stop = QPushButton("Stop (Esc)")
        self.btn_stop.setObjectName("btnDanger")
        self.btn_stop.setToolTip(
            "Zastaví run: 1) vypne PSI/EL, 2) počká min. 8 s a až I≈0,\n"
            "3) ještě 6 s pauza, 4) teprve pak IDLE / stykače.\n"
            "Když proud neklesne, stykače se NEOTEVŘOU."
        )
        self.btn_clear_dtc = QPushButton("Smazat DTC")
        self.btn_clear_dtc.setToolTip(
            "App Command bit4 (Reset Latched DTCs). Vyžaduje živý BMS."
        )
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        self.btn_start.clicked.connect(self._start_run)
        self.btn_stop.clicked.connect(self._stop_run)
        self.btn_clear_dtc.clicked.connect(self._clear_bms_dtcs)
        top.addWidget(self.program_picker, 1)
        top.addWidget(self.profile_label)
        top.addWidget(QLabel("Sériové č."))
        top.addWidget(self.serial_edit)
        top.addWidget(self.btn_connect)
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_stop)
        top.addWidget(self.btn_clear_dtc)

        # Large HW status strip — readable on remote desktop.
        # Status goes ABOVE the toolbar (status first, then the controls).
        self.lbl_hw_banner = QLabel("HW: odpojeno — klikni Připojit HW")
        self.lbl_hw_banner.setAlignment(Qt.AlignCenter)
        self.lbl_hw_banner.setWordWrap(True)
        self.lbl_hw_banner.setMinimumHeight(40)
        self.lbl_hw_banner.setStyleSheet(self._hw_banner_style("off"))
        layout.addWidget(self.lbl_hw_banner)
        layout.addWidget(toolbar)

        # Link strip: bus open ≠ BMU talking
        link_bar = QFrame()
        link_bar.setObjectName("linkBar")
        link_bar.setStyleSheet(card_style("linkBar"))
        link_row = QHBoxLayout(link_bar)
        link_row.setContentsMargins(16, 12, 16, 12)
        link_row.setSpacing(20)
        self.lbl_link_bms = QLabel("BMS: not connected")
        self.lbl_link_ea = QLabel("EA: not connected")
        self.lbl_link_bms.setStyleSheet(f"color:{TEXT_DIM};font-weight:600;")
        self.lbl_link_ea.setStyleSheet(f"color:{TEXT_DIM};font-weight:600;")
        link_row.addWidget(self.lbl_link_bms, 1)
        link_row.addWidget(self.lbl_link_ea, 1)
        layout.addWidget(link_bar)
        self._hw_live = False

        split = QSplitter()
        split.setHandleWidth(6)
        split.setChildrenCollapsible(False)

        # Compact status column — content top-aligned
        left = QFrame()
        left.setObjectName("sidePanel")
        left.setStyleSheet(card_style("sidePanel"))
        left_outer = QVBoxLayout(left)
        left_outer.setContentsMargins(16, 16, 16, 16)
        left_outer.setSpacing(0)
        lf = QFormLayout()
        lf.setContentsMargins(0, 0, 0, 0)
        lf.setSpacing(10)
        lf.setHorizontalSpacing(14)
        lf.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lf.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.lbl_run_state = QLabel("idle")
        self.lbl_step = QLabel("—")
        self.lbl_msg = QLabel("—")
        self.lbl_msg.setWordWrap(True)
        self.lbl_capacity = QLabel("—")
        self.lbl_dcir = QLabel("—")
        self.lbl_bms_state = QLabel("—")
        self.lbl_pack = QLabel("—")
        self.lbl_i_limits = QLabel("—")
        self.lbl_dtc = QLabel("—")
        self.lbl_psi = QLabel("—")
        self.lbl_el = QLabel("—")
        for lab in (
            self.lbl_run_state,
            self.lbl_step,
            self.lbl_msg,
            self.lbl_capacity,
            self.lbl_dcir,
            self.lbl_bms_state,
            self.lbl_pack,
            self.lbl_i_limits,
            self.lbl_dtc,
            self.lbl_psi,
            self.lbl_el,
        ):
            lab.setStyleSheet("font-weight:600;")
        lf.addRow(self._dim("Run"), self.lbl_run_state)
        lf.addRow(self._dim("Step"), self.lbl_step)
        lf.addRow(self._dim("Message"), self.lbl_msg)
        lf.addRow(self._dim("Capacity"), self.lbl_capacity)
        lf.addRow(self._dim("DCIR"), self.lbl_dcir)
        lf.addRow(self._dim("BMU state"), self.lbl_bms_state)
        lf.addRow(self._dim("Pack U/I"), self.lbl_pack)
        lf.addRow(self._dim("BMS I lim"), self.lbl_i_limits)
        lf.addRow(self._dim("DTC"), self.lbl_dtc)
        lf.addRow(self._dim("PSI"), self.lbl_psi)
        lf.addRow(self._dim("EL"), self.lbl_el)
        left_outer.addLayout(lf)
        left_outer.addStretch(1)
        left.setMinimumWidth(260)
        left.setMaximumWidth(320)

        self.dashboard = LiveDashboard()

        split.addWidget(left)
        split.addWidget(self.dashboard)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 900])
        layout.addWidget(split, 1)

        self.tabs.addTab(w, "Běh")

    @staticmethod
    def _dim(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"color:{TEXT_DIM};font-weight:500;")
        return lab

    def _build_editor_tab(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(16)

        # Program-level meta (applies to all steps)
        meta_box = QFrame()
        meta_box.setObjectName("progMeta")
        meta_box.setStyleSheet(card_style("progMeta"))
        meta_outer = QVBoxLayout(meta_box)
        meta_outer.setContentsMargins(16, 14, 16, 14)
        meta_outer.setSpacing(10)
        meta_title = QLabel("Program")
        meta_title.setStyleSheet(f"color:{TEXT_DIM};font-weight:600;font-size:11px;")
        meta_outer.addWidget(meta_title)
        meta = QFormLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(6)
        self.ed_name = QLineEdit()
        self.ed_profile = QComboBox()
        self.ed_profile.setToolTip("Battery module profile for the whole program (all steps).")
        self.ed_profile.currentTextChanged.connect(self._on_editor_profile_changed)
        self.btn_new_module = QPushButton("＋ Nový typ modulu")
        self.btn_new_module.setToolTip(
            "Vytvoří nový typ modulu (profil) naklonováním vybraného jako šablony."
        )
        self.btn_new_module.clicked.connect(self._new_module_type)
        profile_row = QHBoxLayout()
        profile_row.setContentsMargins(0, 0, 0, 0)
        profile_row.setSpacing(6)
        profile_row.addWidget(self.ed_profile, 1)
        profile_row.addWidget(self.btn_new_module)
        self.ed_desc = QLineEdit()
        self.ed_prog_tmax = QDoubleSpinBox()
        self.ed_prog_tmax.setRange(0, 100)
        self.ed_prog_tmax.setSuffix(" °C")
        self.ed_prog_tmax.setValue(50)
        self.ed_prog_tmax.setToolTip(
            "Max teplota pro celý program (abort). Platí u charge/discharge/dcir/goto_soc, "
            "pokud krok nemá vlastní abort.t_max_c."
        )
        self.chk_prog_tmax = QCheckBox("Program Tmax (abort)")
        self.chk_prog_tmax.setChecked(True)
        meta.addRow("Name", self.ed_name)
        meta.addRow("Module profile", profile_row)
        meta.addRow("Description", self.ed_desc)
        meta.addRow("", self.chk_prog_tmax)
        meta.addRow("Tmax celý program", self.ed_prog_tmax)
        meta_outer.addLayout(meta)
        layout.addWidget(meta_box)

        split = QHBoxLayout()
        split.setSpacing(16)

        left = QVBoxLayout()
        left.setSpacing(10)
        steps_lab = QLabel("Steps")
        steps_lab.setStyleSheet(f"color:{TEXT_DIM};font-weight:600;font-size:11px;")
        left.addWidget(steps_lab)
        self.step_list = QListWidget()
        self._editor_row = -1
        self.step_list.currentRowChanged.connect(self._on_step_row_changed)
        btns = QHBoxLayout()
        for label, slot in [
            ("Add", self._add_step),
            ("Duplikovat", self._duplicate_step),
            ("Remove", self._remove_step),
            ("Up", self._move_up),
            ("Down", self._move_down),
        ]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        left.addWidget(self.step_list, 1)
        left.addLayout(btns)

        file_btns = QHBoxLayout()
        self.btn_new = QPushButton("Nový")
        self.btn_open = QPushButton("Otevřít")
        self.btn_save = QPushButton("Uložit")
        self.btn_validate = QPushButton("Validovat")
        self.btn_new.clicked.connect(self._new_program)
        self.btn_open.clicked.connect(self._open_program)
        self.btn_save.clicked.connect(self._save_program)
        self.btn_validate.clicked.connect(self._validate_program)
        for b in (self.btn_new, self.btn_open, self.btn_save, self.btn_validate):
            file_btns.addWidget(b)
        left.addLayout(file_btns)

        right = QVBoxLayout()
        right.setSpacing(10)
        step_lab = QLabel("Selected step")
        step_lab.setStyleSheet(f"color:{TEXT_DIM};font-weight:600;font-size:11px;")
        right.addWidget(step_lab)
        self.step_form = StepForm()
        self.step_form.applied.connect(self._on_step_applied)
        right.addWidget(self.step_form, 1)

        split.addLayout(left, 1)
        split.addLayout(right, 2)
        layout.addLayout(split, 1)
        self.tabs.addTab(w, "Editor")

    def _build_simulate_tab(self) -> None:
        self.simulate_tab = SimulateTab()
        self.simulate_tab.set_program_provider(self._program_for_simulate)
        self.simulate_tab.set_program_list_provider(self._list_programs_for_sim)
        self.simulate_tab.set_file_loader(self._load_program_and_profile)
        self.simulate_tab.refresh_programs()
        self.tabs.addTab(self.simulate_tab, "Simulace")

    def _list_programs_for_sim(self):
        """Module-grouped stepfiles for the Simulate two-level picker."""
        return list_programs_by_module(self.cfg.programs_path, self.cfg.profiles_path)

    def _load_program_and_profile(self, path):
        """Load a stepfile + its module profile for offline simulation."""
        program = load_program(Path(path))
        profile = None
        try:
            profile = load_profile(
                self.cfg.profiles_path / f"{program.meta.module_profile}.yaml"
            )
        except Exception:
            log.exception("Simulate: profile load failed for %s", path)
        return program, profile

    def _program_for_simulate(self):
        """Snapshot editor program + profile for offline preview."""
        if not self.program:
            return None, None
        self._sync_meta_into_program()
        # Preview only: apply the current step-form edit to a COPY, never the
        # live program (mutating it silently skips the unsaved-changes prompt).
        snapshot = copy.deepcopy(self.program)
        row = self.step_list.currentRow()
        if row >= 0:
            try:
                snapshot.steps[row] = self.step_form.build_step()
            except Exception:
                pass
        try:
            profile = self._current_profile()
        except Exception:
            profile = self._active_profile
        return snapshot, profile

    def _build_history_tab(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.history_list = QListWidget()
        row = QHBoxLayout()
        btn_refresh = QPushButton("Obnovit")
        btn_refresh.clicked.connect(self._refresh_history)
        btn_folder = QPushButton("Otevřít složku runs")
        btn_folder.clicked.connect(self._open_runs_folder)
        row.addWidget(btn_refresh)
        row.addWidget(btn_folder)
        row.addStretch(1)
        self.history_list.itemDoubleClicked.connect(self._open_history_item)
        layout.addLayout(row)
        layout.addWidget(self.history_list, 1)
        self.tabs.addTab(w, "Historie")

    def _build_settings_tab(self) -> None:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)

        hdr = QLabel("External CAN (Kvaser ↔ BMU)")
        hdr.setStyleSheet("font-weight:600; margin-top:8px;")
        form.addRow(hdr)

        self.cmb_can_iface = QComboBox()
        self.cmb_can_iface.addItems(["kvaser", "socketcan", "pcan", "vector"])
        idx = self.cmb_can_iface.findText(self.cfg.bms.interface)
        self.cmb_can_iface.setCurrentIndex(max(0, idx))
        form.addRow("Interface", self.cmb_can_iface)

        self.spin_can_channel = QSpinBox()
        self.spin_can_channel.setRange(0, 31)
        self.spin_can_channel.setValue(int(self.cfg.bms.channel))
        form.addRow("Channel", self.spin_can_channel)

        self.cmb_bitrate = QComboBox()
        self.cmb_bitrate.setEditable(True)
        for br in (250_000, 500_000, 125_000, 1_000_000, 100_000, 50_000):
            self.cmb_bitrate.addItem(str(br), br)
        cur = str(int(self.cfg.bms.bitrate))
        i = self.cmb_bitrate.findText(cur)
        if i >= 0:
            self.cmb_bitrate.setCurrentIndex(i)
        else:
            self.cmb_bitrate.setEditText(cur)
        form.addRow("Bitrate", self.cmb_bitrate)

        self.cmb_bmu_addr = QComboBox()
        for a in (0x00, 0x01, 0x02, 0x03):
            self.cmb_bmu_addr.addItem(f"0x{a:02X}", a)
        bmu = int(self.cfg.bms.bmu_address) & 0xFF
        bi = self.cmb_bmu_addr.findData(bmu)
        self.cmb_bmu_addr.setCurrentIndex(bi if bi >= 0 else 3)
        form.addRow("BMU address", self.cmb_bmu_addr)

        self.cmb_app_addr = QComboBox()
        self.cmb_app_addr.addItem("0x20 (ACU / App — spec default)", 0x20)
        self.cmb_app_addr.addItem("0xF9 (BMS Service Tool)", 0xF9)
        app = int(self.cfg.bms.app_address) & 0xFF
        ai = self.cmb_app_addr.findData(app)
        self.cmb_app_addr.setCurrentIndex(ai if ai >= 0 else 0)
        self.cmb_app_addr.setToolTip(
            "BMU accepts App Command only from its configured ACU address. "
            "Wrong SA → pack data may still arrive, but cells/temps stay off."
        )
        form.addRow("App / Tool address", self.cmb_app_addr)

        req = QLabel("App Command requests: cells + temps + balance (bits 1–3) every heartbeat")
        req.setStyleSheet(f"color:{TEXT_DIM};")
        form.addRow(req)

        btn_row = QHBoxLayout()
        self.btn_can_detect = QPushButton("Auto-detekce CAN…")
        self.btn_can_detect.setToolTip(
            "1) Pasivní poslech na běžných bitratech\n"
            "2) Při tichu: probe BMU adres 0x00–0x03\n"
            "Použije App/Tool adresu z Nastavení (zkus 0x20 pokud chybí cells).\n"
            "Nejdřív zavři jiné CAN nástroje."
        )
        self.btn_can_apply = QPushButton("Aplikovat")
        self.btn_can_save = QPushButton("Uložit do config")
        self.btn_can_detect.clicked.connect(self._auto_detect_can)
        self.btn_can_apply.clicked.connect(self._apply_can_settings)
        self.btn_can_save.clicked.connect(self._save_can_settings)
        btn_row.addWidget(self.btn_can_detect)
        btn_row.addWidget(self.btn_can_apply)
        btn_row.addWidget(self.btn_can_save)
        btn_row.addStretch(1)
        form.addRow(btn_row)

        self.lbl_can_detect = QLabel(
            "Auto-detect listens passively, then briefly sends App Command probes."
        )
        self.lbl_can_detect.setWordWrap(True)
        self.lbl_can_detect.setStyleSheet(f"color:{TEXT_DIM};")
        form.addRow(self.lbl_can_detect)

        if self.cfg.ea:
            form.addRow(QLabel(""))
            ea_hdr = QLabel("EA (USB serial — auto COM podle *IDN?)")
            ea_hdr.setStyleSheet("font-weight:600;")
            form.addRow(ea_hdr)
            psi = self.cfg.ea.psi
            el = self.cfg.ea.el
            self.ed_psi_port = QLineEdit(psi.serial_port or "auto")
            self.ed_psi_port.setPlaceholderText("auto / COMx")
            self.ed_psi_baud = QSpinBox()
            self.ed_psi_baud.setRange(9600, 921600)
            self.ed_psi_baud.setValue(int(psi.baudrate or 115200))
            self.ed_el_port = QLineEdit(el.serial_port or "auto")
            self.ed_el_port.setPlaceholderText("auto / COMx")
            self.ed_el_baud = QSpinBox()
            self.ed_el_baud.setRange(9600, 921600)
            self.ed_el_baud.setValue(int(el.baudrate or 115200))
            form.addRow("PSI COM", self.ed_psi_port)
            form.addRow("PSI baud", self.ed_psi_baud)
            form.addRow(
                "EL master COM",
                self.ed_el_port,
            )
            form.addRow(
                f"EL baud (max {el.max_current_a:.0f} A)",
                self.ed_el_baud,
            )
            form.addRow(
                QLabel("EL slave je HW master/slave — softwarově se neadresuje. Prázdné/auto = *IDN? scan.")
            )

        # --- Updates (private GitHub; token is embedded on publish) ---
        form.addRow(QLabel(""))
        upd_hdr = QLabel("Aktualizace (automaticky z GitHubu)")
        upd_hdr.setStyleSheet("font-weight:600;")
        form.addRow(upd_hdr)
        form.addRow(
            QLabel(
                "Při startu apka sama zkontroluje GitHub Releases a nabídne update. "
                "Repo je public — na lab PC nic nevyplňuješ (žádný token)."
            )
        )
        try:
            from bts.version import read_version
            from bts.update import load_update_config, read_token, token_source

            ver = read_version(self.cfg.root)
            ucfg = load_update_config(self.cfg.root)
            has_tok = bool(read_token(self.cfg.root))
            tok_src = token_source(self.cfg.root)
        except Exception:
            ver = "?"
            ucfg = None
            has_tok = False
            tok_src = "chybí"
        self.lbl_app_version = QLabel(f"Verze: {ver}")
        form.addRow(self.lbl_app_version)
        self.ed_github_repo = QLineEdit(ucfg.github_repo if ucfg else "jancihak99/battery-test-sequencer")
        form.addRow("GitHub repo", self.ed_github_repo)
        self.lbl_update_auth = QLabel(
            "Přístup k Releases: public repo (token netřeba)"
            + (f" · override {tok_src}" if has_tok else "")
        )
        self.lbl_update_auth.setStyleSheet(f"color:{TEXT_DIM};")
        form.addRow(self.lbl_update_auth)
        # Optional override kept but not required (hidden unless needed)
        self.ed_github_token = QLineEdit(self)
        self.ed_github_token.setEchoMode(QLineEdit.Password)
        self.ed_github_token.hide()  # override only; normal path uses embedded token
        self.chk_update_startup = QCheckBox("Při startu automaticky kontrolovat GitHub")
        self.chk_update_startup.setChecked(bool(ucfg.check_on_startup) if ucfg else True)
        form.addRow(self.chk_update_startup)
        self.chk_auto_prompt = QCheckBox("Když je nová verze, rovnou nabídnout instalaci")
        self.chk_auto_prompt.setChecked(bool(ucfg.auto_prompt_install) if ucfg else True)
        form.addRow(self.chk_auto_prompt)
        upd_row = QHBoxLayout()
        self.btn_check_update = QPushButton("Zkontrolovat teď")
        self.btn_apply_update = QPushButton("Stáhnout a nainstalovat")
        self.btn_apply_update.setEnabled(False)
        self.btn_save_update = QPushButton("Uložit")
        self.btn_check_update.clicked.connect(self._check_updates)
        self.btn_apply_update.clicked.connect(self._apply_updates)
        self.btn_save_update.clicked.connect(self._save_update_settings)
        upd_row.addWidget(self.btn_check_update)
        upd_row.addWidget(self.btn_apply_update)
        upd_row.addWidget(self.btn_save_update)
        upd_row.addStretch(1)
        form.addRow(upd_row)
        self.lbl_update_status = QLabel("—")
        self.lbl_update_status.setWordWrap(True)
        self.lbl_update_status.setStyleSheet(f"color:{TEXT_DIM};")
        form.addRow(self.lbl_update_status)

        form.addRow(
            QLabel("Po změně CAN/EA: Apply → znovu Připojit HW.")
        )
        form.addRow(QLabel("USB / EA / Kvaser troubleshooting → záložka Diagnostika."))

        root.addLayout(form)
        root.addStretch(1)
        self.tabs.addTab(w, "Nastavení")
        self._pending_release = None

    def _save_update_settings(self) -> None:
        from bts.update import UpdateConfig, save_update_config, write_token

        cfg = UpdateConfig(
            github_repo=self.ed_github_repo.text().strip() or "jancihak99/battery-test-sequencer",
            check_on_startup=self.chk_update_startup.isChecked(),
            auto_prompt_install=self.chk_auto_prompt.isChecked(),
            channel="latest",
        )
        save_update_config(self.cfg.root, cfg)
        tok = self.ed_github_token.text().strip()
        if tok:
            write_token(self.cfg.root, tok)
            self.ed_github_token.clear()
            self.ed_github_token.setPlaceholderText("token uložen — apka kontroluje GitHub sama")
        self.lbl_update_status.setText("Update nastavení uloženo.")

    def _start_update_check(self, *, interactive: bool) -> None:
        """GitHub check in a daemon thread; result marshalled to GUI via Signal.

        Previous QThread+lambda pattern ran the UI callback on the worker thread
        (PySide AutoConnection), which hung the app on „Kontroluji…“.
        """
        if self._update_busy:
            if hasattr(self, "lbl_update_status"):
                self.lbl_update_status.setText("Kontrola už běží…")
            return
        self._update_busy = True
        self._update_check_interactive = interactive
        if hasattr(self, "lbl_update_status"):
            self.lbl_update_status.setText("Kontroluji GitHub…")
        if interactive and hasattr(self, "btn_check_update"):
            self.btn_check_update.setEnabled(False)

        root = self.cfg.root

        def _worker() -> None:
            from bts.update import UpdateCheckResult, check_for_update
            from bts.version import read_version

            try:
                result = check_for_update(root)
            except Exception as exc:
                result = UpdateCheckResult(
                    local_version=read_version(root),
                    remote_version=None,
                    update_available=False,
                    error=str(exc),
                )
            # Signal is thread-safe; slot runs on GUI thread (QueuedConnection).
            self._update_check_done.emit(result)

        import threading

        threading.Thread(target=_worker, name="bts-update-check", daemon=True).start()

        # Hard UI watchdog — never leave the button stuck if network hangs.
        if self._update_watchdog is not None:
            self._update_watchdog.stop()
            self._update_watchdog.deleteLater()
        self._update_watchdog = QTimer(self)
        self._update_watchdog.setSingleShot(True)

        def _timeout() -> None:
            if not self._update_busy:
                return
            from bts.update import UpdateCheckResult
            from bts.version import read_version

            self._update_check_done.emit(
                UpdateCheckResult(
                    local_version=read_version(self.cfg.root),
                    remote_version=None,
                    update_available=False,
                    error=(
                        "Timeout — GitHub neodpověděl do 45 s "
                        "(síť / firewall / SSL). Zkus znovu, nebo BTS-Setup.exe z Releases."
                    ),
                )
            )

        self._update_watchdog.timeout.connect(_timeout)
        self._update_watchdog.start(45_000)

    def _on_update_check_signal(self, result: object) -> None:
        """GUI-thread slot for background update check."""
        if not self._update_busy:
            return  # late result after watchdog already finished
        self._update_busy = False
        if self._update_watchdog is not None:
            self._update_watchdog.stop()
        if hasattr(self, "btn_check_update"):
            self.btn_check_update.setEnabled(True)
        self._on_update_check_finished(result, interactive=self._update_check_interactive)

    def _check_updates(self) -> None:
        try:
            self._save_update_settings()
        except Exception:
            pass
        self._start_update_check(interactive=True)

    def _on_update_check_finished(self, result: object, *, interactive: bool) -> None:
        from bts.update import UpdateCheckResult

        if not isinstance(result, UpdateCheckResult):
            return
        if result.error:
            self._pending_release = None
            if hasattr(self, "btn_apply_update"):
                self.btn_apply_update.setEnabled(False)
            if hasattr(self, "lbl_update_status"):
                # One-line status; full text in dialog when interactive
                short = result.error.split("\n", 1)[0]
                self.lbl_update_status.setText(short)
            if interactive:
                QMessageBox.warning(self, "Aktualizace", result.error)
            else:
                self._log(f"Update check: {result.error.splitlines()[0]}")
            return
        self._refresh_version_ui(result.local_version)
        if result.update_available and result.release:
            self._pending_release = result.release
            if hasattr(self, "btn_apply_update"):
                self.btn_apply_update.setEnabled(True)
            msg = (
                f"Na GitHubu je nová verze {result.remote_version} "
                f"(tady máš {result.local_version})."
            )
            if hasattr(self, "lbl_update_status"):
                self.lbl_update_status.setText(msg)
            self._log(f"Update available: {result.remote_version}")
            if interactive:
                reply = QMessageBox.question(
                    self,
                    "Nová verze",
                    msg + "\n\nStáhnout a nainstalovat teď?",
                )
                if reply == QMessageBox.Yes:
                    self._apply_updates(confirm=False)
            else:
                from bts.update import load_update_config

                ucfg = load_update_config(self.cfg.root)
                if not ucfg.auto_prompt_install:
                    return
                if self._engine_is_alive():
                    return
                reply = QMessageBox.question(
                    self,
                    "Nová verze z GitHubu",
                    msg + "\n\nStáhnout a nainstalovat teď?\n(config a runs zůstanou)",
                )
                if reply == QMessageBox.Yes:
                    self._apply_updates(confirm=False)
        else:
            self._pending_release = None
            if hasattr(self, "btn_apply_update"):
                self.btn_apply_update.setEnabled(False)
            done = result.message or "Žádná nová verze."
            if hasattr(self, "lbl_update_status"):
                self.lbl_update_status.setText(done)
            if interactive:
                QMessageBox.information(self, "Aktualizace", done)

    def _apply_updates(self, *, confirm: bool = True) -> None:
        from bts.update import spawn_external_updater

        if self._engine_is_alive():
            QMessageBox.warning(self, "Aktualizace", "Nejdřív Stop / dokonči běžící test.")
            return
        if self._bench_needs_safe_shutdown():
            QMessageBox.warning(
                self,
                "Aktualizace",
                "Nejdřív bezpečně vypni (stykače / EA) — zavři app přes Bezpečně vypnout, "
                "nebo Stop + IDLE, pak update.",
            )
            return
        if confirm:
            reply = QMessageBox.question(
                self,
                "Aktualizace",
                "Aplikace se zavře a otevře se okno „Aktualizace BTS“ s progress barem.\n"
                "Počkejte, až doběhne — BTS se potom spustí samo.\n\n"
                "Když Windows zeptá na oprávnění správce (Program Files), potvrďte.\n\n"
                "Pokračovat?",
            )
            if reply != QMessageBox.Yes:
                return
        else:
            QMessageBox.information(
                self,
                "Aktualizace",
                "Aplikace se teď zavře.\n\n"
                "Sledujte okno „Aktualizace BTS“ s progress barem — "
                "po dokončení se BTS spustí samo.\n"
                "(Případný dotaz Windows na správce potvrďte.)",
            )
        try:
            spawn_external_updater(self.cfg.root)
            self._log("Update spuštěn — aplikace se zavře; sleduj okno Aktualizace BTS")
            if hasattr(self, "lbl_update_status"):
                self.lbl_update_status.setText(
                    "Aktualizace běží v samostatném okně — apka se teď zavře…"
                )
            self.statusBar().showMessage("Aktualizace — sleduj progress okno", 8000)
            # Give the updater process a moment to appear before we exit
            QTimer.singleShot(900, QApplication.instance().quit)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Aktualizace selhala",
                f"Nepodařilo se spustit updater:\n{exc}\n\n"
                "Stáhni BTS-Setup.exe z GitHub Releases a nainstaluj znovu.",
            )

    def _maybe_check_updates_on_startup(self) -> None:
        """Customer PC: query GitHub Releases in background; prompt if newer."""
        try:
            from bts.update import load_update_config

            ucfg = load_update_config(self.cfg.root)
            if not ucfg.check_on_startup:
                return
            if not hasattr(self, "lbl_update_status"):
                return
            self._start_update_check(interactive=False)
        except Exception:
            logging.getLogger(__name__).exception("Startup update check failed")

    def _read_ea_form(self) -> None:
        if self.cfg.ea is None or not hasattr(self, "ed_psi_port"):
            return
        psi_port = self.ed_psi_port.text().strip() or "auto"
        el_port = self.ed_el_port.text().strip() or "auto"
        self.cfg.ea.psi.transport = "serial"
        self.cfg.ea.psi.serial_port = psi_port
        self.cfg.ea.psi.baudrate = int(self.ed_psi_baud.value())
        self.cfg.ea.el.transport = "serial"
        self.cfg.ea.el.serial_port = el_port
        self.cfg.ea.el.baudrate = int(self.ed_el_baud.value())

    def _read_can_form(self) -> None:
        self.cfg.bms.interface = self.cmb_can_iface.currentText().strip() or "kvaser"
        self.cfg.bms.channel = int(self.spin_can_channel.value())
        br_txt = self.cmb_bitrate.currentText().strip().replace("_", "").replace(",", "")
        try:
            self.cfg.bms.bitrate = int(br_txt)
        except ValueError as exc:
            raise ValueError(f"Invalid bitrate: {br_txt!r}") from exc
        if self.cfg.bms.bitrate <= 0:
            raise ValueError("Bitrate must be > 0")
        self.cfg.bms.bmu_address = int(self.cmb_bmu_addr.currentData()) & 0xFF
        self.cfg.bms.app_address = int(self.cmb_app_addr.currentData()) & 0xFF
        self._read_ea_form()

    def _sync_can_form_from_cfg(self) -> None:
        idx = self.cmb_can_iface.findText(self.cfg.bms.interface)
        if idx >= 0:
            self.cmb_can_iface.setCurrentIndex(idx)
        self.spin_can_channel.setValue(int(self.cfg.bms.channel))
        cur = str(int(self.cfg.bms.bitrate))
        i = self.cmb_bitrate.findText(cur)
        if i >= 0:
            self.cmb_bitrate.setCurrentIndex(i)
        else:
            self.cmb_bitrate.setEditText(cur)
        bmu = int(self.cfg.bms.bmu_address) & 0xFF
        bi = self.cmb_bmu_addr.findData(bmu)
        self.cmb_bmu_addr.setCurrentIndex(bi if bi >= 0 else 3)
        app = int(self.cfg.bms.app_address) & 0xFF
        ai = self.cmb_app_addr.findData(app)
        self.cmb_app_addr.setCurrentIndex(ai if ai >= 0 else 0)

    def _apply_can_settings(self) -> None:
        try:
            self._read_can_form()
        except ValueError as exc:
            QMessageBox.warning(self, "CAN settings", str(exc))
            return
        if not self._confirm_abort_if_running("Apply CAN"):
            return
        self.cfg.use_mock_hardware = False
        # Live drivers hold old bus — force reconnect on next Connect
        if self._bms is not None or self._ea is not None:
            self._disconnect_hw()
            self._log(
                f"CAN settings applied — reconnect: ch={self.cfg.bms.channel} "
                f"@ {self.cfg.bms.bitrate}, BMU=0x{self.cfg.bms.bmu_address:02X}, "
                f"App=0x{self.cfg.bms.app_address:02X}"
            )
        else:
            self._log(
                f"CAN settings applied: ch={self.cfg.bms.channel} @ {self.cfg.bms.bitrate}, "
                f"BMU=0x{self.cfg.bms.bmu_address:02X}, App=0x{self.cfg.bms.app_address:02X}"
            )
        self.lbl_can_detect.setText(
            f"Active: ch={self.cfg.bms.channel} @ {self.cfg.bms.bitrate} · "
            f"BMU=0x{self.cfg.bms.bmu_address:02X} · App=0x{self.cfg.bms.app_address:02X}"
        )
        self.statusBar().showMessage("CAN nastavení aplikováno — znovu Připojit HW", 5000)
        self._sync_run_buttons()

    def _save_can_settings(self) -> None:
        try:
            self._read_can_form()
            self.cfg.use_mock_hardware = False
            path = save_bms_settings(self.cfg)
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        self._apply_can_settings()
        QMessageBox.information(self, "Saved", f"Saved BMS settings to\n{path}")

    def _auto_detect_can(self) -> None:
        if self._bms is not None or self._ea is not None:
            ans = QMessageBox.question(
                self,
                "Auto-detect CAN",
                "Hardware is connected. Disconnect now so the Kvaser is free for scanning?",
            )
            if ans != QMessageBox.Yes:
                return
            self._disconnect_hw()

        self.btn_can_detect.setEnabled(False)
        self.lbl_can_detect.setText("Scanning… close other CAN tools (CANalyzer, etc.).")
        QApplication.processEvents()

        from bts.can_discover import discover_can

        def progress(msg: str) -> None:
            self.lbl_can_detect.setText(msg)
            QApplication.processEvents()

        try:
            result = discover_can(
                interface=self.cmb_can_iface.currentText().strip() or "kvaser",
                channel=None,  # try physical channels
                prefer_bitrate=int(self.cfg.bms.bitrate),
                prefer_bmu=int(self.cfg.bms.bmu_address),
                app_address=int(self.cfg.bms.app_address),
                progress=progress,
            )
        except Exception as exc:
            self.btn_can_detect.setEnabled(True)
            self.lbl_can_detect.setText(f"Detect failed: {exc}")
            QMessageBox.critical(self, "Auto-detect failed", str(exc))
            return
        finally:
            self.btn_can_detect.setEnabled(True)

        if not result.ok:
            self.lbl_can_detect.setText(result.detail)
            QMessageBox.warning(self, "Auto-detect", result.detail)
            return

        assert result.channel is not None
        assert result.bitrate is not None
        assert result.bmu_address is not None
        self.cfg.bms.channel = int(result.channel)
        self.cfg.bms.bitrate = int(result.bitrate)
        self.cfg.bms.bmu_address = int(result.bmu_address) & 0xFF
        self._sync_can_form_from_cfg()
        self.lbl_can_detect.setText(result.detail)
        self._log(f"CAN auto-detect: {result.detail}")
        QMessageBox.information(
            self,
            "Auto-detect OK",
            f"{result.detail}\n\nClick Apply (or Save to config), then Connect HW.",
        )


    def _build_diagnostics_tab(self) -> None:
        self.diagnostics_tab = DiagnosticsTab(self.cfg)
        self.tabs.addTab(self.diagnostics_tab, "Diagnostika")

    def _build_logging_tab(self) -> None:
        self.logging_tab = LoggingTab()
        self.logging_tab.set_bms_provider(lambda: self._bms)
        self.logging_tab.set_name_provider(
            lambda: self.program.meta.name if self.program else "bts-log"
        )
        self.tabs.addTab(self.logging_tab, "Logování")

    def _reload_lists(self) -> None:
        keep = self.program_path
        # Editor module-type combo (categories) — keep current selection if still there.
        self.ed_profile.blockSignals(True)
        cur_prof = self.ed_profile.currentText()
        self.ed_profile.clear()
        for p in list_profiles(self.cfg.profiles_path):
            self.ed_profile.addItem(p.stem, str(p))
        if cur_prof:
            j = self.ed_profile.findText(cur_prof)
            if j >= 0:
                self.ed_profile.setCurrentIndex(j)
        self.ed_profile.blockSignals(False)
        # Two-level picker (module type -> step file). set_groups is silent.
        groups = list_programs_by_module(self.cfg.programs_path, self.cfg.profiles_path)
        self.program_picker.set_groups(groups, keep_path=keep)
        sel = self.program_picker.current_path()
        # Load when nothing is loaded yet, or the kept file vanished (archived/deleted)
        # and the picker landed on a different step file.
        if sel is not None and (
            self.program is None
            or keep is None
            or sel.resolve() != keep.resolve()
        ):
            self._load_program_path(sel)
        self._refresh_history()
        if hasattr(self, "simulate_tab"):
            self.simulate_tab.refresh_programs()

    def _mark_editor_dirty(self) -> None:
        self._editor_dirty = True

    def _confirm_discard_edits(self, action: str = "pokračovat") -> bool:
        if not self._editor_dirty:
            return True
        ans = QMessageBox.question(
            self,
            "Neuložené změny",
            f"Editor má neuložené změny.\nOpravdu {action} a zahodit je?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return ans == QMessageBox.Yes

    def _on_picker_selection(self, path: object) -> None:
        """User picked a step file in the toolbar picker (guarded)."""
        if path is None:
            return
        path = Path(path)
        # Switching away from an unsaved / different program
        if (
            self.program is not None
            and self._editor_dirty
            and (self.program_path is None or path.resolve() != self.program_path.resolve())
        ):
            if not self._confirm_discard_edits("načíst jiný program"):
                # Revert picker to the loaded program (silent)
                self.program_picker.select_path(self.program_path)
                return
        self._load_program_path(path)

    def _load_program_path(self, path: Path) -> None:
        """Load a step file into the editor + as the active program (no guard)."""
        path = Path(path)
        self.program = load_program(path)
        self.program_path = path
        self._editor_dirty = False
        self.profile_label.setText(f"Profil: {self.program.meta.module_profile}")
        try:
            self._active_profile = load_profile(
                self.cfg.profiles_path / f"{self.program.meta.module_profile}.yaml"
            )
            self.dashboard.set_profile(self._active_profile)
            self._apply_profile_capacity_to_form()
        except Exception:
            self._active_profile = None
        self._update_program_estimate()
        self._sync_editor_from_program()

    def _apply_profile_capacity_to_form(self) -> None:
        prof = self._active_profile
        ah = 70.0
        if prof is not None:
            ah = float(
                getattr(prof, "nominal_capacity_ah", None)
                or getattr(prof, "typical_capacity_ah", None)
                or 70.0
            )
        self.step_form.set_capacity_ah(ah)

    def _on_editor_profile_changed(self, name: str) -> None:
        """Keep C↔A link using Ah of the profile selected in the editor."""
        if not name:
            return
        try:
            self._active_profile = load_profile(self.cfg.profiles_path / f"{name}.yaml")
            self.dashboard.set_profile(self._active_profile)
            self._apply_profile_capacity_to_form()
            self._update_program_estimate()
            # Refresh step list summaries (C/A text depends on profile Ah)
            if self.program:
                row = self.step_list.currentRow()
                self.step_list.blockSignals(True)
                for i, s in enumerate(self.program.steps):
                    if i < self.step_list.count():
                        self.step_list.item(i).setText(self._step_summary(s))
                self.step_list.blockSignals(False)
                if row >= 0:
                    self.step_list.setCurrentRow(row)
        except Exception:
            logging.getLogger(__name__).exception("Editor profile change failed")

    def _update_program_estimate(self) -> None:
        if not self.program:
            self.dashboard.progress.set_estimate(None)
            return
        est = estimate_program(self.program, self._active_profile)
        self.dashboard.progress.set_estimate(est)

    def _sync_editor_from_program(self) -> None:
        if not self.program:
            return
        self.ed_name.setText(self.program.meta.name)
        i = self.ed_profile.findText(self.program.meta.module_profile)
        if i >= 0:
            self.ed_profile.setCurrentIndex(i)
        self.ed_desc.setText(self.program.meta.description)
        tmax = getattr(self.program.meta, "t_max_c", None)
        self.chk_prog_tmax.setChecked(tmax is not None)
        if tmax is not None:
            self.ed_prog_tmax.setValue(float(tmax))
        else:
            self.ed_prog_tmax.setValue(50.0)
        self.step_list.clear()
        for s in self.program.steps:
            summary = self._step_summary(s)
            self.step_list.addItem(QListWidgetItem(summary))
        if self.program.steps:
            self.step_list.setCurrentRow(0)

    def _step_summary(self, s: Step) -> str:
        extra = ""
        if s.type == "wait_time":
            extra = f" {s.params.get('seconds', '?')}s"
        elif s.type in ("charge", "discharge", "goto_soc", "dcir"):
            try:
                amp_key = "pulse_a" if s.type == "dcir" else "current_a"
                default = 70.0
                if self._active_profile is not None:
                    default = float(
                        self._active_profile.dcir_pulse_a
                        if s.type == "dcir"
                        else self._active_profile.default_test_current_a
                    )
                if self._active_profile is not None:
                    amps = resolve_amps(
                        s.params,
                        self._active_profile,
                        amp_key=amp_key,
                        default=default,
                    )
                    extra = f" {format_amps_with_crate(amps, self._active_profile)}"
                else:
                    crate = s.params.get("c_rate")
                    amps = s.params.get(amp_key)
                    if crate is not None:
                        extra = f" {float(crate):.2f}C"
                    elif amps is not None:
                        extra = f" {float(amps):.0f}A"
                    else:
                        extra = " ?"
                if s.type == "dcir":
                    extra += f"/{s.params.get('pulse_s', '?')}s"
            except Exception:
                extra = " ?"
        return f"{s.id}  [{s.type}]{extra}"

    def _commit_editor_row(self, row: int) -> None:
        """Persist step form into program without requiring Apply click."""
        if not self.program or row < 0 or row >= len(self.program.steps):
            return
        try:
            self.program.steps[row] = self.step_form.build_step()
            self._editor_dirty = True
        except Exception:
            logging.getLogger(__name__).exception("Auto-apply step failed")

    def _on_step_row_changed(self, row: int) -> None:
        prev = getattr(self, "_editor_row", -1)
        if prev >= 0 and prev != row:
            self._commit_editor_row(prev)
        self._editor_row = row
        self._load_step_form(row)

    def _load_step_form(self, row: int) -> None:
        if not self.program or row < 0 or row >= len(self.program.steps):
            return
        self.step_form.load_step(self.program.steps[row])

    def _on_step_applied(self, step: Step) -> None:
        if not self.program:
            return
        row = self.step_list.currentRow()
        if row < 0:
            return
        self.program.steps[row] = step
        self._editor_dirty = True
        self._sync_editor_from_program()
        self.step_list.setCurrentRow(row)

    def _add_step(self) -> None:
        if not self.program:
            self._new_program()
        assert self.program
        row = self.step_list.currentRow()
        if row >= 0:
            self._commit_editor_row(row)
        n = len(self.program.steps) + 1
        self.program.steps.append(Step(id=f"wait_{n}", type="wait_time", params={"seconds": 60}))
        self._editor_dirty = True
        self._sync_editor_from_program()
        self.step_list.setCurrentRow(len(self.program.steps) - 1)

    def _duplicate_step(self) -> None:
        """Insert a copy of the selected step right below it (unique id)."""
        if not self.program:
            return
        row = self.step_list.currentRow()
        if row < 0 or row >= len(self.program.steps):
            return
        self._commit_editor_row(row)
        src = self.program.steps[row]
        existing = {s.id for s in self.program.steps}
        base = f"{src.id}_copy"
        new_id = base
        n = 2
        while new_id in existing:
            new_id = f"{base}{n}"
            n += 1
        clone = Step(id=new_id, type=src.type, params=copy.deepcopy(src.params))
        self.program.steps.insert(row + 1, clone)
        self._editor_dirty = True
        self._sync_editor_from_program()
        self.step_list.setCurrentRow(row + 1)

    def _remove_step(self) -> None:
        if not self.program:
            return
        row = self.step_list.currentRow()
        if row < 0:
            return
        self._commit_editor_row(row)
        sid = self.program.steps[row].id
        ans = QMessageBox.question(
            self,
            "Smazat krok?",
            f"Opravdu smazat krok „{sid}“?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        del self.program.steps[row]
        self._editor_dirty = True
        self._sync_editor_from_program()
        if self.program.steps:
            self.step_list.setCurrentRow(min(row, len(self.program.steps) - 1))

    def _move_up(self) -> None:
        if not self.program:
            return
        row = self.step_list.currentRow()
        if row > 0:
            self._commit_editor_row(row)
            self.program.steps[row - 1], self.program.steps[row] = (
                self.program.steps[row],
                self.program.steps[row - 1],
            )
            self._editor_dirty = True
            self._sync_editor_from_program()
            self.step_list.setCurrentRow(row - 1)

    def _move_down(self) -> None:
        if not self.program:
            return
        row = self.step_list.currentRow()
        if 0 <= row < len(self.program.steps) - 1:
            self._commit_editor_row(row)
            self.program.steps[row + 1], self.program.steps[row] = (
                self.program.steps[row],
                self.program.steps[row + 1],
            )
            self._editor_dirty = True
            self._sync_editor_from_program()
            self.step_list.setCurrentRow(row + 1)

    def _new_program(self) -> None:
        if not self._confirm_discard_edits("vytvořit nový program"):
            return
        profile = self.ed_profile.currentText() or "LTO_24V_70Ah"
        self.program = Program(
            meta=ProgramMeta(name="New_program", module_profile=profile, description=""),
            steps=[
                Step(id="ready", type="bms_ready", params={"timeout_s": 60}),
                Step(id="idle", type="bms_idle", params={"timeout_s": 30}),
            ],
        )
        self.program_path = None
        self._editor_dirty = True
        self._sync_editor_from_program()

    def _new_module_type(self) -> None:
        """Create a new module type (category) by cloning an existing profile."""
        src = self.ed_profile.currentData()
        if not src:
            profiles = list_profiles(self.cfg.profiles_path)
            if not profiles:
                QMessageBox.warning(
                    self,
                    "Nový typ modulu",
                    "Není žádný profil k naklonování. Přidej profil do profiles/.",
                )
                return
            src = str(profiles[0])
        src_path = Path(src)
        new_id, ok = QInputDialog.getText(
            self,
            "Nový typ modulu",
            f"ID nového typu modulu (klon z '{src_path.stem}').\n"
            "Bez mezer, použije se jako název souboru:",
        )
        if not ok:
            return
        new_id = new_id.strip()
        if not new_id or any(c in new_id for c in ' \\/:*?"<>|'):
            QMessageBox.warning(self, "Nový typ modulu", "Neplatné ID (mezery / speciální znaky).")
            return
        if (self.cfg.profiles_path / f"{new_id}.yaml").exists():
            QMessageBox.warning(self, "Nový typ modulu", f"Typ modulu '{new_id}' už existuje.")
            return
        new_name, ok = QInputDialog.getText(
            self, "Nový typ modulu", "Popisný název:", text=new_id
        )
        if not ok:
            return
        try:
            clone_profile(src_path, new_id, new_name.strip() or new_id, self.cfg.profiles_path)
        except Exception as exc:
            QMessageBox.critical(self, "Nový typ modulu", f"Nepodařilo se vytvořit: {exc}")
            return
        self._reload_lists()
        i = self.ed_profile.findText(new_id)
        if i >= 0:
            self.ed_profile.setCurrentIndex(i)
        QMessageBox.information(
            self,
            "Nový typ modulu",
            f"Vytvořeno profiles/{new_id}.yaml (klon z {src_path.stem}).\n"
            "Uprav specifikace baterie v tom souboru podle potřeby.",
        )
        self.statusBar().showMessage(f"Nový typ modulu: {new_id}", 4000)

    def _delete_stepfile(self, path: object) -> None:
        """Archive a step file (recoverable delete) from the toolbar picker."""
        path = Path(path)
        if not path.exists():
            self._reload_lists()
            return
        reply = QMessageBox.question(
            self,
            "Smazat step file",
            f"Přesunout '{path.stem}' do archivu (programs/_archiv/)?\n"
            "Soubor zůstane obnovitelný, ale zmizí z roletky.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            dest = archive_file(path, self.cfg.programs_path / "_archiv")
        except Exception as exc:
            QMessageBox.critical(self, "Smazat step file", f"Nepodařilo se: {exc}")
            return
        # If we archived the loaded program, drop the stale path so reload picks another.
        if self.program_path is not None and self.program_path.resolve() == path.resolve():
            self.program_path = None
            self._editor_dirty = False
        self._reload_lists()
        self.statusBar().showMessage(f"Přesunuto do archivu: {dest.name}", 4000)

    def _delete_module_type(self, module_id: object) -> None:
        """Archive an empty module type (profile) from the picker."""
        module_id = str(module_id)
        prof_path = self.cfg.profiles_path / f"{module_id}.yaml"
        # Guard: never delete a category that still has step files.
        groups = list_programs_by_module(self.cfg.programs_path, self.cfg.profiles_path)
        grp = next((g for g in groups if g.module_id == module_id), None)
        if grp is not None and grp.programs:
            QMessageBox.warning(
                self,
                "Smazat typ modulu",
                f"Typ modulu '{module_id}' má step fily — nejdřív je smaž/přesuň.",
            )
            return
        if not prof_path.exists():
            self._reload_lists()
            return
        reply = QMessageBox.question(
            self,
            "Smazat typ modulu",
            f"Přesunout prázdný typ modulu '{module_id}' do archivu (profiles/_archiv/)?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            dest = archive_file(prof_path, self.cfg.profiles_path / "_archiv")
        except Exception as exc:
            QMessageBox.critical(self, "Smazat typ modulu", f"Nepodařilo se: {exc}")
            return
        self._reload_lists()
        self.statusBar().showMessage(f"Typ modulu do archivu: {dest.name}", 4000)

    def _open_program(self) -> None:
        if not self._confirm_discard_edits("otevřít jiný program"):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open program", str(self.cfg.programs_path), "YAML (*.yaml)")
        if path:
            self.program = load_program(Path(path))
            self.program_path = Path(path)
            self._editor_dirty = False
            self._sync_editor_from_program()
            self._reload_lists()

    def _sync_meta_into_program(self) -> None:
        if not self.program:
            return
        self.program.meta.name = self.ed_name.text().strip() or "Untitled"
        self.program.meta.module_profile = self.ed_profile.currentText()
        self.program.meta.description = self.ed_desc.text()
        if self.chk_prog_tmax.isChecked():
            self.program.meta.t_max_c = float(self.ed_prog_tmax.value())
        else:
            self.program.meta.t_max_c = None

    def _current_profile(self):
        if not self.program:
            raise ValueError("No program")
        prof_path = self.cfg.profiles_path / f"{self.program.meta.module_profile}.yaml"
        if not prof_path.exists():
            raise FileNotFoundError(f"Profile not found: {prof_path}")
        return load_profile(prof_path)

    def _validate_or_errors(self) -> list[str]:
        self._sync_meta_into_program()
        # Apply current form into selected step so Validate sees latest edits
        row = self.step_list.currentRow()
        if self.program and row >= 0:
            self._commit_editor_row(row)
            self._sync_editor_from_program()
            self.step_list.setCurrentRow(row)
        profile = self._current_profile()
        max_el = self.cfg.ea.el.max_current_a if self.cfg.ea else 1020.0
        assert self.program
        return validate_program(self.program, profile, max_el_combined_a=max_el)

    def _save_program(self) -> None:
        if not self.program:
            return
        errs = self._validate_or_errors()
        if errs:
            QMessageBox.warning(self, "Cannot save — fix validation errors", "\n".join(errs))
            return
        path = self.program_path
        if path is None:
            path = self.cfg.programs_path / f"{self.program.meta.name}.yaml"
            path, _ = QFileDialog.getSaveFileName(self, "Save program", str(path), "YAML (*.yaml)")
            if not path:
                return
            path = Path(path)
        save_program(self.program, path)
        self.program_path = path
        self._editor_dirty = False
        QMessageBox.information(self, "Uloženo", f"Uloženo do\n{path}")
        self._reload_lists()
        self.statusBar().showMessage(f"Program uložen: {path.name}", 4000)

    def _validate_program(self) -> None:
        if not self.program:
            return
        try:
            errs = self._validate_or_errors()
        except Exception as exc:
            QMessageBox.warning(self, "Validate", str(exc))
            return
        if errs:
            QMessageBox.warning(self, "Validation FAILED", "\n".join(errs))
        else:
            QMessageBox.information(self, "Validation", "OK — program is consistent")

    def _hw_banner_style(self, kind: str) -> str:
        # Solid, high-contrast status fills (readable over RDP) — modern rounding,
        # no heavy dark outline, a little more breathing room.
        base = (
            "color:#ffffff;font-weight:800;font-size:15px;"
            "padding:12px 18px;border-radius:6px;border:none;"
        )
        fills = {
            "off": "background:#6b7787;",
            "busy": "background:#c98a00;",
            "ok": "background:#1f8a5b;",
            "partial": "background:#c99400;",
            "bad": "background:#c0392b;",
        }
        return base + fills.get(kind, fills["off"])

    def _signal_attention(self, *, ok: bool) -> None:
        """Beep + flash taskbar — helps when watching the PC over RDP."""
        try:
            QApplication.beep()
        except Exception:
            pass
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_OK if ok else winsound.MB_ICONHAND)
        except Exception:
            pass
        try:
            QApplication.alert(self, 0)
        except Exception:
            pass

    def _set_hw_banner(self, kind: str, text: str) -> None:
        if not hasattr(self, "lbl_hw_banner"):
            return
        self.lbl_hw_banner.setText(text)
        self.lbl_hw_banner.setStyleSheet(self._hw_banner_style(kind))

    def _update_connect_button(self, live: bool) -> None:
        self._hw_live = bool(live)
        if not hasattr(self, "btn_connect"):
            return
        if live:
            self.btn_connect.setText("Odpojit HW")
            self.btn_connect.setStyleSheet(
                "QPushButton { background:#1f8a5b; color:#fff; font-weight:700; "
                "padding:8px 16px; border-radius:4px; border:none; }"
                "QPushButton:hover { background:#1a7a4f; }"
            )
            self.btn_connect.setToolTip("HW je připojené — kliknutím odpojíš BMS + EA")
        else:
            self.btn_connect.setText("Připojit HW")
            self.btn_connect.setStyleSheet("")
            self.btn_connect.setToolTip("Připojí Kvaser + EA. Stav je v zeleném/šedém pruhu pod toolbarem.")

    def _on_connect_clicked(self) -> None:
        if getattr(self, "_hw_live", False) and self._bms is not None and self._ea is not None:
            if self._engine_is_alive():
                ans = QMessageBox.warning(
                    self,
                    "Odpojit HW?",
                    "Běží test — odpojení okamžitě STOPNE sekvenci a odpojí CAN/EA.\n\nPokračovat?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ans != QMessageBox.Yes:
                    return
            self._disconnect_hw()
            self._set_hw_banner("off", "HW: odpojeno")
            self._update_connect_button(False)
            self._sync_run_buttons()
            self._refresh_version_ui()
            self._log("HW odpojeno")
            self._signal_attention(ok=True)
            return
        self._connect_hw()

    def _confirm_abort_if_running(self, action: str) -> bool:
        if not self._engine_is_alive():
            return True
        ans = QMessageBox.warning(
            self,
            action,
            f"{action} během běžícího testu ho ukončí (Stop + odpojení).\n\nPokračovat?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return ans == QMessageBox.Yes

    def _sync_run_buttons(self) -> None:
        alive = self._engine_is_alive()
        busy = bool(getattr(self, "_hw_busy", False))
        if hasattr(self, "btn_start"):
            self.btn_start.setEnabled(not alive and not busy)
            self.btn_start.setToolTip(
                "Test běží — nejdřív Stop" if alive else ("Připojuji HW…" if busy else "Spustit načtený program")
            )
        if hasattr(self, "btn_stop"):
            self.btn_stop.setEnabled(alive)
            self.btn_stop.setToolTip("Zastavit test (EA off → I≈0 → stykače)" if alive else "Žádný běžící test")
        if hasattr(self, "btn_connect") and not busy:
            # connect enable managed separately during connect; when not busy keep as-is unless we need
            pass

    def _disconnect_hw(self) -> None:
        if getattr(self, "logging_tab", None) is not None:
            self.logging_tab.stop_if_running()
        if self.engine is not None:
            try:
                self.engine.abort()
                self.engine.join(timeout_s=CONTACTOR_SAFE_JOIN_S)
            except Exception:
                pass
            self.engine = None
        if self._ea is not None:
            try:
                self._ea.disconnect()
            except Exception:
                logging.getLogger(__name__).exception("EA disconnect failed")
            self._ea = None
        if self._bms is not None:
            try:
                self._bms.disconnect()
            except Exception:
                logging.getLogger(__name__).exception("BMS disconnect failed")
            self._bms = None
        self._set_link_labels(None, None)
        self._update_connect_button(False)
        if hasattr(self, "lbl_hw_banner"):
            self._set_hw_banner("off", "HW: odpojeno — klikni Připojit HW")

    def _engine_is_alive(self) -> bool:
        eng = self.engine
        if eng is None:
            return False
        t = getattr(eng, "_thread", None)
        return t is not None and t.is_alive()

    def _bench_needs_safe_shutdown(self) -> bool:
        """True when quitting would leave EA on or contactors closed."""
        if self._engine_is_alive():
            return True
        if self._ea is not None:
            try:
                et = self._ea.telemetry()
                if et.psi_output_on or et.el_input_on or (et.active_mode in ("charge", "discharge")):
                    return True
                if abs(et.psi_current_a) > 1.0 or abs(et.el_current_a) > 1.0:
                    return True
            except Exception:
                pass
        if self._bms is not None:
            try:
                b = self._bms.telemetry()
                if b.operating_state in (BmuState.READY, BmuState.PRE_CHARGE):
                    return True
                st = b.contactors_effective
                if st.main_pos or st.main_neg or st.precharge:
                    return True
            except Exception:
                pass
        return False

    def _safe_power_down_for_quit(self) -> bool:
        """EA off → wait I≈0 → IDLE. Returns False if contactors could not open."""
        self._log("Bezpečné vypnutí před zavřením…")
        QApplication.processEvents()
        if self._engine_is_alive() and self.engine is not None:
            self.engine.abort()
            self.engine.join(timeout_s=CONTACTOR_SAFE_JOIN_S)
            self.engine = None
        if self._ea is not None:
            try:
                self._ea.all_off()
            except Exception:
                log.exception("EA all_off on quit")
        i_max = 1.5
        timeout_s = 20.0
        min_dwell = 1.5
        hold_s = 1.5
        t0 = time.monotonic()
        while True:
            if self._ea is not None:
                try:
                    self._ea.all_off()
                except Exception:
                    pass
            pack_i = 0.0
            ea_i = 0.0
            if self._bms is not None:
                try:
                    b = self._bms.telemetry()
                    if b.pack_current_a is not None:
                        pack_i = abs(float(b.pack_current_a))
                except Exception:
                    pass
            if self._ea is not None:
                try:
                    e = self._ea.telemetry()
                    ea_i = max(abs(float(e.psi_current_a)), abs(float(e.el_current_a)))
                except Exception:
                    pass
            i_now = max(pack_i, ea_i)
            elapsed = time.monotonic() - t0
            if elapsed >= min_dwell and i_now <= i_max:
                hold_t0 = time.monotonic()
                ok_hold = True
                while time.monotonic() - hold_t0 < hold_s:
                    if self._ea is not None:
                        try:
                            self._ea.all_off()
                        except Exception:
                            pass
                    i_hold = 0.0
                    if self._bms is not None:
                        try:
                            bb = self._bms.telemetry()
                            if bb.pack_current_a is not None:
                                i_hold = abs(float(bb.pack_current_a))
                        except Exception:
                            pass
                    if self._ea is not None:
                        try:
                            ee = self._ea.telemetry()
                            i_hold = max(
                                i_hold,
                                abs(float(ee.psi_current_a)),
                                abs(float(ee.el_current_a)),
                            )
                        except Exception:
                            pass
                    if i_hold > i_max:
                        ok_hold = False
                        break
                    QApplication.processEvents()
                    time.sleep(0.2)
                if ok_hold:
                    break
            if elapsed >= timeout_s:
                QMessageBox.critical(
                    self,
                    "Nelze bezpečně zavřít",
                    f"Proud stále {i_now:.1f} A po {timeout_s:.0f}s — stykače NEOTEVÍRÁME.\n"
                    "Zkontroluj EA off / pojistku / kabeláž a zkus znovu.",
                )
                return False
            QApplication.processEvents()
            time.sleep(0.25)
        if self._bms is not None:
            try:
                self._bms.set_desired_state(DesiredState.IDLE)
                t_idle = time.monotonic()
                idle_ok = False
                while time.monotonic() - t_idle < 8.0:
                    if self._bms.telemetry().operating_state == BmuState.IDLE:
                        idle_ok = True
                        break
                    QApplication.processEvents()
                    time.sleep(0.2)
                if not idle_ok:
                    # Current already confirmed ≈0 above, so this is electrically
                    # safe to close, but the BMU never reported IDLE — record it.
                    self._log(
                        "POZOR: BMU nepotvrdil IDLE do 8 s (proud byl ≈0) — "
                        "zkontroluj stav stykačů."
                    )
            except Exception:
                log.exception("BMS IDLE on quit failed")
                return False
        self._log("Bezpečné vypnutí hotovo — zavírám")
        return True

    def closeEvent(self, event) -> None:  # noqa: N802
        if getattr(self, "logging_tab", None) is not None:
            self.logging_tab.stop_if_running()
        if not self._bench_needs_safe_shutdown():
            # Idle but connected — release Kvaser/COM so other tools can use them
            if self._bms is not None or self._ea is not None:
                try:
                    self._disconnect_hw()
                except Exception:
                    log.exception("disconnect on quit failed")
            event.accept()
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Zavřít BTS?")
        box.setText("Nelze zavřít přímo — běží test, EA je aktivní, nebo jsou sepnuté stykače.")
        box.setInformativeText(
            "Bezpečné vypnutí: vypne zdroj a zátěž, počká až I≈0 "
            "(kontroluje proud, ať se neničí stykače), rozepne stykače a teprve pak ukončí aplikaci."
        )
        safe_btn = box.addButton("Bezpečně vypnout a zavřít", QMessageBox.AcceptRole)
        box.addButton("Zůstat", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() != safe_btn:
            event.ignore()
            return
        if not self._safe_power_down_for_quit():
            event.ignore()
            return
        try:
            self._disconnect_hw()
        except Exception:
            pass
        event.accept()

    def _update_rx_rate(self, rx_count: int) -> float:
        """Smoothed BMU frame rate (frames/s) from rx_count deltas over time."""
        now = time.monotonic()
        if self._rx_rate_count is None or self._rx_rate_mono is None:
            self._rx_rate_count = rx_count
            self._rx_rate_mono = now
            return self._rx_rate
        dt = now - self._rx_rate_mono
        if dt >= 0.4:  # sample window — long enough to be stable, short enough to react
            inst = max(0.0, rx_count - self._rx_rate_count) / dt
            # exponential smoothing to avoid the number jumping around
            self._rx_rate = inst if self._rx_rate <= 0 else 0.6 * self._rx_rate + 0.4 * inst
            self._rx_rate_count = rx_count
            self._rx_rate_mono = now
        return self._rx_rate

    def _set_link_labels(self, bms, ea) -> None:
        ok = "#1a7f37"
        warn = "#9a6700"
        bad = "#cf222e"
        dim = TEXT_DIM

        bms_ok = False
        bms_partial = False
        if bms is None:
            self._rx_rate_count = None  # reset rate estimator when link drops
            self.lbl_link_bms.setText("BMS: not connected")
            self.lbl_link_bms.setStyleSheet(f"color:{dim};font-weight:600;")
        elif getattr(bms, "rx_count", 0) > 0 and bms.connected:
            age = time.monotonic() - bms.last_rx_s if bms.last_rx_s > 0 else 999
            cells = len([v for v in (bms.cell_voltages or []) if v == v])  # not NaN
            state = bms.operating_state.name if bms.operating_state else "?"
            rate = self._update_rx_rate(bms.rx_count)
            if age <= 1.5:
                rate_txt = f"{rate:.0f} fr/s" if rate >= 1 else "…"
                self.lbl_link_bms.setText(
                    f"BMS: RX OK · {rate_txt} · {state}"
                    + (f" · {cells} cells" if cells else " · waiting for cells")
                )
                self.lbl_link_bms.setToolTip(f"Celkem přijato {bms.rx_count} rámců od připojení")
                self.lbl_link_bms.setStyleSheet(f"color:{ok};font-weight:600;")
                bms_ok = True
            else:
                self.lbl_link_bms.setText(f"BMS: stale RX ({age:.1f}s) · check CAN")
                self.lbl_link_bms.setStyleSheet(f"color:{warn};font-weight:600;")
                bms_partial = True
        elif bms.connected:
            self._rx_rate_count = None
            self.lbl_link_bms.setText(
                "BMS: Kvaser open — no BMU frames (External CAN / bitrate / address?)"
            )
            self.lbl_link_bms.setStyleSheet(f"color:{bad};font-weight:600;")
            bms_partial = True
        else:
            self.lbl_link_bms.setText("BMS: disconnected")
            self.lbl_link_bms.setStyleSheet(f"color:{dim};font-weight:600;")

        ea_ok = False
        if ea is None:
            running = (
                self.engine is not None
                and self.engine.status().run_state.name == "RUNNING"
                and self._ea is not None
            )
            if running:
                psi = self.cfg.ea.psi.serial_port if self.cfg.ea else "?"
                el = self.cfg.ea.el.serial_port if self.cfg.ea else "?"
                self.lbl_link_ea.setText(f"EA: run · PSI {psi} · EL {el}")
                self.lbl_link_ea.setStyleSheet(f"color:{ok};font-weight:600;")
                ea_ok = True
            else:
                self.lbl_link_ea.setText("EA: not connected")
                self.lbl_link_ea.setStyleSheet(f"color:{dim};font-weight:600;")
        elif ea.connected:
            psi = self.cfg.ea.psi.serial_port if self.cfg.ea else "?"
            el = self.cfg.ea.el.serial_port if self.cfg.ea else "?"
            self.lbl_link_ea.setText(f"EA: OK · PSI {psi} · EL {el}")
            self.lbl_link_ea.setStyleSheet(f"color:{ok};font-weight:600;")
            ea_ok = True
        else:
            self.lbl_link_ea.setText("EA: disconnected")
            self.lbl_link_ea.setStyleSheet(f"color:{bad};font-weight:600;")

        live = self._bms is not None and self._ea is not None
        run_active = (
            self.engine is not None
            and self.engine.status().run_state.name == "RUNNING"
        )
        self._update_connect_button(live)
        if not live:
            self._set_hw_banner("off", "HW: odpojeno — klikni Připojit HW")
        elif run_active:
            self._set_hw_banner("ok", "TEST BĚŽÍ — BMS RX OK · EA OK")
        elif bms_ok and ea_ok:
            self._set_hw_banner("ok", "HW PŘIPOJENO — BMS RX OK · EA OK  (můžeš Start)")
        elif ea_ok and bms_partial:
            self._set_hw_banner(
                "partial",
                "HW částečně — EA OK, ale BMS bez živých dat (zkontroluj External CAN / BMU)",
            )
        elif ea_ok:
            self._set_hw_banner("partial", "HW částečně — EA OK, BMS čeká na data…")
        else:
            self._set_hw_banner("partial", "HW otevřeno — čekám na potvrzení spojení…")

    def _connect_hw(self) -> None:
        if self._hw_busy:
            return
        self.cfg.use_mock_hardware = False
        # Always request full cell voltage stream on External CAN
        self.cfg.bms.request_cell_voltages = True
        self.cfg.bms.request_temperatures = True
        self.cfg.bms.request_cell_balance = True
        self._disconnect_hw()
        self._hw_busy = True
        self._sync_run_buttons()
        self._set_hw_banner("busy", "HW: připojuji Kvaser + EA…")
        self.btn_connect.setEnabled(False)
        QApplication.processEvents()
        try:
            self._bms = create_bms_driver(self.cfg.bms, False)
            assert self.cfg.ea
            self._ea = create_ea_driver(self.cfg.ea, False, mock_bms=None)
            self._bms.connect()
            self._bms.start()
            self._ea.connect()
            cmd_id = (
                0x18EF0000
                | ((self.cfg.bms.bmu_address & 0xFF) << 8)
                | (self.cfg.bms.app_address & 0xFF)
            )
            self._log(
                f"Kvaser open ch={self.cfg.bms.channel} @ {self.cfg.bms.bitrate} — "
                f"App Command 0x{cmd_id:08X} every {self.cfg.bms.heartbeat_period_ms} ms "
                f"(request cells+temps+balance)…"
            )
            QApplication.processEvents()
            got_rx = False
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if self._bms.is_healthy(2.0):
                    got_rx = True
                    break
                QApplication.processEvents()
                time.sleep(0.05)
            tel = self._bms.telemetry()
            ea_tel = self._ea.telemetry()
            self._set_link_labels(tel, ea_tel)
            if got_rx:
                cells = len([v for v in tel.cell_voltages if v == v])
                self._log(
                    f"Connected (live) — BMU RX OK ({tel.rx_count} frames, "
                    f"state={tel.operating_state.name}, cells={cells or 'pending'})"
                )
                self._set_hw_banner(
                    "ok",
                    f"HW PŘIPOJENO — BMS {tel.operating_state.name} · "
                    f"{tel.rx_count} frames · EA OK",
                )
                self._signal_attention(ok=True)
            else:
                detail = self._bms.health_detail()
                self._log(f"Connected (live) — NO BMU frames yet. {detail}")
                self._set_hw_banner(
                    "partial",
                    "HW částečně — EA/Kvaser OK, ale žádná data z BMU (External CAN?)",
                )
                self._signal_attention(ok=False)
                QMessageBox.warning(
                    self,
                    "BMS: no data from BMU",
                    "Kvaser and EA opened, but no CAN frames from the BMU.\n\n"
                    f"{detail}\n\n"
                    "Typical causes:\n"
                    "• Kvaser not on External CAN (internal BMU↔LMU bus)\n"
                    "• BMU not powered / wrong bitrate\n"
                    "• Wrong App SA (try 0x20 ACU vs 0xF9 Service Tool)\n\n"
                    "Do not Start a run until the BMS line turns green.",
                )
            self._refresh_live()
            self._refresh_version_ui()
        except Exception as exc:
            self._disconnect_hw()
            self._set_hw_banner("bad", f"HW PŘIPOJENÍ SELHALO — {exc}")
            self._signal_attention(ok=False)
            QMessageBox.critical(self, "Connect failed", str(exc))
            self._log(f"Connect failed: {exc}")
        finally:
            self._hw_busy = False
            self.btn_connect.setEnabled(True)
            self._sync_run_buttons()

    def _start_run(self) -> None:
        if self._hw_busy:
            QMessageBox.information(self, "Start", "Ještě probíhá připojení HW — chvíli počkej.")
            return
        if self._engine_is_alive():
            QMessageBox.warning(
                self,
                "Start",
                "Test už běží — nejdřív Stop, nebo počkej na dokončení.",
            )
            return
        if self.program is None:
            QMessageBox.warning(self, "Start", "Není načtený program")
            return
        # Commit editor edits before validate/start
        row = self.step_list.currentRow()
        if row >= 0:
            self._commit_editor_row(row)
        path = self.program_path
        if path is not None and "dev" in Path(path).parts:
            ans = QMessageBox.warning(
                self,
                "DEV program",
                f"Program „{path.name}“ je ve složce programs/dev (smoke).\n"
                "Není určen pro ostrý modul.\n\n"
                "Přesto spustit?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        try:
            errs = self._validate_or_errors()
        except Exception as exc:
            QMessageBox.warning(self, "Start", str(exc))
            return
        if errs:
            QMessageBox.warning(self, "Cannot start", "\n".join(errs))
            return
        if self._bms is None or self._ea is None:
            self._connect_hw()
        if self._bms is None or self._ea is None:
            return
        if not self._bms.is_healthy(2.0):
            QMessageBox.warning(
                self,
                "Cannot start — no BMS data",
                "BMU is not sending CAN frames.\n\n"
                f"{self._bms.health_detail()}\n\n"
                "Fix the External CAN link first (BMS line must be green).",
            )
            return
        profile = self._current_profile()
        self.engine = SequenceEngine(
            bms=self._bms,
            ea=self._ea,
            profile=profile,
            program=copy.deepcopy(self.program),
            runs_dir=self.cfg.runs_path,
            poll_period_s=self.cfg.safety.poll_period_ms / 1000.0,
            can_timeout_s=self.cfg.safety.can_timeout_s,
            ea_timeout_s=self.cfg.safety.ea_timeout_s,
            abort_dtc_level=self.cfg.safety.abort_dtc_level,
            serial_number=self.serial_edit.text().strip(),
            ea_i_mismatch_min_set_a=self.cfg.safety.ea_i_mismatch_min_set_a,
            ea_i_slave_ratio_lo=self.cfg.safety.ea_i_slave_ratio_lo,
            ea_i_slave_ratio_hi=self.cfg.safety.ea_i_slave_ratio_hi,
            ea_i_collapse_ratio=self.cfg.safety.ea_i_collapse_ratio,
            ea_i_mismatch_confirm_s=self.cfg.safety.ea_i_mismatch_confirm_s,
        )
        self._update_program_estimate()
        self.dashboard.progress.reset_idle()
        self.dashboard.clear_history()
        self.diagnostics_tab.clear_activity()
        self.engine.add_listener(lambda st: self.engine_status.emit(st))
        self.engine.add_activity_listener(lambda line: self.activity_line.emit(line))
        self.engine.start()
        self._sync_run_buttons()
        self.lbl_msg.setText("Run started…")
        self._log("Run started — Activity console je v Diagnostice")
        self.statusBar().showMessage("Test běží", 3000)

    def _on_engine_status(self, st) -> None:
        if st.message:
            self.lbl_msg.setText(st.message)
        if st.activity_path:
            self.diagnostics_tab.set_activity_log_path(st.activity_path)
        # Terminal states → re-enable Start
        name = getattr(getattr(st, "run_state", None), "name", "") or getattr(st, "run_state", "")
        if str(name).lower() in ("completed", "failed", "aborted", "idle"):
            self._sync_run_buttons()

    def _on_activity_line(self, line: str) -> None:
        self.diagnostics_tab.append_activity(line)

    def _stop_run(self) -> None:
        if not self._engine_is_alive():
            return
        if self.engine:
            self.engine.abort()
            self.lbl_msg.setText("Stop — EA off, čekám I≈0, pak stykače…")
            self.statusBar().showMessage("Stop vyžádán", 5000)
            self._log("Stop — EA off first, contactors open only after I≈0")
            self._sync_run_buttons()

    def _clear_bms_dtcs(self) -> None:
        if self._bms is None:
            QMessageBox.information(self, "Clear DTCs", "Connect HW first.")
            return
        if not self._bms.is_healthy(2.0):
            QMessageBox.warning(
                self,
                "Clear DTCs",
                "No BMU link — Connect HW and wait for green BMS RX first.",
            )
            return
        tel = self._bms.telemetry()
        before = format_active_dtcs(tel.active_dtcs, level=tel.dtc_level)
        try:
            self._bms.request_dtc_reset()
        except Exception as exc:
            QMessageBox.critical(self, "Clear DTCs failed", str(exc))
            return
        self._log(f"BMS DTC reset requested (was {before}) — App Command bit4 pulse")
        QMessageBox.information(
            self,
            "Clear DTCs",
            "Reset Latched DTCs sent to BMU.\n"
            "Only latched codes clear; active hard faults may return immediately.\n"
            f"Previous: {before}",
        )

    def _check_contactor_conflict(self, bms) -> None:
        """Warn when we commanded the mains OPEN but another app on the bus keeps them
        closed. Debounced ~3 s so a normal power-down (STOPPING waits for current to
        decay) doesn't trip it."""
        conflict_now = bool(
            bms is not None and bms.connected and bms.external_contactor_override
        )
        active = False
        if conflict_now:
            if self._contactor_conflict_since is None:
                self._contactor_conflict_since = time.monotonic()
            elif time.monotonic() - self._contactor_conflict_since >= 3.0:
                active = True
        else:
            self._contactor_conflict_since = None
        self.dashboard.schematic.set_contactor_conflict(active)
        if active:
            self.statusBar().showMessage(
                "⚠ Stykače drží sepnuté jiná aplikace — apka poslala pokyn rozepnout, "
                "ale BMU je řízené externě",
                4000,
            )

    def _refresh_live(self) -> None:
        bms = self._bms.telemetry() if self._bms is not None else None
        # While a sequence owns the EA COM ports, don't poll SCPI from the UI
        # thread — that races with charge/discharge commands and trips the watchdog.
        running = (
            self.engine is not None
            and self.engine.status().run_state.name == "RUNNING"
        )
        ea = None
        if self._ea is not None:
            if running and self.engine is not None:
                # Sequence thread owns SCPI — use its cached sample for UI/schematic
                ea = self.engine.last_ea_telemetry()
            else:
                ea = self._ea.telemetry()
        self.dashboard.update_live(bms, ea)
        self._set_link_labels(bms, ea)
        self._check_contactor_conflict(bms)

        if bms is not None:
            self.lbl_bms_state.setText(
                bms.operating_state.name if bms.operating_state else "—"
            )
            v_txt = f"{bms.pack_voltage_v:.2f} V" if bms.pack_voltage_v is not None else "— V"
            i_txt = f"{bms.pack_current_a:.1f} A" if bms.pack_current_a is not None else "— A"
            self.lbl_pack.setText(
                f"{v_txt} / {i_txt}"
                if (bms.pack_voltage_v is not None or bms.pack_current_a is not None)
                else "—"
            )
            ch = bms.charge_current_limit_a
            dch = bms.discharge_current_limit_a
            self.lbl_i_limits.setText(
                f"chg {ch:.0f}A / dch {dch:.0f}A" if ch is not None and dch is not None else "—"
            )
            self.lbl_dtc.setText(format_active_dtcs(bms.active_dtcs, level=bms.dtc_level))
            tips: list[str] = []
            for c in bms.active_dtcs or []:
                s = str(c).strip().upper()
                if s.startswith("FC"):
                    try:
                        tips.append(format_dtc_detail(int(s[2:])))
                    except ValueError:
                        tips.append(s)
                else:
                    tips.append(format_dtc_detail(int(s, 16) if s else 0))
            self.lbl_dtc.setToolTip("\n".join(tips) if tips else "Žádné aktivní DTC")
            self.lbl_dtc.setWordWrap(True)
        if ea is not None:
            self.lbl_psi.setText(
                f"{ea.psi_voltage_v:.2f} V / {ea.psi_current_a:.1f} A  {'ON' if ea.psi_output_on else 'off'}"
            )
            self.lbl_el.setText(
                f"{ea.el_voltage_v:.2f} V / {ea.el_current_a:.1f} A  {'ON' if ea.el_input_on else 'off'}"
            )
        if self.engine:
            st = self.engine.status()
            self.lbl_run_state.setText(st.run_state.value)
            self.lbl_step.setText(st.current_step_id or "—")
            self.lbl_msg.setText(st.message or "—")
            self.lbl_capacity.setText(
                f"{st.measurements.capacity_ah:.3f} Ah" if st.measurements.capacity_ah is not None else "—"
            )
            self.lbl_dcir.setText(
                f"{st.measurements.dcir_mohm:.3f} mΩ" if st.measurements.dcir_mohm is not None else "—"
            )
            ea_charging = bool(ea and (ea.psi_output_on or abs(ea.psi_current_a) > 0.5))
            ea_discharging = bool(ea and (ea.el_input_on or abs(ea.el_current_a) > 0.5))
            # Fallback: BMU pack current (charge +, discharge −) when EA cache is stale
            if bms is not None and bms.pack_current_a is not None:
                pi = float(bms.pack_current_a)
                if pi > 0.5:
                    ea_charging = True
                elif pi < -0.5:
                    ea_discharging = True
            self.dashboard.progress.update_run(
                run_state=st.run_state,
                step_index=st.current_step_index,
                step_id=st.current_step_id,
                step_type=st.current_step_type,
                step_started_mono=st.step_started_mono,
                run_started_mono=st.run_started_mono,
                ea_charging=ea_charging,
                ea_discharging=ea_discharging,
            )
        elif bms is not None or ea is not None:
            charging = bool(ea and (ea.psi_output_on or abs(ea.psi_current_a) > 0.5))
            discharging = bool(ea and (ea.el_input_on or abs(ea.el_current_a) > 0.5))
            ready = bool(bms and bms.operating_state == BmuState.READY)
            waiting = bool(
                bms
                and bms.operating_state in (BmuState.PRE_CHARGE, BmuState.READY)
                and not charging
                and not discharging
            )
            amps = 0.0
            if ea is not None:
                amps = max(abs(float(ea.psi_current_a)), abs(float(ea.el_current_a)))
            if bms is not None and bms.pack_current_a is not None:
                amps = max(amps, abs(float(bms.pack_current_a)))
            self.dashboard.progress.set_preview_activity(
                charging=charging,
                discharging=discharging,
                waiting=waiting and not ready,
                ready=ready and not charging and not discharging,
                current_a=amps,
            )
        self._sync_run_buttons()

    def _open_runs_folder(self) -> None:
        path = self.cfg.runs_path
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        self.statusBar().showMessage(f"Složka: {path}", 4000)

    def _refresh_history(self) -> None:
        self.history_list.clear()
        runs = self.cfg.runs_path
        runs.mkdir(parents=True, exist_ok=True)
        # Prefer HTML reports + activity logs; hide noisy/failed CSV stubs
        covered: set[str] = set()
        files = sorted(runs.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)

        def _stamp(name: str) -> str:
            # YYYYMMDD_HHMMSS prefix when present
            return name[:15] if len(name) >= 15 and name[8] == "_" else Path(name).stem

        for p in files:
            if not p.is_file():
                continue
            suf = p.suffix.lower()
            if suf == ".html":
                self.history_list.addItem(f"📄 {p.name}")
                covered.add(_stamp(p.name))
            elif suf == ".log" and "activity" in p.name.lower():
                self.history_list.addItem(f"📋 {p.name}")
                covered.add(_stamp(p.name))
            elif suf == ".json" and "report" in p.name.lower():
                self.history_list.addItem(f"JSON  {p.name}")
                covered.add(_stamp(p.name))
            elif suf == ".csv":
                st = _stamp(p.name)
                if st in covered or any(st in c for c in covered):
                    continue
                low = p.name.lower()
                if any(tok in low for tok in ("failed", "abort", "error")):
                    continue
                try:
                    nlines = sum(1 for _ in p.open("r", encoding="utf-8", errors="ignore"))
                except Exception:
                    nlines = 99
                if nlines < 8:
                    continue
                self.history_list.addItem(f"📊 {p.name}")
                covered.add(st)

    def _open_history_item(self, item: QListWidgetItem) -> None:
        text = item.text().strip()
        # Strip optional emoji / prefix ("📄 name", "JSON  name")
        parts = text.split()
        name = parts[-1] if parts else text
        path = self.cfg.runs_path / name
        if path.exists():
            import os

            os.startfile(path)  # noqa: S606

    def _log(self, msg: str) -> None:
        if msg:
            line = f"{datetime.now().strftime('%H:%M:%S')}  {msg}"
            self.diagnostics_tab.append_activity(line)


def _set_windows_app_user_model_id() -> None:
    """Let Windows use our icon on the taskbar instead of python.exe."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "EBZnanoPower.BatteryTestSequencer"
        )
    except Exception:
        logging.getLogger(__name__).debug("AppUserModelID not set", exc_info=True)


def _load_app_icon(root: Path) -> QIcon | None:
    """Load app icon with multiple sizes so Windows taskbar picks a sharp glyph."""
    assets = root / "assets"
    ico_path = assets / "bts_app_icon.ico"
    png_path = assets / "bts_app_icon.png"
    icon = QIcon()
    if ico_path.exists():
        icon.addFile(str(ico_path))
    if png_path.exists():
        pix = QPixmap(str(png_path))
        if not pix.isNull():
            for size in (16, 24, 32, 48, 64, 128, 256):
                icon.addPixmap(
                    pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
    return None if icon.isNull() else icon


def run_app(root: Path | None = None) -> int:
    root = root or Path.cwd()
    _set_windows_app_user_model_id()
    cfg = load_config(root=root)
    cfg.bms.request_cell_voltages = True
    cfg.bms.request_temperatures = True
    cfg.bms.request_cell_balance = True
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = QApplication([])
    app.setApplicationName("Battery Test Sequencer")
    app.setOrganizationName("EBZ nano power")
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLESHEET)
    icon = _load_app_icon(cfg.root)
    if icon is not None:
        app.setWindowIcon(icon)
    win = MainWindow(cfg)
    if icon is not None:
        win.setWindowIcon(icon)
    win.show()
    return app.exec()
