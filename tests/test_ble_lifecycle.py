"""Offline fault injection: backend call completion is not hardware proof."""
import asyncio
import sys
from types import SimpleNamespace

import pytest

import ble_lifecycle as lifecycle
import brc1h_spike as spike
import daikin_server as d


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    def __init__(self, device=None, disconnected_callback=None):
        self.is_connected = False
        self.callback = disconnected_callback
        self.notify = None
        self.calls = []
        self.errors = {}
        self.blocks = {}
        self.respond = True
        self.written = asyncio.Event()

    async def step(self, name):
        self.calls.append(name)
        if name in self.blocks:
            await self.blocks[name].wait()
        if name in self.errors:
            raise self.errors[name]

    async def connect(self):
        await self.step("connect")
        self.is_connected = True

    async def start_notify(self, char, callback):
        self.notify = callback  # simulate partially registered notify on error
        await self.step("start_notify")

    async def stop_notify(self, char):
        await self.step("stop_notify")

    async def disconnect(self):
        await self.step("disconnect")
        self.is_connected = False
        if self.callback:
            self.callback(self)

    def reply(self, cid, value=1):
        self.notify(None, bytearray([0, 7, 0, cid >> 8, cid & 255, 0x20, 1, value]))

    async def write_gatt_char(self, char, data, response):
        self.written.set()
        await self.step("write")
        if self.respond:
            self.reply(int.from_bytes(data[3:5], "big"))


def install(monkeypatch, client, found=True):
    async def find(*args, **kwargs):
        return SimpleNamespace(address="offline") if found else None

    backend = sys.modules["bleak"]
    backend.BleakScanner.find_device_by_address = find

    def factory(device, disconnected_callback=None):
        client.callback = disconnected_callback
        return client

    backend.BleakClient = factory


def test_connection_reused_and_matching_response_only(monkeypatch):
    async def case():
        c = FakeClient()
        install(monkeypatch, c)
        ctrl = d.Controller("offline")
        assert await ctrl.request(d.CMD_GET_POWER) == {0x20: b"\x01"}
        c.respond = False
        task = asyncio.create_task(ctrl.request(d.CMD_GET_MODE))
        c.written.clear()
        await c.written.wait()
        c.reply(d.CMD_GET_POWER, 0)
        await asyncio.sleep(0)
        assert not task.done()
        c.reply(d.CMD_GET_MODE, 3)
        assert await task == {0x20: b"\x03"}
        assert c.calls.count("connect") == 1
        assert ctrl.pending == {}
        await ctrl.disconnect()
        assert ctrl.client is None and not ctrl.connected
    run(case())


@pytest.mark.parametrize("stage", ["connect", "start_notify", "write"])
def test_backend_failure_cleans_up_and_preserves_original_error(monkeypatch, stage):
    async def case():
        c = FakeClient()
        original = ValueError("injected " + stage)
        c.errors[stage] = original
        install(monkeypatch, c)
        ctrl = d.Controller("offline")
        with pytest.raises(ValueError) as exc:
            await ctrl.request(d.CMD_GET_POWER)
        assert exc.value is original
        assert "disconnect" in c.calls
        if stage != "connect":
            assert c.calls[-2:] == ["stop_notify", "disconnect"]
        assert not ctrl.connected and not ctrl.latest["connected"]
        assert ctrl.pending == {} and ctrl.reasm.chunks == []
        count = len(c.calls)
        with pytest.raises(ConnectionError, match="review/restart"):
            await ctrl.request(d.CMD_GET_POWER)
        assert len(c.calls) == count  # no automatic reconnect
    run(case())


def test_not_found_latches_failure_without_constructing_client(monkeypatch):
    async def case():
        c = FakeClient()
        install(monkeypatch, c, found=False)
        ctrl = d.Controller("offline")
        with pytest.raises(ConnectionError, match="not found"):
            await ctrl.request(d.CMD_GET_POWER)
        assert ctrl.client is None and c.calls == []
    run(case())


@pytest.mark.parametrize("block_write", [False, True])
def test_timeout_bounds_write_and_response_and_rejects_late_reply(monkeypatch, block_write):
    async def case():
        c = FakeClient()
        c.respond = False
        if block_write:
            c.blocks["write"] = asyncio.Event()
        install(monkeypatch, c)
        ctrl = d.Controller("offline")
        with pytest.raises(asyncio.TimeoutError):
            await ctrl.request(d.CMD_GET_POWER, timeout=0.01)
        c.reply(d.CMD_GET_POWER, 99)
        with pytest.raises(ConnectionError):
            await ctrl.request(d.CMD_GET_POWER)
        assert c.calls.count("write") == 1
        assert c.calls.count("connect") == 1
        assert not ctrl.lock.locked() and ctrl.pending == {}
    run(case())


@pytest.mark.parametrize("stage", ["connect", "start_notify", "write", "response"])
def test_cancel_active_operation_releases_resources_and_lock(monkeypatch, stage):
    async def case():
        c = FakeClient()
        c.respond = False
        if stage != "response":
            c.blocks[stage] = asyncio.Event()
        install(monkeypatch, c)
        ctrl = d.Controller("offline")
        task = asyncio.create_task(ctrl.request(d.CMD_GET_POWER))
        for _ in range(100):
            if (stage in c.calls) or (stage == "response" and c.written.is_set()):
                break
            await asyncio.sleep(0)
        else:
            pytest.fail("injected stage not reached")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "disconnect" in c.calls
        assert ctrl.pending == {} and not ctrl.lock.locked()
        with pytest.raises(ConnectionError):
            await ctrl.request(d.CMD_GET_MODE)
    run(case())


def test_serialization_and_cancelled_waiter_do_not_poison_active_operation(monkeypatch):
    async def case():
        c = FakeClient()
        c.respond = False
        install(monkeypatch, c)
        ctrl = d.Controller("offline")
        first = asyncio.create_task(ctrl.request(d.CMD_GET_POWER))
        await c.written.wait()
        second = asyncio.create_task(ctrl.request(d.CMD_GET_MODE))
        await asyncio.sleep(0)
        assert c.calls.count("write") == 1
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        c.reply(d.CMD_GET_POWER)
        assert await first == {0x20: b"\x01"}
        c.respond = True
        assert await ctrl.request(d.CMD_GET_MODE) == {0x20: b"\x01"}
        assert ctrl.failure is None
        await ctrl.disconnect()
    run(case())


@pytest.mark.parametrize("during_shutdown", [False, True])
def test_disconnect_wakes_pending_and_clears_cached_connected(monkeypatch, during_shutdown):
    async def case():
        c = FakeClient()
        c.respond = False
        install(monkeypatch, c)
        ctrl = d.Controller("offline")
        ctrl.latest = {"connected": True}
        task = asyncio.create_task(ctrl.request(d.CMD_GET_POWER))
        await c.written.wait()
        if during_shutdown:
            shutdown = asyncio.create_task(ctrl.disconnect())
        else:
            c.is_connected = False
            c.callback(c)
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(task, 0.5)
        if during_shutdown:
            await shutdown
        assert not ctrl.connected and not ctrl.latest["connected"]
        assert not ctrl.lock.locked() and ctrl.pending == {}
    run(case())


@pytest.mark.parametrize("stage", ["stop_notify", "disconnect"])
def test_cleanup_error_is_visible_and_disconnect_still_attempted(monkeypatch, stage):
    async def case():
        c = FakeClient()
        c.is_connected = True
        c.errors[stage] = ValueError("cleanup fault")
        with pytest.raises(RuntimeError, match=stage):
            await lifecycle.cleanup_client(c)
        assert c.calls == ["stop_notify", "disconnect"]
    run(case())


def test_cleanup_timeout_still_attempts_disconnect(monkeypatch):
    async def case():
        monkeypatch.setattr(lifecycle, "CLEANUP_TIMEOUT", 0.01)
        c = FakeClient()
        c.is_connected = True
        c.blocks["stop_notify"] = asyncio.Event()
        with pytest.raises(RuntimeError, match="TimeoutError"):
            await lifecycle.cleanup_client(c)
        assert c.calls == ["stop_notify", "disconnect"]
    run(case())


def test_repeated_cancellation_waits_for_cleanup(monkeypatch):
    async def case():
        c = FakeClient()
        c.is_connected = True
        gate = c.blocks["stop_notify"] = asyncio.Event()
        task = asyncio.create_task(lifecycle.cleanup_client(c))
        while "stop_notify" not in c.calls:
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert c.calls == ["stop_notify", "disconnect"]
    run(case())


def test_old_client_callback_is_ignored(monkeypatch):
    async def case():
        c = FakeClient()
        install(monkeypatch, c)
        ctrl = d.Controller("offline")
        await ctrl.ensure_connected()
        ctrl._on_disconnect(FakeClient())
        assert ctrl.connected and ctrl.failure is None
        await ctrl.disconnect()
    run(case())


def test_link_state_changes_before_disconnect_callback_do_not_reconnect(monkeypatch):
    async def case():
        c = FakeClient()
        install(monkeypatch, c)
        ctrl = d.Controller("offline")
        await ctrl.ensure_connected()
        c.is_connected = False  # callback has not been dispatched yet
        with pytest.raises(ConnectionError, match="review/restart"):
            await ctrl.request(d.CMD_GET_POWER)
        assert c.calls.count("connect") == 1
        assert "write" not in c.calls
        assert not ctrl.latest["connected"] and ctrl.pending == {}
    run(case())


@pytest.mark.parametrize("stage", ["scan", "connect"])
def test_shutdown_during_setup_prevents_next_ble_stage(monkeypatch, stage):
    async def case():
        c = FakeClient()
        install(monkeypatch, c)
        entered = asyncio.Event()
        gate = asyncio.Event()
        if stage == "scan":
            async def scan(*args, **kwargs):
                entered.set()
                await gate.wait()
                return SimpleNamespace(address="offline")
            sys.modules["bleak"].BleakScanner.find_device_by_address = scan
        else:
            c.blocks["connect"] = gate
        ctrl = d.Controller("offline")
        request = asyncio.create_task(ctrl.request(d.CMD_GET_POWER))
        if stage == "scan":
            await entered.wait()
        else:
            while "connect" not in c.calls:
                await asyncio.sleep(0)
        shutdown = asyncio.create_task(ctrl.disconnect())
        await asyncio.sleep(0)
        assert ctrl.closed
        gate.set()
        with pytest.raises(ConnectionError):
            await request
        await shutdown
        assert "start_notify" not in c.calls and "write" not in c.calls
        if stage == "scan":
            assert c.calls == []
        assert ctrl.client is None and not ctrl.lock.locked()
    run(case())


@pytest.mark.parametrize("stage", [None, "connect", "start_notify", "write", "disconnect"])
def test_spike_one_attempt_and_visible_failures(monkeypatch, stage, capsys):
    async def case():
        c = FakeClient()
        if stage:
            c.errors[stage] = ValueError("injected")
        install(monkeypatch, c)
        if stage:
            with pytest.raises((ValueError, RuntimeError)):
                await spike.run_queries(SimpleNamespace(address="offline"))
        else:
            await spike.run_queries(SimpleNamespace(address="offline"))
        assert c.calls.count("connect") == 1
        assert c.calls.count("disconnect") == 1
        if stage:
            assert "Done." not in capsys.readouterr().out
    run(case())


def test_spike_setup_timeout_does_not_retry():
    async def case():
        c = FakeClient()
        c.blocks["connect"] = asyncio.Event()
        with pytest.raises(asyncio.TimeoutError):
            await spike.connect_and_subscribe(c, lambda *_: None, pair_window=0.01)
        assert c.calls == ["connect"]
    run(case())


def test_setpoint_keeps_equal_whole_degree_values(monkeypatch):
    async def case():
        ctrl = d.Controller("offline")
        calls = []
        async def request(cmd, args):
            calls.append((cmd, dict(args)))
        monkeypatch.setattr(ctrl, "request", request)
        await ctrl.set_setpoint(21.5)
        cmd, args = calls[0]
        assert cmd == d.CMD_SET_SETPOINT
        assert args[0x20] == args[0x21] == d.temp_to_bytes(22)
        assert len(args) == 2 + len(d.SETPOINT_EXTRA_FIELDS)
    run(case())


def test_malformed_response_stops_session_instead_of_partial_success(monkeypatch):
    async def case():
        c = FakeClient()
        install(monkeypatch, c)
        ctrl = d.Controller("offline")
        c.respond = False
        task = asyncio.create_task(ctrl.request(d.CMD_GET_POWER))
        await c.written.wait()
        # Complete frame, but field 0x20 declares two bytes and provides one.
        c.notify(None, bytearray([0, 7, 0, 0, 0x20, 0x20, 2, 1]))
        with pytest.raises(ValueError, match="Truncated"):
            await task
        assert not ctrl.connected and ctrl.pending == {}
        assert c.calls[-2:] == ["stop_notify", "disconnect"]
    run(case())


def test_disconnect_between_fragments_prevents_remaining_writes(monkeypatch):
    async def case():
        c = FakeClient()
        install(monkeypatch, c)
        async def write(*args, **kwargs):
            await c.step("write")
            c.is_connected = False
            c.callback(c)
        c.write_gatt_char = write
        ctrl = d.Controller("offline")
        with pytest.raises(ConnectionError):
            await ctrl.set_setpoint(22)
        assert c.calls.count("write") == 1  # setpoint normally spans several fragments
        assert ctrl.pending == {} and not ctrl.lock.locked()
    run(case())
