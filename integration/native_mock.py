"""Closed fake Bleak boundary for tests only; never imports the real driver."""
import asyncio
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from madoka_protocol import build_query, split_in_chunks, WRITE_CHAR, NOTIFY_CHAR
from madoka_readonly_canary import GET_COMMANDS
from madoka_multiroom import native_access as access
from madoka_multiroom.config import demo_config, AppConfig
from madoka_multiroom.backends import native_radio
from madoka_multiroom.backends.physical_sessions import PhysicalSessionBudget
from madoka_multiroom.backends.native_fleet import NativeReadonlyFleet

SHA = "4893be8a5bac6c7d9b5f82a435fe335fcb02f38b"


@contextmanager
def closed_native(directory):
    """Run actual preflight/factory with fictitious OS evidence and fake SDK only."""
    directory = Path(directory)
    raw = demo_config().model_dump()
    for device in raw["devices"]:
        device["queries"]["fields"] = ("power", "mode", "setpoint", "sensor")
    config = AppConfig.model_validate(raw)
    identity = config.devices[0].address
    inventory = directory / "inventory.json"
    inventory.write_text(json.dumps({"controllers": {"room-1": {"uuid": identity}}}))
    inventory.chmod(0o600)
    options = SimpleNamespace(calls=[], clients=[], values={}, stage=None, failure=None,
        malformed=None, returned=identity, stop_error=None, disconnect_error=None,
        delay=0, budget=None, inventory=inventory, config=config)

    async def boundary(stage):
        options.calls.append(stage)
        if options.stage == stage:
            if options.delay:
                await asyncio.sleep(options.delay)
            if options.failure:
                raise options.failure

    class Scanner:
        @staticmethod
        async def find_device_by_address(address, timeout):
            assert address == identity and timeout == 15
            assert options.budget.records()[0]["state"] == "reserved"
            await boundary("discovery")
            return None if options.returned is None else SimpleNamespace(address=options.returned)

    class Client:
        def __init__(self, device, *, timeout, disconnected_callback):
            assert device.address == identity and timeout == 120
            self.callback = disconnected_callback
            self.is_connected = False
            options.clients.append(self)

        async def connect(self):
            await boundary("connect")
            self.is_connected = True

        async def start_notify(self, characteristic, callback):
            assert characteristic == NOTIFY_CHAR
            await boundary("subscribe")
            self.notify = callback

        async def write_gatt_char(self, characteristic, data, response):
            command = int.from_bytes(data[3:5], "big")
            assert characteristic == WRITE_CHAR and response is False
            assert command in GET_COMMANDS and data == bytes(build_query(command)[0])
            await boundary("query")
            options.calls.append(command)
            values = {0x20: {0x20: b"\x01"}, 0x30: {0x20: b"\x03"},
                0x40: {0x20: b"\x0b\x00", 0x21: b"\x0b\x00"}, 0x110: {0x40: b"\x17"}}
            objects = options.values.get(command, values[command])
            body = b"".join(bytes((key, len(value))) + value for key, value in objects.items())
            payload = bytes((4+len(body), 0)) + command.to_bytes(2, "big") + body
            chunks = options.malformed(command, payload) if options.malformed else split_in_chunks(bytearray(payload))
            for chunk in chunks:
                self.notify(None, chunk)

        async def stop_notify(self, characteristic):
            await boundary("stop_notify")
            if options.stop_error:
                raise options.stop_error

        async def disconnect(self):
            await boundary("disconnect")
            if options.disconnect_error:
                raise options.disconnect_error
            self.is_connected = False
            self.callback(self)

    module = ModuleType("bleak")
    module.BleakScanner, module.BleakClient = Scanner, Client
    with ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, {"bleak": module}))
        stack.enter_context(patch.dict(os.environ))
        for key in ("PYTHONPATH", "CI", "MADOKA_OFFLINE_ONLY"):
            os.environ.pop(key, None)
        stack.enter_context(patch.object(access, "version", lambda name: "3.0.2"))
        stack.enter_context(patch.object(native_radio, "version", lambda name: "3.0.2"))
        stack.enter_context(patch.object(access, "verify_checkout", lambda sha: None))
        stack.enter_context(patch.object(access.getpass, "getpass", lambda prompt: "READ ONLY WEB " + identity))
        with patch.object(sys, "platform", "darwin"), patch.object(sys.stdin, "isatty", lambda: True), patch.object(sys.stdout, "isatty", lambda: True):
            grant = access.authorize(inventory=str(inventory), controller_id="room-1", reviewed_sha=SHA,
                                     authorization_reference="offline mocked owner GO")
        options.grant = grant
        options.budget = PhysicalSessionBudget(directory / "sessions.sqlite", native_authorization=grant)
        options.fleet = NativeReadonlyFleet(config, options.budget, grant)
        try:
            yield options
        finally:
            options.budget.close_storage()
