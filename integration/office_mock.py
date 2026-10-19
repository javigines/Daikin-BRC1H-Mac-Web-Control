"""Closed six-controller native SDK model. Never imports a physical driver."""
import asyncio
from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from madoka_protocol import (
    CMD_GET_POWER, CMD_GET_MODE, CMD_GET_SETPOINT, CMD_GET_FAN, CMD_GET_SENSOR,
    CMD_SET_POWER, CMD_SET_MODE, CMD_SET_SETPOINT, CMD_SET_FAN,
    SETPOINT_EXTRA_FIELDS, Reassembler, cmd_id_of, parse_objects, split_in_chunks, temp_to_bytes,
)
from madoka_multiroom import office_access as access
from madoka_multiroom.config import demo_config
from madoka_multiroom.backends import native_radio, native_release
from madoka_multiroom.backends.physical_sessions import PhysicalSessionBudget, ConnectionPlan
from madoka_multiroom.backends.office_native import OfficeFleet

SHA = "02fa53c7773e4a551158165cab5f246a0d19b03a"


@contextmanager
def closed_office(directory, *, concurrency=2, port=8443):
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=True)
    rooms = demo_config().model_dump(mode="json")
    rooms["max_concurrent_operations"] = concurrency
    config = access.OfficeConfig.model_validate({"rooms": rooms,
        "controls": {d["id"]: {"power": True, "setpoint": True, "modes": [2, 3, 4], "fans": [1, 2, 3, 4, 5]} for d in rooms["devices"]},
        "certificate": str(directory / "certificate.pem"), "private_key": str(directory / "key.pem"),
        "authorities": [f"127.0.0.1:{port}"], "port": port})
    identifiers = {d.address: d.id for d in config.rooms.devices}
    options = SimpleNamespace(config=config, calls=[], frames=[], clients=[], stage=None, error=None,
        room=None, delay=0, apply_writes=True, ack={}, fail_readback=False, no_callback=False,
        disconnect_error=False, stop_error=False, states={}, native_state_override=None,
        connected_override=None, active=0, peak=0)
    options.failed_effect_rooms = set()
    for d in config.rooms.devices:
        block = {0x20: temp_to_bytes(22), 0x21: temp_to_bytes(21),
                 **{k: value.to_bytes(size, "big") for k, size, value in SETPOINT_EXTRA_FIELDS}}
        block.update({k: temp_to_bytes(16) for k in (0xA2, 0xA3)})
        block.update({k: temp_to_bytes(32) for k in (0xB2, 0xB3)})
        options.states[d.id] = {CMD_GET_POWER: {0x20: b"\x01"}, CMD_GET_MODE: {0x20: b"\x03"},
            CMD_GET_SETPOINT: block, CMD_GET_FAN: {0x20: b"\x03", 0x21: b"\x02"}, CMD_GET_SENSOR: {0x40: b"\x17"}}

    async def boundary(stage, room):
        options.calls.append((room, stage))
        options.active += 1
        options.peak = max(options.peak, options.active)
        try:
            if options.stage == stage and options.room in (None, room):
                await asyncio.sleep(options.delay)
                if options.error:
                    raise options.error
        finally:
            options.active -= 1

    class CBUUID:
        @staticmethod
        def UUIDWithString_(value):
            return value

    class Peripheral:
        def __init__(self, client):
            self.client = client
        def identifier(self):
            return SimpleNamespace(UUIDString=lambda: self.client.identity)
        def state(self):
            return options.native_state_override if options.native_state_override is not None else 2 if self.client.is_connected else 0

    class Central:
        def state(self):
            return 5
        def retrieveConnectedPeripheralsWithServices_(self, services):
            return [c._backend._peripheral for c in options.clients if
                (options.connected_override if options.connected_override is not None else c.is_connected)]

    class Scanner:
        @staticmethod
        async def find_device_by_address(identity, timeout):
            room = identifiers[identity]
            assert any(r["room"] == room and r["state"] == "reserved" for r in options.budget.records())
            await boundary("discovery", room)
            return SimpleNamespace(address=identity)

    class Client:
        def __init__(self, device, *, timeout, disconnected_callback):
            self.identity, self.room = device.address, identifiers[device.address]
            self.is_connected = self.wrote = False
            self.callback, self.reasm = disconnected_callback, Reassembler()
            self._backend = SimpleNamespace(_peripheral=Peripheral(self), _central_manager_delegate=SimpleNamespace(central_manager=Central()))
            options.clients.append(self)
        async def connect(self):
            await boundary("connect", self.room)
            self.is_connected = True
        async def start_notify(self, char, callback):
            self.notify = callback
            await boundary("subscribe", self.room)
        async def write_gatt_char(self, char, data, response):
            options.frames.append((self.room, bytes(data)))
            payload = self.reasm.feed(bytearray(data))
            if payload is None:
                return
            command, args = cmd_id_of(payload), parse_objects(payload)
            options.calls.append((self.room, command, args))
            is_set = command in (CMD_SET_POWER, CMD_SET_MODE, CMD_SET_SETPOINT, CMD_SET_FAN)
            if is_set:
                self.wrote = True
            await boundary("write" if is_set else "readback" if self.wrote else "query", self.room)
            if not is_set and self.wrote and options.fail_readback:
                raise RuntimeError("Closed mock failed readback")
            if is_set:
                if options.apply_writes and self.room not in options.failed_effect_rooms:
                    group = {CMD_SET_POWER: CMD_GET_POWER, CMD_SET_MODE: CMD_GET_MODE,
                             CMD_SET_SETPOINT: CMD_GET_SETPOINT, CMD_SET_FAN: CMD_GET_FAN}[command]
                    options.states[self.room][group].update(args)
                objects = options.ack
            else:
                objects = options.states[self.room][command]
            body = b"".join(bytes((k, len(v))) + v for k, v in objects.items())
            answer = bytes((4 + len(body), 0)) + command.to_bytes(2, "big") + body
            for chunk in split_in_chunks(bytearray(answer)):
                self.notify(None, chunk)
        async def stop_notify(self, char):
            await boundary("stop_notify", self.room)
            if options.stop_error:
                raise RuntimeError("Mock unsubscribe failed")
        async def disconnect(self):
            await boundary("disconnect", self.room)
            if options.disconnect_error:
                raise RuntimeError("Mock disconnect failed")
            self.is_connected = False
            if not options.no_callback:
                self.callback(self)

    async def fast_absence(snapshot, **kwargs):
        if snapshot() != (0, False):
            raise RuntimeError("Mock native absence not established")

    bleak, cb = ModuleType("bleak"), ModuleType("CoreBluetooth")
    bleak.BleakScanner, bleak.BleakClient, cb.CBUUID = Scanner, Client, CBUUID
    with ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, {"bleak": bleak, "CoreBluetooth": cb}))
        stack.enter_context(patch.dict(os.environ))
        for key in ("CI", "MADOKA_OFFLINE_ONLY", "PYTHONPATH"):
            os.environ.pop(key, None)
        stack.enter_context(patch.object(access, "environment", lambda: None))
        stack.enter_context(patch.object(access, "verify_checkout", lambda sha: None))
        stack.enter_context(patch.object(access.getpass, "getpass", lambda prompt: "AUTHORIZE OFFICE " + SHA))
        stack.enter_context(patch.object(sys.stdin, "isatty", lambda: True))
        stack.enter_context(patch.object(sys.stdout, "isatty", lambda: True))
        stack.enter_context(patch("madoka_multiroom.native_access.pwd.getpwuid", lambda uid: SimpleNamespace(pw_dir=str(directory))))
        stack.enter_context(patch.object(native_radio, "version", lambda name: "3.0.2"))
        stack.enter_context(patch.object(native_release, "stable_absence", fast_absence))
        options.password = access.authorize_service(config, SHA, "closed mocked commissioning GO")
        options.grants, _ = access.service_grants(config)
        options.budget = PhysicalSessionBudget(directory / ".madoka-readonly-web" / "native-sessions.sqlite",
            ConnectionPlan("retained", 6, concurrency), native_authorization=next(iter(options.grants.values())), native_grants=options.grants)
        options.fleet = OfficeFleet(config, options.budget, options.grants)
        try:
            yield options
        finally:
            options.budget.close_storage()
