# Lab bring-up checklist

Use this on the real bench (PSI 9080-340 + 2× EL 9080-510 B + Kvaser + BMU).

## Before power

- [ ] Module profile selected (`LTO_24V_70Ah` or `LTO_24V_60Ah`)
- [ ] Control box wiring: EA ↔ contactors ↔ module correct polarity
- [ ] Kvaser on **External CAN** (not internal BMU↔LMU bus)
- [ ] `config/default.yaml`: bitrate matches BMU; `bmu_address` in 0x00–0x03; App SA `0x20` (ACU) unless BMU flashed for Service Tool `0xF9`
- [ ] PSI + EL **master** USB serial present (Device Manager COM); `serial_port: auto` or set in Nastavení
- [ ] Confirm master shows combined / MS mode on device HMI before Connect
- [ ] `use_mock_hardware: false`

## READY handshake

- [ ] Launch app → Connect HW
- [ ] Run a program with only `bms_ready` then `bms_idle`
- [ ] BMS state transitions: IDLE → PRE_CHARGE → READY
- [ ] Contactors close under BMU control (audible/visual)
- [ ] App Command heartbeat keeps FC103 clear (no Lost Comms DTC)
- [ ] Cell voltages / temps appear when request bits enabled
- [ ] `bms_idle` opens contactors after EA is off

## Capacity run (1C)

- [ ] Load `Capacity_70Ah_claim.yaml` (or 60Ah variant)
- [ ] Enter module serial / claim ID
- [ ] Confirm charge current and pack/cell stop limits
- [ ] Start — charge to limit, wait_temp, discharge with `measure: capacity_ah`
- [ ] CSV log created under `runs/`
- [ ] HTML/JSON report contains capacity_ah
- [ ] Final step leaves BMS IDLE / contactors open

## DCIR

- [ ] Pulse current within EL combined rating and module pulse rating
- [ ] Report shows `dcir_mohm`
- [ ] No stuck EL input after pulse

## Stop / external dropout

- [ ] Press **Stop (Esc)** mid-charge → EA off, wait I≈0 (Activity shows dwell), then IDLE; if I stays high, contactors stay CLOSED
- [ ] Unplug Kvaser briefly → `EXT FAIL — BMS CAN`, EA off, IDLE after I≈0
- [ ] Unplug EL master USB mid-discharge → `EXT FAIL — EA SCPI`
- [ ] (If possible) power-cycle one EL of the MS pair mid-discharge → Activity shows `EXT FAIL — EL master/slave` (I ≈ ½ setpoint) then safe path
- [ ] DTC level ≥ abort threshold (step or global) → stop
- [ ] Confirm PSI and EL never both ON (mutex): during charge EL input off; during discharge PSI output off

## Offline smoke (no hardware)

```powershell
python scripts\smoke_test.py
python scripts\test_el_slave_fail.py
```

Expected:
- `Quick_smoke_mock.yaml` completes with capacity + DCIR (mock)
- `Fail_EL_slave_mock.yaml` ends **FAILED** with `EXT FAIL — EL master/slave` (mock half-current)
