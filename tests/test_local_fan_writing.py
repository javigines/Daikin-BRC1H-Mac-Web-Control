"""Entirely synthetic Local fan writes through the closed SDK and real ledger."""
import asyncio
import copy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from integration.office_mock import closed_office
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.backends.daikin import WriteNotConfirmed
from madoka_multiroom.backends.office_native import fan_ack_schema, UnknownFanAck
from madoka_multiroom.fan_policy import FanPolicy, fan_block
from madoka_multiroom.office_access import Controls
from madoka_multiroom.manager import RoomManager
from madoka_multiroom.storage import RoomStore
from madoka_protocol import (CMD_GET_FAN, CMD_GET_MODE, CMD_GET_POWER, CMD_GET_SETPOINT,
                             CMD_SET_FAN, CMD_SET_POWER, CMD_SET_MODE, CMD_SET_SETPOINT)

FIELDS = ('power', 'mode', 'setpoint', 'sensor', 'fan')


def sets(local):
    return [c for c in local.calls if len(c) == 3 and c[1] in
            (CMD_SET_FAN, CMD_SET_POWER, CMD_SET_MODE, CMD_SET_SETPOINT)]


def commissioned(**kwargs):
    return Controls(fans=(1, 3, 5), fan_policy=FanPolicy(cooling_steps=(1, 5), heating_steps=(3, 5)), **kwargs)


async def ready(local, mode=3, pair=(3, 0), room='room-1'):
    local.states[room][CMD_GET_MODE][0x20] = bytes([mode])
    local.states[room][CMD_GET_FAN] = {0x20: bytes([pair[0]]), 0x21: bytes([pair[1]]), 0x97: b'\x39\x42'}
    backend = await local.fleet.ensure_room(room)
    backend.controls = commissioned()
    await backend.snapshot(FIELDS)
    return backend


async def change(backend, value=5, identity=None, desired=None):
    return await backend.manual('fan', value, identity or str(uuid4()), 'Synthetic fan acceptance', desired or (lambda *args: None))


@pytest.mark.parametrize('data', [dict(cooling_steps=[0]), dict(heating_steps=[6]), dict(cooling_steps=[True]),
    dict(cooling_steps=[1, 1]), dict(cooling_steps=[1.0]), dict(ack_profile='guessed'), dict(extra=True)])
def test_commissioning_never_infers_auto_or_unknown_profiles(data):
    with pytest.raises(ValidationError): FanPolicy.model_validate(data)
    with pytest.raises(ValidationError): Controls(fans=(1,), fan_policy=FanPolicy(cooling_steps=(5,)))


@pytest.mark.parametrize('mode', [3, 4])
@pytest.mark.parametrize('pair', [(3, 2), (3, 0), (0, 2), (0, 0)])
def test_each_banked_class_single_journaled_set_then_strict_get_preserves_bytes(tmp_path, mode, pair):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backend = await ready(local, mode, pair)
            before = copy.deepcopy(local.states)
            operation = str(uuid4())
            desired = []
            def reply(room, command, objects, wrote):
                if command == CMD_SET_FAN:
                    assert local.budget.operation(operation)['status'] == 'pending'
                    assert desired == [('fan_id', 5)]
                    # Source-shaped ACK does not echo the requested value.
                    return {0x20: b'\x00', 0x21: b'\x01'}
                if command in (CMD_GET_FAN, CMD_GET_MODE, CMD_GET_SETPOINT) and wrote:
                    objects[0x98] = b'\x75'  # Newly returned GET-only data.
                return objects
            local.reply_hook = reply
            try:
                result = await change(backend, identity=operation, desired=lambda *args: desired.append(args))
                target, inactive = (0x20, 0x21) if mode == 3 else (0x21, 0x20)
                assert result['fan_id'] == 5
                assert sets(local) == [('room-1', CMD_SET_FAN, {target: b'\x05', inactive: before['room-1'][CMD_GET_FAN][inactive]})]
                assert local.budget.operation(operation)['status'] == 'confirmed'
                expected = copy.deepcopy(before)
                expected['room-1'][CMD_GET_FAN][target] = b'\x05'
                assert local.states == expected
                commands = [c[1] for c in local.calls if len(c) == 3]
                index = commands.index(CMD_SET_FAN)
                assert commands[index + 1] == CMD_GET_FAN
                assert backend.raw[CMD_GET_FAN][0x97] == b'\x39\x42'
                assert backend.raw[CMD_GET_FAN][0x98] == b'\x75'
                with pytest.raises(BackendUnavailable): await change(backend, identity=operation)
                assert len(sets(local)) == 1
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('mode', [3, 4])
@pytest.mark.parametrize('fault', ['missing_active', 'missing_inactive', 'wide', 'empty', 'reserved',
    'unknown', 'wrong_type', 'no_permission', 'other_mode', 'auto_selection', 'unconfirmed_step'])
def test_rejected_fan_controls_have_zero_set_intent_and_journal(tmp_path, mode, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backend = await ready(local, mode)
            active, inactive = (0x20, 0x21) if mode == 3 else (0x21, 0x20)
            if fault.startswith('missing'): local.states['room-1'][CMD_GET_FAN].pop(active if fault.endswith('active') and not fault.endswith('inactive') else inactive)
            if fault in ('wide', 'empty', 'reserved', 'unknown'):
                local.states['room-1'][CMD_GET_FAN][inactive] = {'wide': b'\x00\x00', 'empty': b'', 'reserved': b'\x06', 'unknown': b'\xff'}[fault]
            if fault == 'no_permission': backend.controls = Controls(fans=(1, 3, 5))
            if fault == 'other_mode': local.states['room-1'][CMD_GET_MODE][0x20] = b'\x02'
            await backend.snapshot(FIELDS)
            # Wrong types cannot arrive from the wire; verify the pure gate.
            if fault == 'wrong_type': backend.raw[CMD_GET_FAN][inactive] = bytearray([0])
            value = 0 if fault == 'auto_selection' else 2 if fault == 'unconfirmed_step' else 5
            try:
                with pytest.raises(ValueError):
                    await change(backend, value, desired=lambda *a: pytest.fail('Rejected write recorded desired state'))
                assert not sets(local) and local.budget.last_operation('room-1') is None
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('fault', ['mode_heat', 'mode_other', 'inactive_auto_to_unknown', 'partial',
    'permission_revoked', 'permission_revoked_last_get', 'mode_changes_last_get', 'pending_last_get', 'setpoint_partial'])
def test_fresh_baseline_revalidates_before_intent_or_journal(tmp_path, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backend = await ready(local, pair=(3, 2))
            if fault.startswith('mode_') and fault != 'mode_changes_last_get': local.states['room-1'][CMD_GET_MODE][0x20] = bytes([4 if fault == 'mode_heat' else 2])
            if fault == 'inactive_auto_to_unknown': local.states['room-1'][CMD_GET_FAN][0x21] = b'\xff'
            if fault == 'partial': local.states['room-1'][CMD_GET_FAN].pop(0x21)
            if fault == 'permission_revoked': backend.controls = Controls()
            if fault == 'setpoint_partial': local.states['room-1'][CMD_GET_SETPOINT].pop(0xA2)
            count = 0
            def reply(room, command, objects, wrote):
                nonlocal count
                if command == CMD_GET_MODE:
                    count += 1
                    if count == 2:
                        if fault == 'mode_changes_last_get': objects[0x20] = b'\x04'
                        if fault == 'permission_revoked_last_get': backend.controls = Controls()
                        if fault == 'pending_last_get':
                            local.budget.begin_operation(backend.port, backend, str(uuid4()), 'fan', 1, {})
                return objects
            local.reply_hook = reply
            try:
                with pytest.raises(ValueError): await change(backend, desired=lambda *a: pytest.fail('Stale permission'))
                assert not sets(local)
                if fault != 'pending_last_get': assert local.budget.last_operation('room-1') is None
                assert not backend.stopped
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('fault', ['no_effect', 'inactive_changed', 'fan_extension', 'fan_extension_missing',
    'power', 'mode', 'setpoint', 'setpoint_flag', 'setpoint_extension', 'partial_get', 'wide_get',
    'unknown_ack', 'empty_ack', 'ack_extensions', 'ack_width', 'ack_reserved'])
def test_unconfirmed_effect_is_durable_uncertain_and_stop_without_retry(tmp_path, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backend = await ready(local)
            local.states['room-1'][CMD_GET_SETPOINT][0x99] = b'\x23'
            operation = str(uuid4())
            local.ack = {0x20: b'\x00', 0x21: b'\x00'}
            if fault == 'no_effect': local.apply_writes = False
            if fault == 'unknown_ack': local.ack = {0x91: b'\x00'}
            if fault == 'empty_ack': local.ack = {}
            if fault == 'ack_extensions': local.ack[0x93] = b'\x00'
            if fault == 'ack_width': local.ack[0x21] = b'\x00\x00'
            if fault == 'ack_reserved': local.ack[0x21] = b'\xff'
            def reply(room, command, objects, wrote):
                if not wrote: return objects
                if command == CMD_GET_FAN:
                    if fault == 'inactive_changed': objects[0x21] = b'\x05'
                    if fault == 'fan_extension': objects[0x97] = b'\x00'
                    if fault == 'fan_extension_missing': objects.pop(0x97)
                    if fault == 'partial_get': objects.pop(0x21)
                    if fault == 'wide_get': objects[0x21] = b'\x00\x00'
                if command == CMD_GET_POWER and fault == 'power': objects[0x20] = b'\x00'
                if command == CMD_GET_MODE and fault == 'mode': objects[0x20] = b'\x04'
                if command == CMD_GET_SETPOINT:
                    if fault == 'setpoint': objects[0x21] = b'\x0c\x00'
                    if fault == 'setpoint_flag': objects[0x30] = b'\x00' if objects[0x30] != b'\x00' else b'\x01'
                    if fault == 'setpoint_extension': objects[0x99] = b'\x24'
                return objects
            local.reply_hook = reply
            try:
                with pytest.raises(WriteNotConfirmed): await change(backend, identity=operation)
                assert len(sets(local)) == 1
                record = local.budget.operation(operation)
                assert record['status'] == 'uncertain'
                if fault in ('unknown_ack', 'empty_ack', 'ack_extensions', 'ack_width', 'ack_reserved'):
                    assert isinstance(backend.last_manual_diagnostic, dict)
                    assert backend.last_manual_diagnostic['reason'] == 'ack_profile_unknown'
                    # Even an exact GET match cannot rescue an unknown ACK.
                    assert local.states['room-1'][CMD_GET_FAN][0x20] == b'\x05'
                local.reply_hook = None
                await backend.snapshot(FIELDS)
                assert backend.control_view()['fans'] == []
                with pytest.raises(BackendUnavailable): await change(backend)
                local.budget.acknowledge_operations('room-1', backend.latest, 'Synthetic GET-only review')
                assert local.budget.pending_operation('room-1')
                assert local.budget.operation(operation)['status'] == 'uncertain'
                assert len(sets(local)) == 1
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('fault', ['disconnect', 'transport', 'get_transport', 'wrong_correlation'])
def test_wire_failure_keeps_uncertainty_one_logical_set(tmp_path, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backend = await ready(local)
            local.ack = {0x20: b'\x00', 0x21: b'\x00'}
            if fault == 'disconnect': local.disconnect_after_set = True
            if fault == 'transport': local.stage, local.error = 'write', ConnectionError('synthetic')
            if fault == 'get_transport': local.fail_readback = True
            if fault == 'wrong_correlation': local.raw_replies[CMD_SET_FAN] = b'\x04\x00\x40\x30'
            try:
                with pytest.raises(Exception): await change(backend)
                assert local.budget.last_operation('room-1')['status'] == 'uncertain'
                with pytest.raises(BackendUnavailable): await change(backend)
                assert len(sets(local)) == 1 and not backend.port.radio.write_frames
            finally: await backend.close()
        asyncio.run(case())


def test_six_rooms_permissions_uncertainty_and_raw_state_are_isolated(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backends = [await ready(local, pair=pair, room=f'room-{i}') for i, pair in enumerate(
                [(3, 2), (3, 0), (0, 2), (0, 0), (1, 0), (0, 5)], 1)]
            backends[1].controls = Controls(fans=(1, 3, 5))
            backends[2].controls = Controls(fans=(3,), fan_policy=FanPolicy(heating_steps=(3,)))
            before = copy.deepcopy(local.states)
            local.ack = {0x20: b'\x00', 0x21: b'\x00'}
            try:
                assert [b.control_view()['fans'] for b in backends] == [[1,5], [], [], [1,5], [1,5], [1,5]]
                local.failed_effect_rooms.add('room-4')
                results = await asyncio.gather(change(backends[0]), change(backends[3]), change(backends[4]), return_exceptions=True)
                assert isinstance(results[1], WriteNotConfirmed)
                assert results[0]['fan_id'] == results[2]['fan_id'] == 5
                for room in ('room-2', 'room-3', 'room-6'): assert local.states[room] == before[room]
                assert local.budget.pending_operation('room-4')
                assert not any(local.budget.pending_operation(r) for r in local.states if r != 'room-4')
                assert len(sets(local)) == 3
            finally:
                for backend in backends: await backend.close()
        asyncio.run(case())


def test_unknown_ack_survives_actual_get_only_review_and_runtime_restart(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            store = RoomStore(':memory:', mode='real')
            manager = RoomManager(local.config.rooms, store, offline_fleet=local.fleet)
            try:
                backend = await ready(local)
                manager.room('room-1').backend = backend
                await manager.operate('room-1')
                with pytest.raises(UnknownFanAck): await change(backend)
                result = await manager.native_recover('room-1', 'Synthetic GET review')
                assert result['last_operation']['status'] == 'uncertain'
                assert not result['manual_controls']['fans']
                assert len(sets(local)) == 1
            finally:
                await manager.close()
                store.close()
        asyncio.run(case())
    with closed_office(tmp_path, local=True) as restarted:
        assert restarted.budget.pending_operation('room-1')
        assert restarted.budget.last_operation('room-1')['status'] == 'uncertain'
        assert not sets(restarted)


def test_noop_and_commissioning_persistence_never_send_set(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backend = await ready(local, pair=(1, 0))
            try:
                local.fleet.configure_controls('room-1', commissioned())
                local.fleet.reload_controls()
                assert backend.controls.fan_policy == commissioned().fan_policy
                await change(backend, 1)
                assert local.budget.last_operation('room-1')['status'] == 'confirmed'
                assert backend.last_manual_diagnostic['stage'] == 'confirmed_noop'
                assert not sets(local)
            finally: await backend.close()
        asyncio.run(case())


def test_block_and_ack_helpers_do_not_normalize_or_forward_extensions():
    raw = {0x20: b'\x00', 0x21: b'\x03', 0x99: b'\xff\x00'}
    original = copy.deepcopy(raw)
    assert dict(fan_block(raw, 4, 5)) == {0x20: b'\x00', 0x21: b'\x05'}
    assert raw == original
    assert fan_ack_schema({0x20:b'\x00',0x21:b'\x03'}, commissioned=True) == 'pair_v1'
    with pytest.raises(UnknownFanAck): fan_ack_schema(raw, commissioned=True)
    with pytest.raises(UnknownFanAck): fan_ack_schema({}, commissioned=True)


@pytest.mark.parametrize('stage', ['journal', 'desired'])
def test_persistence_must_succeed_before_any_set(tmp_path, monkeypatch, stage):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backend = await ready(local)
            def fail(*args): raise OSError('Synthetic storage fault')
            if stage == 'journal': monkeypatch.setattr(local.budget, 'begin_operation', fail)
            try:
                with pytest.raises(OSError): await change(backend, desired=fail)
                assert not sets(local)
                record = local.budget.last_operation('room-1')
                assert record is None if stage == 'journal' else record['status'] == 'uncertain'
            finally: await backend.close()
        asyncio.run(case())


def test_ack_permission_snapshot_cannot_be_widened_after_set(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backend = await ready(local)
            def reply(room, command, objects, wrote):
                if command == CMD_SET_FAN:
                    backend.controls = Controls(fans=(1,3,5))
                    return {}  # Never fall back to legacy after transmission.
                return objects
            local.reply_hook = reply
            try:
                with pytest.raises(UnknownFanAck): await change(backend)
                record = local.budget.last_operation('room-1')
                assert record['status'] == 'uncertain'
                assert record['before_state']['fan_commissioning']['ack_profile'] == 'pair_v1'
                assert len(sets(local)) == 1
            finally: await backend.close()
        asyncio.run(case())


def test_cancelled_fan_set_is_never_replayed(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backend = await ready(local)
            local.stage, local.error = 'write', asyncio.CancelledError()
            try:
                with pytest.raises(asyncio.CancelledError): await change(backend)
                assert local.budget.last_operation('room-1')['status'] == 'uncertain'
                with pytest.raises(BackendUnavailable): await change(backend)
                assert len(sets(local)) == 1 and not backend.port.radio.write_frames
            finally: await backend.close()
        asyncio.run(case())
