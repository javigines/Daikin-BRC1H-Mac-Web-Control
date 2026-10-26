"""Integration of independently gated modes and commissioned target pairs.

Only synthetic identities, closed SDKs and temporary SQLite files are used.
"""
import asyncio
import copy
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from integration.office_mock import closed_office, synthetic_setpoint_ack
from madoka_multiroom.api import build_local_app
from madoka_multiroom.coordinated_setpoint import SetpointConfirmation
from madoka_multiroom.local_modes import LocalControls
from madoka_multiroom.local_runtime import LocalConfig, load_local_config
from madoka_multiroom.manager import RoomManager, RoomUnavailable
from madoka_multiroom.storage import RoomStore
from madoka_protocol import (CMD_GET_MODE, CMD_GET_SETPOINT, CMD_SET_MODE,
                             CMD_SET_SETPOINT, temp_to_bytes)


def mode_controls():
    now = time.time()
    return LocalControls(power=True, setpoint=True, modes=tuple(range(6)), fans=(1,3,5),
        mode_commissioning=dict(wall_modes=list(range(6)), checked_at=now-1, expires_at=now+3600,
            restrictions='unlocked', physical_scope='independent', ventilation_hardware=True))


def sets(local):
    return [c for c in local.calls if len(c) == 3 and c[1] >= 0x4000]


async def setup(local):
    local.fleet.configure_controls('room-1', mode_controls())
    manager = RoomManager(local.config.rooms, RoomStore(':memory:', mode='real'), offline_fleet=local.fleet)
    for room in manager.rooms:
        manager.room(room).backend = await local.fleet.ensure_room(room)
        await manager.operate(room)
    return manager


def consent(manager):
    proposal, = manager.snapshot('room-1')['manual_controls']['setpoint_proposals']
    return SetpointConfirmation(token=proposal['token'], confirmed=True)


async def close(manager):
    await manager.close()
    manager.store.close()


@pytest.mark.parametrize('target', [0,1,2,4,5])
def test_mode_transition_preserves_pair_and_cannot_reuse_temperature_consent(tmp_path, target):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            try:
                token = consent(manager)
                before = copy.deepcopy(local.states)
                local.ack = {0x20:b'\x00'}
                changed = await manager.native_manual('room-1','mode',target,str(uuid4()),
                    'Synthetic mode transition', expected_mode=3)
                assert changed['last_operation']['status'] == 'confirmed'
                assert changed['manual_controls']['setpoint_proposals'] == []
                assert local.states['room-1'][CMD_GET_SETPOINT] == before['room-1'][CMD_GET_SETPOINT]
                assert changed['state']['setpoint'] == (25 if target == 4 else None)
                with pytest.raises(ValueError):
                    await manager.native_manual('room-1','setpoint',25,str(uuid4()),
                        'Synthetic stale pair consent', setpoint_confirmation=token)
                assert sets(local) == [('room-1',CMD_SET_MODE,{0x20:bytes([target])})]
                assert all(local.states[r] == before[r] for r in local.states if r != 'room-1')
            finally: await close(manager)
        asyncio.run(case())


def test_confirmed_pair_then_mode_uses_updated_stored_targets_and_independent_journals(tmp_path):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            try:
                token = consent(manager)
                local.ack = synthetic_setpoint_ack()
                view = await manager.native_manual('room-1','setpoint',25,str(uuid4()),
                    'Synthetic pair consent', setpoint_confirmation=token, expected_mode=3)
                authorization = view['last_operation']['before_state']['setpoint_authorization']
                assert authorization['targets'] == [0x20,0x21]
                assert (view['state']['cooling_setpoint'],view['state']['heating_setpoint']) == (25,24)
                retained = copy.deepcopy(local.states['room-1'][CMD_GET_SETPOINT])
                for target, previous in ((1,3),(4,1),(3,4)):
                    local.ack = {0x20:b'\x00'}
                    view = await manager.native_manual('room-1','mode',target,str(uuid4()),
                        'Synthetic independent mode', expected_mode=previous)
                    assert local.states['room-1'][CMD_GET_SETPOINT] == retained
                    assert view['manual_controls']['setpoint_proposals'] == []
                    assert 'setpoint_authorization' not in view['last_operation']['before_state']
                    assert view['last_operation']['before_state']['mode_contract']['to_mode'] == target
                assert view['state']['setpoint'] == 25
                assert [c[1] for c in sets(local)] == [CMD_SET_SETPOINT,CMD_SET_MODE,CMD_SET_MODE,CMD_SET_MODE]
            finally: await close(manager)
        asyncio.run(case())


@pytest.mark.parametrize('action', ['setpoint','mode'])
def test_unknown_ack_keeps_correct_get_contract_and_independent_review_semantics(tmp_path, action):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            try:
                options = {'setpoint_confirmation':consent(manager)} if action == 'setpoint' else {'expected_mode':3}
                local.ack = {0x99:b'\x01'}
                operation = str(uuid4())
                with pytest.raises(RoomUnavailable):
                    await manager.native_manual('room-1',action,25 if action == 'setpoint' else 0,
                        operation,'Synthetic unknown ACK', **options)
                row = local.budget.operation(operation)
                assert row['status'] == 'uncertain'
                observations = [e['details'] for e in local.budget.operation_events(operation)
                                if e['stage'] == 'unknown_ack_observation']
                assert observations[-1]['result'] == 'requested_state_observed'
                wire = [c for c in local.calls if len(c) == 3]
                index = wire.index(sets(local)[0])
                assert wire[index+1][1] == (CMD_GET_SETPOINT if action == 'setpoint' else CMD_GET_MODE)
                view = await manager.native_recover('room-1','Synthetic GET-only review')
                assert view['last_operation']['status'] == ('reviewed_uncertain' if action == 'setpoint' else 'uncertain')
                assert local.budget.pending_operation('room-1') == (action == 'mode')
                if action == 'mode':
                    with pytest.raises(RoomUnavailable):
                        await manager.native_manual('room-1','setpoint',25,str(uuid4()),
                            'Synthetic blocked temperature', setpoint_confirmation=options.get('setpoint_confirmation'))
                else:
                    local.ack = {0x20:b'\x03'}
                    await manager.native_manual('room-1','mode',1,str(uuid4()),
                        'Synthetic independent mode after review',expected_mode=3)
                assert len(sets(local)) == (1 if action == 'mode' else 2)
            finally: await close(manager)
        asyncio.run(case())


def test_commissioning_namespaces_roundtrip_and_controls_cannot_enable_private_pair(tmp_path):
    with closed_office(tmp_path/'closed', local=True, coordinated_room='room-1') as local:
        data = local.config.model_dump(mode='json')
        data['controls']['room-1'] = mode_controls().model_dump(mode='json')
        private = tmp_path/'synthetic-config.json'
        private.write_text(json.dumps(data)); private.chmod(0o600)
        config = load_local_config(private)
        assert isinstance(config.controls['room-1'],LocalControls)
        assert config.controls['room-1'].modes == tuple(range(6))
        assert config.coordinated_setpoints == local.config.coordinated_setpoints
        local.fleet.configure_controls('room-1',config.controls['room-1'])
        local.fleet.reload_controls()
        assert local.fleet.backends['room-1'].controls == config.controls['room-1']
        assert local.fleet._backend(config.rooms.devices[0]).coordinated_setpoint_profile == config.coordinated_setpoints['room-1']
        saved = json.loads(local.fleet.settings_path.read_text())
        assert 'coordinated_setpoints' not in saved
        with pytest.raises(ValidationError):
            LocalControls.model_validate({**data['controls']['room-1'],'coordinated_setpoints':data['coordinated_setpoints']})
        default = LocalConfig(rooms=config.rooms,controls=config.controls)
        assert default.coordinated_setpoints == {}
        assert not sets(local)


def test_http_forwards_both_kwargs_without_leaking_consent_to_mode_action(tmp_path):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        local.fleet.configure_controls('room-1',mode_controls())
        app = build_local_app(local.config,str(tmp_path/'history.db'),fleet=local.fleet)
        with TestClient(app,base_url='http://127.0.0.1:8090',client=('127.0.0.1',1234)) as client:
            session = client.get('/api/session').json()
            headers = {'X-Demo-Token':session['token'],'Origin':'http://127.0.0.1:8090'}
            # Lifespan pollers admit the closed SDK; wait through normal GET API.
            deadline = time.monotonic()+5
            while True:
                view = client.get('/api/controllers/room-1').json()
                if view['manual_controls']['setpoint_proposals'] and not view['stale']: break
                assert time.monotonic() < deadline
                time.sleep(.01)
            proposal, = view['manual_controls']['setpoint_proposals']
            token = {'token':proposal['token'],'confirmed':True}
            payload = {'operation_id':str(uuid4()),'action':'mode','value':0,'expected_mode':3,'setpoint_confirmation':token}
            assert client.post('/api/controllers/room-1/manual',headers=headers,json=payload).status_code == 422
            assert not sets(local)
            payload.update(operation_id=str(uuid4()),action='setpoint',value=25)
            local.ack = synthetic_setpoint_ack()
            response = client.post('/api/controllers/room-1/manual',headers=headers,json=payload)
            assert response.status_code == 200, response.text
            assert response.json()['state']['heating_setpoint'] == 24
            payload = {'operation_id':str(uuid4()),'action':'mode','value':0,'expected_mode':4}
            assert client.post('/api/controllers/room-1/manual',headers=headers,json=payload).status_code == 422
            local.ack = {0x20:b'\x00'}
            payload.update(operation_id=str(uuid4()),expected_mode=3)
            response = client.post('/api/controllers/room-1/manual',headers=headers,json=payload)
            assert response.status_code == 200, response.text
            assert response.json()['state']['setpoint'] is None
            assert [c[1] for c in sets(local)] == [CMD_SET_SETPOINT,CMD_SET_MODE]
            assert local.states['room-1'][CMD_GET_SETPOINT][0x20] == temp_to_bytes(25)
