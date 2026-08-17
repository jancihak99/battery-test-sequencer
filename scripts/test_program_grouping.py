"""Unit checks for module grouping + profile clone / archive helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bts.models.program import (
    archive_file,
    clone_profile,
    list_programs_by_module,
    program_label,
    program_module_of,
)


def _write_program(path: Path, module: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"meta": {"name": path.stem, "module_profile": module}, "steps": []},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_profile(path: Path, pid: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"id": pid, "name": f"{pid} module", "pack_v_max": 27.5}),
        encoding="utf-8",
    )


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    programs = tmp_path / "programs"
    profiles = tmp_path / "profiles"
    _write_profile(profiles / "A.yaml", "A")
    _write_profile(profiles / "B.yaml", "B")  # empty category (no step files)
    _write_program(programs / "p1.yaml", "A")
    _write_program(programs / "p2.yaml", "A")
    _write_program(programs / "p3.yaml", "C")  # unknown profile
    _write_program(programs / "dev" / "d1.yaml", "A")
    _write_program(programs / "_archiv" / "old.yaml", "A")  # must be skipped
    return programs, profiles


def test_grouping_by_module(tmp_path):
    programs, profiles = _setup(tmp_path)
    groups = list_programs_by_module(programs, profiles)
    by_id = {g.module_id: g for g in groups}

    # Every profile is a category, even empty B; unknown C is a trailing group.
    assert [g.module_id for g in groups] == ["A", "B", "C"]
    assert by_id["A"].known_profile and by_id["B"].known_profile
    assert not by_id["C"].known_profile
    assert "neznámý profil" in by_id["C"].label

    a_names = {lbl for lbl, _ in by_id["A"].programs}
    assert a_names == {"p1", "p2", "[DEV] d1"}   # dev prefix kept
    assert by_id["B"].programs == []             # empty category present
    # Archived file never appears in any group.
    all_paths = [p.name for g in groups for _lbl, p in g.programs]
    assert "old.yaml" not in all_paths


def test_program_helpers(tmp_path):
    p = tmp_path / "programs" / "dev" / "x.yaml"
    _write_program(p, "LTO_24V_70Ah")
    assert program_module_of(p) == "LTO_24V_70Ah"
    assert program_label(p) == "[DEV] x"
    assert program_label(tmp_path / "programs" / "y.yaml") == "y"


def test_clone_profile(tmp_path):
    profiles = tmp_path / "profiles"
    _write_profile(profiles / "A.yaml", "A")
    dest = clone_profile(profiles / "A.yaml", "NEW_ID", "New name", profiles)
    assert dest == profiles / "NEW_ID.yaml"
    data = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert data["id"] == "NEW_ID" and data["name"] == "New name"
    assert data["pack_v_max"] == 27.5  # template specs carried over
    with pytest.raises(FileExistsError):
        clone_profile(profiles / "A.yaml", "NEW_ID", "dup", profiles)


def test_archive_file(tmp_path):
    programs = tmp_path / "programs"
    _write_program(programs / "gone.yaml", "A")
    dest = archive_file(programs / "gone.yaml", programs / "_archiv")
    assert dest.exists() and dest.parent.name == "_archiv"
    assert not (programs / "gone.yaml").exists()
    # Collision → suffixed, no overwrite
    _write_program(programs / "gone.yaml", "A")
    dest2 = archive_file(programs / "gone.yaml", programs / "_archiv")
    assert dest2 != dest and dest2.exists()


if __name__ == "__main__":
    import tempfile

    for fn in (test_program_helpers, test_clone_profile, test_archive_file):
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
    print("OK")
