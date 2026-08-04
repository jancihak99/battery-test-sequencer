"""Diagnostics tab — USB / serial / EA / Kvaser + activity console."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bts.diagnostics import DiagnosticReport, build_report, real_serial_ports
from bts.ui.theme import BORDER, OK, TEXT, TEXT_DIM

if TYPE_CHECKING:
    from bts.models.config import AppConfig


class _ScanWorker(QThread):
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg

    def run(self) -> None:  # noqa: N802
        try:
            ea = self.cfg.ea
            report = build_report(
                ea_psi_host=ea.psi.host if ea else None,
                ea_psi_port=int(ea.psi.port) if ea else 5025,
                ea_el_host=ea.el.host if ea else None,
                ea_el_port=int(ea.el.port) if ea else 5025,
                ea_psi_transport=ea.psi.transport if ea else "tcp",
                ea_el_transport=ea.el.transport if ea else "tcp",
                ea_psi_serial=ea.psi.serial_port if ea else None,
                ea_el_serial=ea.el.serial_port if ea else None,
                ea_psi_baud=int(ea.psi.baudrate) if ea else 115200,
                ea_el_baud=int(ea.el.baudrate) if ea else 115200,
                bms_interface=self.cfg.bms.interface,
                bms_channel=int(self.cfg.bms.channel),
                bms_bitrate=int(self.cfg.bms.bitrate),
                mock=bool(self.cfg.use_mock_hardware),
            )
            self.finished_ok.emit(report)
        except Exception as exc:
            self.failed.emit(str(exc))


class DiagnosticsTab(QWidget):
    def __init__(self, cfg: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._worker: _ScanWorker | None = None
        self._last: DiagnosticReport | None = None
        self._activity_log_path: Path | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 8)
        root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("Diagnostics — USB & connectivity")
        title.setStyleSheet(f"color:{TEXT};font-weight:700;font-size:14px;")
        hint = QLabel("Hardware scan + activity log from Run")
        hint.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setObjectName("btnPrimary")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_copy = QPushButton("Copy report")
        self.btn_copy.clicked.connect(self._copy)
        self.btn_copy.setToolTip("Celý text včetně Lab dump — pošli z druhého PC")
        head.addWidget(title)
        head.addWidget(hint, 1)
        head.addWidget(self.btn_copy)
        head.addWidget(self.btn_refresh)
        root.addLayout(head)

        vsplit = QSplitter(Qt.Vertical)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Detail"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background:#ffffff; border:1px solid {BORDER}; border-radius:6px; }}"
        )
        split.addWidget(self.tree)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 10))
        self.log.setPlaceholderText("Full text report appears here…")
        self.log.setStyleSheet(
            f"QTextEdit {{ background:#ffffff; border:1px solid {BORDER}; border-radius:6px; }}"
        )
        split.addWidget(self.log)
        split.setSizes([520, 480])
        vsplit.addWidget(split)

        console_box = QFrame()
        console_box.setObjectName("activityConsole")
        console_box.setStyleSheet(
            f"#activityConsole {{ background:#ffffff; border:1px solid {BORDER}; border-radius:8px; }}"
        )
        console_lay = QVBoxLayout(console_box)
        console_lay.setContentsMargins(10, 8, 10, 8)
        console_lay.setSpacing(6)
        console_head = QHBoxLayout()
        console_title = QLabel("Activity console")
        console_title.setStyleSheet("font-weight:600;")
        console_hint = QLabel("čas · BMU / stykače / DTC / chyby  ·  runs/*_activity.log")
        console_hint.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        self.btn_clear_activity = QPushButton("Clear")
        self.btn_clear_activity.clicked.connect(self.clear_activity)
        self.btn_copy_activity = QPushButton("Copy")
        self.btn_copy_activity.clicked.connect(self.copy_activity)
        self.btn_open_activity = QPushButton("Open log")
        self.btn_open_activity.clicked.connect(self.open_activity_log)
        console_head.addWidget(console_title)
        console_head.addWidget(console_hint, 1)
        console_head.addWidget(self.btn_clear_activity)
        console_head.addWidget(self.btn_copy_activity)
        console_head.addWidget(self.btn_open_activity)
        console_lay.addLayout(console_head)
        self.activity_console = QTextEdit()
        self.activity_console.setReadOnly(True)
        self.activity_console.setFont(QFont("Consolas", 10))
        self.activity_console.setMinimumHeight(120)
        self.activity_console.setPlaceholderText(
            "Po Start na Run sem běží záznam: READY / stykače / DTC / EA… "
            "Při chybě uvidíš přesný FAIL řádek a snapshot BMU."
        )
        self.activity_console.setStyleSheet(
            "QTextEdit { background:#0f1419; color:#d7e0ea; border:1px solid #2a3340; border-radius:6px; }"
        )
        console_lay.addWidget(self.activity_console)
        vsplit.addWidget(console_box)
        vsplit.setSizes([420, 240])
        root.addWidget(vsplit, 1)

        self.status = QLabel("Press Refresh to scan USB and check EA / Kvaser.")
        self.status.setStyleSheet(f"color:{TEXT_DIM};font-size:11px;")
        root.addWidget(self.status)

    def refresh(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self.btn_refresh.setEnabled(False)
        self.status.setText("Scanning…")
        self._worker = _ScanWorker(self.cfg)
        self._worker.finished_ok.connect(self._on_report)
        self._worker.failed.connect(self._on_fail)
        self._worker.finished.connect(lambda: self.btn_refresh.setEnabled(True))
        self._worker.start()

    def _on_fail(self, msg: str) -> None:
        self.status.setText(f"Scan failed: {msg}")
        self.log.setPlainText(msg)

    def _on_report(self, report: DiagnosticReport) -> None:
        self._last = report
        self.log.setPlainText(report.to_text())
        self._fill_tree(report)
        fails = sum(1 for c in report.checks if c.ok is False)
        self.status.setText(
            f"Scan done · {len(report.usb)} USB · {len(real_serial_ports(report.serial_ports))} serial · "
            f"{fails} check(s) failed"
        )

    def _fill_tree(self, report: DiagnosticReport) -> None:
        self.tree.clear()

        checks = QTreeWidgetItem(["Checks", ""])
        self.tree.addTopLevelItem(checks)
        for c in report.checks:
            if c.ok is True:
                mark = "OK"
                color = OK
            elif c.ok is False:
                mark = "FAIL"
                color = "#c0392b"
            else:
                mark = "INFO"
                color = TEXT_DIM
            item = QTreeWidgetItem([f"[{mark}] {c.name}", c.detail])
            item.setForeground(0, _brush(color))
            checks.addChild(item)
        checks.setExpanded(True)

        usb_root = QTreeWidgetItem(["USB devices", f"{len(report.usb)} present"])
        self.tree.addTopLevelItem(usb_root)
        # Group by kind
        by_kind: dict[str, list] = {}
        for d in report.usb:
            by_kind.setdefault(d.kind, []).append(d)
        kind_order = ["kvaser", "serial", "network", "other", "hid", "storage"]
        for kind in kind_order:
            devices = by_kind.get(kind) or []
            if not devices:
                continue
            group = QTreeWidgetItem([kind.upper(), f"{len(devices)}"])
            if kind == "kvaser":
                group.setForeground(0, _brush(OK))
            usb_root.addChild(group)
            for d in devices:
                child = QTreeWidgetItem([d.name, f"{d.status} · {d.instance_id}"])
                if d.hint:
                    child.addChild(QTreeWidgetItem(["hint", d.hint]))
                group.addChild(child)
            group.setExpanded(kind in {"kvaser", "serial", "network"})
        usb_root.setExpanded(True)

        ser = QTreeWidgetItem(["Serial ports", f"{len(report.serial_ports)}"])
        self.tree.addTopLevelItem(ser)
        for s in report.serial_ports:
            ser.addChild(QTreeWidgetItem([s.device, f"{s.description} · {s.hwid}"]))
        ser.setExpanded(True)

        if report.lab_dump:
            dump = QTreeWidgetItem(["Lab dump", f"{len(report.lab_dump)} lines"])
            self.tree.addTopLevelItem(dump)
            for line in report.lab_dump:
                dump.addChild(QTreeWidgetItem(["·", line]))
            dump.setExpanded(False)

        if report.notes:
            notes = QTreeWidgetItem(["Notes", ""])
            self.tree.addTopLevelItem(notes)
            for n in report.notes:
                notes.addChild(QTreeWidgetItem(["·", n]))
            notes.setExpanded(True)

    def _copy(self) -> None:
        text = self._last.to_text() if self._last else self.log.toPlainText()
        QGuiApplication.clipboard().setText(text or "")
        self.status.setText("Report copied to clipboard")

    def clear_activity(self) -> None:
        self.activity_console.clear()
        self._activity_log_path = None

    def append_activity(self, line: str) -> None:
        if not line:
            return
        self.activity_console.append(line)
        sb = self.activity_console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_activity_log_path(self, path: Path | str | None) -> None:
        self._activity_log_path = Path(path) if path else None

    def copy_activity(self) -> None:
        QGuiApplication.clipboard().setText(self.activity_console.toPlainText())
        self.status.setText("Activity console copied")

    def open_activity_log(self) -> None:
        path = self._activity_log_path
        if path is None or not path.exists():
            runs = self.cfg.runs_path
            logs = sorted(runs.glob("*_activity.log"), reverse=True) if runs.exists() else []
            path = logs[0] if logs else None
        if path is None or not Path(path).exists():
            QMessageBox.information(self, "Activity log", "Zatím žádný activity log — spusť Run.")
            return
        import os

        os.startfile(path)  # noqa: S606


def _brush(color: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(color))
