"""Lazy Bleak 3.0.2 transport with an independent four-GET wire allowlist."""
import asyncio
from importlib.metadata import version

from madoka_protocol import WRITE_CHAR, NOTIFY_CHAR, build_query, build_command, CMD_GET_FAN
from madoka_readonly_canary import GET_COMMANDS, canonical_uuid, REVIEWED_BLEAK_VERSION
from ..native_access import require_authorization
from .base import BackendUnavailable


class NativeRadio:
    """One native client, no reconnect; admission is durable before driver import."""
    def __init__(self, grant):
        self.grant = require_authorization(grant)
        self.identity = grant.identity
        self.client = self.device = None
        self.reserved = self.discovery_attempted = self.connect_attempted = False
        self.allowed_frames = frozenset(bytes(chunk) for command in GET_COMMANDS for chunk in build_query(command))
        if getattr(grant, "office", False):
            self.allowed_frames |= frozenset(bytes(c) for c in build_query(CMD_GET_FAN))
        self.write_frames = []
        self.disconnect_requested = self.disconnect_returned = False
        self.disconnect_callback_seen = self.disconnect_started_connected = False
        self.connected_once = self.callback_before_cleanup = False

    def _disconnected(self, client):
        if client is not self.client:
            return
        self.disconnect_callback_seen = True
        self.callback_before_cleanup |= not self.disconnect_requested and self.connected_once
        self.write_frames.clear()
        self.disconnected_callback(self)

    def arm_write(self, command, args):
        """One exact ordered SET frame sequence; consume each before awaiting I/O."""
        require_authorization(self.grant)
        from madoka_protocol import CMD_SET_POWER, CMD_SET_MODE, CMD_SET_FAN, CMD_SET_SETPOINT
        if (not getattr(self.grant, "office", False) or not self.reserved or not self.is_connected
                or self.write_frames or command not in (CMD_SET_POWER, CMD_SET_MODE, CMD_SET_FAN, CMD_SET_SETPOINT)):
            raise BackendUnavailable("Native write admission rejected")
        self.write_frames = [bytes(c) for c in build_command(command, args)]

    @property
    def is_connected(self):
        try:
            return bool(self.client is not None and self.client.is_connected)
        except Exception:
            return False

    async def discover(self, identity, timeout):
        require_authorization(self.grant)
        if (not self.reserved or self.discovery_attempted or identity != self.identity
                or timeout != 15 or version("bleak") != REVIEWED_BLEAK_VERSION):
            raise BackendUnavailable("Native discovery admission rejected")
        self.discovery_attempted = True
        try:
            from bleak import BleakScanner
            self.device = await asyncio.wait_for(
                BleakScanner.find_device_by_address(identity, timeout=15), 15)
            if self.device is None or canonical_uuid(self.device.address) != identity:
                raise BackendUnavailable("Selected native device unavailable")
            return identity
        except asyncio.CancelledError:
            raise
        except Exception:
            raise BackendUnavailable("Native discovery failed; STOP") from None

    async def connect(self, *, timeout):
        require_authorization(self.grant)
        if not self.reserved or self.device is None or self.connect_attempted or timeout != 120:
            raise BackendUnavailable("Native connection admission rejected")
        self.connect_attempted = True
        try:
            from bleak import BleakClient
            self.client = BleakClient(self.device, timeout=120,
                disconnected_callback=self._disconnected)
            await asyncio.wait_for(self.client.connect(), 120)
            self.connected_once = True
        except asyncio.CancelledError:
            raise
        except Exception:
            raise BackendUnavailable("Native connection failed; STOP") from None

    async def _call(self, name, *args, **kwargs):
        if self.client is None:
            raise BackendUnavailable("Native client unavailable")
        try:
            return await getattr(self.client, name)(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise BackendUnavailable("Native operation failed; STOP") from None

    async def start_notify(self, characteristic, callback):
        if characteristic != NOTIFY_CHAR or not self.is_connected:
            raise BackendUnavailable("Native subscription rejected")
        await asyncio.wait_for(self._call("start_notify", characteristic, callback), 120)

    async def write_gatt_char(self, characteristic, data, response=False):
        require_authorization(self.grant)
        frame = bytes(data)
        if (not self.reserved or not self.is_connected or characteristic != WRITE_CHAR
                or response is not False):
            raise BackendUnavailable("Native frame boundary rejected")
        if self.write_frames:
            if frame != self.write_frames[0]:
                self.write_frames.clear()
                raise BackendUnavailable("Native SET frame sequence mismatch; STOP")
            self.write_frames.pop(0)
        elif frame not in self.allowed_frames:
            raise BackendUnavailable("Only exact reviewed GET frames are permitted")
        await self._call("write_gatt_char", characteristic, frame, response=False)

    async def stop_notify(self, characteristic):
        if characteristic != NOTIFY_CHAR:
            raise BackendUnavailable("Native cleanup characteristic rejected")
        if getattr(self.grant, "office", False) and self.callback_before_cleanup:
            from .native_release import native_snapshot
            if native_snapshot(self.client, self.identity) == (0, False):
                return  # Native callback ended the subscription; no GATT call on a lost link.
        await self._call("stop_notify", characteristic)

    async def disconnect(self):
        self.write_frames.clear()
        if getattr(self.grant, "office", False):
            from .native_release import native_snapshot
            self.disconnect_requested = True
            self.disconnect_started_connected = native_snapshot(self.client, self.identity)[0] == 2
        await self._call("disconnect")
        self.disconnect_returned = True
