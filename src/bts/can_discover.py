"""Passive / active discovery of External CAN bitrate and BMU / App addresses."""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

COMMON_BITRATES = (250_000, 500_000, 125_000, 1_000_000, 100_000, 50_000)
# Lab: ACU/App SA defaults to 0x20 (spec). BMS Service Tool often uses 0xF9.
DEFAULT_APP_ADDRESS = 0x20
APP_ADDRESS_CHOICES = (0x20, 0xF9)
BMU_ADDRESS_CANDIDATES = (0x00, 0x01, 0x02, 0x03)


@dataclass
class CanDiscoverResult:
    ok: bool
    channel: int | None = None
    bitrate: int | None = None
    bmu_address: int | None = None
    app_address: int | None = None
    frame_count: int = 0
    detail: str = ""


def _bmu_scan_order(prefer: int | None = None) -> list[int]:
    """Only lab BMU SAs: 0x00–0x03 (prefer current first)."""
    ordered: list[int] = []
    seen: set[int] = set()
    if prefer is not None and (prefer & 0xFF) in BMU_ADDRESS_CANDIDATES:
        a = prefer & 0xFF
        ordered.append(a)
        seen.add(a)
    for a in BMU_ADDRESS_CANDIDATES:
        if a not in seen:
            ordered.append(a)
            seen.add(a)
    return ordered


def _list_channels(interface: str = "kvaser") -> list[int]:
    channels: list[int] = []
    if interface == "kvaser":
        try:
            from bts.diagnostics import list_kvaser_channels

            infos = list_kvaser_channels()
            physical = []
            virtual = []
            for info in infos:
                ch = int(info.get("channel", 0))
                name = str(info.get("device_name") or info.get("raw") or "").lower()
                if "virtual" in name:
                    virtual.append(ch)
                else:
                    physical.append(ch)
            channels = physical or virtual or [0]
        except Exception:
            channels = [0]
    else:
        channels = [0]
    return channels


def _msg_key(can_id: int) -> int:
    return (can_id >> 8) & 0xFFFF


def _looks_like_bmu(can_id: int) -> bool:
    key = _msg_key(can_id)
    if 0xFF00 <= key <= 0xFFCF:
        return True
    # Broaden: any 0x18FFxxxx extended proprietary
    aid = can_id & 0x1FFFFFFF
    return 0x18FF0000 <= aid <= 0x18FFFFFF


def _score_rx(ids: list[int], expect_sa: int | None = None) -> int:
    score = 0
    for can_id in ids:
        if expect_sa is not None and (can_id & 0xFF) != (expect_sa & 0xFF):
            continue
        key = _msg_key(can_id)
        if 0xFF10 <= key <= 0xFF8F:
            score += 5
        elif 0xFF00 <= key <= 0xFF0F:
            score += 2
        elif _looks_like_bmu(can_id):
            score += 1
    return score


def _listen(
    *,
    interface: str,
    channel: int,
    bitrate: int,
    duration_s: float,
) -> list[int]:
    """Return list of extended arbitration IDs received (passive)."""
    from bts.can_bus import open_bus

    bus = None
    ids: list[int] = []
    try:
        bus = open_bus(
            interface=interface,
            channel=channel,
            bitrate=bitrate,
            receive_own_messages=False,
        )
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            msg = bus.recv(timeout=0.05)
            if msg is None:
                continue
            if getattr(msg, "is_extended_id", False):
                ids.append(int(msg.arbitration_id))
    except Exception as exc:
        log.debug("Listen ch=%s bitrate=%s failed: %s", channel, bitrate, exc)
        return []
    finally:
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:
                pass
    return ids


def _best_bmu_sa(ids: list[int]) -> tuple[int | None, int]:
    """Pick source address that sends the most BMU-like PGNs."""
    sa_hits: Counter[int] = Counter()
    for can_id in ids:
        if not _looks_like_bmu(can_id):
            continue
        sa_hits[can_id & 0xFF] += 1
    if not sa_hits:
        all_sa = Counter(i & 0xFF for i in ids)
        if not all_sa:
            return None, 0
        sa, n = all_sa.most_common(1)[0]
        return sa, n
    sa, n = sa_hits.most_common(1)[0]
    return sa, n


def _open_bus(interface: str, channel: int, bitrate: int):
    from bts.can_bus import open_bus

    return open_bus(
        interface=interface,
        channel=channel,
        bitrate=bitrate,
        receive_own_messages=False,
    )


def _send_app_cmd(bus, bmu: int, app: int, hb: int) -> None:
    import can

    cmd_id = 0x18EF0000 | ((bmu & 0xFF) << 8) | (app & 0xFF)
    # desired IDLE, cmd_bits: cells + temps + balance (bits 1|2|3 = 0x0E)
    data = bytes([0x02, 0x01, 0x0E, hb & 0xFF, 0, 0, 0, 0])
    bus.send(can.Message(arbitration_id=cmd_id, data=data, is_extended_id=True))


def _drain(bus, duration_s: float) -> list[int]:
    ids: list[int] = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        msg = bus.recv(timeout=0.02)
        if msg is None:
            continue
        if getattr(msg, "is_extended_id", False):
            ids.append(int(msg.arbitration_id))
    return ids


def _active_address_scan(
    *,
    interface: str,
    channel: int,
    bitrate: int,
    prefer_bmu: int | None,
    app_address: int,
    progress: Callable[[str], None] | None,
    per_addr_s: float = 0.08,
) -> CanDiscoverResult | None:
    """Probe BMU SA candidates 0x00–0x03 with fixed tool (App) SA."""
    app = app_address & 0xFF
    bus = None
    try:
        bus = _open_bus(interface, channel, bitrate)
    except Exception as exc:
        log.debug("Active scan open failed ch=%s @ %s: %s", channel, bitrate, exc)
        return None

    best: CanDiscoverResult | None = None
    try:
        for i, bmu in enumerate(_bmu_scan_order(prefer_bmu)):
            if progress:
                try:
                    progress(
                        f"Active scan ch={channel} @ {bitrate}: BMU 0x{bmu:02X} "
                        f"(0x00–0x03, tool 0x{app:02X})…"
                    )
                except Exception:
                    pass
            try:
                _send_app_cmd(bus, bmu, app, i & 0xFF)
                ids = _drain(bus, per_addr_s)
            except Exception:
                continue
            if not ids:
                continue
            matched = [x for x in ids if (x & 0xFF) == bmu]
            use = matched or ids
            sa, hits = _best_bmu_sa(use)
            if sa is None or sa not in BMU_ADDRESS_CANDIDATES:
                # If we probed this BMU and got any RX from it, trust probe target
                if matched:
                    sa = bmu
                    hits = len(matched)
                else:
                    continue
            score = _score_rx(use, expect_sa=sa)
            if score <= 0 and not matched:
                continue
            cand = CanDiscoverResult(
                ok=True,
                channel=channel,
                bitrate=bitrate,
                bmu_address=sa,
                app_address=app,
                frame_count=len(ids),
                detail=(
                    f"Active probe hit: BMU SA=0x{sa:02X} on ch={channel} @ {bitrate} "
                    f"({len(ids)} RX, score={score}; tool 0x{app:02X})"
                ),
            )
            if best is None or len(ids) > best.frame_count:
                best = cand
            if score >= 2 or (matched and hits >= 1):
                return cand
    finally:
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:
                pass
    return best


def discover_can(
    *,
    interface: str = "kvaser",
    channel: int | None = None,
    prefer_bitrate: int | None = None,
    prefer_bmu: int | None = None,
    app_address: int = DEFAULT_APP_ADDRESS,
    listen_s: float = 1.0,
    progress: Callable[[str], None] | None = None,
) -> CanDiscoverResult:
    """Scan bitrates + BMU 0x00–0x03. Tool/App SA stays fixed (default 0xF9)."""

    def _prog(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    app = int(app_address) & 0xFF
    channels = [channel] if channel is not None else _list_channels(interface)
    bitrates = list(COMMON_BITRATES)
    if prefer_bitrate and prefer_bitrate not in bitrates:
        bitrates.insert(0, prefer_bitrate)
    elif prefer_bitrate:
        bitrates.remove(prefer_bitrate)
        bitrates.insert(0, prefer_bitrate)

    best: CanDiscoverResult | None = None

    # 1) Passive listen (BMU often broadcasts SOC without App Command)
    for ch in channels:
        for br in bitrates:
            _prog(f"Passive listen ch={ch} @ {br} …")
            ids = _listen(interface=interface, channel=ch, bitrate=br, duration_s=listen_s)
            if not ids:
                continue
            # Prefer SAs in lab candidate set
            sa_hits: Counter[int] = Counter()
            for can_id in ids:
                if not _looks_like_bmu(can_id):
                    continue
                sa = can_id & 0xFF
                if sa in BMU_ADDRESS_CANDIDATES:
                    sa_hits[sa] += 1
            if not sa_hits:
                continue
            sa, hits = sa_hits.most_common(1)[0]
            cand = CanDiscoverResult(
                ok=True,
                channel=ch,
                bitrate=br,
                bmu_address=sa,
                app_address=app,
                frame_count=len(ids),
                detail=(
                    f"Passive: {len(ids)} frames; BMU SA=0x{sa:02X} ({hits} hits) "
                    f"on ch={ch} @ {br}; tool 0x{app:02X}"
                ),
            )
            if best is None or cand.frame_count > best.frame_count:
                best = cand
            if hits >= 3:
                best = cand
                break
        if best and best.frame_count >= 5:
            break

    # 2) Active: tool SA fixed, BMU candidates 0x00–0x03
    if best is None:
        _prog(
            f"No passive traffic — scanning BMU 0x00–0x03 (tool 0x{app:02X})…"
        )
        for ch in channels:
            for br in bitrates:
                hit = _active_address_scan(
                    interface=interface,
                    channel=ch,
                    bitrate=br,
                    prefer_bmu=prefer_bmu,
                    app_address=app,
                    progress=_prog,
                )
                if hit is not None:
                    best = hit
                    break
            if best is not None:
                break

    if best is None or not best.ok:
        return CanDiscoverResult(
            ok=False,
            detail=(
                "No CAN frames from BMU addresses 0x00–0x03 "
                f"(passive + active; tool SA 0x{app:02X}). "
                "Likely wrong physical bus (need External CAN), BMU unpowered, "
                "or Kvaser held by another app."
            ),
        )

    best.app_address = app
    if "tool 0x" not in best.detail:
        best.detail += f"; tool 0x{app:02X}"
    return best
