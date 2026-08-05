"""Classic wizard installer — embeds current BTS (no GitHub on first install).

Default: C:\\Program Files\\EBZ\\BatteryTestSequencer (64-bit → Program Files).
Requires Administrator. Ships Uninstall BTS.bat for complete removal.

Build: scripts\\setup_exe\\build_setup_exe.bat
"""
from __future__ import annotations

import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import winreg
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

DEFAULT_REPO = "jancihak99/battery-test-sequencer"
APP_TITLE = "Battery Test Sequencer"
APP_PUBLISHER = "EBZ nano power"
UNINSTALL_REG = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\EBZ_BatteryTestSequencer"


def default_install_dir() -> Path:
    """64-bit embeddable Python → Program Files (not x86)."""
    pf = os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"
    return Path(pf) / "EBZ" / "BatteryTestSequencer"


DEFAULT_INSTALL = default_install_dir()


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _ensure_admin_or_relaunch() -> bool:
    """Relaunch with UAC if needed. Return True if this process should exit."""
    if _is_admin() or "--elevated" in sys.argv or os.name != "nt":
        return False
    try:
        extra = " ".join(f'"{a}"' for a in sys.argv[1:] if a != "--elevated")
        params = (extra + ' --elevated').strip()
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
        return int(rc) > 32
    except Exception:
        return False


def _resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / name
        if p.exists():
            return p
    here = Path(__file__).resolve().parent
    for base in (here, here.parents[1] / "assets"):
        p = base / name
        if p.exists():
            return p
    return here / name


def _run(cmd: list[str], *, cwd: Path | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return 1, str(exc)


def _payload_zip() -> Path:
    p = _resource_path("bts_payload.zip")
    if not p.exists():
        raise FileNotFoundError(
            "V instalátoru chybí bts_payload.zip.\n"
            "Znovu sestav: scripts\\setup_exe\\build_setup_exe.bat"
        )
    return p


def _write_uninstaller(install_dir: Path, log) -> Path:
    """Ship Uninstall BTS.exe (+ .bat fallback) into the install folder."""
    src_py = _resource_path("uninstall_bts.py")
    if not src_py.exists():
        src_py = Path(__file__).resolve().parent / "uninstall_bts.py"
    if src_py.exists():
        shutil.copy2(src_py, install_dir / "uninstall_bts.py")

    src_exe = _resource_path("Uninstall BTS.exe")
    if not src_exe.exists():
        src_exe = Path(__file__).resolve().parent / "Uninstall BTS.exe"

    exe_dest = install_dir / "Uninstall BTS.exe"
    if src_exe.exists():
        shutil.copy2(src_exe, exe_dest)
        log("Odinstalátor: Uninstall BTS.exe")
        primary = exe_dest
    else:
        log("Varování: Uninstall BTS.exe chybí v Setup — použije se .bat")
        primary = install_dir / "Uninstall BTS.bat"

    bat = install_dir / "Uninstall BTS.bat"
    bat.write_text(
        "@echo off\n"
        "setlocal\n"
        'cd /d "%~dp0"\n'
        'if exist "%~dp0Uninstall BTS.exe" (\n'
        '  start "" "%~dp0Uninstall BTS.exe" %*\n'
        "  exit /b 0\n"
        ")\n"
        'if exist "python\\pythonw.exe" (\n'
        '  "python\\pythonw.exe" "%~dp0uninstall_bts.py" %*\n'
        ") else if exist \"python\\python.exe\" (\n"
        '  "python\\python.exe" "%~dp0uninstall_bts.py" %*\n'
        ") else (\n"
        "  echo Chybi odinstalator.\n"
        "  pause\n"
        "  exit /b 1\n"
        ")\n",
        encoding="utf-8",
    )
    return primary

def _register_uninstall(install_dir: Path, uninstall_bat: Path, version: str) -> None:
    """Apps & Features entry (requires admin / HKLM)."""
    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_LOCAL_MACHINE,
            UNINSTALL_REG,
            0,
            winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY,
        )
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_TITLE)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, APP_PUBLISHER)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{uninstall_bat}"')
        winreg.SetValueEx(key, "QuietUninstallString", 0, winreg.REG_SZ, f'"{uninstall_bat}" --quiet')
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        ico = install_dir / "assets" / "bts_app_icon.ico"
        if ico.exists():
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(ico))
        winreg.CloseKey(key)
    except OSError:
        # Fallback HKCU if HKLM denied
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_REG, 0, winreg.KEY_WRITE)
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_TITLE)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, APP_PUBLISHER)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{uninstall_bat}"')
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)


def install_from_payload(install_dir: Path, log) -> None:
    """Extract bundled app + portable Python + wheels; offline pip install."""
    install_dir = install_dir.expanduser().resolve()
    payload = _payload_zip()
    log(f"Instalační balíček: {payload.name}")
    log(f"Cíl: {install_dir}")
    try:
        install_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise RuntimeError(
            f"Nelze zapsat do {install_dir}.\n"
            "Spusť BTS-Setup.exe jako správce (Program Files)."
        ) from exc

    preserve = []
    for rel in ("config/default.yaml", ".github_token", "config/update.yaml"):
        src = install_dir / rel
        if src.exists():
            preserve.append((rel, src.read_bytes()))

    for name in ("src", "assets", "profiles", "programs", "scripts", "docs", "python", "wheels"):
        p = install_dir / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    for name in (
        "main.py",
        "VERSION",
        "requirements.txt",
        "pyproject.toml",
        "Start BTS.bat",
        "Start BTS.vbs",
        "README.md",
        "Uninstall BTS.bat",
        "Uninstall BTS.exe",
        "uninstall_bts.py",
    ):
        p = install_dir / name
        if p.is_file():
            p.unlink()

    log("Rozbaluji aplikaci…")
    with zipfile.ZipFile(payload, "r") as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name.endswith("/"):
                continue
            if name.startswith("app/"):
                dest = install_dir / name[len("app/") :]
            elif name.startswith("python/"):
                dest = install_dir / "python" / name[len("python/") :]
            elif name.startswith("wheels/"):
                dest = install_dir / "wheels" / name[len("wheels/") :]
            elif name == "payload_meta.txt":
                dest = install_dir / "payload_meta.txt"
            else:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)

    for rel, data in preserve:
        dest = install_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(data)

    (install_dir / "runs").mkdir(parents=True, exist_ok=True)

    upd = install_dir / "config" / "update.yaml"
    upd.parent.mkdir(parents=True, exist_ok=True)
    if not upd.exists():
        upd.write_text(
            f"github_repo: {DEFAULT_REPO}\n"
            "check_on_startup: true\n"
            "auto_prompt_install: true\n"
            "channel: latest\n",
            encoding="utf-8",
        )

    py = install_dir / "python" / "python.exe"
    if not py.exists():
        raise RuntimeError("V balíčku chybí přenosný Python.")

    log("Instaluji závislosti (offline ze zabudovaných wheels)…")
    code, out = _run([str(py), "-c", "import pip"])
    if code != 0:
        get_pip = install_dir / "python" / "get-pip.py"
        if not get_pip.exists():
            raise RuntimeError("Chybí get-pip.py v balíčku.")
        code, out = _run(
            [
                str(py),
                str(get_pip),
                "--no-warn-script-location",
                "--no-index",
                f"--find-links={install_dir / 'wheels'}",
            ]
        )
        log((out or "").strip().splitlines()[-1] if out.strip() else "get-pip")
        if code != 0:
            raise RuntimeError(f"get-pip selhal:\n{out[-800:]}")

    wheels = install_dir / "wheels"
    req = install_dir / "requirements.txt"
    code, out = _run(
        [
            str(py),
            "-m",
            "pip",
            "install",
            "--no-index",
            f"--find-links={wheels}",
            "-r",
            str(req),
            "--no-warn-script-location",
        ]
    )
    if out.strip():
        for line in out.strip().splitlines()[-8:]:
            log(line)
    if code != 0:
        raise RuntimeError(f"pip install selhal:\n{out[-1200:]}")

    site = install_dir / "python" / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    (site / "bts_app.pth").write_text(str(install_dir / "src") + "\n", encoding="utf-8")

    pythonw = install_dir / "python" / "pythonw.exe"
    python_exe = install_dir / "python" / "python.exe"
    bat = install_dir / "Start BTS.bat"
    bat.write_text(
        "@echo off\n"
        'cd /d "%~dp0"\n'
        'if exist "python\\pythonw.exe" (\n'
        '  start "" "python\\pythonw.exe" "%~dp0main.py"\n'
        "  exit /b 0\n"
        ")\n"
        '"python\\python.exe" "%~dp0main.py"\n'
        "if errorlevel 1 pause\n",
        encoding="utf-8",
    )
    vbs = install_dir / "Start BTS.vbs"
    vbs.write_text(
        'Set sh = CreateObject("WScript.Shell")\n'
        'Set fso = CreateObject("Scripting.FileSystemObject")\n'
        "dir = fso.GetParentFolderName(WScript.ScriptFullName)\n"
        "sh.CurrentDirectory = dir\n"
        'pyw = dir & "\\python\\pythonw.exe"\n'
        "If fso.FileExists(pyw) Then\n"
        '  sh.Run """" & pyw & """ """ & dir & "\\main.py""", 0, False\n'
        "Else\n"
        '  sh.Run """" & dir & "\\Start BTS.bat""", 1, False\n'
        "End If\n",
        encoding="utf-8",
    )

    uninstall_bat = _write_uninstaller(install_dir, log)

    ver = "?"
    vf = install_dir / "VERSION"
    if vf.exists():
        ver = vf.read_text(encoding="utf-8").strip()
    _register_uninstall(install_dir, uninstall_bat, ver)
    log("Záznam v Nastavení Windows → Aplikace OK")

    log("Vytvářím zástupce…")
    ico = install_dir / "assets" / "bts_app_icon.ico"
    if not ico.exists():
        ico = _resource_path("bts_app_icon.ico")
    target = str(pythonw if pythonw.exists() else python_exe)
    args = f'"{install_dir / "main.py"}"'
    work = str(install_dir)
    un_bat = str(uninstall_bat)
    # Public Desktop + all-users Start Menu (ProgramData) when elevated
    ps = f"""
$w = New-Object -ComObject WScript.Shell
$desk = Join-Path $env:PUBLIC 'Desktop\\Battery Test Sequencer.lnk'
$smDir = Join-Path $env:ProgramData 'Microsoft\\Windows\\Start Menu\\Programs\\EBZ'
New-Item -ItemType Directory -Force -Path $smDir | Out-Null
$paths = @(
  $desk,
  (Join-Path $smDir 'Battery Test Sequencer.lnk')
)
foreach ($p in $paths) {{
  $l = $w.CreateShortcut($p)
  $l.TargetPath = '{target}'
  $l.Arguments = '{args}'
  $l.WorkingDirectory = '{work}'
  $l.Description = 'EBZ Battery Test Sequencer'
  if (Test-Path '{ico}') {{ $l.IconLocation = '{ico}' }}
  $l.Save()
}}
$u = $w.CreateShortcut((Join-Path $smDir 'Uninstall Battery Test Sequencer.lnk'))
$u.TargetPath = '{un_bat}'
$u.WorkingDirectory = '{work}'
$u.Description = 'Odinstalovat Battery Test Sequencer'
if (Test-Path '{ico}') {{ $u.IconLocation = '{ico}' }}
$u.Save()
"""
    # un_bat may be .exe — quote already in path via str
    code, out = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps])
    if code != 0:
        log("Varování: zástupce se nepodařil — použij Start BTS.bat / Uninstall BTS.exe")
    else:
        log("Zástupce: Plocha + Start menu (+ Uninstall)")

    log(f"Hotovo — BTS {ver} → {install_dir}")


class Wizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE} — Setup")
        self.geometry("640x440")
        self.minsize(560, 400)
        self.resizable(False, False)
        self._page = 0
        self._q: queue.Queue = queue.Queue()
        self.install_dir = tk.StringVar(value=str(DEFAULT_INSTALL))
        self.launch_after = tk.BooleanVar(value=True)
        self._busy = False

        ico = _resource_path("bts_app_icon.ico")
        if ico.exists():
            try:
                self.iconbitmap(default=str(ico))
            except Exception:
                pass

        self.configure(bg="#f0f0f0")
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except Exception:
            pass

        self.header = tk.Frame(self, bg="#1a5f7a", height=72)
        self.header.pack(fill=tk.X)
        self.header.pack_propagate(False)
        self.hdr_title = tk.Label(
            self.header,
            text=APP_TITLE,
            fg="white",
            bg="#1a5f7a",
            font=("Segoe UI", 16, "bold"),
            anchor="w",
        )
        self.hdr_title.pack(side=tk.LEFT, padx=20, pady=8)
        self.hdr_sub = tk.Label(
            self.header,
            text="Instalační průvodce",
            fg="#d0e8f0",
            bg="#1a5f7a",
            font=("Segoe UI", 10),
            anchor="w",
        )
        self.hdr_sub.pack(side=tk.LEFT, padx=(0, 20))

        self.body = tk.Frame(self, bg="#f0f0f0")
        self.body.pack(fill=tk.BOTH, expand=True, padx=24, pady=16)

        self.pages: list[tk.Frame] = []
        self.pages.append(self._page_welcome())
        self.pages.append(self._page_folder())
        self.pages.append(self._page_progress())
        self.pages.append(self._page_done())
        for p in self.pages:
            p.place(in_=self.body, x=0, y=0, relwidth=1, relheight=1)

        foot = tk.Frame(self, bg="#e8e8e8", height=52)
        foot.pack(fill=tk.X, side=tk.BOTTOM)
        foot.pack_propagate(False)
        inner = tk.Frame(foot, bg="#e8e8e8")
        inner.pack(side=tk.RIGHT, padx=16, pady=10)
        self.btn_back = ttk.Button(inner, text="< Zpět", width=12, command=self._back)
        self.btn_next = ttk.Button(inner, text="Další >", width=12, command=self._next)
        self.btn_cancel = ttk.Button(inner, text="Zrušit", width=12, command=self._cancel)
        self.btn_cancel.pack(side=tk.RIGHT, padx=(8, 0))
        self.btn_next.pack(side=tk.RIGHT, padx=(8, 0))
        self.btn_back.pack(side=tk.RIGHT)

        self._show(0)
        self.after(200, self._pump)

    def _page_welcome(self) -> tk.Frame:
        f = tk.Frame(self.body, bg="#f0f0f0")
        tk.Label(
            f,
            text=f"Vítejte v instalaci\n{APP_TITLE}",
            font=("Segoe UI", 14, "bold"),
            bg="#f0f0f0",
            justify=tk.LEFT,
            anchor="w",
        ).pack(anchor="w", pady=(8, 12))
        tk.Label(
            f,
            text=(
                "Tento průvodce nainstaluje Battery Test Sequencer na tento počítač.\n\n"
                "• Výchozí složka: Program Files\\EBZ\\… (64bit aplikace)\n"
                "• Vyžaduje oprávnění správce (UAC)\n"
                "• Není potřeba účet GitHub při instalaci\n"
                "• Aktualizace později apka stáhne sama z GitHubu\n"
                "• Odinstalace: Uninstall BTS.exe ve složce aplikace\n\n"
                "Klikněte na Další pro pokračování."
            ),
            font=("Segoe UI", 10),
            bg="#f0f0f0",
            justify=tk.LEFT,
            anchor="w",
        ).pack(anchor="w")
        return f

    def _page_folder(self) -> tk.Frame:
        f = tk.Frame(self.body, bg="#f0f0f0")
        tk.Label(
            f,
            text="Cílová složka",
            font=("Segoe UI", 14, "bold"),
            bg="#f0f0f0",
        ).pack(anchor="w", pady=(8, 12))
        tk.Label(
            f,
            text="Aplikace se nainstaluje sem (doporučeno ponechat Program Files):",
            bg="#f0f0f0",
            font=("Segoe UI", 10),
        ).pack(anchor="w")
        row = tk.Frame(f, bg="#f0f0f0")
        row.pack(fill=tk.X, pady=12)
        ttk.Entry(row, textvariable=self.install_dir).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Procházet…", command=self._browse).pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(
            f,
            text="Zástupce na Ploše a ve Start menu. Odinstalace smaže vše včetně config/runs.",
            bg="#f0f0f0",
            font=("Segoe UI", 9),
            fg="#555",
        ).pack(anchor="w", pady=(8, 0))
        if not _is_admin():
            tk.Label(
                f,
                text="Upozornění: Setup neběží jako správce — zápis do Program Files může selhat.",
                bg="#f0f0f0",
                font=("Segoe UI", 9),
                fg="#a04000",
            ).pack(anchor="w", pady=(8, 0))
        return f

    def _page_progress(self) -> tk.Frame:
        f = tk.Frame(self.body, bg="#f0f0f0")
        tk.Label(
            f,
            text="Instalace",
            font=("Segoe UI", 14, "bold"),
            bg="#f0f0f0",
        ).pack(anchor="w", pady=(8, 12))
        self.lbl_status = tk.Label(f, text="Připraveno…", bg="#f0f0f0", font=("Segoe UI", 10), anchor="w")
        self.lbl_status.pack(fill=tk.X)
        self.prog = ttk.Progressbar(f, mode="indeterminate")
        self.prog.pack(fill=tk.X, pady=12)
        self.log = tk.Text(f, height=12, font=("Consolas", 9), state=tk.DISABLED, bg="white")
        self.log.pack(fill=tk.BOTH, expand=True)
        return f

    def _page_done(self) -> tk.Frame:
        f = tk.Frame(self.body, bg="#f0f0f0")
        tk.Label(
            f,
            text="Instalace dokončena",
            font=("Segoe UI", 14, "bold"),
            bg="#f0f0f0",
        ).pack(anchor="w", pady=(8, 12))
        self.lbl_done = tk.Label(
            f,
            text="Battery Test Sequencer byl úspěšně nainstalován.",
            bg="#f0f0f0",
            font=("Segoe UI", 10),
            justify=tk.LEFT,
            anchor="w",
        )
        self.lbl_done.pack(anchor="w")
        ttk.Checkbutton(
            f,
            text="Spustit Battery Test Sequencer",
            variable=self.launch_after,
        ).pack(anchor="w", pady=16)
        return f

    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.install_dir.get())
        if d:
            self.install_dir.set(d)

    def _show(self, idx: int) -> None:
        self._page = idx
        for i, p in enumerate(self.pages):
            if i == idx:
                p.lift()
        self.btn_back.configure(state=tk.NORMAL if idx > 0 and idx < 2 else tk.DISABLED)
        if idx == 0:
            self.btn_next.configure(text="Další >", state=tk.NORMAL)
            self.hdr_sub.configure(text="Vítejte")
        elif idx == 1:
            self.btn_next.configure(text="Instalovat", state=tk.NORMAL)
            self.hdr_sub.configure(text="Cílová složka")
        elif idx == 2:
            self.btn_next.configure(text="Další >", state=tk.DISABLED)
            self.btn_back.configure(state=tk.DISABLED)
            self.btn_cancel.configure(state=tk.DISABLED)
            self.hdr_sub.configure(text="Probíhá instalace…")
        else:
            self.btn_next.configure(text="Dokončit", state=tk.NORMAL)
            self.btn_back.configure(state=tk.DISABLED)
            self.btn_cancel.configure(state=tk.DISABLED)
            self.hdr_sub.configure(text="Hotovo")

    def _back(self) -> None:
        if self._page == 1:
            self._show(0)

    def _next(self) -> None:
        if self._page == 0:
            self._show(1)
        elif self._page == 1:
            self._start_install()
        elif self._page == 3:
            self._finish()

    def _cancel(self) -> None:
        if self._busy:
            return
        if messagebox.askyesno("Zrušit", "Opravdu zrušit instalaci?"):
            self.destroy()

    def _append_log(self, msg: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)
        self.lbl_status.configure(text=msg[:80])

    def _start_install(self) -> None:
        dest = Path(self.install_dir.get().strip())
        if not dest:
            messagebox.showerror("Instalace", "Zadej cílovou složku.")
            return
        self._show(2)
        self._busy = True
        self.prog.start(12)

        def worker() -> None:
            try:
                install_from_payload(dest, log=lambda m: self._q.put(("log", str(m))))
                self._q.put(("ok", str(dest)))
            except Exception as exc:
                self._q.put(("err", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "ok":
                    self.prog.stop()
                    self._busy = False
                    self.lbl_done.configure(
                        text=(
                            "Battery Test Sequencer byl úspěšně nainstalován.\n\n"
                            f"Složka:\n{payload}\n\n"
                            "Odinstalace: Uninstall BTS.exe (nebo Start menu → Uninstall…)"
                        )
                    )
                    self._show(3)
                elif kind == "err":
                    self.prog.stop()
                    self._busy = False
                    self._append_log("CHYBA: " + payload)
                    messagebox.showerror("Instalace selhala", payload)
                    self.btn_cancel.configure(state=tk.NORMAL)
                    self.btn_back.configure(state=tk.NORMAL)
                    self._show(1)
        except queue.Empty:
            pass
        self.after(200, self._pump)

    def _finish(self) -> None:
        if self.launch_after.get():
            dest = Path(self.install_dir.get().strip())
            bat = dest / "Start BTS.bat"
            pyw = dest / "python" / "pythonw.exe"
            main = dest / "main.py"
            try:
                if pyw.exists() and main.exists():
                    subprocess.Popen([str(pyw), str(main)], cwd=str(dest))
                elif bat.exists():
                    subprocess.Popen(["cmd", "/c", str(bat)], cwd=str(dest))
            except Exception as exc:
                messagebox.showwarning("Spuštění", f"Nepodařilo se spustit:\n{exc}")
        self.destroy()


def main() -> int:
    if _ensure_admin_or_relaunch():
        return 0
    try:
        _payload_zip()
    except FileNotFoundError as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("BTS Setup", str(exc))
        return 1
    if not _is_admin():
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            "BTS Setup",
            "Instalace do Program Files vyžaduje oprávnění správce.\n"
            "Spusť BTS-Setup.exe znovu a potvrď UAC.",
        )
        return 1
    app = Wizard()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
