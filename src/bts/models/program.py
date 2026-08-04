from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


STEP_TYPES = (
    "bms_ready",
    "bms_idle",
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

    def charge_pack_vmax(self, t_max_c: float | None) -> float:
        """Pack charge ceiling from temperature-aware datasheet cutoffs."""
        warm = self.charge_cutoff_v_warm
        cold = self.charge_cutoff_v_cold
        if t_max_c is not None and warm is not None and cold is not None:
            return float(warm if t_max_c >= 20.0 else cold)
        if warm is not None:
            return float(warm)
        if cold is not None:
            return float(cold)
        return float(self.pack_v_max)

    def discharge_pack_vmin(self, t_max_c: float | None) -> float:
        """Pack discharge floor from temperature-aware datasheet cutoffs."""
        cold = self.discharge_cutoff_v_cold
        hot = self.discharge_cutoff_v_hot
        if t_max_c is not None and cold is not None and hot is not None:
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
        return {
            "meta": {
                "name": self.meta.name,
                "module_profile": self.meta.module_profile,
                "description": self.meta.description,
            },
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
    """Production YAMLs in programs/, then programs/dev/ (smoke/mock only)."""
    root = sorted(directory.glob("*.yaml"))
    dev = sorted((directory / "dev").glob("*.yaml")) if (directory / "dev").is_dir() else []
    return root + dev
