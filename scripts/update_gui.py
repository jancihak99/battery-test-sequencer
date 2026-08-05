"""Visible update progress window — launched after BTS exits.

Usage:
  pythonw scripts/update_gui.py <root> [--wait-exit] [--restart]
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

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
    if not (root / "main.py").exists():
        messagebox.showerror("Aktualizace BTS", f"Nenalezen main.py v:\n{root}")
        return 1

    from bts.update import apply_best_update, check_for_update

    q: queue.Queue[tuple[str, object]] = queue.Queue()
    win = tk.Tk()
    win.title("Aktualizace Battery Test Sequencer")
    win.geometry("460x180")
    win.resizable(False, False)
    try:
        win.attributes("-topmost", True)
    except Exception:
        pass

    frm = ttk.Frame(win, padding=16)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="Stahuji a instaluji novou verzi…", font=("Segoe UI", 11, "bold")).pack(
        anchor="w"
    )
    status = ttk.Label(frm, text="Připravuji…", wraplength=420)
    status.pack(anchor="w", pady=(10, 6))
    bar = ttk.Progressbar(frm, mode="determinate", maximum=1000)
    bar.pack(fill="x", pady=4)
    hint = ttk.Label(
        frm,
        text="Nezavírejte toto okno. Po dokončení se BTS spustí samo.",
        foreground="#555",
    )
    hint.pack(anchor="w", pady=(8, 0))

    def set_progress(msg: str, frac: float | None) -> None:
        q.put(("progress", (msg, frac)))

    def worker() -> None:
        try:
            if wait_exit:
                set_progress("Čekám na ukončení aplikace…", None)
                _wait_for_bts_exit(root)
            set_progress("Kontroluji GitHub Releases…", None)
            result = check_for_update(root)
            if result.error:
                raise RuntimeError(result.error)
            if not result.update_available:
                set_progress(result.message or "Už jsi na nejnovější verzi.", 1.0)
                q.put(("done", result.message or "Žádná nová verze"))
                return
            set_progress(f"Stahuji {result.remote_version or 'update'}…", 0.0)
            msg = apply_best_update(root, result.release, on_progress=set_progress)
            set_progress("Hotovo", 1.0)
            q.put(("done", msg))
        except Exception as exc:
            q.put(("error", str(exc)))

    def pump() -> None:
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "progress":
                    msg, frac = payload  # type: ignore[misc]
                    status.configure(text=str(msg))
                    if frac is None:
                        if str(bar.cget("mode")) != "indeterminate":
                            bar.configure(mode="indeterminate")
                            bar.start(12)
                    else:
                        if str(bar.cget("mode")) == "indeterminate":
                            bar.stop()
                            bar.configure(mode="determinate", maximum=1000)
                        bar["value"] = int(max(0.0, min(1.0, float(frac))) * 1000)
                elif kind == "done":
                    status.configure(text=str(payload))
                    bar.configure(mode="determinate")
                    bar["value"] = 1000
                    if restart:
                        status.configure(text=f"{payload}\nSpouštím BTS…")
                        win.update_idletasks()
                        time.sleep(0.6)
                        _restart_bts(root)
                        win.after(400, win.destroy)
                    else:
                        messagebox.showinfo("Aktualizace BTS", str(payload))
                        win.destroy()
                    return
                elif kind == "error":
                    try:
                        bar.stop()
                    except Exception:
                        pass
                    messagebox.showerror(
                        "Aktualizace selhala",
                        f"{payload}\n\n"
                        "Když je BTS v Program Files, spusť update přes "
                        "BTS-Setup.exe jako správce, nebo znovu z Nastavení.",
                    )
                    win.destroy()
                    return
        except queue.Empty:
            pass
        win.after(100, pump)

    threading.Thread(target=worker, name="bts-update-gui", daemon=True).start()
    win.after(100, pump)
    win.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
