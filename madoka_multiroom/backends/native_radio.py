"""Lazy Bleak 3.0.2 transport with an independent four-GET wire allowlist."""
import asyncio
from importlib.metadata import version

from madoka_protocol import WRITE_CHAR, NOTIFY_CHAR, build_query
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
                disconnected_callback=lambda _client: self.disconnected_callback(self))
            await asyncio.wait_for(self.client.connect(), 120)
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
        if (not self.reserved or not self.is_connected or characteristic != WRITE_CHAR
                or response is not False or bytes(data) not in self.allowed_frames):
            raise BackendUnavailable("Only exact reviewed GET frames are permitted")
        await self._call("write_gatt_char", characteristic, bytes(data), response=False)

    async def stop_notify(self, characteristic):
        if characteristic != NOTIFY_CHAR:
            raise BackendUnavailable("Native cleanup characteristic rejected")
        await self._call("stop_notify", characteristic)

    async def disconnect(self):
        await self._call("disconnect")
