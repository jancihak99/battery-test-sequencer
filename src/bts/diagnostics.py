"""PC / lab connectivity diagnostics — USB, serial, EA ping, Kvaser."""
from __future__ import annotations

import json
import platform
import re
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class UsbDeviceInfo:
    name: str
    status: str
    instance_id: str
    device_class: str = ""
    kind: str = "other"  # kvaser | serial | network | storage | hid | other
    hint: str = ""


@dataclass
class SerialPortInfo:
    device: str
    description: str
    hwid: str = ""
    manufacturer: str = ""
    kind: str = "serial"


@dataclass
class CheckResult:
    name: str
    ok: bool | None  # None = unknown / skipped
    detail: str


@dataclass
class DiagnosticReport:
    generated_at: str
    hostname: str
    platform: str
    python: str
    app_version: str = ""
    usb: list[UsbDeviceInfo] = field(default_factory=list)
    serial_ports: list[SerialPortInfo] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Raw Windows evidence for support / second PC (Copy report)
    lab_dump: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [
            "Battery Test Sequencer — Diagnostics",
            f"Generated: {self.generated_at}",
            f"Host: {self.hostname}  ·  {self.platform}  ·  Python {self.python}",
            f"App: BTS {self.app_version}" if self.app_version else "App: BTS (version unknown)",
            "",
            "=== Checks ===",
        ]
        for c in self.checks:
            mark = "OK" if c.ok is True else ("FAIL" if c.ok is False else "INFO")
            lines.append(f"[{mark}] {c.name}: {c.detail}")
        lines.append("")
        lines.append("=== USB (present) ===")
        if not self.usb:
            lines.append("(none found / enumeration failed)")
        for d in self.usb:
            tag = f" [{d.kind}]" if d.kind != "other" else ""
            lines.append(f"- {d.name}{tag}  status={d.status}")
            lines.append(f"    {d.instance_id}")
            if d.hint:
                lines.append(f"    -> {d.hint}")
        lines.append("")
        lines.append("=== Serial ports ===")
        real_ser = real_serial_ports(self.serial_ports)
        if not real_ser:
            lines.append("(none)")
            for s in self.serial_ports:
                if (s.description or "").startswith("list_ports failed"):
                    lines.append(f"- enumeration error: {s.description}")
        for s in real_ser:
            lines.append(f"- {s.device}: {s.description}  ({s.hwid})")
        if self.lab_dump:
            lines.append("")
            lines.append("=== Lab dump (Windows evidence) ===")
            lines.extend(self.lab_dump)
        if self.notes:
            lines.append("")
            lines.append("=== Notes ===")
            lines.extend(f"- {n}" for n in self.notes)
        lines.append("")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "hostname": self.hostname,
            "platform": self.platform,
            "python": self.python,
            "app_version": self.app_version,
            "usb": [asdict(u) for u in self.usb],
            "serial_ports": [asdict(s) for s in self.serial_ports],
            "checks": [asdict(c) for c in self.checks],
            "notes": list(self.notes),
            "lab_dump": list(self.lab_dump),
        }


# Heuristics for recognizing lab-relevant USB gear
_KIND_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "kvaser",
        re.compile(r"kvaser|leaf|memorator|usbc?an|vid_0bfd|vid_1a94", re.I),
        "Kvaser CAN adapter — use for External CAN to BMU",
    ),
    (
        "serial",
        # EA Elektro-Automatik USB VCP is often VID_232E; also CDC/FTDI/etc.
        re.compile(
            r"elektro.?automatik|\bea[-_ ]?(psi|el|psb)|if-?ab|vid_232e|"
            r"ftdi|cp210|ch340|prolific|cdc.?acm|usb.?serial|usb.?uart|"
            r"com\d+|vid_0403|vid_10c4|vid_1a86|vid_067b|vid_04b4",
            re.I,
        ),
        "USB–serial / EA VCP candidate — should appear under Ports (COM & LPT)",
    ),
    (
        "network",
        re.compile(r"ethernet|realtek|intel\(r\).*i2|usb.?nic|asix|lan75|lan78", re.I),
        "USB network — EA PSI/EL usually need LAN reachability",
    ),
    (
        "storage",
        re.compile(r"mass.?storage|disk|flash|sandisk|kingston|usb.?hdd", re.I),
        "USB storage (usually unrelated to test rack)",
    ),
    (
        "hid",
        re.compile(r"hid|keyboard|mouse|input", re.I),
        "HID input device",
    ),
]


def _classify(name: str, instance_id: str, device_class: str) -> tuple[str, str]:
    blob = f"{name} {instance_id} {device_class}"
    for kind, pat, hint in _KIND_RULES:
        if pat.search(blob):
            return kind, hint
    if device_class.lower() in {"usb", "usbdevice"} and "vid_" in instance_id.lower():
        return "other", "Generic USB device"
    return "other", ""


def _run_ps(command: str, timeout_s: float = 12.0) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (completed.stdout or "") + (completed.stderr or "")
        return completed.returncode == 0, out.strip()
    except Exception as exc:
        return False, str(exc)


def list_usb_devices() -> tuple[list[UsbDeviceInfo], str | None]:
    """Enumerate present USB PnP devices on Windows."""
    if platform.system() != "Windows":
        return [], "USB enumeration is implemented for Windows only"

    # JSON is easier to parse than Format-Table
    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-PnpDevice -PresentOnly |
  Where-Object {
    $_.InstanceId -like 'USB*' -or
    $_.Class -in @('USB','USBDevice','Ports','Net','MEDIA','HIDClass') -and
    ($_.InstanceId -like 'USB*' -or $_.FriendlyName -match 'USB|Kvaser|FTDI|Serial|CAN')
  } |
  Select-Object FriendlyName, Status, InstanceId, Class |
  ConvertTo-Json -Compress -Depth 3
"""
    ok, out = _run_ps(ps)
    if not ok and not out:
        return [], "PowerShell Get-PnpDevice failed"
    if not out:
        # Broader fallback
        ps2 = r"""
Get-PnpDevice -PresentOnly |
  Where-Object { $_.InstanceId -like 'USB\*' } |
  Select-Object FriendlyName, Status, InstanceId, Class |
  ConvertTo-Json -Compress -Depth 3
"""
        ok, out = _run_ps(ps2)
        if not out:
            return [], "No USB devices returned (or access denied)"

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return [], f"Could not parse PnP JSON: {out[:200]}"

    if isinstance(data, dict):
        data = [data]

    devices: list[UsbDeviceInfo] = []
    seen: set[str] = set()
    for row in data:
        name = str(row.get("FriendlyName") or "(unnamed)")
        status = str(row.get("Status") or "")
        iid = str(row.get("InstanceId") or "")
        cls = str(row.get("Class") or "")
        if not iid or iid in seen:
            continue
        # Prefer real USB instance paths
        if not iid.upper().startswith("USB") and "USB" not in name.upper() and cls not in {
            "USB",
            "USBDevice",
            "Ports",
        }:
            continue
        seen.add(iid)
        kind, hint = _classify(name, iid, cls)
        devices.append(
            UsbDeviceInfo(
                name=name,
                status=status,
                instance_id=iid,
                device_class=cls,
                kind=kind,
                hint=hint,
            )
        )

    # Sort: interesting kinds first
    order = {"kvaser": 0, "serial": 1, "network": 2, "other": 3, "hid": 4, "storage": 5}
    devices.sort(key=lambda d: (order.get(d.kind, 9), d.name.lower()))
    return devices, None


def list_serial_ports() -> list[SerialPortInfo]:
    out: list[SerialPortInfo] = []
    try:
        from serial.tools import list_ports

        for p in list_ports.comports():
            kind, _ = _classify(
                f"{p.description} {p.manufacturer or ''}",
                p.hwid or "",
                "Ports",
            )
            out.append(
                SerialPortInfo(
                    device=p.device,
                    description=p.description or "",
                    hwid=p.hwid or "",
                    manufacturer=p.manufacturer or "",
                    kind=kind if kind in ("kvaser", "serial") else "serial",
                )
            )
    except Exception as exc:
        out.append(
            SerialPortInfo(
                device="",
                description=f"list_ports failed: {exc}",
                kind="other",
            )
        )
    return out


def real_serial_ports(ports: list[SerialPortInfo]) -> list[SerialPortInfo]:
    """Drop placeholder / error rows from list_serial_ports()."""
    return [
        p
        for p in ports
        if p.device
        and p.device not in {"—", "-", "?"}
        and not (p.description or "").startswith("list_ports failed")
    ]

def _python_can_version() -> str:
    try:
        import can

        return str(getattr(can, "__version__", "?") or "?")
    except Exception as exc:
        return f"missing ({exc})"


def collect_windows_lab_dump() -> list[str]:
    """Deep Windows evidence for 'EA was plugged in but no COM' cases.

    Captures Ports class, problem/unknown USB, registry SERIALCOMM,
    .NET port names, and lab-related processes — independent of pyserial.
    """
    lines: list[str] = [f"python-can: {_python_can_version()}"]
    if platform.system() != "Windows":
        lines.append("(lab dump is Windows-only)")
        return lines

    ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
$o = [ordered]@{}

# pyserial-independent COM names
try {
  $o.net_ports = [System.IO.Ports.SerialPort]::GetPortNames()
} catch { $o.net_ports = @() }

# Registry map COM -> device
try {
  $map = Get-ItemProperty 'HKLM:\HARDWARE\DEVICEMAP\SERIALCOMM' -ErrorAction Stop
  $o.registry_serialcomm = @{}
  $map.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object {
    $o.registry_serialcomm[$_.Name] = $_.Value
  }
} catch { $o.registry_serialcomm = @{} }

# Device Manager "Ports (COM & LPT)"
$o.pnp_ports = @(Get-PnpDevice -Class Ports -PresentOnly |
  Select-Object FriendlyName, Status, InstanceId, Class |
  ForEach-Object {
    @{ name=$_.FriendlyName; status=$_.Status; id=$_.InstanceId; class=$_.Class }
  })

# Problem / Error / Unknown present devices (missing drivers often land here)
$o.problem = @(Get-PnpDevice -PresentOnly |
  Where-Object {
    $_.Status -ne 'OK' -or
    $_.FriendlyName -match 'Unknown|Nezn|Other devices|Jina zar'
  } |
  Select-Object FriendlyName, Status, InstanceId, Class |
  Select-Object -First 40 |
  ForEach-Object {
    @{ name=$_.FriendlyName; status=$_.Status; id=$_.InstanceId; class=$_.Class }
  })

# USB VID devices that look like serial / EA / CDC (even if not classified Ports yet)
$o.usb_serialish = @(Get-PnpDevice -PresentOnly |
  Where-Object {
    $_.InstanceId -match 'USB\\VID_' -and (
      $_.InstanceId -match 'VID_232E|VID_0403|VID_10C4|VID_1A86|VID_067B|VID_04B4|VID_0483' -or
      $_.FriendlyName -match 'Serial|UART|CDC|FTDI|CP210|CH340|Prolific|EA |Elektro|IF-AB|COM\d'
    )
  } |
  Select-Object FriendlyName, Status, InstanceId, Class |
  Select-Object -First 30 |
  ForEach-Object {
    @{ name=$_.FriendlyName; status=$_.Status; id=$_.InstanceId; class=$_.Class }
  })

# Win32_SerialPort (older path; sometimes empty even when COM exists)
try {
  $o.wmi_serial = @(Get-CimInstance Win32_SerialPort -ErrorAction SilentlyContinue |
    Select-Object DeviceID, Name, Description, PNPDeviceID |
    ForEach-Object {
      @{ device=$_.DeviceID; name=$_.Name; desc=$_.Description; id=$_.PNPDeviceID }
    })
} catch { $o.wmi_serial = @() }

# Processes that commonly lock COM / CAN
$rx = 'Power.?Control|EasyPower|EasyLoad|EA[-_]?PSI|CanKing|CANalyzer|Busmaster|python|BTS|sequencer'
$o.processes = @(Get-Process |
  Where-Object { $_.ProcessName -match $rx -or $_.MainWindowTitle -match $rx } |
  Select-Object -First 25 ProcessName, Id, MainWindowTitle |
  ForEach-Object {
    @{ name=$_.ProcessName; pid=$_.Id; title=($_.MainWindowTitle -replace '\s+',' ').Trim() }
  })

$o | ConvertTo-Json -Compress -Depth 5
"""
    ok, out = _run_ps(ps, timeout_s=20.0)
    if not out:
        lines.append(f"PowerShell lab dump failed (ok={ok})")
        return lines

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        lines.append(f"lab dump JSON parse error: {out[:240]}")
        return lines

    def _rows(key: str) -> list[dict[str, Any]]:
        raw = data.get(key) or []
        if isinstance(raw, dict):
            return [raw]
        if isinstance(raw, list):
            return [r for r in raw if isinstance(r, dict)]
        return []

    net = data.get("net_ports") or []
    if isinstance(net, str):
        net = [net]
    lines.append(
        "NET SerialPort.GetPortNames: "
        + (", ".join(str(x) for x in net) if net else "(empty)")
    )

    reg = data.get("registry_serialcomm") or {}
    if isinstance(reg, dict) and reg:
        lines.append("Registry SERIALCOMM:")
        for k, v in sorted(reg.items(), key=lambda kv: str(kv[1])):
            lines.append(f"  {v} <- {k}")
    else:
        lines.append("Registry SERIALCOMM: (empty)")

    ports = _rows("pnp_ports")
    lines.append(f"PnP Class=Ports (present): {len(ports)}")
    for r in ports[:20]:
        lines.append(f"  [{r.get('status')}] {r.get('name')} | {r.get('id')}")

    usbish = _rows("usb_serialish")
    lines.append(f"USB serial/EA-like VID (present): {len(usbish)}")
    for r in usbish[:20]:
        lines.append(f"  [{r.get('status')}] {r.get('name')} | {r.get('id')}")
    if not usbish:
        lines.append(
            "  -> none — EA USB not enumerated (cable/port/power) OR driver never bound"
        )

    problems = _rows("problem")
    lines.append(f"Problem / non-OK PnP (sample): {len(problems)}")
    for r in problems[:25]:
        lines.append(f"  [{r.get('status')}] {r.get('name')} | {r.get('class')} | {r.get('id')}")

    wmi = _rows("wmi_serial")
    lines.append(f"Win32_SerialPort: {len(wmi)}")
    for r in wmi[:15]:
        lines.append(f"  {r.get('device')}: {r.get('name')} | {r.get('id')}")

    procs = _rows("processes")
    lines.append(f"Lab-related processes: {len(procs)}")
    for r in procs[:20]:
        title = r.get("title") or ""
        extra = f" — {title}" if title else ""
        lines.append(f"  {r.get('name')} pid={r.get('pid')}{extra}")
    if not procs:
        lines.append("  (none matching Power Control / CanKing / python / BTS)")

    return lines


def summarize_ea_usb_visibility(
    *,
    serial_ports: list[SerialPortInfo],
    usb: list[UsbDeviceInfo],
    lab_dump: list[str],
) -> CheckResult:
    """Explain why EA COM is missing using dump evidence."""
    name = "EA USB visibility"
    real = real_serial_ports(serial_ports)
    if real:
        ports = ", ".join(s.device for s in real[:8])
        return CheckResult(name, True, f"Windows exposes COM: {ports}")

    serialish_usb = [d for d in usb if d.kind == "serial"]
    dump = "\n".join(lab_dump)
    has_usb_candidate = bool(
        re.search(r"USB serial/EA-like VID \(present\): ([1-9]\d*)\b", dump)
    )
    has_problem = bool(re.search(r"Problem / non-OK PnP \(sample\): ([1-9]\d*)\b", dump))
    reg_empty = "Registry SERIALCOMM: (empty)" in dump

    if serialish_usb or has_usb_candidate:
        return CheckResult(
            name,
            False,
            "USB serial/EA-like device seen, but no COM port — install/repair EA USB VCP "
            "driver (Device Manager → update driver), then unplug/replug",
        )
    if has_problem and reg_empty:
        return CheckResult(
            name,
            False,
            "No COM + problem PnP devices present — open Device Manager for yellow ! "
            "on Unknown USB; often missing EA/FTDI driver on a new PC",
        )
    return CheckResult(
        name,
        False,
        "No COM and no EA/serial-like USB VID — Windows does not see EA USB. "
        "Check cable to this PC, EA front USB, powered hub; then Refresh",
    )

def list_local_ipv4() -> list[str]:
    """Local IPv4 addresses (excluding loopback) for subnet hints."""
    if platform.system() != "Windows":
        try:
            hostname = socket.gethostname()
            return sorted(
                {
                    a[4][0]
                    for a in socket.getaddrinfo(hostname, None, socket.AF_INET)
                    if not a[4][0].startswith("127.")
                }
            )
        except Exception:
            return []
    ps = r"""
Get-NetIPAddress -AddressFamily IPv4 |
  Where-Object { $_.IPAddress -notlike '127.*' } |
  Select-Object -ExpandProperty IPAddress
"""
    ok, out = _run_ps(ps)
    if not ok or not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _same_subnet(ip_a: str, ip_b: str, prefix: int = 24) -> bool:
    try:
        def to_int(ip: str) -> int:
            parts = [int(x) for x in ip.split(".")]
            return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]

        mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
        return (to_int(ip_a) & mask) == (to_int(ip_b) & mask)
    except Exception:
        return False


def check_tcp(host: str, port: int, timeout_s: float = 1.5) -> CheckResult:
    name = f"TCP {host}:{port}"
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return CheckResult(name, True, "open / reachable")
    except OSError as exc:
        return CheckResult(name, False, str(exc))


def check_ping(host: str) -> CheckResult:
    try:
        completed = subprocess.run(
            ["ping", "-n", "1", "-w", "800", host],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (completed.stdout or "") + (completed.stderr or "")
        ok = ("TTL=" in out) or ("ttl=" in out)
        return CheckResult(f"Ping {host}", ok, "host responds" if ok else "no reply (wrong IP / other subnet / ICMP blocked)")
    except Exception as exc:
        return CheckResult(f"ping {host}", False, str(exc))


def list_kvaser_channels() -> list[dict[str, Any]]:
    """Return all Kvaser CANlib channels (physical + virtual)."""
    try:
        from can.interfaces.kvaser import canGetNumberOfChannels, get_channel_info, init_kvaser_library
        import ctypes
    except Exception:
        return []

    try:
        init_kvaser_library()
    except Exception:
        pass

    n = 8
    try:
        count = ctypes.c_int(0)
        canGetNumberOfChannels(ctypes.byref(count))
        if count.value > 0:
            n = int(count.value)
    except Exception:
        pass

    infos: list[dict[str, Any]] = []
    for ch in range(0, n):
        try:
            info = get_channel_info(ch)
            if isinstance(info, dict):
                row = dict(info)
            else:
                row = {"device_name": str(info), "raw": str(info)}
            row["channel"] = ch
            infos.append(row)
        except Exception:
            continue
    return infos


def check_kvaser_channels() -> CheckResult:
    """Detect Kvaser channels; prefer physical Leaf over Virtual CAN."""
    try:
        import can  # noqa: F401
    except Exception as exc:
        return CheckResult("Kvaser driver", False, f"python-can missing: {exc}")

    infos = list_kvaser_channels()
    if not infos:
        # Fallback open
        try:
            from bts.can_bus import open_bus

            bus = open_bus(interface="kvaser", channel=0, bitrate=250000, receive_own_messages=False)
            bus.shutdown()
            return CheckResult(
                "Kvaser driver",
                None,
                "Channel 0 opens, but channel list empty — check Kvaser drivers",
            )
        except Exception as exc:
            return CheckResult("Kvaser driver", False, f"cannot open channel: {exc}")

    physical = []
    virtual = []
    for i in infos:
        name = str(i.get("device_name") or i.get("raw") or "")
        if "virtual" in name.lower():
            virtual.append(i)
        else:
            physical.append(i)

    if physical:
        parts = [
            f"ch{i.get('channel')}: {i.get('device_name')} "
            f"(S/N {i.get('serial', '?')})"
            for i in physical
        ]
        # Suggest config channel = first physical
        suggest = physical[0].get("channel", 0)
        return CheckResult(
            "Kvaser driver",
            True,
            f"{' | '.join(parts)}. Use config bms.channel: {suggest}",
        )

    names = " | ".join(str(i.get("device_name")) for i in virtual[:4])
    return CheckResult(
        "Kvaser driver",
        None,
        f"Only VIRTUAL channels ({names}). "
        f"USB may show a Leaf, but CANlib sees no physical channel — "
        f"replug Leaf / reinstall Kvaser drivers.",
    )


def check_bmu_link(
    *,
    interface: str = "kvaser",
    channel: int = 0,
    bitrate: int = 250_000,
    listen_s: float = 1.5,
) -> CheckResult:
    """Kvaser open ≠ BMU alive. Listen briefly for BMU-like frames on External CAN."""
    name = "BMU on CAN (traffic)"
    try:
        import can  # noqa: F401
    except Exception as exc:
        return CheckResult(name, False, f"python-can missing: {exc}")

    bus = None
    frames = 0
    bmu_like = 0
    sample_ids: list[str] = []
    try:
        from bts.can_bus import open_bus

        bus = open_bus(
            interface=interface,
            channel=channel,
            bitrate=bitrate,
            receive_own_messages=False,
        )
        deadline = time.monotonic() + listen_s
        while time.monotonic() < deadline:
            msg = bus.recv(timeout=0.05)
            if msg is None:
                continue
            if not getattr(msg, "is_extended_id", False):
                continue
            frames += 1
            aid = int(msg.arbitration_id)
            key = (aid >> 8) & 0xFFFF
            if 0xFF00 <= key <= 0xFFCF or (0x18FF0000 <= (aid & 0x1FFFFFFF) <= 0x18FFFFFF):
                bmu_like += 1
                if len(sample_ids) < 3:
                    sample_ids.append(f"0x{aid:08X}")
    except Exception as exc:
        detail = str(exc)
        hint = ""
        if "canIoCtl" in detail or "Error Code -1" in detail:
            hint = (
                " — known python-can 4.6+ / Kvaser driver bug; "
                "update BTS or pin python-can==4.5.0; close CanKing and retry"
            )
        return CheckResult(
            name,
            False,
            f"cannot open ch={channel} @ {bitrate}: {detail}{hint}",
        )
    finally:
        if bus is not None:
            try:
                bus.shutdown()
            except Exception:
                pass

    if bmu_like > 0:
        return CheckResult(
            name,
            True,
            f"{bmu_like} BMU-like frames in {listen_s:.1f}s "
            f"(ch={channel} @ {bitrate}; e.g. {', '.join(sample_ids)})",
        )
    if frames > 0:
        return CheckResult(
            name,
            False,
            f"CAN traffic present ({frames} ext frames) but none look like BMU PGNs — "
            f"wrong bus or bitrate? ch={channel} @ {bitrate}",
        )
    return CheckResult(
        name,
        False,
        f"Kvaser open OK, but ZERO frames in {listen_s:.1f}s "
        f"(ch={channel} @ {bitrate}). BMU likely OFF, wrong External CAN cable, "
        f"or another app owns the channel.",
    )


def check_serial_port(port: str, baudrate: int = 115200, *, probe_idn: bool = True) -> CheckResult:
    name = f"Serial {port}"
    try:
        import serial
    except Exception as exc:
        return CheckResult(name, False, f"pyserial missing: {exc}")
    try:
        ser = serial.Serial(port, baudrate, timeout=1.5)
    except Exception as exc:
        return CheckResult(name, False, f"cannot open: {exc}")
    detail = f"open OK @ {baudrate} baud"
    if probe_idn:
        try:
            ser.reset_input_buffer()
            ser.write(b"*IDN?\n")
            line = ser.readline().decode("ascii", errors="replace").strip()
            if line:
                detail = f"open OK · *IDN? = {line[:80]}"
            else:
                detail = f"open OK @ {baudrate} · no *IDN? reply (port free? close EA Power Control first)"
        except Exception as exc:
            detail = f"open OK · IDN probe failed: {exc}"
    try:
        ser.close()
    except Exception:
        pass
    return CheckResult(name, True, detail)


def build_report(
    *,
    ea_psi_host: str | None = None,
    ea_psi_port: int = 5025,
    ea_el_host: str | None = None,
    ea_el_port: int = 5025,
    ea_psi_transport: str = "tcp",
    ea_el_transport: str = "tcp",
    ea_psi_serial: str | None = None,
    ea_el_serial: str | None = None,
    ea_psi_baud: int = 115200,
    ea_el_baud: int = 115200,
    bms_interface: str = "kvaser",
    bms_channel: int = 0,
    bms_bitrate: int = 250_000,
    mock: bool = False,
) -> DiagnosticReport:
    report = DiagnosticReport(
        generated_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        hostname=platform.node(),
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        python=sys.version.split()[0],
    )
    try:
        from bts.version import read_version

        report.app_version = read_version()
    except Exception:
        report.app_version = ""

    usb, usb_err = list_usb_devices()
    report.usb = usb
    if usb_err:
        report.notes.append(usb_err)

    report.serial_ports = list_serial_ports()
    report.lab_dump = collect_windows_lab_dump()
    report.checks.append(
        CheckResult("python-can", None, f"version {_python_can_version()} (need ≤4.5.x for some Kvaser Leaf)")
    )
    report.checks.append(
        summarize_ea_usb_visibility(
            serial_ports=report.serial_ports,
            usb=report.usb,
            lab_dump=report.lab_dump,
        )
    )
    dump_text = "\n".join(report.lab_dump)
    if re.search(r"CanKing|CANalyzer|Busmaster", dump_text, re.I):
        report.checks.append(
            CheckResult(
                "CAN apps running",
                None,
                "CanKing/CANalyzer-like process detected — can block Leaf open (canIoCtl). "
                "Quit CanKing (and CanKingService if stuck) before Connect",
            )
        )
    local_ips = list_local_ipv4()
    if local_ips:
        report.checks.append(
            CheckResult(
                "This PC IPv4",
                None,
                ", ".join(local_ips),
            )
        )

    if mock:
        report.checks.append(
            CheckResult(
                "App mode",
                None,
                "MOCK is ON (Settings). App will NOT talk to real BMS/EA until you uncheck mock. "
                "EA timeouts below are then expected even if gear is plugged in.",
            )
        )
    else:
        report.checks.append(
            CheckResult("App mode", True, "LIVE — real Kvaser + EA required")
        )

    kvaser_usb = [d for d in usb if d.kind == "kvaser"]
    kvaser_usb_ok = bool(kvaser_usb)
    if kvaser_usb_ok:
        names = ", ".join(d.name for d in kvaser_usb[:3])
        report.checks.append(CheckResult("Kvaser USB (physical)", True, names))
    else:
        report.checks.append(
            CheckResult(
                "Kvaser USB (physical)",
                False,
                "No Kvaser Leaf/USB in the USB list",
            )
        )

    kvaser_drv: CheckResult
    if bms_interface.lower() == "kvaser":
        kvaser_drv = check_kvaser_channels()
        report.checks.append(kvaser_drv)
        # Warn if config channel points at virtual
        infos = list_kvaser_channels()
        for i in infos:
            if int(i.get("channel", -1)) == int(bms_channel):
                name = str(i.get("device_name") or "")
                if "virtual" in name.lower():
                    report.checks.append(
                        CheckResult(
                            "Config bms.channel",
                            False,
                            f"channel {bms_channel} is {name} — set bms.channel to the Leaf channel "
                            f"(usually 0 when Leaf is plugged in)",
                        )
                    )
                else:
                    report.checks.append(
                        CheckResult(
                            "Config bms.channel",
                            True,
                            f"channel {bms_channel} = {name}",
                        )
                    )
                break
    else:
        kvaser_drv = CheckResult("Kvaser driver", None, f"skipped (interface={bms_interface})")
        report.checks.append(kvaser_drv)

    # Adapter present ≠ BMU powered / on External CAN
    bmu_check: CheckResult
    if mock:
        bmu_check = CheckResult(
            "BMU on CAN (traffic)",
            None,
            "skipped while MOCK is ON — turn mock off to verify real BMU",
        )
        report.checks.append(bmu_check)
    elif kvaser_drv.ok is True or (bms_interface.lower() != "kvaser" and kvaser_usb_ok):
        bmu_check = check_bmu_link(
            interface=bms_interface,
            channel=int(bms_channel),
            bitrate=int(bms_bitrate),
        )
        report.checks.append(bmu_check)
    else:
        bmu_check = CheckResult(
            "BMU on CAN (traffic)",
            False,
            "skipped — fix Kvaser first, then Refresh",
        )
        report.checks.append(bmu_check)

    ea_results: list[CheckResult] = []

    def _check_ea_channel(
        *,
        label: str,
        transport: str,
        host: str | None,
        port: int,
        serial_port: str | None,
        baud: int,
    ) -> None:
        transport = (transport or "tcp").lower()
        if transport == "serial":
            auto = not serial_port or str(serial_port).strip().lower() in {"auto", "any", "*"}
            if auto:
                try:
                    from bts.drivers.ea import discover_ea_serial_map

                    role = "psi" if "PSI" in label.upper() or "source" in label.lower() else "el"
                    mapping = discover_ea_serial_map(baudrate=baud)
                    hit = mapping.get(role)
                    if hit:
                        port, idn = hit
                        c = CheckResult(
                            f"EA {label} (auto→{port})",
                            True,
                            f"*IDN? {idn} — USB cables can be swapped; BTS maps by identity",
                        )
                    else:
                        c = CheckResult(
                            f"EA {label} (auto)",
                            False,
                            "No matching *IDN? on any COM — close EA Power Control, check USB, Refresh",
                        )
                        if mock:
                            c.ok = None
                except Exception as exc:
                    c = CheckResult(f"EA {label} (auto)", False, str(exc))
                    if mock:
                        c.ok = None
            else:
                c = check_serial_port(serial_port, baud)
                c.name = f"EA {label} ({serial_port})"
                if not c.ok and mock:
                    c.ok = None
                    c.detail = f"{c.detail} — close EA Power Control before BTS Connect (COM exclusive)"
                elif c.ok and "no *IDN?" in c.detail and mock:
                    c.ok = None
            ea_results.append(c)
            report.checks.append(c)
            return

        # TCP path
        if host and local_ips and not any(_same_subnet(ip, host) for ip in local_ips):
            report.checks.append(
                CheckResult(
                    f"EA {label} network path",
                    False,
                    f"PC IPs {', '.join(local_ips)} vs config {host} — different subnet. "
                    f"Your EA Power Control uses USB COM; prefer transport: serial in config, "
                    f"or put PC on 192.168.0.x LAN.",
                )
            )
        if host:
            report.checks.append(check_ping(host))
            c = check_tcp(host, port)
            c.name = f"EA {label} {host}:{port}"
            if not c.ok and mock:
                c.ok = None
                c.detail = f"{c.detail} — informational while MOCK is on"
            ea_results.append(c)
            report.checks.append(c)

    _check_ea_channel(
        label="source PSI",
        transport=ea_psi_transport,
        host=ea_psi_host,
        port=ea_psi_port,
        serial_port=ea_psi_serial,
        baud=ea_psi_baud,
    )
    _check_ea_channel(
        label="load EL",
        transport=ea_el_transport,
        host=ea_el_host,
        port=ea_el_port,
        serial_port=ea_el_serial,
        baud=ea_el_baud,
    )

    ea_ok = any(c.ok is True for c in ea_results)
    drv_ok = kvaser_drv.ok is True
    bmu_ok = bmu_check.ok is True
    using_serial = (ea_psi_transport or "").lower() == "serial" or (ea_el_transport or "").lower() == "serial"

    if mock:
        report.checks.insert(
            0,
            CheckResult(
                "Lab setup",
                None,
                "MOCK is still ON — turn OFF in Settings for live Connect. "
                f"Kvaser={'OK' if kvaser_usb_ok and drv_ok else 'check'}, "
                f"BMU=not checked, "
                f"EA={'OK' if ea_ok else 'check COM/LAN'}"
                + (" (config uses USB serial like Power Control)" if using_serial else ""),
            ),
        )
    elif kvaser_usb_ok and drv_ok and bmu_ok and ea_ok:
        report.checks.insert(
            0,
            CheckResult("Lab setup", True, "Kvaser + BMU traffic + EA ready for live Connect"),
        )
    elif kvaser_usb_ok and drv_ok and not bmu_ok:
        report.checks.insert(
            0,
            CheckResult(
                "Lab setup",
                False,
                "Kvaser OK, but no BMU on CAN — power the BMU, use External CAN "
                "(not internal BMU↔LMU), then Refresh",
            ),
        )
    elif kvaser_usb_ok and drv_ok and bmu_ok and not ea_ok:
        report.checks.insert(
            0,
            CheckResult(
                "Lab setup",
                False,
                "Kvaser + BMU OK, but EA not reachable — if Power Control has the device open, "
                "close it (COM ports are exclusive), then Refresh",
            ),
        )
    else:
        report.checks.insert(
            0,
            CheckResult(
                "Lab setup",
                False,
                "Not ready for live Connect — fix FAIL items below",
            ),
        )

    report.notes.append(
        "Kvaser USB OK only means the adapter is plugged in. "
        "BMU on CAN must show traffic — if FAIL with 0 frames, BMU is likely OFF or on the wrong bus."
    )

    if any(
        c.name == "BMU on CAN (traffic)" and c.ok is False and "canIoCtl" in (c.detail or "")
        for c in report.checks
    ):
        report.notes.append(
            "canIoCtl Error -1: Leaf is seen by USB, but open fails (python-can 4.6 ioctl or "
            "another app holds the channel). Close CanKing/CANalyzer, install current Kvaser "
            "Drivers, or use a BTS build with python-can 4.5.0."
        )

    if using_serial:
        report.notes.append(
            "COM = Windows name for USB virtual serial (not Ethernet). "
            "serial_port: auto maps PSI vs EL by *IDN? even if USB cables are swapped. "
            "Close Power Control before Connect — only one app can own a COM port."
        )
    elif report.serial_ports:
        report.notes.append(
            "COM ports are present (Power Control style). "
            "If LAN fails, set ea.*.transport: serial and serial_port: auto in config."
        )
    else:
        report.notes.append(
            "Serial ports: (none) — see Lab dump section (PnP Ports, USB VID, problem devices, "
            "SERIALCOMM). That evidence shows whether EA USB is missing vs driver-only."
        )

    report.notes.append(
        "Legend: OK = good for lab, FAIL = must fix for live, INFO = explanation / mock."
    )
    return report
