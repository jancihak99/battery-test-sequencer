"""Offline stepfile preview — accelerated pack U/I simulation (no hardware)."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Literal

from bts.models.current import resolve_amps
from bts.models.program import ModuleProfile, Program, Step

PhaseKind = Literal["idle", "charge", "discharge", "wait", "pulse", "done"]


@dataclass
class SimSample:
    t_s: float  # simulated time from start
    voltage_v: float
    current_a: float
    soc_pct: float
    temp_c: float
    step_id: str
    step_type: str
    phase: PhaseKind


@dataclass
class SimState:
    t_s: float = 0.0
    soc_pct: float = 50.0
    voltage_v: float = 24.0
    current_a: float = 0.0
    temp_c: float = 28.0
    ah_step: float = 0.0
    step_index: int = 0
    step_id: str = ""
    step_type: str = ""
    phase: PhaseKind = "idle"
    finished: bool = False
    message: str = ""
    samples: list[SimSample] = field(default_factory=list)


class StepfileSimulator:
    """Walk a program with time-scaled pack physics for UI preview graphs."""

    def __init__(
        self,
        program: Program,
        profile: ModuleProfile,
        *,
        speed: float = 60.0,
        r_internal_ohm: float = 0.008,
    ) -> None:
        self.program = program
        self.profile = profile
        self.speed = max(1.0, float(speed))
        self.r_internal = r_internal_ohm
        self.state = SimState()
        self._step_t0 = 0.0
        self._pulse_left = 0.0
        self._cap_ah = float(
            profile.typical_capacity_ah or profile.nominal_capacity_ah or 70.0
        )
        self.reset(randomize=True)

    def reset(self, *, randomize: bool = True) -> None:
        soc = random.uniform(22.0, 78.0) if randomize else 50.0
        temp = random.uniform(24.0, 38.0) if randomize else 28.0
        self.state = SimState(
            soc_pct=soc,
            temp_c=temp,
            voltage_v=self._ocv(soc),
            message="Ready — random start state" if randomize else "Ready",
        )
        self._step_t0 = 0.0
        self._pulse_left = 0.0
        self._enter_step(0)
        self._push_sample()

    def set_speed(self, speed: float) -> None:
        self.speed = max(1.0, float(speed))

    def _ocv(self, soc: float) -> float:
        lo, hi = self.profile.pack_v_min, self.profile.pack_v_max
        s = max(0.0, min(1.0, soc / 100.0))
        # Mild curve so mid-SOC is not perfectly linear
        s_eff = 0.08 + 0.84 * (0.5 - 0.5 * math.cos(math.pi * s))
        return lo + s_eff * (hi - lo)

    def _terminal_v(self, soc: float, current_a: float) -> float:
        return self._ocv(soc) + current_a * self.r_internal

    def _enter_step(self, idx: int) -> None:
        steps = self.program.steps
        if idx >= len(steps):
            self.state.finished = True
            self.state.phase = "done"
            self.state.current_a = 0.0
            self.state.voltage_v = self._ocv(self.state.soc_pct)
            self.state.step_id = ""
            self.state.step_type = ""
            self.state.message = "Simulation complete"
            return
        step = steps[idx]
        self.state.step_index = idx
        self.state.step_id = step.id
        self.state.step_type = step.type
        self.state.ah_step = 0.0
        self._step_t0 = self.state.t_s
        self._pulse_left = 0.0
        self.state.phase = self._phase_for(step)
        self.state.message = f"Step {step.id}: {step.type}"
        if step.type == "dcir":
            pulse = float(step.params.get("pulse_s", self.profile.dcir_pulse_s))
            self._pulse_left = pulse

    @staticmethod
    def _phase_for(step: Step) -> PhaseKind:
        t = step.type
        if t == "charge":
            return "charge"
        if t == "discharge":
            return "discharge"
        if t == "dcir":
            return "pulse"
        if t in ("wait_time", "wait_temp"):
            return "wait"
        return "idle"

    def tick(self, wall_dt: float) -> SimState:
        """Advance by wall_dt seconds of real time (scaled by speed)."""
        if self.state.finished or not self.program.steps:
            return self.state
        # Soft cap so the graph still draws visibly at high speed
        sim_dt = min(max(0.0, wall_dt) * self.speed, 8.0)
        self._advance_sim(sim_dt, sample_every=0.35)
        return self.state

    def run_all(self, *, sample_every: float = 1.0, max_sim_s: float = 48 * 3600) -> SimState:
        """Compute the entire stepfile trajectory at once (no realtime playback)."""
        if not self.program.steps:
            self.state.finished = True
            self.state.message = "No steps"
            return self.state
        # Keep going until finished or safety cap
        while not self.state.finished and self.state.t_s < max_sim_s:
            # Large chunks; sampling handled inside
            self._advance_sim(30.0, sample_every=sample_every)
        if not self.state.finished:
            self.state.message = "Stopped — simulation exceeded time cap"
            self.state.finished = True
            self.state.phase = "done"
            self.state.current_a = 0.0
            self._push_sample()
        else:
            self.state.message = (
                f"Full preview · {len(self.program.steps)} steps · "
                f"{self.state.t_s / 3600.0:.2f} h · end SOC {self.state.soc_pct:.0f}%"
            )
        return self.state

    def _advance_sim(self, sim_dt: float, *, sample_every: float) -> None:
        since_sample = 0.0
        remain = max(0.0, sim_dt)
        while remain > 1e-9 and not self.state.finished:
            chunk = min(remain, 0.25)
            self._advance(chunk)
            remain -= chunk
            since_sample += chunk
            if since_sample >= sample_every:
                self._push_sample()
                since_sample = 0.0
        self._push_sample()

    def _advance(self, dt: float) -> None:
        if self.state.finished:
            return
        steps = self.program.steps
        if self.state.step_index < 0 or self.state.step_index >= len(steps):
            self.state.finished = True
            return
        step = steps[self.state.step_index]
        done = self._advance_step(step, dt)
        self.state.t_s += dt
        if done:
            self._enter_step(self.state.step_index + 1)

    def _advance_step(self, step: Step, dt: float) -> bool:
        p = step.params or {}
        t = step.type
        elapsed = self.state.t_s - self._step_t0 + dt

        if t in ("notify", "report"):
            self.state.current_a = 0.0
            self.state.voltage_v = self._ocv(self.state.soc_pct)
            return elapsed >= 1.0

        if t in ("bms_ready", "bms_idle"):
            self.state.current_a = 0.0
            self.state.voltage_v = self._ocv(self.state.soc_pct)
            # Short handshake preview (+ settle after READY / contactors)
            hand = min(8.0, float(p.get("timeout_s", 30)) * 0.15)
            if t == "bms_ready":
                hand += float(p.get("settle_s", 10.0))
            return elapsed >= hand

        if t == "wait_time":
            self.state.current_a = 0.0
            self.state.voltage_v = self._ocv(self.state.soc_pct)
            self.state.temp_c = max(22.0, self.state.temp_c - 0.002 * dt)
            return elapsed >= float(p.get("seconds", p.get("timeout_s", 1)))

        if t == "wait_temp":
            self.state.current_a = 0.0
            self.state.voltage_v = self._ocv(self.state.soc_pct)
            # Cool toward ambient
            self.state.temp_c += (24.0 - self.state.temp_c) * min(1.0, 0.02 * dt)
            timeout = float(p.get("timeout_s", 7200))
            t_max = p.get("t_max_c")
            t_min = p.get("t_min_c")
            ok = True
            if t_max is not None and self.state.temp_c > float(t_max):
                ok = False
            if t_min is not None and self.state.temp_c < float(t_min):
                ok = False
            return ok or elapsed >= timeout

        if t == "dcir":
            try:
                pulse_a = abs(
                    resolve_amps(
                        p,
                        self.profile,
                        amp_key="pulse_a",
                        default=self.profile.dcir_pulse_a,
                    )
                )
            except Exception:
                pulse_a = abs(float(p.get("pulse_a", self.profile.dcir_pulse_a)))
            if self._pulse_left > 0:
                use = min(dt, self._pulse_left)
                self.state.current_a = -abs(pulse_a)
                self.state.voltage_v = self._terminal_v(self.state.soc_pct, self.state.current_a)
                self._pulse_left -= use
                # tiny Ah during pulse
                dah = abs(self.state.current_a) * use / 3600.0
                self.state.soc_pct = max(0.0, self.state.soc_pct - dah / self._cap_ah * 100.0)
                return self._pulse_left <= 0 and elapsed >= float(p.get("pulse_s", 10)) + 1.0
            self.state.current_a = 0.0
            self.state.voltage_v = self._ocv(self.state.soc_pct)
            return elapsed >= float(p.get("pulse_s", 10)) + 1.5

        if t == "charge":
            return self._power_step(step, dt, elapsed, mode="charge")

        if t == "discharge":
            return self._power_step(step, dt, elapsed, mode="discharge")

        self.state.current_a = 0.0
        self.state.voltage_v = self._ocv(self.state.soc_pct)
        return elapsed >= 1.0

    def _power_step(self, step: Step, dt: float, elapsed: float, *, mode: str) -> bool:
        p = step.params or {}
        stop = p.get("stop") or {}
        try:
            current = abs(
                resolve_amps(
                    p,
                    self.profile,
                    amp_key="current_a",
                    default=self.profile.default_test_current_a,
                )
            )
        except Exception:
            current = abs(float(p.get("current_a", self.profile.default_test_current_a)))
        current = max(0.1, current)
        timeout = float(stop.get("timeout_s", p.get("timeout_s", 8 * 3600)))

        if mode == "charge":
            i = current
            # CV taper near pack_v_max / voltage setpoint
            v_set = float(p.get("voltage_v", self.profile.pack_v_max))
            if self.state.voltage_v >= v_set - 0.15:
                i = max(current * 0.15, current * (v_set - self.state.voltage_v) / 0.3)
        else:
            i = -current

        self.state.current_a = i
        dah = i * dt / 3600.0
        self.state.ah_step += abs(dah)
        self.state.soc_pct = max(0.0, min(100.0, self.state.soc_pct + dah / self._cap_ah * 100.0))
        self.state.voltage_v = self._terminal_v(self.state.soc_pct, i)
        # Heating ~ I²
        self.state.temp_c += (abs(i) / 500.0) ** 2 * 0.8 * dt
        self.state.temp_c += (26.0 - self.state.temp_c) * 0.001 * dt

        if elapsed >= timeout:
            return True
        if "ah_target" in stop and self.state.ah_step >= float(stop["ah_target"]):
            return True
        if mode == "charge":
            if "soc_pct" in stop and self.state.soc_pct >= float(stop["soc_pct"]):
                return True
            # With i_min_a, Vmax alone does not end (mirrors live engine CV hold)
            if "i_min_a" not in stop:
                if "pack_v_max" in stop and self.state.voltage_v >= float(stop["pack_v_max"]):
                    return True
                if "cell_v_max" in stop:
                    approx = float(stop["cell_v_max"]) * 10
                    if self.state.voltage_v >= approx:
                        return True
            if "i_min_a" in stop and self.state.voltage_v >= v_set - 0.15:
                if abs(i) <= float(stop["i_min_a"]):
                    return True
            if self.state.soc_pct >= 99.5:
                return True
        else:
            if "soc_pct" in stop and self.state.soc_pct <= float(stop["soc_pct"]):
                return True
            if "pack_v_min" in stop and self.state.voltage_v <= float(stop["pack_v_min"]):
                return True
            if "cell_v_min" in stop:
                approx = float(stop["cell_v_min"]) * 10
                if self.state.voltage_v <= approx:
                    return True
            if self.state.soc_pct <= 0.5:
                return True
        return False

    def _push_sample(self) -> None:
        s = self.state
        s.samples.append(
            SimSample(
                t_s=s.t_s,
                voltage_v=s.voltage_v,
                current_a=s.current_a,
                soc_pct=s.soc_pct,
                temp_c=s.temp_c,
                step_id=s.step_id,
                step_type=s.step_type,
                phase=s.phase,
            )
        )
        # Keep chart memory bounded
        if len(s.samples) > 8000:
            s.samples = s.samples[-6000:]
