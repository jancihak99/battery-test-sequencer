"""Offline smoke test: load program, run mock sequence to completion."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bts.drivers import create_bms_driver, create_ea_driver
from bts.drivers.bms import MockBmsDriver
from bts.engine import SequenceEngine, RunState, validate_program
from bts.models.config import load_config
from bts.models.program import load_profile, load_program


def main() -> int:
    cfg = load_config(root=ROOT)
    program = load_program(ROOT / "programs" / "dev" / "Quick_smoke_mock.yaml")
    profile = load_profile(ROOT / "profiles" / "LTO_24V_70Ah.yaml")
    errs = validate_program(program, profile)
    if errs:
        print("VALIDATION FAILED:", errs)
        return 1

    bms = create_bms_driver(cfg.bms, use_mock=True)
    assert isinstance(bms, MockBmsDriver)
    ea = create_ea_driver(cfg.ea, use_mock=True, mock_bms=bms)

    engine = SequenceEngine(
        bms=bms,
        ea=ea,
        profile=profile,
        program=program,
        runs_dir=ROOT / "runs",
        poll_period_s=0.1,
        can_timeout_s=5.0,
        ea_timeout_s=5.0,
        serial_number="SMOKE-001",
    )
    engine.start()
    t0 = time.time()
    while True:
        st = engine.status()
        print(f"[{st.run_state.value}] step={st.current_step_id} msg={st.message}")
        if st.run_state in (RunState.COMPLETED, RunState.FAILED, RunState.ABORTED):
            break
        if time.time() - t0 > 180:
            print("TIMEOUT")
            engine.abort()
            time.sleep(1)
            return 2
        time.sleep(0.5)

    st = engine.status()
    print("capacity_ah=", st.measurements.capacity_ah)
    print("dcir_mohm=", st.measurements.dcir_mohm)
    print("report=", st.report_path)
    print("log=", st.log_path)
    ok = st.run_state == RunState.COMPLETED and st.measurements.capacity_ah is not None
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
