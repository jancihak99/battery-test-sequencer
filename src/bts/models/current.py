"""Resolve charge/discharge/DCIR current from amps or C-rate × profile Ah."""
from __future__ import annotations

from typing import Any

from bts.models.program import ModuleProfile


def resolve_amps(
    params: dict[str, Any],
    profile: ModuleProfile,
    *,
    amp_key: str = "current_a",
    crate_key: str = "c_rate",
    default: float | None = None,
) -> float:
    """Prefer ``c_rate`` × nominal Ah when set; else absolute amps."""
    if params.get(crate_key) is not None and str(params.get(crate_key)).strip() != "":
        crate = float(params[crate_key])
        if crate <= 0:
            raise ValueError(f"{crate_key} must be > 0")
        ah = float(profile.nominal_capacity_ah)
        if ah <= 0:
            raise ValueError("profile.nominal_capacity_ah must be > 0 for C-rate")
        return crate * ah
    if params.get(amp_key) is not None and str(params.get(amp_key)).strip() != "":
        return float(params[amp_key])
    if default is not None:
        return float(default)
    return float(profile.default_test_current_a)


def format_amps_with_crate(amps: float, profile: ModuleProfile) -> str:
    ah = float(profile.nominal_capacity_ah) or 1.0
    crate = amps / ah
    return f"{amps:.1f} A ({crate:.2f}C @ {ah:.0f} Ah)"
