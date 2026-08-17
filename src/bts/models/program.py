from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


STEP_TYPES = (
    "bms_ready",
    "bms_idle",
    "goto_soc",
    "charge",
    "discharge",
    "wait_temp",
    "wait_time",
    "dcir",
    "report",
    "notify",
)


@dataclass
class ModuleProfile:
    id: str
    name: str
    nominal_capacity_ah: float
    pack_v_min: float
    pack_v_max: float
    cell_v_min: float
    cell_v_max: float
    t_min_c: float
    t_max_c: float
    max_continuous_current_a: float
    default_test_current_a: float
    dcir_pulse_a: float = 300.0
    dcir_pulse_s: float = 10.0
    min_capacity_ah: float | None = None
    typical_capacity_ah: float | None = None
    # Datasheet temperature-dependent pack cutoffs (optional)
    discharge_cutoff_v_cold: float | None = None  # typically ≤ +30 °C
    discharge_cutoff_v_hot: float | None = None   # typically > +30 °C
    charge_cutoff_v_warm: float | None = None     # typically ≥ +20 °C
    charge_cutoff_v_cold: float | None = None     # typically < +20 °C
    notes: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModuleProfile:
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data and k != "raw"}
        return cls(raw=data, **known)

    def charge_pack_vmax(self, temp_c: float | None) -> float:
        """Pack charge ceiling from temperature-aware datasheet cutoffs.

        Cold-charge limits are governed by the *coldest* cell, so callers
        should pass ``t_min_c``. When temperature is unknown, pick the more
        conservative (lower) ceiling instead of assuming "warm".
        """
        warm = self.charge_cutoff_v_warm
        cold = self.charge_cutoff_v_cold
        if warm is not None and cold is not None:
            if temp_c is None:
                return float(min(warm, cold))
            return float(warm if temp_c >= 20.0 else cold)
        if warm is not None:
            return float(warm)
        if cold is not None:
            return float(cold)
        return float(self.pack_v_max)

    def discharge_pack_vmin(self, t_max_c: float | None) -> float:
        """Pack discharge floor from temperature-aware datasheet cutoffs.

        When temperature is unknown, pick the more conservative (higher)
        floor so an unknown-but-hot pack is not over-discharged.
        """
        cold = self.discharge_cutoff_v_cold
        hot = self.discharge_cutoff_v_hot
        if cold is not None and hot is not None:
            if t_max_c is None:
                return float(max(cold, hot))
            return float(hot if t_max_c > 30.0 else cold)
        if cold is not None:
            return float(cold)
        if hot is not None:
            return float(hot)
        return float(self.pack_v_min)


@dataclass
class ProgramMeta:
    name: str
    module_profile: str
    description: str = ""
    # Applied to power steps when step abort.t_max_c is omitted
    t_max_c: float | None = None


@dataclass
class Step:
    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {"id": self.id, "type": self.type}
        out.update(self.params)
        return out


@dataclass
class Program:
    meta: ProgramMeta
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        meta = {
            "name": self.meta.name,
            "module_profile": self.meta.module_profile,
            "description": self.meta.description,
        }
        if self.meta.t_max_c is not None:
            meta["t_max_c"] = float(self.meta.t_max_c)
        return {
            "meta": meta,
            "steps": [s.to_dict() for s in self.steps],
        }


def load_profile(path: Path) -> ModuleProfile:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ModuleProfile.from_dict(data)


def load_program(path: Path) -> Program:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    meta_raw = data.get("meta") or {}
    meta = ProgramMeta(
        name=str(meta_raw.get("name") or path.stem),
        module_profile=str(meta_raw.get("module_profile") or ""),
        description=str(meta_raw.get("description") or ""),
        t_max_c=float(meta_raw["t_max_c"]) if meta_raw.get("t_max_c") is not None else None,
    )
    steps: list[Step] = []
    for item in data.get("steps") or []:
        item = dict(item)
        sid = str(item.pop("id"))
        stype = str(item.pop("type"))
        steps.append(Step(id=sid, type=stype, params=item))
    return Program(meta=meta, steps=steps)


def save_program(program: Program, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(program.to_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def list_profiles(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.yaml"))


def list_programs(directory: Path) -> list[Path]:
    """Production YAMLs in programs/, then programs/dev/ (smoke/mock only).

    Archived files under programs/_archiv/ are intentionally not globbed.
    """
    root = sorted(directory.glob("*.yaml"))
    dev = sorted((directory / "dev").glob("*.yaml")) if (directory / "dev").is_dir() else []
    return root + dev


def program_label(path: Path) -> str:
    """Display name for a step file (``[DEV]`` prefix for programs/dev/)."""
    return f"[DEV] {path.stem}" if "dev" in path.parts else path.stem


def program_module_of(path: Path) -> str:
    """Read only ``meta.module_profile`` from a step file (cheap, tolerant)."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ""
    meta = data.get("meta") or {}
    return str(meta.get("module_profile") or "")


@dataclass
class ModuleGroup:
    """A module type (category) and the step files that target it."""

    module_id: str
    label: str
    programs: list[tuple[str, Path]] = field(default_factory=list)
    known_profile: bool = True  # False = referenced by a program but no profile file


def list_programs_by_module(programs_dir: Path, profiles_dir: Path) -> list[ModuleGroup]:
    """Group step files by their module type for the two-level picker.

    Every profile becomes a category (even with no step files, so a first step file
    can be created for it). Step files whose ``module_profile`` has no matching profile
    file are collected into trailing ``(neznámý profil: X)`` groups so they stay
    selectable. Ordering: profiles alphabetically, then unknown-profile groups.
    """
    groups: dict[str, ModuleGroup] = {}
    for prof in list_profiles(profiles_dir):
        mid = prof.stem
        groups[mid] = ModuleGroup(module_id=mid, label=mid, known_profile=True)

    unknown: dict[str, ModuleGroup] = {}
    for prog in list_programs(programs_dir):
        mid = program_module_of(prog) or "?"
        entry = (program_label(prog), prog)
        if mid in groups:
            groups[mid].programs.append(entry)
        else:
            grp = unknown.get(mid)
            if grp is None:
                grp = unknown[mid] = ModuleGroup(
                    module_id=mid,
                    label=f"(neznámý profil: {mid})",
                    known_profile=False,
                )
            grp.programs.append(entry)

    ordered = [groups[k] for k in sorted(groups)]
    ordered += [unknown[k] for k in sorted(unknown)]
    return ordered


def clone_profile(src_path: Path, new_id: str, new_name: str, profiles_dir: Path) -> Path:
    """Copy an existing module profile as a template under a new id/name."""
    data = yaml.safe_load(src_path.read_text(encoding="utf-8")) or {}
    data["id"] = new_id
    data["name"] = new_name
    dest = profiles_dir / f"{new_id}.yaml"
    if dest.exists():
        raise FileExistsError(f"Profil už existuje: {dest}")
    profiles_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return dest


def archive_file(path: Path, archive_dir: Path) -> Path:
    """Move a YAML aside into ``_archiv/`` (recoverable delete). Returns new path."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / path.name
    n = 1
    while dest.exists():
        dest = archive_dir / f"{path.stem}_{n}{path.suffix}"
        n += 1
    shutil.move(str(path), str(dest))
    return dest
