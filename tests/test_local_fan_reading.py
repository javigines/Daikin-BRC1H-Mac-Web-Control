"""Invented GET_FAN shapes only; observation must not expand write authority."""
import asyncio
import copy
from uuid import uuid4

import pytest

from integration.office_mock import closed_office, synthetic_fan_blocks
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.backends.office_native import local_fan_observation
from madoka_multiroom.manager import RoomManager, RoomUnavailable
from madoka_multiroom.office_access import Controls
from madoka_multiroom.storage import RoomStore
from madoka_protocol import CMD_GET_FAN, CMD_GET_MODE, CMD_SET_FAN, CMD_SET_MODE, CMD_SET_POWER, CMD_SET_SETPOINT


def sets(local):
    return [c for c in local.calls if len(c) == 3 and c[1] in
            (CMD_SET_FAN, CMD_SET_MODE, CMD_SET_POWER, CMD_SET_SETPOINT)]


async def no_fan_write(backend, local):
    for value in range(6):
        with pytest.raises(ValueError):
            await backend.manual('fan', value, str(uuid4()), 'Synthetic fan observation',
                                 lambda *args: pytest.fail('Rejected fan observation authorized desired state'))
    assert not sets(local)
    assert local.budget.last_operation(backend.room_id) is None


def test_six_shapes_mode_selection_raw_extensions_isolation_and_no_new_writes(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            blocks = synthetic_fan_blocks()
            for room, block in zip(local.states, blocks):
                local.states[room][CMD_GET_FAN] = copy.deepcopy(block)
            original = copy.deepcopy(blocks)
            backends = [await local.fleet.ensure_room(room) for room in local.states]
            try:
                for mode, expected in ((3, [1, 3, 4, 5, 0, 2]), (4, [1, 0, 0, 0, 2, 5])):
                    for index, backend in enumerate(backends):
                        local.states[backend.room_id][CMD_GET_MODE][0x20] = bytes([mode])
                        state = await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                        assert state['fan_id'] == expected[index]
                        assert state['cooling_fan_id'] == [1, 3, 4, 5, 0, 2][index]
                        assert state['heating_fan_id'] == [1, 0, 0, 0, 2, 5][index]
                        assert state['field_status']['fan_id'] == 'available'
                        assert backend.raw[CMD_GET_FAN] == original[index]
                        assert len(backend.raw[CMD_GET_FAN]) == 6
                        if index in (0, 5):
                            # Preserve existing access, without adding any level or Auto.
                            assert backend.control_view()['fans'] == [1, 2, 3, 4, 5]
                            with pytest.raises(ValueError): backend.validate_action('fan', 0)
                        else:
                            assert backend.control_view()['fans'] == []
                            await no_fan_write(backend, local)
                before = [copy.deepcopy(b.latest) for b in backends]
                retained = [copy.deepcopy(b.raw) for b in backends]
                local.states['room-2'][CMD_GET_FAN][0x21] = b'\x05'
                await backends[1].snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                assert all(b.latest == before[i] and b.raw == retained[i]
                           for i, b in enumerate(backends) if i != 1)
                assert blocks == original and not sets(local)
            finally:
                for backend in backends: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('mode,active,inactive', [(3, 0x20, 0x21), (4, 0x21, 0x20)])
@pytest.mark.parametrize('bad', [None, b'\xff', b'\x06', b'', b'\x01\x00'])
@pytest.mark.parametrize('bad_is_active', [False, True])
def test_fields_decode_independently_and_unknown_never_grants_a_write(tmp_path, mode, active, inactive, bad, bad_is_active):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            block = synthetic_fan_blocks()[0]
            block.update({active: b'\x04', inactive: b'\x02'})
            oid = active if bad_is_active else inactive
            if bad is None: block.pop(oid)
            else: block[oid] = bad
            local.states['room-1'][CMD_GET_FAN] = copy.deepcopy(block)
            local.states['room-1'][CMD_GET_MODE][0x20] = bytes([mode])
            backend = await local.fleet.ensure_room('room-1')
            try:
                state = await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                assert state['room_temp'] == 23
                assert state['fan_id'] == (None if bad_is_active else 4)
                field = 'cooling_fan_id' if oid == 0x20 else 'heating_fan_id'
                status = 'not_observed' if bad is None else 'unknown'
                assert state[field] is None and state['field_status'][field] == status
                assert state['field_status']['fan_id'] == (status if bad_is_active else 'available')
                assert backend.raw[CMD_GET_FAN] == block
                assert not backend.control_view()['fans']
                await no_fan_write(backend, local)
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('value', [bytearray([0]), 0, True, '0', None])
def test_decoder_rejects_nonbytes_without_coercion_or_input_changes(value):
    block = {0x20: value, 0x21: b'\x00'}
    original = copy.deepcopy(block)
    observed, status = local_fan_observation(block, 3)
    assert observed == {'cooling_fan_id': None, 'heating_fan_id': 0, 'fan_id': None}
    assert status['cooling_fan_id'] == status['fan_id'] == 'unknown'
    assert status['heating_fan_id'] == 'available' and block == original


@pytest.mark.parametrize('mode', [0, 1, 2, 5])
def test_other_modes_have_no_inferred_active_fan_or_new_permission(tmp_path, mode):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.states['room-1'][CMD_GET_MODE][0x20] = bytes([mode])
            local.states['room-1'][CMD_GET_FAN] = {0x20: b'\x00', 0x21: b'\x05'}
            backend = await local.fleet.ensure_room('room-1')
            try:
                state = await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                assert state['fan_id'] is None and state['field_status']['fan_id'] == 'not_applicable'
                assert state['cooling_fan_id'] == 0 and state['heating_fan_id'] == 5
                assert backend.control_view()['fans'] == []
                assert backend.controls.modes == (2, 3, 4)
                await no_fan_write(backend, local)
            finally: await backend.close()
        asyncio.run(case())


def test_reading_does_not_depend_on_configured_write_choices(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.backends['room-1'].controls = Controls()
            local.states['room-1'][CMD_GET_FAN] = {0x20: b'\x00', 0x21: b'\x03'}
            backend = await local.fleet.ensure_room('room-1')
            try:
                state = await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                assert state['fan_id'] == 0 and state['heating_fan_id'] == 3
                assert any(len(c) == 3 and c[1] == CMD_GET_FAN for c in local.calls)
                assert backend.control_view()['fans'] == []
                await no_fan_write(backend, local)
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('active', [0x20, 0x21])
def test_both_auto_and_fully_missing_fan_reply_are_distinct(tmp_path, active):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.states['room-1'][CMD_GET_MODE][0x20] = bytes([3 if active == 0x20 else 4])
            local.states['room-1'][CMD_GET_FAN] = {0x20: b'\x00', 0x21: b'\x00'}
            backend = await local.fleet.ensure_room('room-1')
            try:
                assert (await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan")))['fan_id'] == 0
                await no_fan_write(backend, local)
                local.states['room-1'][CMD_GET_FAN] = {}
                state = await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                assert state['fan_id'] is None and state['field_status']['fan_id'] == 'not_observed'
                await no_fan_write(backend, local)
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('fault', ['active_auto', 'inactive_auto', 'missing', 'unknown', 'mode'])
def test_fresh_baseline_rechecks_legacy_write_gate_before_any_set_or_journal(tmp_path, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backend = await local.fleet.ensure_room('room-1')
            try:
                await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                assert backend.control_view()['fans']
                if fault == 'mode': local.states['room-1'][CMD_GET_MODE][0x20] = b'\x02'
                elif fault == 'missing': local.states['room-1'][CMD_GET_FAN].pop(0x21)
                else:
                    local.states['room-1'][CMD_GET_FAN][0x20 if fault == 'active_auto' else 0x21] = b'\xff' if fault == 'unknown' else b'\x00'
                with pytest.raises(ValueError):
                    await backend.manual('fan', 5, str(uuid4()), 'Synthetic stale presentation',
                                         lambda *args: pytest.fail('Stale fan permission authorized a write'))
                assert not backend.stopped and not backend.control_view()['fans']
                assert not sets(local) and local.budget.last_operation('room-1') is None
            finally: await backend.close()
        asyncio.run(case())


def test_uncertain_stays_blocked_on_poll_and_review_is_get_only(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            store = RoomStore(':memory:', mode='real')
            manager = RoomManager(local.config.rooms, store, offline_fleet=local.fleet)
            try:
                backend = await local.fleet.ensure_room('room-1')
                manager.room('room-1').backend = backend
                await manager.operate('room-1')
                operation = str(uuid4())
                # Seed a synthetic durable outcome; no SET is emitted to create it.
                local.budget.begin_operation(backend.port, backend, operation, 'fan', 5,
                                             {'state': copy.deepcopy(backend.latest), 'reason': 'Synthetic uncertain'})
                local.budget.finish_operation(operation, 'uncertain')
                local.states['room-1'][CMD_GET_FAN] = {0x20: b'\x00', 0x21: b'\x03'}
                await manager.operate('room-1')
                assert manager.snapshot('room-1')['state']['fan_id'] == 0
                assert local.budget.pending_operation('room-1')
                with pytest.raises(RoomUnavailable):
                    await manager.native_manual('room-1', 'fan', 5, str(uuid4()), 'Synthetic blocked write')
                reviewed = await manager.native_recover('room-1', 'Synthetic GET-only review')
                assert reviewed['last_operation']['status'] == 'reviewed_uncertain'
                assert reviewed['state']['fan_id'] == 0 and not reviewed['manual_controls']['fans']
                assert not sets(local)
            finally:
                await manager.close()
                store.close()
        asyncio.run(case())


def test_office_reader_does_not_adopt_local_auto_profile(tmp_path):
    with closed_office(tmp_path) as office:
        async def case():
            office.states['room-1'][CMD_GET_FAN] = {0x20: b'\x00', 0x21: b'\x05'}
            backend = await office.fleet.ensure_room('room-1')
            try:
                with pytest.raises(BackendUnavailable): await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                assert not sets(office)
            finally: await backend.close()
        asyncio.run(case())
