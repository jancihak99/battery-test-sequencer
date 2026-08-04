"""Mock scenario: EL master/slave slave-drop → EXT FAIL + safe shutdown."""
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
    program = load_program(ROOT / "programs" / "dev" / "Fail_EL_slave_mock.yaml")
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
        ea_i_mismatch_confirm_s=1.0,
        serial_number="FAIL-EL-SLAVE",
    )
    engine.start()
    t0 = time.time()
    while True:
        st = engine.status()
        line = f"[{st.run_state.value}] step={st.current_step_id} msg={st.message}"
        print(line.encode("ascii", "replace").decode("ascii"))
        if st.run_state in (RunState.COMPLETED, RunState.FAILED, RunState.ABORTED):
            break
        if time.time() - t0 > 120:
            print("TIMEOUT")
            engine.abort()
            time.sleep(2)
            return 2
        time.sleep(0.4)

    st = engine.status()
    msg = st.message or ""
    ok = st.run_state == RunState.FAILED and (
        "EXT FAIL" in msg or "master/slave" in msg or "half" in msg
    )
    print(("final_msg=" + msg).encode("ascii", "replace").decode("ascii"))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
