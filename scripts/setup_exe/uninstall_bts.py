"""Complete uninstall for Battery Test Sequencer (Program Files install).

Run elevated. Removes install folder, shortcuts, and Apps & Features entry.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

APP_NAME = "Battery Test Sequencer"
UNINSTALL_REG = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\EBZ_BatteryTestSequencer"
SHORTCUT_NAME = "Battery Test Sequencer.lnk"
UNINSTALL_SHORTCUT = "Uninstall Battery Test Sequencer.lnk"
START_MENU_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / (
    r"Microsoft\Windows\Start Menu\Programs\EBZ"
)
USER_START_MENU_DIR = (
    Path(os.environ.get("APPDATA", ""))
    / r"Microsoft\Windows\Start Menu\Programs\EBZ"
)


def install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def is_admin() -> bool:
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated() -> int:
    import ctypes

    root = install_root()
    if getattr(sys, "frozen", False):
        exe = str(sys.executable)
        params = "--elevated"
    else:
        pyw = root / "python" / "pythonw.exe"
        script = root / "uninstall_bts.py"
        if not pyw.exists():
            pyw = root / "python" / "python.exe"
        exe = str(pyw if pyw.exists() else sys.executable)
        params = f'"{script}" --elevated'
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", exe, params, str(root), 1
    )
    return 0 if int(rc) > 32 else 1


def _kill_bts_processes(root: Path) -> None:
    root_esc = str(root).replace("'", "''")
    ps = f"""
$ErrorActionPreference = 'SilentlyContinue'
$root = '{root_esc}'.ToLowerInvariant()
Get-CimInstance Win32_Process | ForEach-Object {{
  $cl = $_.CommandLine
  if (-not $cl) {{ return }}
  $cll = $cl.ToLowerInvariant()
  if ($cll -match 'main\\.py' -or $cll.Contains($root)) {{
    if ($_.Name -match 'python') {{
      try {{ Stop-Process -Id $_.ProcessId -Force }} catch {{}}
    }}
  }}
}}
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    time.sleep(0.6)


def _remove_file(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except Exception:
        pass


def remove_shortcuts() -> None:
    for desk in (
        Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop",
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop",
    ):
        _remove_file(desk / SHORTCUT_NAME)

    for base in (START_MENU_DIR, USER_START_MENU_DIR):
        if not base.exists():
            continue
        _remove_file(base / SHORTCUT_NAME)
        _remove_file(base / UNINSTALL_SHORTCUT)
        try:
            if base.is_dir() and not any(base.iterdir()):
                base.rmdir()
        except Exception:
            pass


def remove_uninstall_registry() -> None:
    try:
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                winreg.DeleteKey(hive, UNINSTALL_REG)
            except FileNotFoundError:
                pass
            except OSError:
                pass
    except Exception:
        pass


def remove_legacy_localappdata(current: Path) -> None:
    legacy = (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        / "EBZ"
        / "BatteryTestSequencer"
    )
    try:
        if not legacy.exists():
            return
        if legacy.resolve() == current.resolve():
            return
        if (legacy / "main.py").exists() or (legacy / "VERSION").exists():
            shutil.rmtree(legacy, ignore_errors=True)
        ebz = legacy.parent
        if ebz.is_dir() and not any(ebz.iterdir()):
            ebz.rmdir()
    except Exception:
        pass


def uninstall(root: Path) -> None:
    _kill_bts_processes(root)
    remove_shortcuts()
    remove_uninstall_registry()
    remove_legacy_localappdata(root)

    try:
        shutil.rmtree(root)
    except Exception:
        time.sleep(1.0)
        _kill_bts_processes(root)
        try:
            shutil.rmtree(root)
        except Exception as exc:
            subprocess.run(
                ["cmd", "/c", "rmdir", "/s", "/q", str(root)],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if root.exists():
                raise RuntimeError(
                    f"Nepodařilo se smazat složku:\n{root}\n\n{exc}\n"
                    "Zavři BTS a zkus Uninstall znovu."
                ) from exc

    parent = root.parent
    try:
        if parent.name.upper() == "EBZ" and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception:
        pass


def relaunch_from_temp_then_delete(root: Path) -> int:
    """Copy uninstaller to %TEMP% so Program Files tree can be deleted."""
    import tempfile

    tmp = Path(tempfile.gettempdir()) / f"bts_uninstall_{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=True)
    script_src = root / "uninstall_bts.py"
    if getattr(sys, "frozen", False):
        # Frozen Uninstall BTS.exe — copy exe
        exe_src = Path(sys.executable)
        exe_dst = tmp / exe_src.name
        shutil.copy2(exe_src, exe_dst)
        # Marker file with install root
        (tmp / "install_root.txt").write_text(str(root), encoding="utf-8")
        subprocess.Popen(
            [str(exe_dst), "--from-temp", "--elevated"],
            cwd=str(tmp),
            close_fds=True,
        )
        return 0

    if not script_src.exists():
        # Already running a copy? fall through
        uninstall(root)
        return 0

    script_dst = tmp / "uninstall_bts.py"
    shutil.copy2(script_src, script_dst)
    (tmp / "install_root.txt").write_text(str(root), encoding="utf-8")
    # Prefer system pythonw if bundled python is under root (will be deleted)
    py = shutil.which("pythonw") or shutil.which("python") or sys.executable
    subprocess.Popen(
        [py, str(script_dst), "--from-temp", "--elevated"],
        cwd=str(tmp),
        close_fds=True,
    )
    return 0


def resolve_root_from_args() -> Path:
    if "--from-temp" in sys.argv:
        marker = Path(__file__).resolve().parent / "install_root.txt"
        if marker.exists():
            return Path(marker.read_text(encoding="utf-8").strip())
        # frozen: next to exe
        if getattr(sys, "frozen", False):
            m2 = Path(sys.executable).parent / "install_root.txt"
            if m2.exists():
                return Path(m2.read_text(encoding="utf-8").strip())
    return install_root()


def main() -> int:
    quiet = "--quiet" in sys.argv
    from_temp = "--from-temp" in sys.argv
    root = resolve_root_from_args()

    if not is_admin():
        return relaunch_elevated()

    tw = tk.Tk()
    tw.withdraw()
    try:
        if not quiet and not from_temp:
            ok = messagebox.askyesno(
                f"Odinstalovat {APP_NAME}",
                f"Opravdu odinstalovat {APP_NAME}?\n\n"
                f"Smaže se celá složka:\n{root}\n\n"
                "Včetně config, runs, zástupců a záznamu v Nastavení Windows.\n"
                "Tuto akci nelze vrátit.",
                icon="warning",
            )
            if not ok:
                return 0

        # First launch from install dir → hop to TEMP then delete
        if not from_temp and root.exists():
            tw.destroy()
            return relaunch_from_temp_then_delete(root)

        try:
            uninstall(root)
        except Exception as exc:
            messagebox.showerror("Odinstalace selhala", str(exc))
            return 1
        if not quiet:
            messagebox.showinfo(
                "Odinstalace",
                f"{APP_NAME} byl kompletně odstraněn z tohoto PC.",
            )
    finally:
        try:
            tw.destroy()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
