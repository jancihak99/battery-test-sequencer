"""Single-file GUI installer for Battery Test Sequencer.

Build (from repo root):
  scripts\\setup_exe\\build_setup_exe.bat
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

DEFAULT_REPO = "jancihak99/battery-test-sequencer"
DEFAULT_INSTALL = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "EBZ" / "BatteryTestSequencer"


def _resource_path(name: str) -> Path:
    """Resolve bundled resource (PyInstaller) or sibling file."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / name
        if p.exists():
            return p
    here = Path(__file__).resolve().parent
    for base in (here, here.parents[1] / "assets", here.parents[1] / "installer"):
        p = base / name
        if p.exists():
            return p
    return here / name


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None) -> tuple[int, str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=merged,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out
    except Exception as exc:
        return 1, str(exc)


def _python_ok() -> str | None:
    py = _which("python") or _which("py")
    if not py:
        return None
    code, out = _run([py, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"])
    if code != 0:
        return None
    ver = out.strip().splitlines()[-1] if out.strip() else ""
    try:
        major, minor = (int(x) for x in ver.split(".")[:2])
        if major < 3 or (major == 3 and minor < 10):
            return None
    except Exception:
        return None
    return ver


def _gh_authenticated() -> bool:
    if not _which("gh"):
        return False
    code, _ = _run(["gh", "auth", "status"])
    return code == 0


def install_bts(
    *,
    install_dir: Path,
    repo: str,
    token: str,
    log,
) -> None:
    py_ver = _python_ok()
    if not py_ver:
        raise RuntimeError(
            "Python 3.10+ neni na PATH.\n"
            "Nainstaluj z python.org a zaškrtni 'Add python.exe to PATH'."
        )
    log(f"Python {py_ver}")

    use_gh = _gh_authenticated()
    token = (token or os.environ.get("BTS_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if use_gh:
        log("GitHub: pouzivam prihlasene gh CLI")
    elif token:
        log("GitHub: pouzivam PAT token")
    else:
        raise RuntimeError(
            "Soukromy repozitar potrebuje pristup.\n"
            "Bud: gh auth login  NEBO vloz fine-grained PAT (Contents: Read)."
        )

    install_dir = install_dir.expanduser().resolve()
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    git_dir = install_dir / ".git"
    git = _which("git")
    if not git and not use_gh:
        raise RuntimeError("Git neni na PATH. Nainstaluj Git for Windows.")

    if git_dir.is_dir():
        log("Existujici instalace — git pull…")
        if use_gh:
            code, out = _run(["git", "pull", "--ff-only"], cwd=install_dir)
        else:
            code, out = _run(
                ["git", "-c", f"http.extraheader=AUTHORIZATION: bearer {token}", "pull", "--ff-only"],
                cwd=install_dir,
            )
        log(out.strip() or "(pull)")
        if code != 0:
            raise RuntimeError("git pull selhal — viz log")
    else:
        log(f"Klonuji {repo} → {install_dir}")
        if install_dir.exists():
            for child in list(install_dir.iterdir()):
                if child.name == ".github_token":
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    try:
                        child.unlink()
                    except Exception:
                        pass
        install_dir.mkdir(parents=True, exist_ok=True)
        if use_gh:
            # clone into empty/existing dir
            code, out = _run(["gh", "repo", "clone", repo, str(install_dir)])
        else:
            url = f"https://x-access-token:{token}@github.com/{repo}.git"
            code, out = _run([git, "clone", url, str(install_dir)])
            if code == 0:
                _run(["git", "remote", "set-url", "origin", f"https://github.com/{repo}.git"], cwd=install_dir)
        log(out.strip() or "(clone)")
        if code != 0:
            raise RuntimeError("Klonovani selhalo — zkontroluj token / gh auth")

    if token:
        tok_path = install_dir / ".github_token"
        tok_path.write_text(token + "\n", encoding="utf-8")
        try:
            subprocess.run(["attrib", "+H", str(tok_path)], check=False, capture_output=True)
        except Exception:
            pass
        log("Token ulozen (.github_token)")

    upd = install_dir / "config" / "update.yaml"
    upd.parent.mkdir(parents=True, exist_ok=True)
    upd.write_text(
        f"github_repo: {repo}\n"
        "check_on_startup: true\n"
        "auto_prompt_install: true\n"
        "channel: latest\n",
        encoding="utf-8",
    )

    log("Vytvarim .venv + zavislosti…")
    py = _which("python") or "python"
    venv_py = install_dir / ".venv" / "Scripts" / "python.exe"
    if not venv_py.exists():
        code, out = _run([py, "-m", "venv", str(install_dir / ".venv")], cwd=install_dir)
        log(out.strip())
        if code != 0:
            raise RuntimeError("venv selhalo")
    for cmd in (
        [str(venv_py), "-m", "pip", "install", "--upgrade", "pip"],
        [str(venv_py), "-m", "pip", "install", "-r", "requirements.txt"],
        [str(venv_py), "-m", "pip", "install", "-e", "."],
    ):
        log(" ".join(cmd[-4:]))
        code, out = _run(cmd, cwd=install_dir)
        if out.strip():
            # keep log short
            lines = out.strip().splitlines()
            log(lines[-1] if lines else "")
        if code != 0:
            raise RuntimeError(f"pip selhalo: {' '.join(cmd)}")

    # Desktop + Start Menu shortcuts with app icon
    log("Zástupce na Plochu…")
    ico = install_dir / "assets" / "bts_app_icon.ico"
    if not ico.exists():
        ico = _resource_path("bts_app_icon.ico")
    start_bat = install_dir / "Start BTS.bat"
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "EBZ"
    start_menu.mkdir(parents=True, exist_ok=True)

    ps = f"""
$dir = r'{install_dir}'
$bat = r'{start_bat}'
$ico = r'{ico}'
$w = New-Object -ComObject WScript.Shell
foreach ($p in @(
  (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Battery Test Sequencer.lnk'),
  (Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs\\EBZ\\Battery Test Sequencer.lnk')
)) {{
  $l = $w.CreateShortcut($p)
  $l.TargetPath = $bat
  $l.WorkingDirectory = $dir
  $l.Description = 'EBZ Battery Test Sequencer'
  if (Test-Path $ico) {{ $l.IconLocation = $ico }}
  $l.Save()
}}
"""
    code, out = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps])
    if code != 0:
        log("Varovani: zástupce se nepovedl — spust Start BTS.bat rucne")
        log(out.strip())
    else:
        log("Zástupce OK")

    log(f"HOTOVO → {install_dir}")


class SetupApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Battery Test Sequencer — Instalace")
        self.geometry("560x520")
        self.minsize(480, 420)
        self._q: queue.Queue = queue.Queue()
        self._busy = False

        ico = _resource_path("bts_app_icon.ico")
        if ico.exists():
            try:
                self.iconbitmap(default=str(ico))
            except Exception:
                pass

        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Battery Test Sequencer", font=("Segoe UI", 14, "bold")).pack(anchor=tk.W)
        ttk.Label(
            frm,
            text="Nainstaluje apku z privátního GitHubu (bez admin práv).",
            wraplength=500,
        ).pack(anchor=tk.W, **pad)

        grid = ttk.Frame(frm)
        grid.pack(fill=tk.X, pady=8)
        ttk.Label(grid, text="Cílová složka").grid(row=0, column=0, sticky=tk.W)
        self.ed_dir = ttk.Entry(grid)
        self.ed_dir.insert(0, str(DEFAULT_INSTALL))
        self.ed_dir.grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Label(grid, text="GitHub repo").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.ed_repo = ttk.Entry(grid)
        self.ed_repo.insert(0, DEFAULT_REPO)
        self.ed_repo.grid(row=1, column=1, sticky=tk.EW, padx=8, pady=4)
        ttk.Label(grid, text="GitHub PAT").grid(row=2, column=0, sticky=tk.W)
        self.ed_token = ttk.Entry(grid, show="•")
        self.ed_token.grid(row=2, column=1, sticky=tk.EW, padx=8)
        if _gh_authenticated():
            self.ed_token.insert(0, "")
            ttk.Label(grid, text="(gh už je přihlášené — PAT není nutný)", foreground="#555").grid(
                row=3, column=1, sticky=tk.W, padx=8
            )
        else:
            ttk.Label(
                grid,
                text="Fine-grained PAT: Contents Read na repo (nebo gh auth login)",
                foreground="#555",
            ).grid(row=3, column=1, sticky=tk.W, padx=8)
        grid.columnconfigure(1, weight=1)

        self.btn = ttk.Button(frm, text="Nainstalovat", command=self._start)
        self.btn.pack(anchor=tk.E, pady=8)

        self.log_box = scrolledtext.ScrolledText(frm, height=16, state=tk.DISABLED, font=("Consolas", 9))
        self.log_box.pack(fill=tk.BOTH, expand=True)

        self.after(200, self._pump)

    def _log(self, msg: str) -> None:
        self._q.put(("log", msg))

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "log":
                    self.log_box.configure(state=tk.NORMAL)
                    self.log_box.insert(tk.END, payload + "\n")
                    self.log_box.see(tk.END)
                    self.log_box.configure(state=tk.DISABLED)
                elif kind == "done":
                    self._busy = False
                    self.btn.configure(state=tk.NORMAL)
                    ok, text = payload
                    if ok:
                        messagebox.showinfo("Instalace", text)
                    else:
                        messagebox.showerror("Instalace selhala", text)
        except queue.Empty:
            pass
        self.after(200, self._pump)

    def _start(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn.configure(state=tk.DISABLED)
        install_dir = Path(self.ed_dir.get().strip())
        repo = self.ed_repo.get().strip() or DEFAULT_REPO
        token = self.ed_token.get().strip()

        def worker() -> None:
            try:
                install_bts(
                    install_dir=install_dir,
                    repo=repo,
                    token=token,
                    log=lambda m: self._log(str(m)),
                )
                self._q.put(
                    (
                        "done",
                        (
                            True,
                            f"Hotovo.\n\nSložka:\n{install_dir}\n\n"
                            "Spusť zástupce „Battery Test Sequencer“ na Ploše.\n"
                            "V Nastavení nastav CAN/EA a ulož.",
                        ),
                    )
                )
            except Exception as exc:
                self._log(f"ERROR: {exc}")
                self._q.put(("done", (False, str(exc))))

        threading.Thread(target=worker, daemon=True).start()


def main() -> int:
    app = SetupApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
