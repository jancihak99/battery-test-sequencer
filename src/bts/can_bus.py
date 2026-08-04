"""Open python-can buses with Kvaser workarounds.

python-can 4.6.x can fail on open with:
  Function canIoCtl failed - Error in parameter [Error Code -1]
when CANlib rejects canIOCTL_SET_LOCAL_TXACK (see hardbyte/python-can#2015).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_KVASER_TXACK_PATCHED = False


def _patch_kvaser_local_txack() -> None:
    """Make optional Kvaser ioctls non-fatal (python-can 4.6.x + newer CANlib).

    Leaf Light often rejects SET_LOCAL_TXACK / TXECHO / TIMER_SCALE with
    canERR_PARAM (-1). See hardbyte/python-can#2015, #2050, #2051.
    """
    global _KVASER_TXACK_PATCHED
    if _KVASER_TXACK_PATCHED:
        return
    try:
        from can.interfaces.kvaser import canlib, canstat
    except Exception:
        return

    skip_ioctls = {
        c
        for c in (
            getattr(canstat, "canIOCTL_SET_LOCAL_TXACK", None),
            getattr(canstat, "canIOCTL_SET_LOCAL_TXECHO", None),
            getattr(canstat, "canIOCTL_SET_TIMER_SCALE", None),
        )
        if c is not None
    }
    if not skip_ioctls:
        _KVASER_TXACK_PATCHED = True
        return

    orig_init = getattr(canlib, "canIoCtlInit", None)
    if orig_init is None:
        _KVASER_TXACK_PATCHED = True
        return

    def _wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return orig_init(*args, **kwargs)
        except Exception as exc:
            ioctl = args[1] if len(args) > 1 else kwargs.get("ioctl")
            if ioctl in skip_ioctls:
                log.warning("Ignoring optional Kvaser ioctl %s failure: %s", ioctl, exc)
                return getattr(canstat, "canOK", 0)
            raise

    canlib.canIoCtlInit = _wrapper  # type: ignore[assignment]
    _KVASER_TXACK_PATCHED = True
    log.info("Applied Kvaser optional-ioctl workaround for python-can/CANlib")


def format_can_open_error(exc: BaseException) -> str:
    msg = str(exc)
    if "canIoCtl" in msg or "Error Code -1" in msg:
        return (
            "Kvaser se nepodařilo otevřít (canIoCtl / Error -1).\n\n"
            "Zkus postupně:\n"
            "1) Zavři CanKing, CANalyzer, jinou BTS nebo Virtual CAN nástroje\n"
            "2) Nainstaluj / oprav Kvaser Drivers z https://www.kvaser.com/download/\n"
            "3) Odpoj a znovu zapoj Leaf, pak Připojit HW\n"
            "4) V Diagnostice ověř, že Kvaser driver je OK\n\n"
            f"Detail: {msg}"
        )
    return msg


def open_bus(
    *,
    interface: str,
    channel: int,
    bitrate: int,
    receive_own_messages: bool = False,
    **kwargs: Any,
):
    """Open a python-can Bus; apply Kvaser ioctl workaround on known failure."""
    import can

    iface = (interface or "kvaser").lower()
    args = dict(
        interface=iface,
        channel=int(channel),
        bitrate=int(bitrate),
        receive_own_messages=bool(receive_own_messages),
        **kwargs,
    )
    if iface != "kvaser":
        return can.interface.Bus(**args)

    _patch_kvaser_local_txack()
    try:
        return can.interface.Bus(**args)
    except Exception as exc:
        # Second attempt after late patch (if first import path differed)
        if "canIoCtl" in str(exc) or "Error Code -1" in str(exc):
            _patch_kvaser_local_txack()
            try:
                return can.interface.Bus(**args)
            except Exception as exc2:
                raise RuntimeError(format_can_open_error(exc2)) from exc2
        raise RuntimeError(format_can_open_error(exc)) from exc
