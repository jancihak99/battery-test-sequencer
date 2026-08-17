"""Visible update progress window — launched after BTS exits.

Uses PySide6 (which every BTS install already ships, since the app itself is PySide6).
The offline Setup bundles an *embeddable* Python that has NO tkinter, so a tkinter
window would crash on import and the update would silently do nothing.

Usage:
  pythonw scripts/update_gui.py <root> [--wait-exit] [--restart]
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT_HINT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_HINT / "src"))


def _bts_python_pids(root: Path) -> list[int]:
    """PIDs of python processes running this install's main.py (not this updater)."""
    import os

    me = os.getpid()
    root_s = str(root.resolve()).lower()
    main_s = str((root / "main.py").resolve()).lower()
    pids: list[int] = []
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |"
                " Where-Object { $_.Name -match 'python' -and $_.CommandLine } |"
                " Select-Object ProcessId,CommandLine |"
                " ConvertTo-Json -Compress",
            ],
            text=True,
            timeout=15,
        )
    except Exception:
        return []
    out = (out or "").strip()
    if not out:
        return []
    import json

    try:
        data = json.loads(out)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    for row in data or []:
        try:
            pid = int(row.get("ProcessId") or 0)
            cmd = str(row.get("CommandLine") or "").lower()
        except Exception:
            continue
        if pid <= 0 or pid == me:
            continue
        if "update_gui.py" in cmd:
            continue
        if main_s in cmd or (root_s in cmd and "main.py" in cmd):
            pids.append(pid)
    return pids


def _wait_for_bts_exit(root: Path, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        pids = _bts_python_pids(root)
        if not pids:
            return
        time.sleep(0.4)
    # Last resort — force stop leftover GUI so files can be overwritten
    for pid in _bts_python_pids(root):
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, capture_output=True)
        except Exception:
            pass
    time.sleep(0.5)


def _restart_bts(root: Path) -> None:
    vbs = root / "Start BTS.vbs"
    bat = root / "Start BTS.bat"
    if vbs.exists():
        subprocess.Popen(["wscript.exe", str(vbs)], cwd=str(root))
        return
    if bat.exists():
        subprocess.Popen(["cmd.exe", "/c", str(bat)], cwd=str(root))
        return
    pyw = root / "python" / "pythonw.exe"
    if not pyw.exists():
        pyw = root / ".venv" / "Scripts" / "pythonw.exe"
    main = root / "main.py"
    if pyw.exists() and main.exists():
        subprocess.Popen([str(pyw), str(main)], cwd=str(root))


def main(argv: list[str]) -> int:
    args = list(argv)
    wait_exit = "--wait-exit" in args
    restart = "--restart" in args
    args = [a for a in args if a not in ("--wait-exit", "--restart")]
    root = Path(args[0]).resolve() if args else ROOT_HINT

    from PySide6.QtCore import Qt, QObject, QTimer, Signal
    from PySide6.QtWidgets import (
        QApplication,
        QLabel,
        QMessageBox,
        QProgressBar,
        QVBoxLayout,
        QWidget,
    )

    app = QApplication(sys.argv[:1])

    if not (root / "main.py").exists():
        QMessageBox.critical(None, "Aktualizace BTS", f"Nenalezen main.py v:\n{root}")
        return 1

    from bts.update import apply_best_update, check_for_update

    win = QWidget()
    win.setWindowTitle("Aktualizace Battery Test Sequencer")
    win.setFixedSize(480, 190)
    win.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    lay = QVBoxLayout(win)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(10)
    title = QLabel("Stahuji a instaluji novou verzi…")
    title.setStyleSheet("font-size:15px;font-weight:700;")
    status = QLabel("Připravuji…")
    status.setWordWrap(True)
    bar = QProgressBar()
    bar.setRange(0, 1000)
    bar.setValue(0)
    hint = QLabel("Nezavírejte toto okno. Po dokončení se BTS spustí samo.")
    hint.setStyleSheet("color:#555;")
    hint.setWordWrap(True)
    lay.addWidget(title)
    lay.addWidget(status)
    lay.addWidget(bar)
    lay.addWidget(hint)
    win.show()

    class Bridge(QObject):
        progress = Signal(str, object)  # message, frac (float | None)
        done = Signal(str)
        failed = Signal(str)

    bridge = Bridge()

    def set_progress(msg: str, frac) -> None:
        status.setText(str(msg))
        if frac is None:
            bar.setRange(0, 0)  # indeterminate / busy
        else:
            if bar.maximum() == 0:
                bar.setRange(0, 1000)
            bar.setValue(int(max(0.0, min(1.0, float(frac))) * 1000))

    def on_done(msg: str) -> None:
        set_progress(msg, 1.0)
        if restart:
            status.setText(f"{msg}\nSpouštím BTS…")
            app.processEvents()

            def _go() -> None:
                _restart_bts(root)
                QTimer.singleShot(400, win.close)

            QTimer.singleShot(500, _go)
        else:
            QMessageBox.information(win, "Aktualizace BTS", str(msg))
            win.close()

    def on_failed(msg: str) -> None:
        QMessageBox.critical(
            win,
            "Aktualizace selhala",
            f"{msg}\n\n"
            "Když je BTS v Program Files, spusť update přes "
            "BTS-Setup.exe jako správce, nebo znovu z Nastavení.",
        )
        win.close()

    bridge.progress.connect(set_progress)  # queued: worker thread -> GUI thread
    bridge.done.connect(on_done)
    bridge.failed.connect(on_failed)

    def worker() -> None:
        try:
            if wait_exit:
                bridge.progress.emit("Čekám na ukončení aplikace…", None)
                _wait_for_bts_exit(root)
            bridge.progress.emit("Kontroluji GitHub Releases…", None)
            result = check_for_update(root)
            if result.error:
                raise RuntimeError(result.error)
            if not result.update_available:
                bridge.done.emit(result.message or "Žádná nová verze")
                return
            bridge.progress.emit(f"Stahuji {result.remote_version or 'update'}…", 0.0)
            msg = apply_best_update(
                root, result.release, on_progress=lambda m, f: bridge.progress.emit(m, f)
            )
            bridge.done.emit(msg)
        except Exception as exc:  # noqa: BLE001
            bridge.failed.emit(str(exc))

    import threading

    threading.Thread(target=worker, name="bts-update-worker", daemon=True).start()
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
