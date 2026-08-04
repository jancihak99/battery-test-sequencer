"""App version helpers."""
from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    # src/bts/version.py → src/bts → src → root
    return Path(__file__).resolve().parents[2]


def read_version(root: Path | None = None) -> str:
    root = root or project_root()
    for candidate in (root / "VERSION", root / "pyproject.toml"):
        if candidate.name == "VERSION" and candidate.exists():
            return candidate.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    # Fallback: pyproject
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "0.0.0"


def parse_version(text: str) -> tuple[int, ...]:
    raw = text.strip().lstrip("vV")
    parts: list[int] = []
    for bit in raw.split("."):
        digits = "".join(ch for ch in bit if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)
