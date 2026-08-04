"""Build offline payload for BTS-Setup.exe: app + wheels + embeddable Python."""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "bts_payload.zip"
ICON = Path(__file__).resolve().parent / "bts_app_icon.ico"
WHEEL_CACHE = Path(__file__).resolve().parent / "wheelhouse"

# Keep in sync with supported platform
PYTHON_EMBED_URL = (
    "https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

INCLUDE_TOP = [
    "main.py",
    "VERSION",
    "pyproject.toml",
    "requirements.txt",
    "Start BTS.bat",
    "Start BTS.vbs",
    "README.md",
    "assets",
    "config",
    "docs",
    "profiles",
    "programs",
    "scripts",
    "src",
]

EXCLUDE_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    ".pytest_cache",
    "installer",
    "runs",
}


def _read_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def _skip_rel(rel: Path) -> bool:
    parts = rel.parts
    if any(p in EXCLUDE_DIR_NAMES for p in parts):
        return True
    if rel.suffix in {".pyc", ".pyo"}:
        return True
    name = rel.name.lower()
    if name in {".github_token", "bts-setup.exe", "bts_payload.zip"}:
        return True
    if name.endswith(".partial") or name.endswith(".zip.partial"):
        return True
    # Never pack installer build artifacts into the payload
    if "setup_exe" in parts and (
        name.startswith("bts_payload")
        or name in {"pack_log.txt"}
    ):
        return True
    if "setup_exe" in parts and "build" in parts:
        return True
    if "setup_exe" in parts and "wheelhouse" in parts:
        return True
    return False


def _download(url: str, dest: Path) -> None:
    print(f"Downloading {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)  # noqa: S310


def _download_wheels(wheels_dir: Path) -> None:
    wheels_dir.mkdir(parents=True, exist_ok=True)
    WHEEL_CACHE.mkdir(parents=True, exist_ok=True)
    # Reuse cache when present
    cached = list(WHEEL_CACHE.glob("*.whl"))
    if len(cached) >= 8:
        print(f"Using cached wheels in {WHEEL_CACHE} ({len(cached)} files)")
        for whl in cached:
            shutil.copy2(whl, wheels_dir / whl.name)
        return
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    req = ROOT / "requirements.txt"
    cmd = [
        str(py),
        "-m",
        "pip",
        "download",
        "-r",
        str(req),
        "-d",
        str(WHEEL_CACHE),
        "--only-binary=:all:",
        "--python-version",
        "3.12",
        "--platform",
        "win_amd64",
        "--implementation",
        "cp",
        "--abi",
        "cp312",
    ]
    print(" ", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    subprocess.run(
        [
            str(py),
            "-m",
            "pip",
            "download",
            "pip",
            "setuptools",
            "wheel",
            "-d",
            str(WHEEL_CACHE),
            "--only-binary=:all:",
            "--python-version",
            "3.12",
            "--platform",
            "win_amd64",
        ],
        check=True,
        cwd=str(ROOT),
    )
    for whl in WHEEL_CACHE.glob("*.whl"):
        shutil.copy2(whl, wheels_dir / whl.name)


def _patch_embed_pth(extract_dir: Path) -> None:
    """Enable site-packages in embeddable Python."""
    pths = list(extract_dir.glob("*._pth"))
    if not pths:
        return
    pth = pths[0]
    text = pth.read_text(encoding="utf-8")
    if "import site" not in text:
        text = text.replace("#import site", "import site")
        if "import site" not in text:
            text += "\nimport site\n"
    if "Lib\\site-packages" not in text and "Lib/site-packages" not in text:
        text += "\nLib\\site-packages\n"
    pth.write_text(text, encoding="utf-8")


def build() -> Path:
    version = _read_version()
    print(f"Packing BTS {version} offline payload...")
    if not ICON.exists():
        src_ico = ROOT / "assets" / "bts_app_icon.ico"
        if src_ico.exists():
            shutil.copy2(src_ico, ICON)

    with tempfile.TemporaryDirectory(prefix="bts_pack_") as tmp:
        tmp_path = Path(tmp)
        embed_zip = tmp_path / "python-embed.zip"
        get_pip = tmp_path / "get-pip.py"
        wheels = tmp_path / "wheels"
        embed_dir = tmp_path / "python"
        # Zip lives ONLY in temp until finished — never beside scripts/ while packing
        staged_zip = tmp_path / "bts_payload.zip"

        print("Fetching embeddable Python + get-pip...")
        _download(PYTHON_EMBED_URL, embed_zip)
        _download(GET_PIP_URL, get_pip)
        print("Preparing wheels...")
        _download_wheels(wheels)

        with zipfile.ZipFile(embed_zip, "r") as zf:
            zf.extractall(embed_dir)
        _patch_embed_pth(embed_dir)
        shutil.copy2(get_pip, embed_dir / "get-pip.py")

        print("Writing zip (app + python + wheels)...")
        with zipfile.ZipFile(staged_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("payload_meta.txt", f"version={version}\n")
            for name in INCLUDE_TOP:
                src = ROOT / name
                if not src.exists():
                    continue
                if src.is_file():
                    if not _skip_rel(Path(name)):
                        zf.write(src, f"app/{name}")
                else:
                    for p in src.rglob("*"):
                        if not p.is_file():
                            continue
                        rel = p.relative_to(ROOT)
                        if _skip_rel(rel):
                            continue
                        zf.write(p, f"app/{rel.as_posix()}")
            zf.writestr("app/runs/.keep", "")
            for p in embed_dir.rglob("*"):
                if p.is_file():
                    zf.write(p, f"python/{p.relative_to(embed_dir).as_posix()}")
            for p in wheels.iterdir():
                if p.is_file():
                    zf.write(p, f"wheels/{p.name}")

        size_mb = staged_zip.stat().st_size / (1024 * 1024)
        print(f"Staged payload {size_mb:.1f} MB — copying to {OUT.name}")
        if size_mb > 500:
            raise RuntimeError(
                f"Payload suspiciously large ({size_mb:.0f} MB). "
                "Aborting — check that zip is not packing itself."
            )
        if OUT.exists():
            OUT.unlink()
        shutil.copy2(staged_zip, OUT)

    print(f"Wrote {OUT} ({OUT.stat().st_size / (1024 * 1024):.1f} MB)")
    return OUT


if __name__ == "__main__":
    try:
        build()
    except subprocess.CalledProcessError as exc:
        print("pip download failed", file=sys.stderr)
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
