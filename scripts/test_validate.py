"""Validation unit checks."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bts.engine.validation import validate_program, validate_step
from bts.models.program import Step, load_profile, load_program, Program, ProgramMeta


def test_wait_time_requires_seconds():
    profile = load_profile(ROOT / "profiles" / "LTO_24V_70Ah.yaml")
    bad = Step(id="w", type="wait_time", params={})
    errs = validate_step(bad, profile, 1020)
    assert any("seconds" in e for e in errs), errs
    good = Step(id="w", type="wait_time", params={"seconds": 30})
    assert not validate_step(good, profile, 1020)


def test_charge_requires_stop():
    profile = load_profile(ROOT / "profiles" / "LTO_24V_70Ah.yaml")
    bad = Step(id="c", type="charge", params={"current_a": 70, "voltage_v": 27.5})
    errs = validate_step(bad, profile, 1020)
    assert any("stop" in e for e in errs), errs


def test_bundled_programs_ok():
    profile70 = load_profile(ROOT / "profiles" / "LTO_24V_70Ah.yaml")
    profile60 = load_profile(ROOT / "profiles" / "LTO_24V_60Ah.yaml")
    for name, profile in [
        ("Capacity_70Ah_claim.yaml", profile70),
        ("Capacity_60Ah_second_life.yaml", profile60),
        ("dev/Quick_smoke_mock.yaml", profile70),
        ("dev/Fail_EL_slave_mock.yaml", profile70),
    ]:
        prog = load_program(ROOT / "programs" / name)
        errs = validate_program(prog, profile)
        assert not errs, (name, errs)


def test_empty_program_fails():
    profile = load_profile(ROOT / "profiles" / "LTO_24V_70Ah.yaml")
    prog = Program(meta=ProgramMeta(name="x", module_profile="LTO_24V_70Ah"), steps=[])
    errs = validate_program(prog, profile)
    assert any("no steps" in e for e in errs)


if __name__ == "__main__":
    test_wait_time_requires_seconds()
    test_charge_requires_stop()
    test_bundled_programs_ok()
    test_empty_program_fails()
    print("OK")
