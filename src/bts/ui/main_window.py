from __future__ import annotations

import copy
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFont, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
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
from bts.drivers.bms import MOCK_PREVIEW_SCENARIOS, MockBmsDriver
from bts.drivers.ea import MockEaRack
from bts.dtc_catalog import format_active_dtcs, format_dtc_detail
from bts.engine import SequenceEngine, estimate_program, validate_program
from bts.models.config import AppConfig, load_config, save_bms_settings
from bts.models.program import (
    Program,
    ProgramMeta,
    Step,
    list_profiles,
    list_programs,
    load_profile,
    load_program,
    save_program,
)
from bts.models.telemetry import BmuState
from bts.ui.dashboard import LiveDashboard
from bts.ui.diagnostics_tab import DiagnosticsTab
from bts.ui.simulate_tab import SimulateTab
from bts.ui.step_form import StepForm
from bts.ui.theme import APP_STYLESHEET, TEXT_DIM

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    status_tick = Signal()

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("Battery Test Sequencer | EBZ nano power")
        self.resize(1400, 860)
        self._apply_window_icon()

        self.program: Program | None = None
        self.program_path: Path | None = None
        self.engine: SequenceEngine | None = None
        self._bms = None
        self._ea = None
        self._active_profile = None

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self._build_run_tab()
        self._build_editor_tab()
        self._build_simulate_tab()
        self._build_history_tab()
        self._build_settings_tab()
        self._build_diagnostics_tab()
        self._build_branding_bar()
        self._update_mock_banner()

        self._reload_lists()
        QShortcut(QKeySequence("Esc"), self, activated=self._stop_run)

        self.timer = QTimer(self)
        self.timer.setInterval(cfg.safety.poll_period_ms)
        self.timer.timeout.connect(self._refresh_live)
        self.timer.start()

        QTimer.singleShot(2500, self._maybe_check_updates_on_startup)

    def _apply_window_icon(self) -> None:
        icon = _load_app_icon(self.cfg.root)
        if icon is not None:
            self.setWindowIcon(icon)

    def _build_branding_bar(self) -> None:
        """Subtle footer: company logo + internal-tool notice."""
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

        notice = QLabel("Internal tool  ·  not for distribution")
        notice.setStyleSheet("color:#8a96a3;font-size:11px;")
        notice.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        row.addWidget(logo, 0, Qt.AlignVCenter)
        row.addStretch(1)
        row.addWidget(notice, 0, Qt.AlignVCenter)
        wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(wrap, 1)

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
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(10)

        # Toolbar card
        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        toolbar.setStyleSheet(
            "#toolbar { background:#ffffff; border:1px solid #d5dde5; border-radius:8px; }"
        )
        top = QHBoxLayout(toolbar)
        top.setContentsMargins(12, 10, 12, 10)
        top.setSpacing(8)
        self.program_combo = QComboBox()
        self.program_combo.setMinimumWidth(220)
        self.profile_label = QLabel("Profile: —")
        self.profile_label.setStyleSheet(f"color:{TEXT_DIM};")
        self.serial_edit = QLineEdit()
        self.serial_edit.setPlaceholderText("Module serial / claim ID")
        self.serial_edit.setMinimumWidth(140)
        self.btn_connect = QPushButton("Připojit HW")
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
        self.btn_connect.clicked.connect(self._connect_hw)
        self.btn_start.clicked.connect(self._start_run)
        self.btn_stop.clicked.connect(self._stop_run)
        self.btn_clear_dtc.clicked.connect(self._clear_bms_dtcs)
        top.addWidget(QLabel("Program"))
        top.addWidget(self.program_combo, 1)
        top.addWidget(self.profile_label)
        top.addWidget(QLabel("Sériové č."))
        top.addWidget(self.serial_edit)
        top.addWidget(self.btn_connect)
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_stop)
        top.addWidget(self.btn_clear_dtc)
        layout.addWidget(toolbar)

        self.lbl_mock_banner = QLabel("")
        self.lbl_mock_banner.setVisible(False)
        self.lbl_mock_banner.setStyleSheet(
            "background:#9a6700;color:#fff;font-weight:700;padding:8px 12px;border-radius:6px;"
        )
        layout.addWidget(self.lbl_mock_banner)
        self._update_mock_banner()

        # Link strip: bus open ≠ BMU talking
        link_bar = QFrame()
        link_bar.setObjectName("linkBar")
        link_bar.setStyleSheet(
            "#linkBar { background:#f4f7fa; border:1px solid #d5dde5; border-radius:8px; }"
        )
        link_row = QHBoxLayout(link_bar)
        link_row.setContentsMargins(12, 8, 12, 8)
        link_row.setSpacing(16)
        self.lbl_link_bms = QLabel("BMS: not connected")
        self.lbl_link_ea = QLabel("EA: not connected")
        self.lbl_link_bms.setStyleSheet(f"color:{TEXT_DIM};font-weight:600;")
        self.lbl_link_ea.setStyleSheet(f"color:{TEXT_DIM};font-weight:600;")
        link_row.addWidget(self.lbl_link_bms, 1)
        link_row.addWidget(self.lbl_link_ea, 1)
        layout.addWidget(link_bar)

        split = QSplitter()
        split.setHandleWidth(6)
        split.setChildrenCollapsible(False)

        # Compact status column — content top-aligned
        left = QFrame()
        left.setObjectName("sidePanel")
        left.setStyleSheet(
            "#sidePanel { background:#ffffff; border:1px solid #d5dde5; border-radius:8px; }"
        )
        left_outer = QVBoxLayout(left)
        left_outer.setContentsMargins(12, 12, 12, 12)
        left_outer.setSpacing(0)
        lf = QFormLayout()
        lf.setContentsMargins(0, 0, 0, 0)
        lf.setSpacing(6)
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
        self.program_combo.currentIndexChanged.connect(self._on_program_selected)

    @staticmethod
    def _dim(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"color:{TEXT_DIM};font-weight:500;")
        return lab

    def _build_editor_tab(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 10, 12, 8)
        layout.setSpacing(10)

        # Program-level meta (applies to all steps)
        meta_box = QFrame()
        meta_box.setObjectName("progMeta")
        meta_box.setStyleSheet(
            "#progMeta { background:#ffffff; border:1px solid #d5dde5; border-radius:8px; }"
        )
        meta_outer = QVBoxLayout(meta_box)
        meta_outer.setContentsMargins(12, 10, 12, 10)
        meta_outer.setSpacing(6)
        meta_title = QLabel("Program")
        meta_title.setStyleSheet(f"color:{TEXT_DIM};font-weight:600;font-size:11px;")
        meta_outer.addWidget(meta_title)
        meta = QFormLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(6)
        self.ed_name = QLineEdit()
        self.ed_profile = QComboBox()
        self.ed_profile.setToolTip("Battery module profile for the whole program (all steps).")
        self.ed_desc = QLineEdit()
        meta.addRow("Name", self.ed_name)
        meta.addRow("Module profile", self.ed_profile)
        meta.addRow("Description", self.ed_desc)
        meta_outer.addLayout(meta)
        layout.addWidget(meta_box)

        split = QHBoxLayout()
        split.setSpacing(10)

        left = QVBoxLayout()
        steps_lab = QLabel("Steps")
        steps_lab.setStyleSheet(f"color:{TEXT_DIM};font-weight:600;font-size:11px;")
        left.addWidget(steps_lab)
        self.step_list = QListWidget()
        self._editor_row = -1
        self.step_list.currentRowChanged.connect(self._on_step_row_changed)
        btns = QHBoxLayout()
        for label, slot in [
            ("Add", self._add_step),
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
        self.tabs.addTab(self.simulate_tab, "Simulace")

    def _program_for_simulate(self):
        """Snapshot editor program + profile for offline preview."""
        if not self.program:
            return None, None
        self._sync_meta_into_program()
        row = self.step_list.currentRow()
        if row >= 0:
            try:
                self.program.steps[row] = self.step_form.build_step()
            except Exception:
                pass
        try:
            profile = self._current_profile()
        except Exception:
            profile = self._active_profile
        return copy.deepcopy(self.program), profile

    def _build_history_tab(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.history_list = QListWidget()
        btn = QPushButton("Obnovit / otevřít složku")
        btn.clicked.connect(self._refresh_history)
        self.history_list.itemDoubleClicked.connect(self._open_history_item)
        layout.addWidget(btn)
        layout.addWidget(self.history_list, 1)
        self.tabs.addTab(w, "Historie")

    def _build_settings_tab(self) -> None:
        w = QWidget()
        root = QVBoxLayout(w)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)
        self.chk_mock = QCheckBox("Use mock hardware (offline)")
        self.chk_mock.setChecked(self.cfg.use_mock_hardware)
        self.chk_mock.toggled.connect(self._on_mock_toggled)
        form.addRow(self.chk_mock)

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

        # --- Updates (private GitHub) ---
        form.addRow(QLabel(""))
        upd_hdr = QLabel("Aktualizace (privátní GitHub)")
        upd_hdr.setStyleSheet("font-weight:600;")
        form.addRow(upd_hdr)
        try:
            from bts.version import read_version
            from bts.update import load_update_config, read_token

            ver = read_version(self.cfg.root)
            ucfg = load_update_config(self.cfg.root)
            has_tok = bool(read_token(self.cfg.root))
        except Exception:
            ver = "?"
            ucfg = None
            has_tok = False
        self.lbl_app_version = QLabel(f"Verze: {ver}")
        form.addRow(self.lbl_app_version)
        self.ed_github_repo = QLineEdit(ucfg.github_repo if ucfg else "jancihak99/battery-test-sequencer")
        form.addRow("GitHub repo", self.ed_github_repo)
        self.ed_github_token = QLineEdit()
        self.ed_github_token.setEchoMode(QLineEdit.Password)
        self.ed_github_token.setPlaceholderText(
            "fine-grained PAT (Contents: Read) — uloží se do .github_token"
            if not has_tok
            else "token už je uložený — vyplň jen při změně"
        )
        form.addRow("GitHub token", self.ed_github_token)
        self.chk_update_startup = QCheckBox("Kontrolovat aktualizace při startu")
        self.chk_update_startup.setChecked(bool(ucfg.check_on_startup) if ucfg else False)
        form.addRow(self.chk_update_startup)
        upd_row = QHBoxLayout()
        self.btn_check_update = QPushButton("Zkontrolovat aktualizace")
        self.btn_apply_update = QPushButton("Stáhnout a nainstalovat")
        self.btn_apply_update.setEnabled(False)
        self.btn_save_update = QPushButton("Uložit update nastavení")
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

    def _on_mock_toggled(self, checked: bool) -> None:
        self.cfg.use_mock_hardware = bool(checked)
        self._update_mock_banner()

    def _save_update_settings(self) -> None:
        from bts.update import UpdateConfig, save_update_config, write_token

        cfg = UpdateConfig(
            github_repo=self.ed_github_repo.text().strip() or "jancihak99/battery-test-sequencer",
            check_on_startup=self.chk_update_startup.isChecked(),
            channel="latest",
        )
        save_update_config(self.cfg.root, cfg)
        tok = self.ed_github_token.text().strip()
        if tok:
            write_token(self.cfg.root, tok)
            self.ed_github_token.clear()
            self.ed_github_token.setPlaceholderText("token uložen — vyplň jen při změně")
        self.lbl_update_status.setText("Update nastavení uloženo.")

    def _check_updates(self) -> None:
        from bts.update import check_for_update

        # Persist repo/token first if user typed them
        try:
            self._save_update_settings()
        except Exception:
            pass
        self.lbl_update_status.setText("Kontroluji GitHub…")
        QApplication.processEvents()
        result = check_for_update(self.cfg.root)
        if result.error:
            self._pending_release = None
            self.btn_apply_update.setEnabled(False)
            self.lbl_update_status.setText(result.error)
            QMessageBox.warning(self, "Aktualizace", result.error)
            return
        self.lbl_app_version.setText(f"Verze: {result.local_version}")
        if result.update_available and result.release:
            self._pending_release = result.release
            self.btn_apply_update.setEnabled(True)
            msg = (
                f"Dostupná verze {result.remote_version} "
                f"(lokálně {result.local_version}).\n"
                f"{result.release.name}"
            )
            self.lbl_update_status.setText(msg)
            QMessageBox.information(self, "Aktualizace", msg + "\n\nKlikni Stáhnout a nainstalovat.")
        else:
            self._pending_release = None
            self.btn_apply_update.setEnabled(False)
            self.lbl_update_status.setText(result.message or "Žádná nová verze.")
            QMessageBox.information(self, "Aktualizace", result.message or "Žádná nová verze.")

    def _apply_updates(self) -> None:
        from bts.update import apply_best_update, spawn_external_updater

        if self.engine and getattr(self.engine, "_thread", None) and self.engine._thread.is_alive():
            QMessageBox.warning(self, "Aktualizace", "Nejdřív Stop / dokonči běžící test.")
            return
        reply = QMessageBox.question(
            self,
            "Aktualizace",
            "Aplikace se ukončí, stáhne update z GitHubu a znovu spustí.\nPokračovat?",
        )
        if reply != QMessageBox.Yes:
            return
        try:
            # Prefer detached updater (can overwrite files while we exit)
            spawn_external_updater(self.cfg.root)
            self._log("Update spuštěn — aplikace se zavře")
            QTimer.singleShot(400, QApplication.instance().quit)
        except Exception as exc:
            # Fallback in-process
            try:
                msg = apply_best_update(self.cfg.root, self._pending_release)
                QMessageBox.information(
                    self,
                    "Aktualizace",
                    f"{msg}\n\nRestartuj BTS (Start BTS.bat).",
                )
            except Exception as exc2:
                QMessageBox.critical(self, "Aktualizace selhala", f"{exc}\n{exc2}")

    def _maybe_check_updates_on_startup(self) -> None:
        try:
            from bts.update import check_for_update, load_update_config

            if not load_update_config(self.cfg.root).check_on_startup:
                return
            result = check_for_update(self.cfg.root)
            if result.update_available and result.remote_version:
                self.lbl_update_status.setText(
                    f"Nová verze {result.remote_version} — Nastavení → Aktualizace"
                )
                self._log(f"Update available: {result.remote_version}")
        except Exception:
            pass

    def _update_mock_banner(self) -> None:
        if not hasattr(self, "lbl_mock_banner"):
            return
        mock_on = bool(getattr(self, "chk_mock", None) and self.chk_mock.isChecked())
        if mock_on or self.cfg.use_mock_hardware:
            self.lbl_mock_banner.setText(
                "MOCK HW ZAPNUTÝ — žádná komunikace se skutečným PSI/EL/BMU. "
                "Pro laboratoř vypni v Nastavení."
            )
            self.lbl_mock_banner.setVisible(True)
        else:
            self.lbl_mock_banner.setVisible(False)

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
        self.cfg.use_mock_hardware = self.chk_mock.isChecked()
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

    def _save_can_settings(self) -> None:
        try:
            self._read_can_form()
            self.cfg.use_mock_hardware = self.chk_mock.isChecked()
            path = save_bms_settings(self.cfg)
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        self._apply_can_settings()
        QMessageBox.information(self, "Saved", f"Saved BMS settings to\n{path}")

    def _auto_detect_can(self) -> None:
        if self.chk_mock.isChecked():
            QMessageBox.information(
                self,
                "Auto-detect",
                "Turn OFF mock hardware first — discovery needs a real Kvaser + BMU.",
            )
            return
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

    def _reload_lists(self) -> None:
        self.program_combo.blockSignals(True)
        self.program_combo.clear()
        for p in list_programs(self.cfg.programs_path):
            label = p.stem
            if "dev" in p.parts:
                label = f"[DEV/MOCK] {p.stem}"
            self.program_combo.addItem(label, str(p))
        self.program_combo.blockSignals(False)
        self.ed_profile.clear()
        for p in list_profiles(self.cfg.profiles_path):
            self.ed_profile.addItem(p.stem, str(p))
        if self.program_combo.count():
            self._on_program_selected(0)
        self._refresh_history()

    def _on_program_selected(self, idx: int) -> None:
        if idx < 0:
            return
        path = Path(self.program_combo.itemData(idx))
        self.program = load_program(path)
        self.program_path = path
        self.profile_label.setText(f"Profile: {self.program.meta.module_profile}")
        try:
            self._active_profile = load_profile(
                self.cfg.profiles_path / f"{self.program.meta.module_profile}.yaml"
            )
            self.dashboard.set_profile(self._active_profile)
        except Exception:
            self._active_profile = None
        self._update_program_estimate()
        self._sync_editor_from_program()

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
        self.step_list.clear()
        for s in self.program.steps:
            summary = self._step_summary(s)
            self.step_list.addItem(QListWidgetItem(summary))
        if self.program.steps:
            self.step_list.setCurrentRow(0)

    @staticmethod
    def _step_summary(s: Step) -> str:
        extra = ""
        if s.type == "wait_time":
            extra = f" {s.params.get('seconds', '?')}s"
        elif s.type in ("charge", "discharge"):
            extra = f" {s.params.get('current_a', '?')}A"
        elif s.type == "dcir":
            extra = f" {s.params.get('pulse_a', '?')}A/{s.params.get('pulse_s', '?')}s"
        return f"{s.id}  [{s.type}]{extra}"

    def _commit_editor_row(self, row: int) -> None:
        """Persist step form into program without requiring Apply click."""
        if not self.program or row < 0 or row >= len(self.program.steps):
            return
        try:
            self.program.steps[row] = self.step_form.build_step()
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
        self._sync_editor_from_program()
        self.step_list.setCurrentRow(row)

    def _add_step(self) -> None:
        if not self.program:
            self._new_program()
        assert self.program
        n = len(self.program.steps) + 1
        self.program.steps.append(Step(id=f"wait_{n}", type="wait_time", params={"seconds": 60}))
        self._sync_editor_from_program()
        self.step_list.setCurrentRow(len(self.program.steps) - 1)

    def _remove_step(self) -> None:
        if not self.program:
            return
        row = self.step_list.currentRow()
        if row >= 0:
            del self.program.steps[row]
            self._sync_editor_from_program()

    def _move_up(self) -> None:
        if not self.program:
            return
        row = self.step_list.currentRow()
        if row > 0:
            self.program.steps[row - 1], self.program.steps[row] = (
                self.program.steps[row],
                self.program.steps[row - 1],
            )
            self._sync_editor_from_program()
            self.step_list.setCurrentRow(row - 1)

    def _move_down(self) -> None:
        if not self.program:
            return
        row = self.step_list.currentRow()
        if 0 <= row < len(self.program.steps) - 1:
            self.program.steps[row + 1], self.program.steps[row] = (
                self.program.steps[row],
                self.program.steps[row + 1],
            )
            self._sync_editor_from_program()
            self.step_list.setCurrentRow(row + 1)

    def _new_program(self) -> None:
        profile = self.ed_profile.currentText() or "LTO_24V_70Ah"
        self.program = Program(
            meta=ProgramMeta(name="New_program", module_profile=profile, description=""),
            steps=[
                Step(id="ready", type="bms_ready", params={"timeout_s": 60}),
                Step(id="idle", type="bms_idle", params={"timeout_s": 30}),
            ],
        )
        self.program_path = None
        self._sync_editor_from_program()

    def _open_program(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open program", str(self.cfg.programs_path), "YAML (*.yaml)")
        if path:
            self.program = load_program(Path(path))
            self.program_path = Path(path)
            self._sync_editor_from_program()
            self._reload_lists()

    def _sync_meta_into_program(self) -> None:
        if not self.program:
            return
        self.program.meta.name = self.ed_name.text().strip() or "Untitled"
        self.program.meta.module_profile = self.ed_profile.currentText()
        self.program.meta.description = self.ed_desc.text()

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
        QMessageBox.information(self, "Saved", f"Saved to {path}")
        self._reload_lists()

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

    def _disconnect_hw(self) -> None:
        if self.engine is not None:
            try:
                self.engine.abort()
                self.engine.join(timeout_s=25.0)
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

    def _set_link_labels(self, bms, ea) -> None:
        ok = "#1a7f37"
        warn = "#9a6700"
        bad = "#cf222e"
        dim = TEXT_DIM

        if bms is None:
            self.lbl_link_bms.setText("BMS: not connected")
            self.lbl_link_bms.setStyleSheet(f"color:{dim};font-weight:600;")
        elif getattr(bms, "rx_count", 0) > 0 and bms.connected:
            age = time.monotonic() - bms.last_rx_s if bms.last_rx_s > 0 else 999
            cells = len([v for v in (bms.cell_voltages or []) if v == v])  # not NaN
            state = bms.operating_state.name if bms.operating_state else "?"
            if age <= 1.5:
                self.lbl_link_bms.setText(
                    f"BMS: RX OK · {bms.rx_count} frames · {state}"
                    + (f" · {cells} cells" if cells else " · waiting for cells")
                )
                self.lbl_link_bms.setStyleSheet(f"color:{ok};font-weight:600;")
            else:
                self.lbl_link_bms.setText(f"BMS: stale RX ({age:.1f}s) · check CAN")
                self.lbl_link_bms.setStyleSheet(f"color:{warn};font-weight:600;")
        elif bms.connected:
            self.lbl_link_bms.setText(
                "BMS: Kvaser open — no BMU frames (External CAN / bitrate / address?)"
            )
            self.lbl_link_bms.setStyleSheet(f"color:{bad};font-weight:600;")
        else:
            self.lbl_link_bms.setText("BMS: disconnected")
            self.lbl_link_bms.setStyleSheet(f"color:{dim};font-weight:600;")

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
            else:
                self.lbl_link_ea.setText("EA: not connected")
                self.lbl_link_ea.setStyleSheet(f"color:{dim};font-weight:600;")
        elif ea.connected:
            psi = self.cfg.ea.psi.serial_port if self.cfg.ea else "?"
            el = self.cfg.ea.el.serial_port if self.cfg.ea else "?"
            self.lbl_link_ea.setText(f"EA: OK · PSI {psi} · EL {el}")
            self.lbl_link_ea.setStyleSheet(f"color:{ok};font-weight:600;")
        else:
            self.lbl_link_ea.setText("EA: disconnected")
            self.lbl_link_ea.setStyleSheet(f"color:{bad};font-weight:600;")

    def _connect_hw(self) -> None:
        use_mock = self.chk_mock.isChecked()
        self.cfg.use_mock_hardware = use_mock
        # Always request full cell voltage stream on External CAN
        self.cfg.bms.request_cell_voltages = True
        self.cfg.bms.request_temperatures = True
        self.cfg.bms.request_cell_balance = True
        self._disconnect_hw()
        try:
            self._bms = create_bms_driver(self.cfg.bms, use_mock)
            mock_bms = self._bms if isinstance(self._bms, MockBmsDriver) else None
            assert self.cfg.ea
            self._ea = create_ea_driver(self.cfg.ea, use_mock, mock_bms=mock_bms)
            self._bms.connect()
            self._bms.start()
            self._ea.connect()
            if mock_bms is not None and isinstance(self._ea, MockEaRack):
                scenario = random.choice(MOCK_PREVIEW_SCENARIOS)
                current = random.choice([40.0, 70.0, 100.0, 150.0])
                mock_bms.apply_ui_preview(scenario, current_a=current)
                self._ea.apply_ui_preview(scenario, current_a=current)
                self._log(
                    f"Connected (mock) — preview: {scenario}"
                    + (f" @ {current:.0f} A" if scenario in ("charge", "discharge") else "")
                )
            else:
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
                else:
                    detail = self._bms.health_detail()
                    self._log(f"Connected (live) — NO BMU frames yet. {detail}")
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
        except Exception as exc:
            self._disconnect_hw()
            QMessageBox.critical(self, "Connect failed", str(exc))

    def _start_run(self) -> None:
        if self.program is None:
            QMessageBox.warning(self, "Start", "Není načtený program")
            return
        # Commit editor edits before validate/start
        row = self.step_list.currentRow()
        if row >= 0:
            self._commit_editor_row(row)
        path = self.program_path
        if path is not None and "dev" in Path(path).parts and not self.chk_mock.isChecked():
            ans = QMessageBox.warning(
                self,
                "DEV/MOCK program",
                f"Program „{path.name}“ je ve složce programs/dev (smoke/mock).\n"
                "Není určen pro ostrý modul se zapnutým živým HW.\n\n"
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
        # Drop Connect-time UI preview before a real sequence
        if isinstance(self._bms, MockBmsDriver):
            self._bms.clear_ui_preview()
        if isinstance(self._ea, MockEaRack):
            self._ea.all_off()
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
        self.diagnostics_tab.clear_activity()
        self.engine.add_listener(self._on_engine_status)
        self.engine.add_activity_listener(self._on_activity_line)
        self.engine.start()
        self._log("Run started — Activity console je v Diagnostice")

    def _on_engine_status(self, st) -> None:
        if st.message:
            self.lbl_msg.setText(st.message)
        if st.activity_path:
            self.diagnostics_tab.set_activity_log_path(st.activity_path)

    def _on_activity_line(self, line: str) -> None:
        self.diagnostics_tab.append_activity(line)

    def _stop_run(self) -> None:
        if self.engine:
            self.engine.abort()
            self._log("Stop — EA off first, contactors open only after I≈0")

    def _clear_bms_dtcs(self) -> None:
        if self._bms is None:
            QMessageBox.information(self, "Clear DTCs", "Connect HW first.")
            return
        if not self._bms.is_healthy(2.0) and not isinstance(self._bms, MockBmsDriver):
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

        if bms is not None:
            self.lbl_bms_state.setText(
                bms.operating_state.name if bms.operating_state else "—"
            )
            self.lbl_pack.setText(
                f"{bms.pack_voltage_v:.2f} V / {bms.pack_current_a:.1f} A"
                if bms.pack_voltage_v is not None
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
            self.dashboard.progress.update_run(
                run_state=st.run_state,
                step_index=st.current_step_index,
                step_id=st.current_step_id,
                step_type=st.current_step_type,
                step_started_mono=st.step_started_mono,
                run_started_mono=st.run_started_mono,
                ea_charging=bool(ea and ea.psi_output_on),
                ea_discharging=bool(ea and ea.el_input_on),
            )
        elif bms is not None or ea is not None:
            charging = bool(ea and ea.psi_output_on)
            discharging = bool(ea and ea.el_input_on)
            waiting = bool(
                bms
                and bms.operating_state in (BmuState.PRE_CHARGE, BmuState.READY)
                and not charging
                and not discharging
            )
            self.dashboard.progress.set_preview_activity(
                charging=charging,
                discharging=discharging,
                waiting=waiting,
            )

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
