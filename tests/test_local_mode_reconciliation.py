"""P0 physical incident recovery: closed SDK only, six distinct rooms, no live BLE."""
import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from integration.office_mock import closed_office
from madoka_multiroom.api import build_local_app
from madoka_multiroom.manager import RoomManager, RoomUnavailable
from madoka_multiroom.storage import RoomStore
from madoka_protocol import CMD_GET_MODE, CMD_SET_MODE, CMD_SET_POWER, CMD_SET_SETPOINT, CMD_SET_FAN


def set_calls(local):
    return [c for c in local.calls if len(c) == 3 and c[1] in
            (CMD_SET_MODE, CMD_SET_POWER, CMD_SET_SETPOINT, CMD_SET_FAN)]


async def uncertain_cool_to_heat(local):
    store = RoomStore(":memory:", mode="real")
    manager = RoomManager(local.config.rooms, store, offline_fleet=local.fleet)
    backend = await local.fleet.ensure_room("room-1")
    manager.room("room-1").backend = backend
    await manager.operate("room-1")
    local.apply_writes = False
    operation = str(uuid4())
    with pytest.raises(RoomUnavailable):
        await manager.native_manual("room-1", "mode", 4, operation,
                                    "Closed model incident", expected_mode=3)
    assert local.budget.operation(operation)["status"] == "uncertain"
    assert local.budget.pending_operation("room-1")
    assert len(set_calls(local)) == 1
    # Independent later physical state: may have changed on the wall, but this
    # observation must never retroactively confirm the original SET.
    local.states["room-1"][CMD_GET_MODE][0x20] = b"\x04"
    return manager, store, operation


def test_explicit_wall_reconciliation_restores_only_room_write_authority(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager, store, operation = await uncertain_cool_to_heat(local)
            try:
                before = list(set_calls(local))
                reply = await manager.native_reconcile_local_mode(
                    "room-1", operation, wall_mode=4, wall_target=21,
                    wall_power=True, wall_checked=True,
                    operator_note="Physically checked Heat and 21 on this room wall")
                assert len(set_calls(local)) == len(before) == 1
                assert reply["state"]["mode_id"] == 4
                assert reply["last_operation"]["operation_id"] == operation
                assert reply["last_operation"]["status"] == "reviewed_uncertain"
                assert not local.budget.pending_operation("room-1")
                assert reply["manual_controls"]["power"]
                assert reply["manual_controls"]["modes"]
                assert any(e["stage"] == "operator_mode_reconciled" for e in
                           local.budget.operation_events(operation))
                assert any(e["stage"] == "failed" for e in
                           local.budget.operation_events(operation))
                assert not local.budget.last_operation("room-2")
                assert not local.budget.pending_operation("room-2")
            finally:
                await manager.close()
                store.close()
        asyncio.run(case())


@pytest.mark.parametrize("changes", [
    {"wall_mode": 3}, {"wall_target": 22}, {"wall_power": False},
    {"wall_checked": False}, {"operator_note": "no"},
])
def test_mismatched_or_unattested_wall_never_rearms(tmp_path, changes):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager, store, operation = await uncertain_cool_to_heat(local)
            try:
                before = len(set_calls(local))
                data = dict(wall_mode=4, wall_target=21, wall_power=True, wall_checked=True,
                            operator_note="Physically checked wall controller in this room")
                data.update(changes)
                with pytest.raises(RoomUnavailable):
                    await manager.native_reconcile_local_mode("room-1", operation, **data)
                assert local.budget.pending_operation("room-1")
                assert local.budget.operation(operation)["status"] == "uncertain"
                assert len(set_calls(local)) == before
            finally:
                await manager.close()
                store.close()
        asyncio.run(case())


def test_unstable_mode_metadata_across_two_gets_keeps_stop(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager, store, operation = await uncertain_cool_to_heat(local)
            reads = 0
            def mutate_second_read(room, cmd, objects, wrote):
                nonlocal reads
                if room == "room-1" and cmd == CMD_GET_MODE:
                    reads += 1
                    if reads == 2:
                        objects[0x99] = b"\x01"
                return objects
            local.reply_hook = mutate_second_read
            try:
                with pytest.raises(RoomUnavailable):
                    await manager.native_reconcile_local_mode(
                        "room-1", operation, wall_mode=4, wall_target=21,
                        wall_power=True, wall_checked=True,
                        operator_note="Physically checked this wall controller")
                assert reads == 2
                assert local.budget.pending_operation("room-1")
                assert local.budget.operation(operation)["status"] == "uncertain"
                assert len(set_calls(local)) == 1
            finally:
                await manager.close()
                store.close()
        asyncio.run(case())


def test_wrong_operation_identity_cannot_reconcile(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager, store, operation = await uncertain_cool_to_heat(local)
            try:
                with pytest.raises(RoomUnavailable):
                    await manager.native_reconcile_local_mode(
                        "room-1", str(uuid4()), wall_mode=4, wall_target=21,
                        wall_power=True, wall_checked=True,
                        operator_note="Physical wall observed in the office")
                assert local.budget.operation(operation)["status"] == "uncertain"
            finally:
                await manager.close()
                store.close()
        asyncio.run(case())


def test_mode_reconciliation_endpoint_requires_local_csrf_and_unresolved_mode(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        app = build_local_app(local.config, str(tmp_path / "observations.db"), fleet=local.fleet)
        with TestClient(app) as client:
            body = dict(operation_id=str(uuid4()), wall_mode=4, wall_target=21,
                        wall_power=True, wall_checked=True,
                        operator_note="Physical wall observed in the office")
            url = "/api/controllers/room-1/reconcile-mode"
            assert client.post(url, json=body).status_code == 403
            token = client.get("/api/session").json()["token"]
            response = client.post(url, json=body, headers={"X-Demo-Token": token})
            assert response.status_code == 503
            assert not set_calls(local)
