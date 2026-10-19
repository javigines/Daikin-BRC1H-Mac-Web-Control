"""Ready-only hardened Controller bridge with explicit isolated native admission."""
import asyncio
from collections import deque
from dataclasses import dataclass
from uuid import UUID

from daikin_server import Controller
from ble_lifecycle import cleanup_client
from madoka_protocol import (
    Reassembler, cmd_id_of, parse_objects, split_in_chunks, NOTIFY_CHAR,
    CMD_GET_POWER, CMD_GET_MODE, CMD_GET_SETPOINT, CMD_GET_FAN, CMD_GET_SENSOR,
    CMD_SET_POWER, CMD_SET_MODE, CMD_SET_SETPOINT, CMD_SET_FAN, temp_to_bytes,
)
from .base import BackendUnavailable
from ..native_access import manual_transport

GETS = (CMD_GET_POWER, CMD_GET_MODE, CMD_GET_SETPOINT, CMD_GET_FAN, CMD_GET_SENSOR)
SETS = (CMD_SET_POWER, CMD_SET_MODE, CMD_SET_SETPOINT, CMD_SET_FAN)


@dataclass(frozen=True)
class WireReply:
    objects: dict | None = None
    chunks: tuple[bytes, ...] | None = None
    delay: float = 0
    error: BaseException | None = None
    disconnect: bool = False


class ScriptedRadio:
    """Data-only Bleak-shaped wire model, never a callback/driver factory.

    State changes model a SET effect explicitly; apply_writes=False models an
    ACK with no effect. Plans can override reads or inject raw malformed frames.
    """
    def __init__(self, identity, *, plan=None, setup_delay=0, setup_error=None,
                 stop_error=None, disconnect_error=None, apply_writes=True,
                 connect_delay=0, connect_error=None, subscribe_delay=0, subscribe_error=None,
                 stop_delay=0, disconnect_delay=0):
        self.identity = str(UUID(identity))
        self.plan = {k: deque(v) for k, v in (plan or {}).items()}
        self.setup_delay, self.setup_error = setup_delay, setup_error
        self.stop_error, self.disconnect_error = stop_error, disconnect_error
        self.stop_delay, self.disconnect_delay = stop_delay, disconnect_delay
        self.connect_delay, self.connect_error = connect_delay, connect_error
        self.subscribe_delay, self.subscribe_error = subscribe_delay, subscribe_error
        self.apply_writes = apply_writes
        self.is_connected = False
        self.calls, self.setup_calls, self.cleanup_calls = [], [], []
        self.reassembler = Reassembler()
        self.state = {"power": 1, "mode": 3, "fan_cool": 3, "fan_heat": 3,
                      "cool": 22, "heat": 22, "room": 23, "min": 16, "max": 32}

    async def discover(self, identity, timeout):
        self.setup_calls.append(("discover", timeout))
        if identity != self.identity:
            raise BackendUnavailable("Selected identity mismatch")
        await asyncio.sleep(self.setup_delay)
        if self.setup_error:
            raise self.setup_error
        return self.identity

    async def connect(self, *, timeout):
        self.setup_calls.append(("connect", timeout))
        await asyncio.sleep(self.connect_delay)
        if self.connect_error:
            raise self.connect_error
        self.is_connected = True

    async def start_notify(self, characteristic, callback):
        self.setup_calls.append(("subscribe", characteristic))
        self.notify = callback
        await asyncio.sleep(self.subscribe_delay)
        if self.subscribe_error:
            raise self.subscribe_error

    def observations(self, command):
        s = self.state
        return {
            CMD_GET_POWER: {0x20: bytes([s["power"]])},
            CMD_GET_MODE: {0x20: bytes([s["mode"]])},
            CMD_GET_FAN: {0x20: bytes([s["fan_cool"]]), 0x21: bytes([s["fan_heat"]])},
            CMD_GET_SETPOINT: {0x20: temp_to_bytes(s["cool"]), 0x21: temp_to_bytes(s["heat"]),
                **{k: temp_to_bytes(s["min"]) for k in (0xA2, 0xA3)},
                **{k: temp_to_bytes(s["max"]) for k in (0xB2, 0xB3)}},
            CMD_GET_SENSOR: {0x40: int(s["room"]).to_bytes(1, "big", signed=True)},
        }[command]

    async def write_gatt_char(self, characteristic, data, response=False):
        payload = self.reassembler.feed(bytearray(data))
        if payload is None:
            return
        command, args = cmd_id_of(payload), parse_objects(payload)
        self.calls.append((command, args))
        plan = self.plan.get(command)
        reply = plan.popleft() if plan and len(plan) > 1 else plan[0] if plan else WireReply()
        await asyncio.sleep(reply.delay)
        if reply.disconnect:
            self.is_connected = False
            self.disconnected_callback(self)
            raise ConnectionError("Scripted link lost")
        if reply.error:
            raise reply.error
        if command in SETS and self.apply_writes:
            if command == CMD_SET_POWER: self.state["power"] = args[0x20][0]
            elif command == CMD_SET_MODE: self.state["mode"] = args[0x20][0]
            elif command == CMD_SET_FAN:
                self.state.update(fan_cool=args[0x20][0], fan_heat=args[0x21][0])
            else:
                self.state.update(cool=int.from_bytes(args[0x20], "big") / 128,
                                  heat=int.from_bytes(args[0x21], "big") / 128)
        objects = reply.objects if reply.objects is not None else self.observations(command) if command in GETS else {}
        body = b"".join(bytes((k, len(v))) + v for k, v in objects.items())
        answer = bytes((4 + len(body), 0)) + command.to_bytes(2, "big") + body
        for chunk in reply.chunks if reply.chunks is not None else split_in_chunks(bytearray(answer)):
            self.notify(None, chunk)

    async def stop_notify(self, characteristic):
        self.cleanup_calls.append("stop_notify")
        await asyncio.sleep(self.stop_delay)
        if self.stop_error:
            raise self.stop_error

    async def disconnect(self):
        self.cleanup_calls.append("disconnect")
        await asyncio.sleep(self.disconnect_delay)
        if self.disconnect_error:
            raise self.disconnect_error
        self.is_connected = False


class _CleanupCalls:
    is_connected = True

    def __init__(self, client, report):
        self.client, self.report = client, report

    async def call(self, name, *args):
        item = {"call": name, "status": "uncertain"}
        self.report.append(item)
        try:
            await getattr(self.client, name)(*args)
            item["status"] = "public_return_native_unverified"
        except asyncio.CancelledError:
            raise RuntimeError("Cleanup call cancelled") from None

    async def stop_notify(self, characteristic):
        await self.call("stop_notify", characteristic)

    async def disconnect(self):
        await self.call("disconnect")


class HardenedControllerBridge(Controller):
    """No lazy setup, no second connection, strict callbacks, retained uncertainty."""
    def __init__(self, identity, radio, *, native_authorization=None):
        self.native = native_authorization is not None
        if self.native:
            from .native_radio import NativeRadio
            from ..native_access import require_native_context, manual_transport
            grant = require_native_context(native_authorization)
            if type(radio) is not NativeRadio or radio.grant is not grant:
                raise BackendUnavailable("Explicit native radio authorization required")
        elif type(radio) is not ScriptedRadio:
            raise BackendUnavailable("Only the closed offline radio model is allowed")
        if str(UUID(identity)) != radio.identity:
            raise BackendUnavailable("Selected radio identity mismatch")
        super().__init__(str(UUID(identity)))
        self.radio = radio
        self.cleanup_report = []
        self.cleanup_attempted = self.preparation_attempted = False
        self.cleanup_failed = False

    @classmethod
    def from_physical(cls, *args, **kwargs):
        raise BackendUnavailable("Hardware runtime factory disabled pending physical review")

    @classmethod
    def from_native_readonly(cls, grant):
        from .native_radio import NativeRadio
        return cls(grant.identity, NativeRadio(grant), native_authorization=grant)

    @property
    def ready(self):
        return bool(self.connected and not self.failure and not self.closed
                    and self.client is not None and self.client.is_connected)

    async def prepare(self):
        if self.preparation_attempted or self.closed or self.failure:
            raise BackendUnavailable("One preparation only; no retry/reconnection")
        self.preparation_attempted = True
        async with self.lock:
            try:
                async with asyncio.timeout(150):
                    found = await asyncio.wait_for(self.radio.discover(self.address, 15), 15)
                    if found != self.address:
                        raise BackendUnavailable("Discovery identity mismatch")
                    self.client = self.radio
                    self.radio.disconnected_callback = self._on_disconnect
                    # Pairing can occur during subscription. This shared setup
                    # window starts AFTER discovery, outside operation timers.
                    async with asyncio.timeout(120):
                        await self.radio.connect(timeout=120)
                        await self.radio.start_notify(NOTIFY_CHAR, self._on_notify)
                    if not self.radio.is_connected or self.failure or self.closed:
                        raise BackendUnavailable("Setup did not establish readiness")
                    self.connected = True
            except (Exception, asyncio.CancelledError):
                self._fail("Prepared setup interrupted; STOP")
                await self._cleanup_after_error()
                raise

    async def _ensure_connected(self):
        if not self.ready:
            raise BackendUnavailable("Ready-only exchange unavailable; no reconnect")

    def _on_notify(self, sender, data):
        if self.closed or self.failure:
            return
        try:
            if not self.pending or (self.reasm.chunks and data and data[0] == 0):
                raise ValueError("Unexpected notification")
            payload = self.reasm.feed(bytearray(data))
            if payload is None:
                if not self.reasm.chunks:
                    raise ValueError("Malformed chunk")
                return
            command = cmd_id_of(payload)
            if payload[1] != 0 or command not in self.pending or self.pending[command].done():
                raise ValueError("Unexpected response")
            parse_objects(payload)
            seen, offset = set(), 4
            while offset < len(payload):
                oid = payload[offset]
                if oid in seen:
                    raise ValueError("Duplicate object")
                seen.add(oid)
                offset += 2 + payload[offset + 1]
            self.pending[command].set_result(payload)
        except Exception:
            self._fail("Malformed or ambiguous response; STOP")

    async def exchange_ready(self, cmd_id, args=None, timeout=6):
        if self.native:
            from madoka_readonly_canary import GET_COMMANDS
            office = manual_transport(self.radio.grant)
            allowed = GET_COMMANDS + ((CMD_GET_FAN,) if office else ())
            if cmd_id not in allowed and not (office and cmd_id in SETS and self.radio.write_frames):
                raise BackendUnavailable("Native bridge permits four GETs only")
        if cmd_id not in GETS + SETS:
            raise ValueError("Command not allowed")
        if cmd_id in GETS and args:
            raise ValueError("GET arguments rejected")
        await self._ensure_connected()
        return await self.request(cmd_id, args, timeout)

    async def _cleanup_after_error(self):
        if self.cleanup_failed:
            raise BackendUnavailable("Previous cleanup failed; native state remains uncertain")
        if self.client is not None and not self.cleanup_attempted:
            self.cleanup_attempted = True
            try:
                await cleanup_client(_CleanupCalls(self.client, self.cleanup_report))
                if self.native and manual_transport(self.radio.grant):
                    from .native_release import verify_live_release
                    self.radio.release_evidence = await verify_live_release(self.radio)
            except BaseException:
                self.cleanup_failed = True
                raise
            # Retain client identity even after return: native state is unverified.

    async def disconnect(self):
        self.closed = True
        self._fail("Bridge closed; native state unverified")
        async with self.lock:
            if (self.native and manual_transport(self.radio.grant)
                    and not self.radio.connect_attempted and self.radio.client is None):
                self.radio.release_evidence = {"kind": "no_native_connect_attempted", "application_ownership_only": True}
            await self._cleanup_after_error()
