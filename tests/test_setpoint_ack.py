"""Independent ACK schema, exact GET confirmation, synthetic closed SDKs only."""
import asyncio
import copy
import json
from uuid import uuid4

import pytest

from integration.office_mock import closed_office, synthetic_setpoint_block, synthetic_setpoint_ack
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.backends.office_native import (
    UnknownSetpointAck, setpoint_fields, validate_set_ack,
)
from madoka_multiroom.manager import RoomManager, RoomUnavailable
from madoka_multiroom.storage import RoomStore
from madoka_protocol import CMD_GET_MODE, CMD_GET_POWER, CMD_GET_SETPOINT, CMD_SET_SETPOINT, temp_to_bytes


async def setup(local):
    manager = RoomManager(local.config.rooms, RoomStore(':memory:', mode='real'), offline_fleet=local.fleet)
    for room, state in local.states.items():
        state[CMD_GET_SETPOINT] = synthetic_setpoint_block(extended=True)
        manager.room(room).backend = await local.fleet.ensure_room(room)
        await manager.operate(room)
    return manager


def wire_calls(local):
    return [c for c in local.calls if len(c) == 3]


def writes(local):
    return [c for c in wire_calls(local) if c[1] == CMD_SET_SETPOINT]


async def change(manager, room, value, op=None):
    return await manager.native_manual(room, 'setpoint', value, op or str(uuid4()), 'Synthetic user action')


def test_compact_ack_is_not_a_get_and_payload_values_are_not_status_codes():
    ack = synthetic_setpoint_ack()
    assert len(ack) == 17
    assert validate_set_ack(CMD_SET_SETPOINT, ack) == 'compact_fields'
    with pytest.raises(BackendUnavailable): setpoint_fields(ack)
    # ACK values have no interpreted success, flag, temperature or limit meaning.
    full = {k: b'\xff' * len(v) for k, v in synthetic_setpoint_block().items()}
    assert validate_set_ack(CMD_SET_SETPOINT, full) == 'full_fields'
    with pytest.raises(BackendUnavailable): setpoint_fields(full)


@pytest.mark.parametrize('room', [f'room-{i}' for i in range(1, 7)])
@pytest.mark.parametrize('mode,target,initial', [(3, 0x20, 22), (4, 0x21, 21)])
@pytest.mark.parametrize('delta', [-1, 1])
@pytest.mark.parametrize('effect', [False, True])
def test_compact_ack_six_rooms_both_directions_confirm_only_strict_get(tmp_path, room, mode, target, initial, delta, effect):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.states[room][CMD_GET_MODE][0x20] = bytes([mode])
            manager = await setup(local)
            before = copy.deepcopy(local.states)
            local.ack, local.apply_writes = synthetic_setpoint_ack(), effect
            op = str(uuid4())
            if effect:
                view = await change(manager, room, initial + delta, op)
                assert view['last_operation']['status'] == 'confirmed'
                assert view['state']['setpoint'] == initial + delta
                before[room][CMD_GET_SETPOINT][target] = temp_to_bytes(initial + delta)
            else:
                with pytest.raises(RoomUnavailable): await change(manager, room, initial + delta, op)
                record = local.budget.last_operation(room)
                assert record['status'] == 'uncertain'
                assert record['diagnostics'][-1]['details']['reason'] == 'no_effect'
                with pytest.raises(RoomUnavailable): await change(manager, room, initial - delta)
                assert not manager.snapshot(room)['manual_controls']['setpoint']
            calls = wire_calls(local)
            idx = next(i for i, c in enumerate(calls) if c[1] == CMD_SET_SETPOINT)
            assert calls[idx + 1][:2] == (room, CMD_GET_SETPOINT)
            assert sum(c[1] == CMD_GET_SETPOINT for c in calls[idx+1:]) == 1
            assert len(writes(local)) == 1
            assert local.states == before  # All inactive bytes and five other rooms.
            assert all(manager.snapshot(r)['manual_controls']['setpoint'] for r in manager.rooms if r != room)
            assert any(e['details'].get('schema') == 'compact_fields' for e in local.budget.operation_events(op))
            await manager.close()
            manager.store.close()
        asyncio.run(case())


@pytest.mark.parametrize('fault,reason', [
    ('mixed', 'ack_field_widths'), ('zero', 'ack_field_widths'), ('large', 'ack_field_widths'),
    ('extra', 'ack_object_ids'), ('missing', 'ack_object_ids'), ('type', 'ack_value_type'),
])
def test_unknown_profiles_are_explicitly_bounded(fault, reason):
    ack = synthetic_setpoint_ack()
    if fault == 'mixed': ack[0x20] = b'\x00\x01'
    if fault == 'zero': ack[0x20] = b''
    if fault == 'large': ack[0x20] = b'\x00\x01\x02'
    if fault == 'extra': ack[0x99] = b'\x00'
    if fault == 'missing': ack.pop(0x31)
    if fault == 'type': ack[0x20] = bytearray(b'\x01')
    with pytest.raises(UnknownSetpointAck) as error: validate_set_ack(CMD_SET_SETPOINT, ack)
    assert error.value.reason == reason


@pytest.mark.parametrize('effect', [False, True])
@pytest.mark.parametrize('get_failed', [False, True])
def test_unknown_ack_get_is_diagnostic_only_and_never_clears_stop(tmp_path, effect, get_failed):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager = await setup(local)
            local.ack, local.apply_writes = {0x99: b'\x05'}, effect
            local.fail_readback = get_failed
            with pytest.raises(RoomUnavailable): await change(manager, 'room-1', 23)
            record = local.budget.last_operation('room-1')
            assert record['status'] == 'uncertain'
            events = record['diagnostics']
            observation = next(e['details'] for e in events if e['stage'] == 'unknown_ack_observation')
            assert observation['result'] == ('requested_state_observed' if effect and not get_failed else 'not_verified')
            assert events[-1]['details']['failed_stage'] == 'ack_validate'
            assert events[-1]['details']['reason'] == 'ack_object_ids'
            calls = wire_calls(local)
            idx = next(i for i, c in enumerate(calls) if c[1] == CMD_SET_SETPOINT)
            assert calls[idx+1][:2] == ('room-1', CMD_GET_SETPOINT)
            assert sum(c[1] == CMD_GET_SETPOINT for c in calls[idx+1:]) == 1
            local.fail_readback = False
            if not get_failed: await manager.operate('room-1')
            with pytest.raises(RoomUnavailable): await change(manager, 'room-1', 24)
            assert local.budget.pending_operation('room-1') and len(writes(local)) == 1
            # Explicit review may accept the observed state, never replay intent.
            view = await manager.native_recover('room-1', 'Synthetic authorized GET-only review')
            assert view['last_operation']['status'] == 'reviewed_uncertain'
            assert len(writes(local)) == 1
            await manager.close()
            manager.store.close()
        asyncio.run(case())


@pytest.mark.parametrize('fault', ['duplicate', 'truncated', 'wrong_command', 'nonzero_header'])
def test_malformed_or_uncorrelated_ack_never_initiates_observation(tmp_path, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager = await setup(local)
            command, header, body = CMD_SET_SETPOINT, 0, b''
            if fault == 'duplicate': body = b'\x20\x01\x00\x20\x01\x01'
            if fault == 'truncated': body = b'\x20\x02\x00'
            if fault == 'wrong_command': command = CMD_GET_SETPOINT
            if fault == 'nonzero_header': header = 1
            local.raw_replies[CMD_SET_SETPOINT] = bytes([len(body)+4, header]) + command.to_bytes(2, 'big') + body
            with pytest.raises(RoomUnavailable): await change(manager, 'room-1', 23)
            calls = wire_calls(local)
            assert calls[-1][1] == CMD_SET_SETPOINT and len(writes(local)) == 1
            assert local.budget.pending_operation('room-1')
            assert local.budget.last_operation('room-1')['status'] == 'uncertain'
            assert local.budget.last_operation('room-1')['diagnostics'][-1]['details']['reason'] == 'reply_framing_or_correlation'
            assert not manager.room('room-1').backend.port.ready
            assert all(manager.snapshot(r)['connected'] for r in manager.rooms if r != 'room-1')
            await manager.close()
            manager.store.close()
        asyncio.run(case())


@pytest.mark.parametrize('fault,reason', [('inactive', 'preserved_field_mismatch'), ('limit', 'preserved_field_mismatch'),
    ('symbol', 'preserved_field_mismatch'), ('config', 'preserved_field_mismatch'), ('target', 'active_target_mismatch'),
    ('mode', 'state_mismatch'), ('power', 'state_mismatch')])
def test_compact_ack_does_not_override_contradictory_readback(tmp_path, fault, reason):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager = await setup(local)
            local.ack = synthetic_setpoint_ack()
            def reply(room, command, objects, wrote):
                if room == 'room-1' and wrote:
                    if command == CMD_GET_MODE and fault == 'mode': return {0x20: b'\x04'}
                    if command == CMD_GET_POWER and fault == 'power': return {0x20: b'\x00'}
                    if command == CMD_GET_SETPOINT:
                        k = {'inactive': 0x21, 'limit': 0xA2, 'symbol': 0xA4, 'config': 0x32, 'target': 0x20}.get(fault)
                        if k: objects[k] = (int.from_bytes(objects[k], 'big') + 1).to_bytes(len(objects[k]), 'big')
                return objects
            local.reply_hook = reply
            with pytest.raises(RoomUnavailable): await change(manager, 'room-1', 23)
            record = local.budget.last_operation('room-1')
            assert record['status'] == 'uncertain' and record['diagnostics'][-1]['details']['reason'] == reason
            assert len(writes(local)) == 1
            await manager.close()
            manager.store.close()
        asyncio.run(case())


def test_ack_and_transport_diagnostics_never_copy_payloads_or_exception_text(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager = await setup(local)
            secret = b'SYNTHETIC-PRIVATE-REPLY-DO-NOT-COPY'
            local.ack = {0x99: secret}
            local.fail_readback = True
            with pytest.raises(RoomUnavailable): await change(manager, 'room-1', 23)
            diagnostic = json.dumps(local.budget.last_operation('room-1')['diagnostics'])
            session_details = json.dumps([dict(r) for r in local.budget.db.execute('SELECT details FROM session_events')])
            for output in (diagnostic, session_details):
                assert secret.decode() not in output and secret.hex() not in output
                assert 'reply_hex' not in output and 'objects' not in output
            await manager.close()
            manager.store.close()
        asyncio.run(case())


def test_recognized_compact_ack_get_failure_never_confirms_or_retries(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            manager = await setup(local)
            local.ack, local.fail_readback = synthetic_setpoint_ack(), True
            with pytest.raises(RoomUnavailable): await change(manager, 'room-1', 23)
            record = local.budget.last_operation('room-1')
            assert record['status'] == 'uncertain' and len(writes(local)) == 1
            assert record['diagnostics'][-1]['details']['failed_stage'] == 'readback_get'
            assert record['diagnostics'][-1]['details']['reason'] == 'get_unavailable'
            assert local.states['room-1'][CMD_GET_SETPOINT][0x20] == temp_to_bytes(23)
            with pytest.raises(RoomUnavailable): await change(manager, 'room-1', 24)
            assert len(writes(local)) == 1
            await manager.close()
            manager.store.close()
        asyncio.run(case())


def test_unknown_ack_remains_uncertain_after_restart_until_get_only_review(tmp_path):
    async def run(local, restart):
        manager = await setup(local)
        if not restart:
            local.ack = {0x99: b'\x07'}
            with pytest.raises(RoomUnavailable): await change(manager, 'room-1', 23)
            assert len(writes(local)) == 1
        else:
            assert local.budget.last_operation('room-1')['status'] == 'uncertain'
            with pytest.raises(RoomUnavailable): await change(manager, 'room-1', 24)
            assert not writes(local)
            view = await manager.native_recover('room-1', 'Synthetic authorized GET-only review after restart')
            assert view['last_operation']['status'] == 'reviewed_uncertain'
            assert not writes(local)
        await manager.close()
        manager.store.close()
    with closed_office(tmp_path, local=True) as local: asyncio.run(run(local, False))
    with closed_office(tmp_path, local=True) as local: asyncio.run(run(local, True))
