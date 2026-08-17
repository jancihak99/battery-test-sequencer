"""Unit checks for the live-telemetry CSV logger."""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bts.csv_logger import (
    FORMAT_SERVICE,
    FORMAT_STANDARD,
    CsvTelemetryLogger,
    available_parameters,
    available_signals,
    _fmt_value,
)
from bts.models.telemetry import BmsTelemetry, BmuState


class _FakeDriver:
    def __init__(self, tel: BmsTelemetry) -> None:
        self._t = tel

    def telemetry(self) -> BmsTelemetry:
        return self._t


def _sample() -> BmsTelemetry:
    return BmsTelemetry(
        operating_state=BmuState.READY,
        pack_voltage_v=24.5,
        pack_current_a=-3.25,
        soc_pct=50.0,
        soh_pct=None,  # missing -> empty cell
        cell_voltages=[2.16, 2.16, float("nan")],  # NaN -> empty cell
        temperatures_c=[20.5, 21.0],
    )


def _pick(tel, keys):
    return [s for s in available_signals(tel) if s.key in keys]


def test_fmt_value():
    assert _fmt_value(24.5, 2, True) == "24,50"
    assert _fmt_value(24.5, 2, False) == "24.50"
    assert _fmt_value(3, 0, True) == "3"
    assert _fmt_value(2.16, 4, True) == "2,1600"
    assert _fmt_value(None, 3, True) == ""
    assert _fmt_value(float("nan"), 4, True) == ""
    assert _fmt_value(1.9, 0, False) == "2"  # rounds to int


def test_available_signals_arrays():
    tel = _sample()
    keys = {s.key for s in available_signals(tel)}
    assert "pack_volt" in keys and "pack_power_kw" in keys and "mains_closed" in keys
    assert "cell_v[1]" in keys and "cell_v[3]" in keys and "cell_v[4]" not in keys
    assert "temp[1]" in keys and "temp[2]" in keys and "temp[3]" not in keys


def test_available_parameters():
    tel = _sample()
    params = available_parameters(tel)
    keys = {p.key for p in params}
    assert "pack_volt" in keys and "temp_all" in keys and "cell_v_all" in keys
    assert "cell_v[1]" not in keys  # arrays aggregated into one parameter
    cell = next(p for p in params if p.key == "cell_v_all")
    assert len(cell.specs) == 3  # sample has 3 cell voltages
    temp = next(p for p in params if p.key == "temp_all")
    assert len(temp.specs) == 2
    pv = next(p for p in params if p.key == "pack_volt")
    assert len(pv.specs) == 1 and pv.group == "Core"


def test_service_format(tmp_path):
    tel = _sample()
    specs = _pick(tel, {"pack_volt", "pack_curr", "soh_pct", "cell_v[1]", "temp[1]"})
    lg = CsvTelemetryLogger(_FakeDriver(tel), specs, 0.1, FORMAT_SERVICE, user="tester")
    lg.start(tmp_path / "svc.csv")
    time.sleep(0.35)
    lg.stop()
    lines = (tmp_path / "svc.csv").read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "User:;tester"
    assert any(x.startswith("Update Rate:;0.1 sec") for x in lines[:6])
    hdr = next(x for x in lines if x.startswith("Current Time;"))
    assert hdr == "Current Time;Test Time(sec);pack_volt;pack_curr;soh_pct;cell_v[1];temp[1]"
    row = lines[lines.index(hdr) + 1].split(";")
    assert row[2] == "24,50"     # pack_volt, decimal comma
    assert row[3] == "-3,25"     # pack_curr
    assert row[4] == ""          # soh None -> empty
    assert row[5] == "2,1600"    # cell_v[1] 4 decimals
    assert row[6] == "20,50"     # temp[1]
    assert lg.rows_written >= 2


def test_standard_format(tmp_path):
    tel = _sample()
    specs = _pick(tel, {"pack_volt", "soh_pct", "cell_v[3]"})
    lg = CsvTelemetryLogger(_FakeDriver(tel), specs, 0.1, FORMAT_STANDARD)
    lg.start(tmp_path / "std.csv")
    time.sleep(0.3)
    lg.stop()
    lines = (tmp_path / "std.csv").read_text(encoding="utf-8-sig").splitlines()
    assert lines[0] == "time_iso,t_s,pack_volt,soh_pct,cell_v[3]"  # no metadata block
    row = lines[1].split(",")
    assert row[2] == "24.50"   # decimal dot
    assert row[3] == ""        # soh None
    assert row[4] == ""        # cell_v[3] is NaN -> empty


if __name__ == "__main__":
    import tempfile

    test_fmt_value()
    test_available_signals_arrays()
    test_available_parameters()
    with tempfile.TemporaryDirectory() as d:
        test_service_format(Path(d))
        test_standard_format(Path(d))
    print("OK")
