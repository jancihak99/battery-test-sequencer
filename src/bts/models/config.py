from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

import yaml


@dataclass
class BmsConfig:
    interface: str = "kvaser"
    channel: int = 0
    bitrate: int = 250_000
    bmu_address: int = 0x03
    app_address: int = 0x20
    heartbeat_period_ms: int = 100
    request_cell_voltages: bool = True
    request_temperatures: bool = True
    request_cell_balance: bool = True


@dataclass
class EaChannelConfig:
    name: str
    transport: str = "tcp"
    host: str = "192.168.0.10"
    port: int = 5025
    serial_port: str | None = None
    baudrate: int = 115_200
    max_voltage_v: float = 80.0
    max_current_a: float = 340.0


@dataclass
class EaConfig:
    """PSI for charge; EL is the master of a hardware master/slave pair (one logical load)."""

    psi: EaChannelConfig
    el: EaChannelConfig


@dataclass
class SafetyConfig:
    can_timeout_s: float = 1.0
    ea_timeout_s: float = 2.0
    abort_dtc_level: int = 3
    poll_period_ms: int = 200
    # Active power-path integrity (detect silent EL slave drop ≈ half current, etc.)
    ea_i_mismatch_min_set_a: float = 40.0
    ea_i_slave_ratio_lo: float = 0.35
    ea_i_slave_ratio_hi: float = 0.65
    ea_i_collapse_ratio: float = 0.15
    ea_i_mismatch_confirm_s: float = 2.0


@dataclass
class PathsConfig:
    profiles_dir: str = "profiles"
    programs_dir: str = "programs"
    runs_dir: str = "runs"


@dataclass
class AppConfig:
    use_mock_hardware: bool = False
    bms: BmsConfig = field(default_factory=BmsConfig)
    ea: EaConfig | None = None
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    root: Path = field(default_factory=Path.cwd)

    @property
    def profiles_path(self) -> Path:
        return self.root / self.paths.profiles_dir

    @property
    def programs_path(self) -> Path:
        return self.root / self.paths.programs_dir

    @property
    def runs_path(self) -> Path:
        return self.root / self.paths.runs_dir


def _ea_channel(data: dict[str, Any]) -> EaChannelConfig:
    return EaChannelConfig(**{k: v for k, v in data.items() if k in EaChannelConfig.__dataclass_fields__})


def _as_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, str):
        return int(value.strip(), 0)
    return int(value)


def load_config(path: Path | None = None, root: Path | None = None) -> AppConfig:
    root = root or Path.cwd()
    path = path or (root / "config" / "default.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    bms_raw = {k: v for k, v in (raw.get("bms") or {}).items() if k in BmsConfig.__dataclass_fields__}
    for key in ("channel", "bitrate", "bmu_address", "app_address", "heartbeat_period_ms"):
        if key in bms_raw:
            bms_raw[key] = _as_int(bms_raw[key])
    bms = BmsConfig(**bms_raw)
    ea_raw = raw.get("ea") or {}
    # Prefer `el` (master of MS pair). Legacy el1/el2 configs: use el1 as master.
    el_raw = ea_raw.get("el") or ea_raw.get("el1") or {"name": "EL_MS"}
    if "el" not in ea_raw and "el1" in ea_raw and "el2" in ea_raw:
        # Old split config: promote combined current limit if both listed
        try:
            el_raw = dict(el_raw)
            el_raw.setdefault(
                "max_current_a",
                float(ea_raw["el1"].get("max_current_a", 510))
                + float(ea_raw["el2"].get("max_current_a", 510)),
            )
            el_raw.setdefault("name", "EL_MS_from_el1_el2")
        except Exception:
            pass
    ea = EaConfig(
        psi=_ea_channel(ea_raw.get("psi") or {"name": "PSI"}),
        el=_ea_channel(el_raw),
    )
    safety = SafetyConfig(**{k: v for k, v in (raw.get("safety") or {}).items() if k in SafetyConfig.__dataclass_fields__})
    paths = PathsConfig(**{k: v for k, v in (raw.get("paths") or {}).items() if k in PathsConfig.__dataclass_fields__})
    return AppConfig(
        use_mock_hardware=bool(raw.get("use_mock_hardware", False)),
        bms=bms,
        ea=ea,
        safety=safety,
        paths=paths,
        root=root,
    )


def save_bms_settings(cfg: AppConfig, path: Path | None = None) -> Path:
    """Persist BMS + mock + EA serial settings into config YAML (keeps other sections)."""
    path = path or (cfg.root / "config" / "default.yaml")
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["use_mock_hardware"] = bool(cfg.use_mock_hardware)
    bms = raw.get("bms") or {}
    bms.update(
        {
            "interface": cfg.bms.interface,
            "channel": int(cfg.bms.channel),
            "bitrate": int(cfg.bms.bitrate),
            "bmu_address": int(cfg.bms.bmu_address),
            "app_address": int(cfg.bms.app_address),
            "heartbeat_period_ms": int(cfg.bms.heartbeat_period_ms),
            "request_cell_voltages": bool(cfg.bms.request_cell_voltages),
            "request_temperatures": bool(cfg.bms.request_temperatures),
            "request_cell_balance": bool(cfg.bms.request_cell_balance),
        }
    )
    raw["bms"] = bms
    if cfg.ea is not None:
        ea = raw.get("ea") or {}
        for key, ch in (("psi", cfg.ea.psi), ("el", cfg.ea.el)):
            block = ea.get(key) or {}
            if not isinstance(block, dict):
                block = {}
            block.update(
                {
                    "name": ch.name,
                    "transport": ch.transport,
                    "serial_port": ch.serial_port or "auto",
                    "baudrate": int(ch.baudrate),
                    "host": ch.host,
                    "port": int(ch.port),
                    "max_voltage_v": float(ch.max_voltage_v),
                    "max_current_a": float(ch.max_current_a),
                }
            )
            ea[key] = block
        raw["ea"] = ea
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path

