"""Offline acceptance of six native controllers using a closed SDK only."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from integration.office_mock import closed_office
from madoka_multiroom.api import build_office_app
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.backends.daikin import CleanupUncertain
from madoka_multiroom.backends.office_native import setpoint_block
from madoka_multiroom.backends.native_release import stable_absence
from madoka_multiroom.manager import RoomManager, RoomUnavailable
from madoka_multiroom.storage import RoomStore
from madoka_multiroom.office_access import Controls, OfficeConfig
from madoka_protocol import *


@pytest.fixture
def office(tmp_path):
    with closed_office(tmp_path) as runtime:
        yield runtime


async def ready(office, room="room-1"):
    backend = office.fleet.backends[room]
    await backend.prepare()
    await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
    return backend


async def change(backend, action="setpoint", value=23, operation_id=None):
    return await backend.manual(action, value, operation_id or str(uuid4()), "Owner authorized closed test", lambda *args: None)


def sets(office, room=None):
    return [c for c in office.calls if len(c) == 3 and c[1] in (CMD_SET_POWER, CMD_SET_MODE, CMD_SET_SETPOINT, CMD_SET_FAN)
            and room in (None, c[0])]


@pytest.mark.parametrize("action,value,field", [("power", False, "power_on"), ("mode", 4, "mode_id"),
    ("mode", 2, "mode_id"), ("fan", 5, "fan_id"), ("setpoint", 23, "setpoint")])
def test_all_manual_commands_confirm_exact_readback_and_preserve_before(office, action, value, field):
    async def case():
        backend = await ready(office)
        previous = copy.deepcopy(office.states["room-1"])
        operation_id = str(uuid4())
        state = await change(backend, action, value, operation_id)
        assert state[field] == value and len(sets(office)) == 1
        record = office.budget.last_operation("room-1")
        assert record["status"] == "confirmed" and record["operation_id"] == operation_id
        assert record["before_state"]["wire"][str(CMD_GET_SETPOINT)]["33"] == previous[CMD_GET_SETPOINT][0x21].hex()
        if action == "setpoint":
            args = sets(office)[0][2]
            assert args[0x20] == temp_to_bytes(23) and args[0x21] == temp_to_bytes(21)
            assert {k:v for k,v in args.items() if k != 0x20} == {k:v for k,v in previous[CMD_GET_SETPOINT].items() if k != 0x20}
        if action == "fan":
            assert sets(office)[0][2] == {0x20: b"\x05", 0x21: b"\x02"}
        before_calls = len(sets(office))
        with pytest.raises(BackendUnavailable):
            await change(backend, action, value, operation_id)
        assert len(sets(office)) == before_calls
        await backend.close()
        assert not office.budget.owners and office.budget.records()[0]["state"] == "released_native"
    asyncio.run(case())


def test_heat_setpoint_and_fan_only_change_active_fields_and_restore_explicitly(office):
    async def case():
        office.states["room-1"][CMD_GET_MODE][0x20] = b"\x04"
        backend = await ready(office)
        await change(backend, "setpoint", 23)
        assert office.states["room-1"][CMD_GET_SETPOINT][0x20] == temp_to_bytes(22)
        assert office.states["room-1"][CMD_GET_SETPOINT][0x21] == temp_to_bytes(23)
        await change(backend, "setpoint", 21)
        await change(backend, "fan", 4)
        assert sets(office)[-1][2] == {0x20: b"\x03", 0x21: b"\x04"}
        assert len(sets(office)) == 3
        await backend.close()
    asyncio.run(case())


@pytest.mark.parametrize("fault", ["no_effect", "ack", "timeout", "cancel", "disconnect", "readback", "inactive_changed", "mode_changed"])
def test_ambiguous_write_stops_once_without_replay_and_survives_restart(office, fault):
    async def case():
        backend = await ready(office)
        if fault == "no_effect": office.apply_writes = False
        if fault == "ack": office.ack = {0x99: b"\x01"}
        if fault in ("timeout", "cancel", "disconnect"):
            office.stage = "write"
            office.delay = 10 if fault == "timeout" else 0
            backend.timeout = .02 if fault == "timeout" else 30
            office.error = asyncio.CancelledError() if fault == "cancel" else ConnectionError("fake disconnected") if fault == "disconnect" else None
        if fault == "readback": office.fail_readback = True
        if fault in ("inactive_changed", "mode_changed"):
            # Inject only after SET, at the relevant readback. A sleeping task
            # can run during the fresh baseline on a busy CI host and then
            # legitimately becomes part of the preserved baseline instead.
            def external(room, command, objects, wrote):
                if room == "room-1" and wrote:
                    if fault == "inactive_changed" and command == CMD_GET_SETPOINT:
                        objects[0x21] = temp_to_bytes(26)
                    if fault == "mode_changed" and command == CMD_GET_MODE:
                        objects[0x20] = b"\x04"
                return objects
            office.reply_hook = external
        with pytest.raises((BackendUnavailable, asyncio.CancelledError, TimeoutError, ConnectionError)):
            await change(backend)
        assert backend.stopped and office.budget.pending_operation("room-1")
        assert office.budget.last_operation("room-1")["status"] == "uncertain"
        with pytest.raises(BackendUnavailable): await change(backend)
        with pytest.raises(BackendUnavailable): await office.fleet.ensure_room("room-1")
        assert len(sets(office)) == 1
        await backend.close()
        assert not office.budget.owners  # Ownership proof is distinct from unresolved SET.
        assert office.budget.pending_operation("room-1")
    asyncio.run(case())


@pytest.mark.parametrize("fault", ["missing_callback", "connecting", "disconnecting", "other_client", "disconnect_error", "stop_error"])
def test_public_cleanup_never_releases_without_correlated_native_proof(office, fault):
    async def case():
        backend = await ready(office)
        if fault == "missing_callback": office.no_callback = True
        if fault == "connecting": office.native_state_override = 1
        if fault == "disconnecting": office.native_state_override = 3
        if fault == "other_client": office.connected_override = True
        if fault == "disconnect_error": office.disconnect_error = True
        if fault == "stop_error": office.stop_error = True
        with pytest.raises((BackendUnavailable, RuntimeError)): await backend.close()
        assert office.budget.quarantined and office.budget.owners
        old_calls = len(office.calls)
        with pytest.raises(CleanupUncertain): await backend.close()
        assert len(office.calls) == old_calls
        with pytest.raises(BackendUnavailable): await office.fleet.ensure_room("room-1", explicit=True)
    asyncio.run(case())


def test_unexpected_native_disconnect_can_reconnect_after_proof_but_never_replay_write(office):
    async def case():
        backend = await ready(office)
        client = office.clients[0]
        client.is_connected = False
        client.callback(client)
        assert not backend.port.ready
        new = await office.fleet.ensure_room("room-1")
        assert new is not backend and new.port.ready
        assert len(office.budget.owners) == 1 and len(office.budget.records()) == 2
        assert office.budget.records()[0]["state"] == "released_native"
        assert not sets(office)
        await new.close()
    asyncio.run(case())


@pytest.mark.parametrize("action,value", [("setpoint", 22.5), ("setpoint", float("nan")), ("setpoint", True),
    ("setpoint", 33), ("mode", 0), ("mode", True), ("fan", 0), ("power", 1), ("schedule", 22)])
def test_unsupported_values_never_emit_set(office, action, value):
    async def case():
        backend = await ready(office)
        with pytest.raises((ValueError, BackendUnavailable)):
            await change(backend, action, value)
        assert not sets(office)
        await backend.close()
    asyncio.run(case())


@pytest.mark.parametrize("schema", ["missing", "width", "flags", "zero_limits", "narrow_limits", "auto_mode"])
def test_setpoint_uses_reported_capabilities_and_preserves_full_block(office, schema):
    block = copy.deepcopy(office.states["room-1"][CMD_GET_SETPOINT])
    mode = 3
    if schema == "missing": block.pop(0x30)
    if schema == "width": block[0xA2] = b"\x10"
    if schema == "flags": block[0x31] = b"\xff"
    if schema == "zero_limits": block[0xA2] = b"\x00\x00"
    if schema == "narrow_limits": block[0xB2] = temp_to_bytes(22)
    if schema == "auto_mode": mode = 2
    with pytest.raises((ValueError, BackendUnavailable)):
        setpoint_block(block, mode, 23, Controls(setpoint=True))
    assert not office.calls


@pytest.mark.parametrize("concurrency", [1, 2, 6])
def test_six_rooms_load_independence_and_operation_limits(tmp_path, concurrency):
    with closed_office(tmp_path, concurrency=concurrency) as office:
        async def case():
            store = RoomStore(":memory:", mode="real")
            manager = RoomManager(office.config.rooms, store, offline_fleet=office.fleet)
            # Exercise actual global capacity for setup and reads.
            await manager.start()
            deadline = time.monotonic() + 5
            while not all(manager.snapshot(r)["connected"] for r in manager.rooms):
                assert time.monotonic() < deadline
                await asyncio.sleep(.01)
            office.stage, office.delay = "write", .02
            await asyncio.gather(*(manager.native_manual(r, "setpoint", 23+i%2, str(uuid4()), "Owner test") for i, r in enumerate(manager.rooms)))
            assert office.peak <= concurrency and len(sets(office)) == 6
            assert len(office.budget.owners) == 6
            assert all(not manager.snapshot(r)["simulated"] and not manager.snapshot(r)["physical_enforce_enabled"] for r in manager.rooms)
            assert all(store.history(r, 0, limit=100)["samples"] for r in manager.rooms)
            await manager.close()
            assert not office.budget.owners
            store.close()
        asyncio.run(case())


def test_authenticated_https_office_api_csrf_and_no_legacy_write_paths(office, tmp_path):
    app = build_office_app(office.config, str(tmp_path / "history.db"), fleet=office.fleet, password=office.password)
    with TestClient(app, base_url="https://127.0.0.1:8443") as client:
        assert client.get("/").status_code == 401
        client.auth = ("operator", office.password)
        assert client.get("http://127.0.0.1:8443/api/session").status_code == 400
        assert client.get("https://evil.test:8443/api/session").status_code == 400
        session = client.get("/api/session").json()
        assert session["mode"] == "native-office"
        token = {"X-Demo-Token": session["token"]}
        client.portal.call(app.state.manager.operate, "room-1")
        payload = {"operation_id": str(uuid4()), "action": "setpoint", "value": 23, "reason": "Explicit owner test"}
        path = "/api/controllers/room-1/manual"
        assert client.post(path, json=payload).status_code == 403
        assert client.post(path, json=payload, headers={**token, "Origin": "https://evil.test:8443"}).status_code == 403
        result = client.post(path, json=payload, headers=token)
        assert result.status_code == 200 and result.json()["last_operation"]["status"] == "confirmed"
        assert result.json()["protection"]["desired"]["setpoint"] == 23
        assert client.post(path, json=payload, headers=token).status_code == 409
        assert len(sets(office)) == 1
        for action in ("power", "mode", "fan", "setpoint", "simulation", "desired", "external-change", "reset-budget"):
            assert client.post("/api/controllers/room-1/"+action, json={}, headers=token).status_code == 403
        assert client.post("/api/physical/admit", json={}, headers=token).status_code == 403


def test_explicit_uncertain_review_reads_and_never_replays_set(office, tmp_path):
    async def case():
        store = RoomStore(":memory:", mode="real")
        manager = RoomManager(office.config.rooms, store, offline_fleet=office.fleet)
        backend = await ready(office)
        await manager.operate("room-1")
        office.apply_writes = False
        with pytest.raises(RoomUnavailable):
            await manager.native_manual("room-1", "setpoint", 23, str(uuid4()), "Owner test")
        assert office.budget.pending_operation("room-1")
        state = await manager.native_recover("room-1", "Owner accepts actual GET observations")
        assert state["state"]["setpoint"] == 22 and state["last_operation"]["status"] == "reviewed_uncertain"
        assert len(sets(office)) == 1 and not office.budget.pending_operation("room-1")
        await manager.close()
        store.close()
    asyncio.run(case())


def test_stable_absence_does_not_use_elapsed_time_as_proof():
    async def case():
        await stable_absence(lambda: (0, False), duration=.01)
        for state in ((1, False), (3, False), (0, True)):
            with pytest.raises(BackendUnavailable): await stable_absence(lambda: state, duration=.01)
    asyncio.run(case())


def test_owner_limits_and_auto_controls_reject_before_stopping_or_set(office):
    async def case():
        backend = await ready(office)
        backend.controls = Controls(power=True, setpoint=True, modes=(2, 3, 4), fans=(1, 2, 3, 4, 5), minimum=20, maximum=25)
        office.states['room-1'][CMD_GET_SETPOINT][0x20] = temp_to_bytes(25)
        state = await backend.snapshot(('power', 'mode', 'setpoint', 'sensor', 'fan'))
        assert state['max_setpoint'] == 25 and state['device_max_setpoint'] == 32
        with pytest.raises(ValueError): await change(backend, 'setpoint', 26)
        assert not backend.stopped and not office.budget.pending_operation('room-1')
        await change(backend, 'mode', 2)
        assert backend.control_view()['fans'] == [] and not backend.control_view()['setpoint']
        count = len(sets(office))
        with pytest.raises(ValueError): await change(backend, 'fan', 4)
        assert len(sets(office)) == count and not backend.stopped
        await backend.close()
    asyncio.run(case())


def test_office_rate_limit_is_per_direct_tls_client_and_bounded():
    from madoka_multiroom.api import LocalBoundary
    async def case():
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 204, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})
        boundary = LocalBoundary(app, 'token', authorities=('localhost:8443',))
        async def request(client):
            replies = []
            async def send(message): replies.append(message)
            scope = {'type': 'http', 'method': 'GET', 'scheme': 'https', 'headers': [(b'host', b'localhost:8443')], 'client': (client, 1234)}
            await boundary(scope, None, send)
            return replies[0]['status']
        for i in range(5):
            for _ in range(120): assert await request(f'192.168.1.{i+1}') == 204
        for _ in range(1200): assert await request('192.168.1.200') == 204
        assert await request('192.168.1.200') == 429
        assert await request('192.168.1.1') == 204
        assert len(boundary.client_windows) == 6
    asyncio.run(case())


def test_reconnect_episode_rearms_after_health_and_durable_rolling_budget(office):
    async def case():
        backend = await office.fleet.ensure_room('room-1')
        office.fleet.healthy_since['room-1'] -= 61
        office.fleet.note_healthy('room-1')
        assert office.fleet.retries['room-1'] == 0
        for _ in range(2):
            client = office.clients[-1]
            client.is_connected = False
            client.callback(client)
            office.fleet.next_attempt['room-1'] = 0
            backend = await office.fleet.ensure_room('room-1')
        client = office.clients[-1]
        client.is_connected = False
        client.callback(client)
        office.fleet.retries['room-1'] = 0
        office.fleet.next_attempt['room-1'] = 0
        with pytest.raises(BackendUnavailable): await office.fleet.ensure_room('room-1')
        assert office.budget.recent_admissions('room-1') == 3
        await backend.close()
    asyncio.run(case())


def test_other_room_setup_slot_wait_is_outside_ready_room_deadline(office, monkeypatch):
    async def case():
        store = RoomStore(':memory:', mode='real')
        manager = RoomManager(office.config.rooms, store, offline_fleet=office.fleet)
        await ready(office)
        # Model another room's pairing holding all global slots for longer than
        # an ordinary ready read. Shorten only the 30s read window for this test.
        real_timeout = asyncio.timeout
        monkeypatch.setattr(asyncio, 'timeout', lambda seconds: real_timeout(.02 if seconds == 30 else seconds))
        for _ in range(office.config.rooms.max_concurrent_operations): await manager.capacity.acquire()
        task = asyncio.create_task(manager.operate('room-1'))
        await asyncio.sleep(.04)
        assert not task.done() and manager.room('room-1').failure is None
        for _ in range(office.config.rooms.max_concurrent_operations): manager.capacity.release()
        state = await task
        assert state['connected'] and not state['error']
        await manager.close()
        store.close()
    asyncio.run(case())
