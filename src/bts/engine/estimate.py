"""Estimate stepfile duration ahead of a run (best-effort)."""
from __future__ import annotations

from dataclasses import dataclass

from bts.models.current import resolve_amps
from bts.models.program import ModuleProfile, Program, Step


@dataclass(frozen=True)
class StepEstimate:
    step_id: str
    step_type: str
    seconds: float
    uncertain: bool = False
    note: str = ""


@dataclass(frozen=True)
class ProgramEstimate:
    steps: list[StepEstimate]

    @property
    def total_seconds(self) -> float:
        return sum(s.seconds for s in self.steps)

    @property
    def any_uncertain(self) -> bool:
        return any(s.uncertain for s in self.steps)

    def completed_seconds(self, step_index: int) -> float:
        if step_index <= 0:
            return 0.0
        return sum(s.seconds for s in self.steps[:step_index])


def _capacity_ah(profile: ModuleProfile | None) -> float:
    if profile is None:
        return 70.0
    return float(
        profile.typical_capacity_ah
        or profile.nominal_capacity_ah
        or 70.0
    )


def _resolve_step_amps(step: Step, profile: ModuleProfile | None, *, amp_key: str) -> float:
    p = step.params or {}
    default = 70.0
    if profile is not None:
        if amp_key == "pulse_a":
            default = float(profile.dcir_pulse_a)
        else:
            default = float(profile.default_test_current_a)
    if profile is None:
        # No profile — fall back to current_a / pulse_a / default
        raw = p.get(amp_key) or p.get("current_a")
        return max(0.1, abs(float(raw if raw is not None else default)))
    try:
        return max(0.1, abs(resolve_amps(p, profile, amp_key=amp_key, default=default)))
    except Exception:
        return max(0.1, abs(float(p.get(amp_key) or default)))


def estimate_step(step: Step, profile: ModuleProfile | None = None) -> StepEstimate:
    t = step.type
    p = step.params or {}
    cap = _capacity_ah(profile)

    if t == "wait_time":
        sec = float(p.get("seconds", p.get("timeout_s", 1)))
        return StepEstimate(step.id, t, max(0.0, sec), note="fixed")

    if t in ("bms_ready", "bms_idle"):
        # Handshake is usually quick; timeout is only a ceiling.
        timeout = float(p.get("timeout_s", 60 if t == "bms_ready" else 30))
        est = min(15.0, max(5.0, timeout * 0.25))
        if t == "bms_ready":
            # Engine always dwells after contactors close (default 10 s).
            est += float(p.get("settle_s", 10.0))
        return StepEstimate(step.id, t, est, uncertain=True, note="handshake")

    if t == "wait_temp":
        timeout = float(p.get("timeout_s", 7200))
        # Cooling time varies a lot — use a modest fraction of timeout.
        est = min(timeout, max(120.0, timeout * 0.15))
        return StepEstimate(step.id, t, est, uncertain=True, note="cooling")

    if t in ("charge", "discharge", "goto_soc"):
        current = _resolve_step_amps(step, profile, amp_key="current_a")
        stop = p.get("stop") or {}
        timeout = float(stop.get("timeout_s", p.get("timeout_s", 8 * 3600)))

        if t == "goto_soc":
            # Unknown start SOC — assume up to half pack at resolved C-rate/A.
            ah = cap * 0.5
            est = ah / current * 3600.0
            return StepEstimate(
                step.id, t, min(est, timeout), uncertain=True, note="goto_soc @ I"
            )

        if "ah_target" in stop:
            ah = float(stop["ah_target"])
            est = ah / current * 3600.0
            return StepEstimate(step.id, t, min(est, timeout), note="Ah target")

        if "soc_pct" in stop:
            # Unknown starting SOC — assume up to ~half pack as a mid estimate.
            ah = cap * 0.5
            est = ah / current * 3600.0
            return StepEstimate(step.id, t, min(est, timeout), uncertain=True, note="SOC")

        # Full claim charge / capacity discharge: ~nominal Ah at set current
        est = cap / current * 3600.0
        # Prefer estimate over huge default timeout, but never exceed timeout.
        return StepEstimate(
            step.id,
            t,
            min(est, timeout),
            uncertain=True,
            note="capacity @ I",
        )

    if t == "dcir":
        pulse = float(p.get("pulse_s", profile.dcir_pulse_s if profile else 10.0))
        return StepEstimate(step.id, t, pulse + 2.0, note="pulse")

    if t in ("report", "notify"):
        return StepEstimate(step.id, t, 1.0, note="instant")

    return StepEstimate(step.id, t, 5.0, uncertain=True, note="default")


def estimate_program(
    program: Program, profile: ModuleProfile | None = None
) -> ProgramEstimate:
    return ProgramEstimate(
        steps=[estimate_step(s, profile) for s in program.steps]
    )


def format_duration(seconds: float | None, *, approx: bool = False) -> str:
    if seconds is None or seconds < 0:
        return "—"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h > 0:
        body = f"{h}:{m:02d}:{sec:02d}"
    else:
        body = f"{m}:{sec:02d}"
    return f"~{body}" if approx else body
