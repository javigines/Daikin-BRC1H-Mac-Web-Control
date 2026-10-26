"""Invented exact-pair fixtures, closed SDKs and temporary ledgers only."""
import asyncio
import copy
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from integration.office_mock import (closed_office, synthetic_coordinated_block,
    synthetic_coordinated_profile, synthetic_setpoint_ack)
from madoka_multiroom.api import build_local_app
from madoka_multiroom.backends.office_native import (SETPOINT_WIDTHS, SetpointUnconfirmed,
    setpoint_block, validate_setpoint_readback)
from madoka_multiroom.coordinated_setpoint import CoordinatedSetpointProfile, SetpointConfirmation
from madoka_multiroom.local_runtime import LocalConfig, load_local_config
from madoka_multiroom.manager import RoomBusy, RoomManager, RoomUnavailable
from madoka_multiroom.office_access import Controls
from madoka_multiroom.storage import RoomStore
from madoka_protocol import (CMD_GET_FAN, CMD_GET_MODE, CMD_GET_POWER, CMD_GET_SETPOINT,
    CMD_SET_SETPOINT, CMD_SET_POWER, CMD_SET_MODE, CMD_SET_FAN, temp_to_bytes)


def sets(local):
    return [c for c in local.calls if len(c) == 3 and c[1] in
            (CMD_SET_SETPOINT, CMD_SET_POWER, CMD_SET_MODE, CMD_SET_FAN)]


async def setup(local):
    manager = RoomManager(local.config.rooms, RoomStore(':memory:', mode='real'), offline_fleet=local.fleet)
    for room in manager.rooms:
        manager.room(room).backend = await local.fleet.ensure_room(room)
        await manager.operate(room)
    return manager


async def close(manager):
    await manager.close()
    manager.store.close()


def confirmation(manager, room='room-1'):
    proposal, = manager.snapshot(room)['manual_controls']['setpoint_proposals']
    return SetpointConfirmation(token=proposal['token'], confirmed=True)


async def change(manager, room='room-1', *, consent=None, value=25, op=None):
    return await manager.native_manual(room, 'setpoint', value, op or str(uuid4()),
        'Synthetic explicit user action', setpoint_confirmation=consent)


def test_private_config_default_off_exact_scope_and_strict_profile(tmp_path):
    with closed_office(tmp_path / 'closed', local=True) as local:
        assert local.config.coordinated_setpoints == {}
        device = local.config.rooms.devices[0]
        profile = synthetic_coordinated_profile(device.address)
        good = dict(rooms=local.config.rooms, controls=local.config.controls,
                    coordinated_setpoints={device.id: profile})
        assert LocalConfig(**good).coordinated_setpoints[device.id] == profile
        for profiles in ({'unknown-room': profile}, {'room-2': profile},
                         {device.id: profile.model_copy(update={'identity_digest': '0' * 64})}):
            with pytest.raises(ValueError): LocalConfig(**{**good, 'coordinated_setpoints': profiles})
        for patch in ({'profile': 'heat-raise-one-v1'}, {'cooling_before': 27},
                      {'cooling_before': 26.0}, {'heating_before': True}, {'differential': 2}):
            with pytest.raises(ValidationError):
                CoordinatedSetpointProfile.model_validate({**profile.model_dump(), **patch})
        path = tmp_path / 'synthetic-private.json'
        data = LocalConfig(**good).model_dump(mode='json')
        path.write_text(json.dumps(data)); path.chmod(0o600)
        assert load_local_config(path) == LocalConfig(**good)


@pytest.mark.parametrize('room', [f'room-{i}' for i in range(1, 7)])
def test_exact_profile_confirmed_pair_one_set_and_six_room_isolation(tmp_path, room):
    with closed_office(tmp_path, local=True, coordinated_room=room) as local:
        async def case():
            manager = await setup(local)
            original = copy.deepcopy(local.states)
            consent = confirmation(manager, room)
            op = str(uuid4())
            def reply(r, cmd, obj, wrote):
                if cmd == CMD_SET_SETPOINT:
                    row = local.budget.last_operation(r)
                    assert row['status'] == 'pending' and row['operation_id'] == op
                    authorization = row['before_state']['setpoint_authorization']
                    assert authorization['targets'] == [0x20, 0x21]
                    assert authorization['expected'] == {'32': temp_to_bytes(25).hex(), '33': temp_to_bytes(24).hex()}
                    assert authorization['coordinated_confirmation']['token'] == consent.token
                    return synthetic_setpoint_ack()
                if cmd == CMD_GET_SETPOINT and wrote: obj[0x9B] = b'\x07'
                return obj
            local.reply_hook = reply
            view = await change(manager, room, consent=consent, op=op)
            assert view['last_operation']['status'] == 'confirmed'
            assert (view['state']['cooling_setpoint'], view['state']['heating_setpoint']) == (25, 24)
            command, = sets(local)
            assert command[0:2] == (room, CMD_SET_SETPOINT)
            assert command[2].keys() == SETPOINT_WIDTHS.keys()
            expected_args = {k: v for k, v in original[room][CMD_GET_SETPOINT].items() if k in SETPOINT_WIDTHS}
            expected_args.update({0x20: temp_to_bytes(25), 0x21: temp_to_bytes(24)})
            assert command[2] == expected_args  # No unknown extensions sent.
            original[room][CMD_GET_SETPOINT].update({0x20: temp_to_bytes(25), 0x21: temp_to_bytes(24)})
            assert local.states == original  # Every other byte/room unchanged.
            calls = [c for c in local.calls if len(c) == 3]
            index = calls.index(command)
            assert calls[index+1][:2] == (room, CMD_GET_SETPOINT)
            assert sum(c[1] == CMD_GET_SETPOINT for c in calls[index+1:]) == 1
            with pytest.raises(RoomBusy): await change(manager, room, value=26, op=op)
            assert len(sets(local)) == 1
            assert manager.snapshot(room)['manual_controls']['setpoint_proposals'] == []
            # Normal controls persistence does not grant/remove commissioning.
            backend = manager.room(room).backend
            profile = backend.coordinated_setpoint_profile
            local.fleet.configure_controls(room, backend.controls)
            assert local.fleet._backend(manager.room(room).config).coordinated_setpoint_profile == profile
            assert 'coordinated_setpoints' not in json.loads(local.fleet.settings_path.read_text())
            await close(manager)
        asyncio.run(case())


@pytest.mark.parametrize('fault', ['missing_consent', 'wrong_token', 'other_room', 'new_target', 'baseline',
    'inactive', 'flags', 'differential', 'extension', 'new_extension', 'limits', 'owner_limits',
    'power', 'fan', 'mode', 'profile_removed'])
def test_denial_or_stale_consent_rejected_before_ledger_and_set(tmp_path, fault):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            consent = confirmation(manager)
            state = local.states['room-1']
            block = state[CMD_GET_SETPOINT]
            if fault == 'missing_consent': consent = None
            if fault == 'wrong_token': consent = SetpointConfirmation(token='0'*64, confirmed=True)
            if fault == 'other_room':
                other = manager.room('room-2').backend
                other.coordinated_setpoint_profile = synthetic_coordinated_profile(local.grants['room-2'].identity)
                local.states['room-2'][CMD_GET_SETPOINT] = synthetic_coordinated_block()
                await manager.operate('room-2')
                consent = confirmation(manager, 'room-2')
            if fault == 'baseline': block[0x20] = temp_to_bytes(27)
            if fault == 'inactive': block[0x21] = temp_to_bytes(24)
            if fault == 'flags': block[0x31] = b'\x02'
            if fault == 'differential': block[0x32] = b'\x02'
            if fault == 'extension': block[0x99] = b'\x11'
            if fault == 'new_extension': block[0x9C] = b'\x05'
            if fault == 'limits': block[0xA3] = temp_to_bytes(24.5)
            if fault == 'owner_limits': manager.room('room-1').backend.controls = Controls(setpoint=True, minimum=25, maximum=30)
            if fault == 'power': state[CMD_GET_POWER][0x20] = b'\x00'
            if fault == 'fan': state[CMD_GET_FAN][0x21] = b'\x04'
            if fault == 'mode': state[CMD_GET_MODE][0x20] = b'\x04'
            if fault == 'profile_removed': manager.room('room-1').backend.coordinated_setpoint_profile = None
            original = copy.deepcopy(local.states)
            with pytest.raises(ValueError): await change(manager, consent=consent, value=24 if fault == 'new_target' else 25)
            assert not sets(local) and not local.budget.last_operation('room-1')
            assert not local.budget.pending_operation('room-1')
            assert local.states == original
            assert not manager.room('room-1').failure
            await close(manager)
        asyncio.run(case())


@pytest.mark.parametrize('field,value', [(0xA2, 26), (0xB2, 24), (0xA3, 25), (0xB3, 23),
    (0xA3, 0), (0xB3, 41), (0xA3, 24.5)])
def test_both_effective_device_windows_required(tmp_path, field, value):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            consent = confirmation(manager)
            local.states['room-1'][CMD_GET_SETPOINT][field] = temp_to_bytes(value)
            await manager.operate('room-1')
            assert manager.snapshot('room-1')['manual_controls']['setpoint_proposals'] == []
            with pytest.raises(ValueError): await change(manager, consent=consent)
            # Suppressing the token must not turn a known paired request into a simple SET.
            with pytest.raises(ValueError): await change(manager)
            assert not sets(local) and not local.budget.last_operation('room-1')
            await close(manager)
        asyncio.run(case())


@pytest.mark.parametrize('variant', ['no_profile', 'other_room', 'flags30', 'flags31', 'diff', 'other_pair', 'heat', 'increase'])
def test_no_automatic_profile_extrapolation_and_simple_changes_preserved(tmp_path, variant):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            room = 'room-2' if variant == 'other_room' else 'room-1'
            local.states[room][CMD_GET_SETPOINT] = synthetic_coordinated_block()
            if variant == 'flags30': local.states[room][CMD_GET_SETPOINT][0x30] = b'\x01'
            if variant == 'flags31': local.states[room][CMD_GET_SETPOINT][0x31] = b'\x02'
            if variant == 'diff': local.states[room][CMD_GET_SETPOINT][0x32] = b'\x03'
            if variant == 'heat': local.states[room][CMD_GET_MODE][0x20] = b'\x04'
            if variant == 'other_pair': local.states[room][CMD_GET_SETPOINT].update({0x20:temp_to_bytes(28),0x21:temp_to_bytes(27)})
            manager = await setup(local)
            if variant == 'no_profile': manager.room(room).backend.coordinated_setpoint_profile = None
            if variant != 'increase': assert manager.snapshot(room)['manual_controls']['setpoint_proposals'] == []
            original = copy.deepcopy(local.states)
            value = 27 if variant in ('increase', 'other_pair') else 24 if variant == 'heat' else 25
            view = await change(manager, room, value=value)
            target = 0x21 if variant == 'heat' else 0x20
            original[room][CMD_GET_SETPOINT][target] = temp_to_bytes(value)
            assert view['last_operation']['status'] == 'confirmed' and local.states == original
            assert len(sets(local)) == 1
            await close(manager)
        asyncio.run(case())


@pytest.mark.parametrize('fault,reason', [('no_effect','no_effect'), ('active_only','inactive_target_mismatch'),
    ('inactive_only','active_target_mismatch'), ('inactive_wrong','inactive_target_mismatch'),
    ('symbol','preserved_field_mismatch'), ('extension','preserved_field_mismatch'),
    ('missing_extension','preserved_field_mismatch'), ('limit','preserved_field_mismatch'),
    ('flag','preserved_field_mismatch'), ('mode','state_mismatch'), ('power','state_mismatch'),
    ('fan','state_mismatch'), ('unknown_ack','ack_object_ids'), ('get_failed','get_unavailable')])
def test_unconfirmed_pair_stops_without_retry_or_restore_and_review_is_get_only(tmp_path, fault, reason):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            original = copy.deepcopy(local.states)
            consent = confirmation(manager)
            local.ack = {0x99:b'\x01'} if fault == 'unknown_ack' else synthetic_setpoint_ack()
            local.apply_writes = fault != 'no_effect'
            def reply(room, cmd, objects, wrote):
                if room != 'room-1' or not wrote: return objects
                if fault == 'mode' and cmd == CMD_GET_MODE: objects[0x20] = b'\x04'
                if fault == 'power' and cmd == CMD_GET_POWER: objects[0x20] = b'\x00'
                if fault == 'fan' and cmd == CMD_GET_FAN: objects[0x21] = b'\x05'
                if cmd == CMD_GET_SETPOINT:
                    if fault == 'get_failed': raise RuntimeError('Synthetic GET failure')
                    if fault == 'active_only': objects[0x21] = temp_to_bytes(25)
                    if fault == 'inactive_only': objects[0x20] = temp_to_bytes(26)
                    if fault == 'inactive_wrong': objects[0x21] = temp_to_bytes(23)
                    if fault == 'symbol': objects[0xA4] = b'\x04'
                    if fault == 'extension': objects[0x99] = b'\x06'
                    if fault == 'missing_extension': objects.pop(0x99)
                    if fault == 'limit': objects[0xA3] = temp_to_bytes(17)
                    if fault == 'flag': objects[0x30] = b'\x01'
                return objects
            local.reply_hook = reply
            with pytest.raises(RoomUnavailable): await change(manager, consent=consent)
            row = local.budget.last_operation('room-1')
            assert row['status'] == 'uncertain'
            assert row['diagnostics'][-1]['details']['reason'] == reason
            assert len(sets(local)) == 1
            assert {r:s for r,s in local.states.items() if r != 'room-1'} == {r:s for r,s in original.items() if r != 'room-1'}
            for action, value in [('setpoint',27), ('power',False)]:
                with pytest.raises(RoomUnavailable):
                    await manager.native_manual('room-1',action,value,str(uuid4()),'Synthetic action')
            local.reply_hook = None
            if fault != 'get_failed': await manager.operate('room-1')
            assert local.budget.pending_operation('room-1')
            calls = len(local.calls)
            view = await manager.native_recover('room-1','Synthetic authorized GET-only review')
            assert view['last_operation']['status'] == 'reviewed_uncertain'
            assert len(sets(local)) == 1 and all(c[1] < 0x4000 for c in local.calls[calls:] if len(c)==3)
            await close(manager)
        asyncio.run(case())


def test_coordinated_uncertainty_survives_restart_until_get_only_review(tmp_path):
    async def run(local, restart):
        manager = await setup(local)
        if not restart:
            consent = confirmation(manager)
            local.ack, local.apply_writes = synthetic_setpoint_ack(), False
            with pytest.raises(RoomUnavailable): await change(manager, consent=consent)
            assert len(sets(local)) == 1
        else:
            row = local.budget.last_operation('room-1')
            assert row['status'] == 'uncertain'
            assert row['before_state']['setpoint_authorization']['targets'] == [0x20, 0x21]
            with pytest.raises(RoomUnavailable): await change(manager)
            assert not sets(local)
            view = await manager.native_recover('room-1','Synthetic GET-only review after restart')
            assert view['last_operation']['status'] == 'reviewed_uncertain'
            assert not sets(local)
        await close(manager)
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local: asyncio.run(run(local,False))
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local: asyncio.run(run(local,True))


def test_readback_requires_explicit_authorized_pair_and_preserves_all_other_bytes():
    before = synthetic_coordinated_block()
    args = dict(setpoint_block(before,3,25,Controls(setpoint=True)))
    args[0x21] = temp_to_bytes(24)
    observed = {**before, **args, 0x9D:b'\x09'}
    with pytest.raises(SetpointUnconfirmed): validate_setpoint_readback(before,list(args.items()),observed)
    validate_setpoint_readback(before,list(args.items()),observed, authorized_targets=(0x20,0x21),active_target=0x20)
    for field in set(before) - {0x20,0x21}:
        bad = dict(observed); bad[field] = bytes([before[field][0] ^ 0xff]) + before[field][1:]
        with pytest.raises(SetpointUnconfirmed):
            validate_setpoint_readback(before,list(args.items()),bad,authorized_targets=(0x20,0x21),active_target=0x20)
    args[0x30] = b'\x01'
    with pytest.raises(SetpointUnconfirmed):
        validate_setpoint_readback(before,list(args.items()),{**observed,**args},authorized_targets=(0x20,0x21),active_target=0x20)


@pytest.mark.parametrize('consent', [None, {'token':'0'*64,'confirmed':False},
    {'token':'0'*64,'confirmed':True}, {'token':'0'*64,'confirmed':True,'heating_after':0}])
def test_local_api_cannot_bypass_coordinated_consent(tmp_path, consent):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        from tests.test_local import wait_rooms
        app = build_local_app(local.config,str(tmp_path/'history.db'),fleet=local.fleet)
        with TestClient(app,base_url='http://127.0.0.1:8090',client=('127.0.0.1',1234)) as client:
            client.portal.call(wait_rooms,app.state.manager)
            token = {'X-Demo-Token':client.get('/api/session').json()['token']}
            payload = {'operation_id':str(uuid4()),'action':'setpoint','value':25}
            if consent is not None: payload['setpoint_confirmation'] = consent
            response = client.post('/api/controllers/room-1/manual',json=payload,headers=token)
            assert response.status_code == 422
            assert not sets(local) and not local.budget.last_operation('room-1')
