"""Pinned CoreBluetooth evidence for APP ownership, not global radio safety.

Public is_connected conflates disconnected, connecting and disconnecting.
Read the actual peripheral state and the system connected-service inventory.
No scan, connect, pairing or SET is performed by these evidence functions.
"""
import asyncio
import time

from madoka_protocol import SERVICE_UUID
from madoka_readonly_canary import canonical_uuid
from .base import BackendUnavailable


def native_snapshot(client, identity):
    """Bleak 3.0.2 private layout: reject any drift instead of guessing."""
    try:
        from CoreBluetooth import CBUUID
        backend = client._backend
        peripheral = backend._peripheral
        delegate = backend._central_manager_delegate
        central = delegate.central_manager
        if canonical_uuid(peripheral.identifier().UUIDString()) != identity or int(central.state()) != 5:
            raise ValueError("Native identity or adapter state mismatch")
        connected = central.retrieveConnectedPeripheralsWithServices_([CBUUID.UUIDWithString_(SERVICE_UUID)])
        present = any(canonical_uuid(p.identifier().UUIDString()) == identity for p in connected)
        return int(peripheral.state()), present
    except Exception:
        raise BackendUnavailable("Native ownership evidence unavailable; quarantine retained") from None


async def stable_absence(snapshot, *, duration=2.0):
    """Bounded sampling; elapsed time alone never releases ownership."""
    deadline = time.monotonic() + duration
    while True:
        if snapshot() != (0, False):
            raise BackendUnavailable("Native peripheral not demonstrably disconnected")
        if time.monotonic() >= deadline:
            return
        await asyncio.sleep(min(.2, max(0, deadline - time.monotonic())))


async def verify_live_release(radio):
    # The callback belongs to this exact client/epoch. A connected public
    # disconnect must have completed without error; early returns do not count.
    if getattr(radio.grant, "local", False):
        if not radio.disconnect_requested or not radio.disconnect_returned:
            raise BackendUnavailable("No se ha completado el cierre de Bluetooth")
        await stable_absence(lambda: native_snapshot(radio.client, radio.identity))
        return {"kind": "local_completed_cleanup_and_native_absence", "bleak": "3.0.2",
                "callback_seen": radio.disconnect_callback_seen,
                "application_ownership_only": True, "global_radio_release": "not_proven"}
    if (not radio.disconnect_requested or not radio.disconnect_returned
            or not radio.disconnect_callback_seen
            or not (radio.disconnect_started_connected or radio.callback_before_cleanup)):
        raise BackendUnavailable("Correlated native disconnect evidence missing")
    await stable_absence(lambda: native_snapshot(radio.client, radio.identity))
    return {"kind": "correlated_callback_and_native_absence", "bleak": "3.0.2",
            "application_ownership_only": True, "global_radio_release": "not_proven"}
