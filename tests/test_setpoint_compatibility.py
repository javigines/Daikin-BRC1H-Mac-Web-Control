"""Synthetic TLVs only: GET compatibility never weakens single-SET safety."""
import asyncio
import copy
from uuid import uuid4

import pytest

from integration.office_mock import closed_office, synthetic_setpoint_block
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.backends.daikin import WriteNotConfirmed
from madoka_multiroom.backends.office_native import setpoint_block, validate_set_ack
from madoka_multiroom.manager import RoomBusy, RoomManager, RoomUnavailable
from madoka_multiroom.office_access import Controls
from madoka_multiroom.storage import RoomStore
from madoka_protocol import (
    CMD_GET_MODE, CMD_GET_POWER, CMD_GET_SETPOINT, CMD_SET_SETPOINT, temp_to_bytes,
)


def writes(local):
    return [c for c in local.calls if len(c) == 3 and c[1] == CMD_SET_SETPOINT]


async def manager_for(local):
    for state in local.states.values():
        state[CMD_GET_SETPOINT] = synthetic_setpoint_block(extended=True)
    manager = RoomManager(local.config.rooms, RoomStore(':memory:', mode='real'), offline_fleet=local.fleet)
    for room in manager.rooms:
        manager.room(room).backend = await local.fleet.ensure_room(room)
        await manager.operate(room)
    return manager


async def change(manager, room='room-1', value=23, operation_id=None):
    return await manager.native_manual(room, 'setpoint', value, operation_id or str(uuid4()), 'Synthetic user action')


@pytest.mark.parametrize('fault', ['mode', 'limits', 'schema'])
def test_changed_fresh_baseline_rejects_before_journal_or_set(tmp_path, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager = await manager_for(local)
            assert manager.snapshot('room-1')['manual_controls']['setpoint']
            # Change the device after the UI capability check, before manual's
            # fresh baseline. Cached permission must never authorize a SET.
            if fault == 'mode': local.states['room-1'][CMD_GET_MODE][0x20] = b'\x02'
            if fault == 'limits': local.states['room-1'][CMD_GET_SETPOINT][0xB2] = temp_to_bytes(22)
            if fault == 'schema': local.states['room-1'][CMD_GET_SETPOINT].pop(0x32)
            with pytest.raises(ValueError): await change(manager)
            assert not writes(local) and local.budget.last_operation('room-1') is None
            assert not manager.snapshot('room-1')['manual_controls']['setpoint'] or fault == 'limits'
            await manager.close()
            manager.store.close()
        asyncio.run(case())


@pytest.mark.parametrize('room', [f'room-{i}' for i in range(1, 7)])
@pytest.mark.parametrize('mode,target', [(3, 0x20), (4, 0x21)])
@pytest.mark.parametrize('delta', [-1, 1])
def test_six_rooms_extended_get_single_set_preserves_every_other_byte(tmp_path, room, mode, target, delta):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.states[room][CMD_GET_MODE][0x20] = bytes([mode])
            manager = await manager_for(local)
            assert all(manager.snapshot(r)['manual_controls']['setpoint'] for r in manager.rooms)
            before = copy.deepcopy(local.states)
            value = (22 if mode == 3 else 21) + delta
            # A complete status-shaped ACK may contain old targets. A newly
            # reported GET extension is accepted but must never be transmitted.
            local.ack = synthetic_setpoint_block()
            def reply(r, command, objects, wrote):
                if r == room and command == CMD_GET_SETPOINT and wrote:
                    objects[0x9B] = b'\x08'
                return objects
            local.reply_hook = reply
            op = str(uuid4())
            view = await change(manager, room, value, op)
            assert view['last_operation']['status'] == 'confirmed'
            assert view['state']['setpoint'] == value
            assert len(writes(local)) == 1
            sent_room, _, args = writes(local)[0]
            assert sent_room == room
            assert set(args) == set(before[room][CMD_GET_SETPOINT]) - {0x99, 0x9A}
            assert args[target] == temp_to_bytes(value)
            assert all(v == before[room][CMD_GET_SETPOINT][k] for k, v in args.items() if k != target)
            expected = copy.deepcopy(before)
            expected[room][CMD_GET_SETPOINT][target] = temp_to_bytes(value)
            assert local.states == expected
            # Same operation identity cannot write again, even with a new value.
            with pytest.raises(RoomBusy):
                await change(manager, room, value + delta, op)
            assert len(writes(local)) == 1
            await manager.close()
            manager.store.close()
        asyncio.run(case())


@pytest.mark.parametrize('mode', [3, 4])
@pytest.mark.parametrize('lower,upper,value,allowed', [
    (16, 32, 16, True), (16, 32, 32, True), (16.5, 25.5, 17, True),
    (16.5, 25.5, 16, False), (16.5, 25.5, 26, False),
    (0, 32, 23, False), (16, 0, 23, False), (26, 20, 23, False),
    (9, 32, 23, False), (16, 41, 23, False), (16, 32, 22.5, False),
])
def test_effective_active_device_limits(mode, lower, upper, value, allowed):
    block = synthetic_setpoint_block(extended=True)
    lo, hi = (0xA2, 0xB2) if mode == 3 else (0xA3, 0xB3)
    block.update({lo: temp_to_bytes(lower), hi: temp_to_bytes(upper)})
    if allowed:
        assert dict(setpoint_block(block, mode, value, Controls(setpoint=True)))[0x20 if mode == 3 else 0x21] == temp_to_bytes(value)
    else:
        with pytest.raises((ValueError, BackendUnavailable)):
            setpoint_block(block, mode, value, Controls(setpoint=True))


@pytest.mark.parametrize('fault', ['missing', 'width', 'range_flag', 'mode_flag', 'limits', 'owner_limits', 'mode'])
def test_unrecognized_or_disallowed_baseline_keeps_buttons_stopped_and_zero_sets(tmp_path, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager = await manager_for(local)
            block = local.states['room-1'][CMD_GET_SETPOINT]
            if fault == 'missing': block.pop(0x32)
            if fault == 'width': block[0xA4] = b'\x02\x03'
            if fault == 'range_flag': block[0x30] = b'\x02'
            if fault == 'mode_flag': block[0x31] = b'\xff'
            if fault == 'limits': block[0xA2] = temp_to_bytes(30); block[0xB2] = temp_to_bytes(20)
            if fault == 'owner_limits':
                manager.room('room-1').backend.controls = Controls(setpoint=True, minimum=26, maximum=28)
                block[0xB2] = temp_to_bytes(25)
            if fault == 'mode': local.states['room-1'][CMD_GET_MODE][0x20] = b'\x02'
            view = await manager.operate('room-1')
            assert not view['manual_controls']['setpoint']
            with pytest.raises((ValueError, RoomUnavailable)):
                await change(manager)
            assert not writes(local) and not local.budget.pending_operation('room-1')
            assert all(manager.snapshot(r)['manual_controls']['setpoint'] for r in manager.rooms if r != 'room-1')
            await manager.close()
            manager.store.close()
        asyncio.run(case())


@pytest.mark.parametrize('fault', ['target', 'inactive', 'limit', 'symbol', 'config', 'mode', 'power',
                                  'extension_changed', 'extension_missing', 'required_missing', 'width', 'get_failed'])
def test_readback_divergence_is_uncertain_and_blocks_all_writes_until_get_only_review(tmp_path, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager = await manager_for(local)
            before_other = copy.deepcopy({r: s for r, s in local.states.items() if r != 'room-1'})
            local.ack = synthetic_setpoint_block()
            def reply(room, command, objects, wrote):
                if room != 'room-1' or not wrote:
                    return objects
                if fault == 'mode' and command == CMD_GET_MODE: return {0x20: b'\x04'}
                if fault == 'power' and command == CMD_GET_POWER: return {0x20: b'\x00'}
                if command == CMD_GET_SETPOINT:
                    if fault == 'get_failed': raise RuntimeError('Synthetic GET failure')
                    key = {'target': 0x20, 'inactive': 0x21, 'limit': 0xA2, 'symbol': 0xA4,
                           'config': 0x32, 'extension_changed': 0x99}.get(fault)
                    if key is not None:
                        objects[key] = (int.from_bytes(objects[key], 'big') + 1).to_bytes(len(objects[key]), 'big')
                    if fault == 'extension_missing': objects.pop(0x99)
                    if fault == 'required_missing': objects.pop(0x32)
                    if fault == 'width': objects[0xB5] = b'\x02\x03'
                return objects
            local.reply_hook = reply
            with pytest.raises(RoomUnavailable): await change(manager)
            assert local.budget.last_operation('room-1')['status'] == 'uncertain'
            assert local.budget.last_operation('room-1')['diagnostics'][-1]['details']['failed_stage'] in ('readback_validate', 'readback_get')
            local.reply_hook = None
            # Polling / observing the applied value cannot acknowledge intent.
            if fault != 'get_failed': await manager.operate('room-1')
            for action, value in [('setpoint', 24), ('power', False)]:
                with pytest.raises(RoomUnavailable):
                    await manager.native_manual('room-1', action, value, str(uuid4()), 'Synthetic user action')
            assert local.budget.pending_operation('room-1') and len(writes(local)) == 1
            assert not manager.snapshot('room-1')['manual_controls']['setpoint']
            assert {r: s for r, s in local.states.items() if r != 'room-1'} == before_other
            assert all(manager.snapshot(r)['manual_controls']['setpoint'] for r in manager.rooms if r != 'room-1')
            calls = len(local.calls)
            view = await manager.native_recover('room-1', 'Synthetic authorized GET-only review')
            assert view['last_operation']['status'] == 'reviewed_uncertain'
            assert view['state']['setpoint'] == 23
            assert not local.budget.pending_operation('room-1')
            assert any(len(c) == 3 and c[1] == CMD_GET_SETPOINT for c in local.calls[calls:])
            assert len(writes(local)) == 1
            await manager.close()
            manager.store.close()
        asyncio.run(case())


@pytest.mark.parametrize('ack', [{}, {0: b''}, synthetic_setpoint_block(),
    {k: v for k, v in synthetic_setpoint_block(extended=True).items() if k not in (0x99, 0x9A)},
    {k: bytes(len(v)) for k, v in synthetic_setpoint_block().items()}])
def test_recognized_ack_never_confirms_effect_without_get(tmp_path, ack):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager = await manager_for(local)
            local.ack, local.apply_writes = ack, False
            with pytest.raises(RoomUnavailable): await change(manager)
            assert len(writes(local)) == 1 and local.budget.pending_operation('room-1')
            assert local.budget.last_operation('room-1')['status'] == 'uncertain'
            assert local.states['room-1'][CMD_GET_SETPOINT][0x20] == temp_to_bytes(22)
            assert local.budget.last_operation('room-1')['diagnostics'][-1]['details']['failed_stage'] == 'readback_validate'
            await manager.close()
            manager.store.close()
        asyncio.run(case())


@pytest.mark.parametrize('fault', ['partial', 'status_byte', 'unknown', 'extra', 'width', 'zero_length'])
def test_unknown_ack_remains_uncertain_even_when_effect_applied(tmp_path, fault):
    ack = synthetic_setpoint_block()
    if fault == 'partial': ack.pop(0x32)
    if fault == 'status_byte': ack = {0x20: b'\x00'}
    if fault == 'unknown': ack = {0xFF: b'\x01'}
    if fault == 'extra': ack[0x99] = b'\x01'
    if fault == 'width': ack[0x21] = b'\x01'
    if fault == 'zero_length': ack[0x30] = b''
    with pytest.raises(WriteNotConfirmed): validate_set_ack(CMD_SET_SETPOINT, ack)
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager = await manager_for(local)
            local.ack = ack
            with pytest.raises(RoomUnavailable): await change(manager)
            assert local.states['room-1'][CMD_GET_SETPOINT][0x20] == temp_to_bytes(23)
            assert local.budget.last_operation('room-1')['status'] == 'uncertain'
            assert local.budget.last_operation('room-1')['diagnostics'][-1]['details']['failed_stage'] == 'ack_validate'
            await manager.operate('room-1')
            with pytest.raises(RoomUnavailable): await change(manager, value=24)
            assert len(writes(local)) == 1
            await manager.close()
            manager.store.close()
        asyncio.run(case())


def test_uncertain_survives_restart_with_compatible_get_until_explicit_review(tmp_path):
    async def run(local, *, restart):
        manager = await manager_for(local)
        if not restart:
            local.apply_writes = False
            with pytest.raises(RoomUnavailable): await change(manager)
            assert len(writes(local)) == 1
        else:
            assert local.budget.last_operation('room-1')['status'] == 'uncertain'
            with pytest.raises(RoomUnavailable): await change(manager)
            assert not writes(local)
            view = await manager.native_recover('room-1', 'Synthetic authorized GET-only review after restart')
            assert view['last_operation']['status'] == 'reviewed_uncertain'
            assert not writes(local)
        await manager.close()
        manager.store.close()
    with closed_office(tmp_path, local=True) as local: asyncio.run(run(local, restart=False))
    with closed_office(tmp_path, local=True) as local: asyncio.run(run(local, restart=True))
