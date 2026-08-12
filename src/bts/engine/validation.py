from __future__ import annotations

from typing import Any

from bts.models.program import STEP_TYPES, ModuleProfile, Program, Step

REPORT_INCLUDE = ("capacity_ah", "dcir_mohm", "cells", "temps", "dtc")


def _report_include_ok(item: str) -> bool:
    return item in REPORT_INCLUDE or item.startswith("capacity_")


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pos(v: Any) -> bool:
    n = _num(v)
    return n is not None and n > 0


def validate_step(step: Step, profile: ModuleProfile, max_el_a: float) -> list[str]:
    """Return list of errors for one step (empty = OK)."""
    e: list[str] = []
    sid = step.id or "<no-id>"
    p = step.params or {}

    if not step.id or not str(step.id).strip():
        e.append("Step missing id")
    if step.type not in STEP_TYPES:
        e.append(f"{sid}: unknown type '{step.type}'")
        return e

    t = step.type

    if t in ("bms_ready", "bms_idle"):
        if not _pos(p.get("timeout_s")):
            e.append(f"{sid}: timeout_s must be > 0")

    elif t == "wait_time":
        sec = p.get("seconds", p.get("timeout_s"))
        if not _pos(sec):
            e.append(f"{sid}: seconds must be > 0 (set duration for wait_time)")

    elif t == "wait_temp":
        has_max = p.get("t_max_c") is not None
        has_min = p.get("t_min_c") is not None
        if not has_max and not has_min:
            e.append(f"{sid}: wait_temp needs t_max_c and/or t_min_c")
        if has_max and _num(p.get("t_max_c")) is None:
            e.append(f"{sid}: t_max_c invalid")
        if has_min and _num(p.get("t_min_c")) is None:
            e.append(f"{sid}: t_min_c invalid")
        if has_max and has_min:
            tmax, tmin = _num(p["t_max_c"]), _num(p["t_min_c"])
            if tmax is not None and tmin is not None and tmin > tmax:
                e.append(f"{sid}: t_min_c > t_max_c")
        if not _pos(p.get("timeout_s")):
            e.append(f"{sid}: timeout_s must be > 0")

    elif t == "charge":
        has_crate = p.get("c_rate") is not None and str(p.get("c_rate")).strip() != ""
        i = _num(p.get("current_a"))
        crate = _num(p.get("c_rate")) if has_crate else None
        if has_crate:
            if crate is None or crate <= 0:
                e.append(f"{sid}: c_rate must be > 0")
            else:
                i = crate * float(profile.nominal_capacity_ah)
        if i is None or i <= 0:
            e.append(f"{sid}: current_a or c_rate must be > 0")
        elif i > profile.max_continuous_current_a:
            e.append(f"{sid}: current {i:.1f} A > profile max {profile.max_continuous_current_a} A")
        v = _num(p.get("voltage_v"))
        if v is None or v <= 0:
            e.append(f"{sid}: voltage_v must be > 0")
        elif v > profile.pack_v_max + 0.5:
            e.append(f"{sid}: voltage_v {v} far above pack_v_max {profile.pack_v_max}")
        stop = p.get("stop") or {}
        if not isinstance(stop, dict):
            e.append(f"{sid}: stop must be a mapping")
            stop = {}
        stop_ok = any(
            k in stop and _num(stop.get(k)) is not None
            for k in ("pack_v_max", "cell_v_max", "soc_pct", "ah_target", "i_min_a")
        )
        if not stop_ok:
            e.append(
                f"{sid}: charge needs at least one stop condition "
                "(pack_v_max / cell_v_max / soc_pct / ah_target / i_min_a)"
            )
        _validate_stop_abort(sid, stop, p.get("abort"), profile, e, mode="charge")
        to = p.get("timeout_s") or stop.get("timeout_s")
        if to is not None and not _pos(to):
            e.append(f"{sid}: timeout_s must be > 0 when set")

    elif t == "discharge":
        has_crate = p.get("c_rate") is not None and str(p.get("c_rate")).strip() != ""
        i = _num(p.get("current_a"))
        crate = _num(p.get("c_rate")) if has_crate else None
        if has_crate:
            if crate is None or crate <= 0:
                e.append(f"{sid}: c_rate must be > 0")
            else:
                i = crate * float(profile.nominal_capacity_ah)
        if i is None or i <= 0:
            e.append(f"{sid}: current_a or c_rate must be > 0")
        elif i > profile.max_continuous_current_a:
            e.append(f"{sid}: current {i:.1f} A > profile max {profile.max_continuous_current_a} A")
        if i is not None and i > max_el_a:
            e.append(f"{sid}: current {i:.1f} A > EL master limit {max_el_a} A")
        stop = p.get("stop") or {}
        if not isinstance(stop, dict):
            e.append(f"{sid}: stop must be a mapping")
            stop = {}
        stop_ok = any(
            k in stop and _num(stop.get(k)) is not None
            for k in ("pack_v_min", "cell_v_min", "soc_pct", "ah_target")
        )
        if not stop_ok:
            e.append(
                f"{sid}: discharge needs at least one stop condition "
                "(pack_v_min / cell_v_min / soc_pct / ah_target)"
            )
        _validate_stop_abort(sid, stop, p.get("abort"), profile, e, mode="discharge")
        to = p.get("timeout_s") or stop.get("timeout_s")
        if to is not None and not _pos(to):
            e.append(f"{sid}: timeout_s must be > 0 when set")
        meas = p.get("measure")
        if meas is not None and not (meas == "capacity_ah" or str(meas).startswith("capacity_")):
            e.append(f"{sid}: measure must be capacity_ah or capacity_* key")

    elif t == "goto_soc":
        soc = _num(p.get("soc_pct"))
        if soc is None or soc < 0 or soc > 100:
            e.append(f"{sid}: goto_soc needs soc_pct 0..100")
        has_crate = p.get("c_rate") is not None and str(p.get("c_rate")).strip() != ""
        i = _num(p.get("current_a"))
        if has_crate:
            crate = _num(p.get("c_rate"))
            if crate is None or crate <= 0:
                e.append(f"{sid}: c_rate must be > 0")
            else:
                i = crate * float(profile.nominal_capacity_ah)
        if i is None or i <= 0:
            e.append(f"{sid}: current_a or c_rate must be > 0")
        elif i > profile.max_continuous_current_a:
            e.append(f"{sid}: current {i:.1f} A > profile max {profile.max_continuous_current_a} A")
        if i is not None and i > max_el_a:
            e.append(f"{sid}: current {i:.1f} A > EL limit {max_el_a} A (discharge path)")
        _validate_stop_abort(sid, p.get("stop") or {}, p.get("abort"), profile, e, mode="charge")

    elif t == "dcir":
        has_crate = p.get("c_rate") is not None and str(p.get("c_rate")).strip() != ""
        i = _num(p.get("pulse_a"))
        if has_crate:
            crate = _num(p.get("c_rate"))
            if crate is None or crate <= 0:
                e.append(f"{sid}: c_rate must be > 0")
            else:
                i = crate * float(profile.nominal_capacity_ah)
        if i is None or i <= 0:
            e.append(f"{sid}: pulse_a or c_rate must be > 0")
        elif i > max_el_a:
            e.append(f"{sid}: pulse {i:.1f} A > EL master limit {max_el_a} A")
        ps = _num(p.get("pulse_s"))
        if ps is None or ps <= 0:
            e.append(f"{sid}: pulse_s must be > 0")
        elif ps > 60:
            e.append(f"{sid}: pulse_s {ps}s looks too long for DCIR")

    elif t == "notify":
        if not str(p.get("message") or "").strip():
            e.append(f"{sid}: notify needs a non-empty message")

    elif t == "report":
        include = p.get("include")
        if include is not None:
            if not isinstance(include, list) or not include:
                e.append(f"{sid}: include must be a non-empty list")
            else:
                for item in include:
                    if not _report_include_ok(str(item)):
                        e.append(f"{sid}: unknown report include '{item}'")

    return e


def _validate_stop_abort(
    sid: str,
    stop: dict[str, Any],
    abort: Any,
    profile: ModuleProfile,
    e: list[str],
    mode: str,
) -> None:
    for key, val in stop.items():
        if key == "timeout_s":
            continue
        if _num(val) is None:
            e.append(f"{sid}: stop.{key} is not a number")
    if "pack_v_max" in stop:
        v = _num(stop["pack_v_max"])
        if v is not None and v > profile.pack_v_max:
            e.append(f"{sid}: stop.pack_v_max {v} above profile pack_v_max {profile.pack_v_max} — BMU may disconnect")
    if "pack_v_min" in stop:
        v = _num(stop["pack_v_min"])
        if v is not None and v < profile.pack_v_min:
            e.append(f"{sid}: stop.pack_v_min {v} below profile pack_v_min {profile.pack_v_min} — BMU may disconnect")
    if "cell_v_max" in stop:
        v = _num(stop["cell_v_max"])
        if v is not None and v > profile.cell_v_max:
            e.append(f"{sid}: stop.cell_v_max {v} above profile {profile.cell_v_max} — BMU WILL disconnect")
        # Exact profile limit is OK for capacity CC-to-cutoff; engine abort still has ~10 mV margin.
    if "cell_v_min" in stop:
        v = _num(stop["cell_v_min"])
        if v is not None and v < profile.cell_v_min:
            e.append(f"{sid}: stop.cell_v_min {v} below profile {profile.cell_v_min} — BMU WILL disconnect")
        # Exact profile floor is OK for capacity discharge; hard abort keeps a small margin.
    if "soc_pct" in stop:
        v = _num(stop["soc_pct"])
        if v is None or v < 0 or v > 100:
            e.append(f"{sid}: stop.soc_pct must be 0..100")

    if abort is None:
        return
    if not isinstance(abort, dict):
        e.append(f"{sid}: abort must be a mapping")
        return
    for key, val in abort.items():
        if _num(val) is None:
            e.append(f"{sid}: abort.{key} is not a number")
    if "dtc_level_max" in abort:
        d = _num(abort["dtc_level_max"])
        if d is None or d < 0 or d > 4:
            e.append(f"{sid}: abort.dtc_level_max must be 0..4")
    if "t_max_c" in abort:
        tm = _num(abort["t_max_c"])
        prof_tmax = getattr(profile, "t_max_c", None)
        if tm is not None and prof_tmax is not None and tm > float(prof_tmax):
            e.append(
                f"{sid}: abort.t_max_c {tm} above profile t_max_c {prof_tmax} "
                "— thermal abort would exceed the module rating"
            )


def validate_program(program: Program, profile: ModuleProfile, max_el_combined_a: float = 1020.0) -> list[str]:
    errors: list[str] = []
    if not program.meta.name or not str(program.meta.name).strip():
        errors.append("meta.name is required")
    if not program.meta.module_profile:
        errors.append("meta.module_profile is required")
    elif program.meta.module_profile != profile.id and profile.id:
        # soft mismatch warning as error — profile file should match
        if program.meta.module_profile != getattr(profile, "id", ""):
            errors.append(
                f"meta.module_profile '{program.meta.module_profile}' does not match loaded profile '{profile.id}'"
            )
    if not program.steps:
        errors.append("program has no steps")

    ids: set[str] = set()
    types_seen: set[str] = set()
    for step in program.steps:
        if step.id in ids:
            errors.append(f"Duplicate step id: {step.id}")
        ids.add(step.id)
        types_seen.add(step.type)
        errors.extend(validate_step(step, profile, max_el_combined_a))

    # Structural sanity for capacity-style programs
    if "charge" in types_seen or "discharge" in types_seen:
        if "bms_ready" not in types_seen:
            errors.append("Programs with charge/discharge should include a bms_ready step")
    # bms_idle optional: engine post-run safe_shutdown opens contactors if still READY

    # Source vs load must never run in the same step (engine also enforces mutex at runtime)
    for step in program.steps:
        if step.type == "charge" and step.params.get("also_discharge"):
            errors.append(f"{step.id}: cannot charge and discharge together")
        if step.type not in ("charge", "discharge", "dcir"):
            continue
        # dcir uses load only; charge uses source only — OK as separate steps

    return errors
