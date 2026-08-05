"""GitHub-based auto-update for private Battery Test Sequencer installs."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bts.version import is_newer, read_version

log = logging.getLogger(__name__)

DEFAULT_REPO = "jancihak99/battery-test-sequencer"
TOKEN_FILENAME = ".github_token"
UPDATE_FILENAME = "config/update.yaml"


@dataclass
class UpdateConfig:
    github_repo: str = DEFAULT_REPO
    check_on_startup: bool = True
    auto_prompt_install: bool = True
    channel: str = "latest"  # latest release tag


@dataclass
class ReleaseInfo:
    tag: str
    name: str
    body: str
    zipball_url: str
    html_url: str


@dataclass
class UpdateCheckResult:
    local_version: str
    remote_version: str | None
    update_available: bool
    release: ReleaseInfo | None = None
    message: str = ""
    error: str | None = None


def token_path(root: Path) -> Path:
    return root / TOKEN_FILENAME


def load_update_config(root: Path) -> UpdateConfig:
    path = root / UPDATE_FILENAME
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Defaults: check + prompt on startup (customer PCs)
    check = data.get("check_on_startup")
    prompt = data.get("auto_prompt_install")
    return UpdateConfig(
        github_repo=str(data.get("github_repo") or DEFAULT_REPO),
        check_on_startup=True if check is None else bool(check),
        auto_prompt_install=True if prompt is None else bool(prompt),
        channel=str(data.get("channel") or "latest"),
    )


def save_update_config(root: Path, cfg: UpdateConfig) -> Path:
    path = root / UPDATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "github_repo": cfg.github_repo,
                "check_on_startup": cfg.check_on_startup,
                "auto_prompt_install": cfg.auto_prompt_install,
                "channel": cfg.channel,
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def read_token(root: Path) -> str | None:
    """Resolve GitHub token for private release checks.

    Order: env → install ``.github_token`` → ``config/.update_token`` →
    embedded token from the last publish (lab PCs need no manual PAT).
    """
    env = os.environ.get("BTS_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if env and env.strip():
        return env.strip()
    for path in (token_path(root), root / "config" / ".update_token"):
        if path.exists():
            tok = path.read_text(encoding="utf-8").strip()
            if tok:
                return tok
    try:
        from bts._embedded_token import UPDATE_TOKEN

        tok = (UPDATE_TOKEN or "").strip()
        if tok:
            return tok
    except Exception:
        pass
    return None


def token_source(root: Path) -> str:
    """Human-readable where the active token came from ( fore Settings)."""
    env = os.environ.get("BTS_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if env and env.strip():
        return "env"
    if token_path(root).exists() and token_path(root).read_text(encoding="utf-8").strip():
        return "install (.github_token)"
    upd = root / "config" / ".update_token"
    if upd.exists() and upd.read_text(encoding="utf-8").strip():
        return "config/.update_token"
    try:
        from bts._embedded_token import UPDATE_TOKEN

        if (UPDATE_TOKEN or "").strip():
            return "vestavěný (release)"
    except Exception:
        pass
    return "chybí"


def write_token(root: Path, token: str) -> Path:
    path = token_path(root)
    path.write_text(token.strip() + "\n", encoding="utf-8")
    try:
        # Best-effort: hide from casual listing on Windows
        if os.name == "nt":
            subprocess.run(["attrib", "+H", str(path)], check=False, capture_output=True)
    except Exception:
        pass
    return path


def _api_get(url: str, token: str | None) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "BatteryTestSequencer-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_latest_release(repo: str, token: str | None) -> ReleaseInfo:
    data = _api_get(f"https://api.github.com/repos/{repo}/releases/latest", token)
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise RuntimeError("GitHub release has no tag_name")
    return ReleaseInfo(
        tag=tag,
        name=str(data.get("name") or tag),
        body=str(data.get("body") or ""),
        zipball_url=str(data.get("zipball_url") or ""),
        html_url=str(data.get("html_url") or ""),
    )


def check_for_update(root: Path) -> UpdateCheckResult:
    local = read_version(root)
    cfg = load_update_config(root)
    token = read_token(root)
    try:
        rel = fetch_latest_release(cfg.github_repo, token)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            return UpdateCheckResult(
                local_version=local,
                remote_version=None,
                update_available=False,
                error=(
                    f"GitHub API {exc.code} — nelze číst Releases. "
                    f"Repo by mělo být public (jancihak99/battery-test-sequencer); zkontroluj síť."
                ),
            )
        return UpdateCheckResult(
            local_version=local,
            remote_version=None,
            update_available=False,
            error=f"GitHub API error: {exc}",
        )
    except Exception as exc:
        return UpdateCheckResult(
            local_version=local,
            remote_version=None,
            update_available=False,
            error=str(exc),
        )

    remote = rel.tag.lstrip("vV")
    available = is_newer(remote, local)
    return UpdateCheckResult(
        local_version=local,
        remote_version=remote,
        update_available=available,
        release=rel,
        message=(
            f"Nova verze {remote} (lokalne {local})"
            if available
            else f"Aktualni verze {local}"
        ),
    )


def _preserve_paths(root: Path) -> list[tuple[Path, Path]]:
    """Copy lab-local files aside before overwrite (entire config/ + token)."""
    keep: list[Path] = []
    cfg_dir = root / "config"
    if cfg_dir.is_dir():
        keep.extend(sorted(p for p in cfg_dir.rglob("*") if p.is_file()))
    token = root / TOKEN_FILENAME
    if token.exists():
        keep.append(token)
    tmp = Path(tempfile.mkdtemp(prefix="bts_keep_"))
    saved: list[tuple[Path, Path]] = []
    for src in keep:
        if src.exists():
            dst = tmp / src.relative_to(root)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            saved.append((src, dst))
    return saved


def _restore_paths(saved: list[tuple[Path, Path]]) -> None:
    for original, backup in saved:
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, original)


def _download(url: str, dest: Path, token: str | None) -> None:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "BatteryTestSequencer-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        shutil.copyfileobj(resp, out)


def _extract_zipball(zip_path: Path, root: Path) -> None:
    """GitHub zipball has a single top-level folder — copy its contents into root."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if not names:
            raise RuntimeError("Empty zipball")
        top = names[0].split("/")[0]
        tmp = Path(tempfile.mkdtemp(prefix="bts_unzip_"))
        zf.extractall(tmp)
        src_root = tmp / top
        if not src_root.is_dir():
            # Flat zip fallback
            src_root = tmp
        skip = {".venv", "runs", ".git", "__pycache__", ".github_token"}
        for item in src_root.iterdir():
            if item.name in skip:
                continue
            dest = root / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)


def _pip_reinstall(root: Path) -> None:
    py = root / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(shutil.which("python") or "python")
    req = root / "requirements.txt"
    cmds = [
        [str(py), "-m", "pip", "install", "--upgrade", "pip"],
        [str(py), "-m", "pip", "install", "-r", str(req)],
        [str(py), "-m", "pip", "install", "-e", str(root)],
    ]
    for cmd in cmds:
        log.info("Update pip: %s", " ".join(cmd))
        subprocess.run(cmd, cwd=str(root), check=False)


def apply_update_from_release(root: Path, release: ReleaseInfo) -> str:
    token = read_token(root)
    if not release.zipball_url:
        raise RuntimeError("Release has no zipball_url")
    saved = _preserve_paths(root)
    zip_path = Path(tempfile.mkstemp(suffix=".zip", prefix="bts_rel_")[1])
    try:
        _download(release.zipball_url, zip_path, token)
        _extract_zipball(zip_path, root)
        _restore_paths(saved)
        _pip_reinstall(root)
    finally:
        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            pass
    return f"Updated to {release.tag}"


def apply_update_via_git(root: Path) -> str:
    if not (root / ".git").exists():
        raise RuntimeError("Not a git checkout")
    subprocess.run(["git", "fetch", "--tags", "--force"], cwd=str(root), check=True)
    subprocess.run(["git", "pull", "--ff-only"], cwd=str(root), check=True)
    _pip_reinstall(root)
    return f"git pull OK — version {read_version(root)}"


def apply_best_update(root: Path, release: ReleaseInfo | None = None) -> str:
    """Prefer git pull when clone exists; else download release zipball."""
    if (root / ".git").exists():
        try:
            return apply_update_via_git(root)
        except Exception as exc:
            log.warning("git update failed (%s) — falling back to zipball", exc)
    if release is None:
        cfg = load_update_config(root)
        release = fetch_latest_release(cfg.github_repo, read_token(root))
    return apply_update_from_release(root, release)


def spawn_external_updater(root: Path) -> Path:
    """Write and launch a detached PowerShell updater so the GUI can exit."""
    candidates = [
        root / "scripts" / "Update-BTS.ps1",
        root / "installer" / "Update-BTS.ps1",
    ]
    ps1 = next((p for p in candidates if p.exists()), None)
    if ps1 is None:
        raise FileNotFoundError("Missing scripts/Update-BTS.ps1")
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ps1),
            "-Root",
            str(root),
            "-Restart",
        ],
        cwd=str(root),
        creationflags=flags,
    )
    return ps1
