"""Synthetic coexistence of fan, mode and coordinated setpoint contracts.

Closed SDKs and temporary ledgers only; no installation data or physical driver.
"""
import asyncio
import copy
import json
import time
from uuid import uuid4

import pytest

from integration.office_mock import closed_office, synthetic_setpoint_ack
from madoka_multiroom.coordinated_setpoint import SetpointConfirmation
from madoka_multiroom.fan_policy import FanPolicy
from madoka_multiroom.local_modes import LocalControls
from madoka_multiroom.local_runtime import LocalConfig
from madoka_multiroom.manager import RoomManager, RoomUnavailable
from madoka_multiroom.storage import RoomStore
from madoka_protocol import (CMD_GET_MODE, CMD_GET_FAN, CMD_GET_SETPOINT,
    CMD_SET_FAN, CMD_SET_MODE, CMD_SET_POWER, CMD_SET_SETPOINT, temp_to_bytes)


def combined_controls():
    now = time.time()
    return LocalControls(power=True, setpoint=True, modes=tuple(range(6)),
        fans=(1, 3, 5), fan_policy=FanPolicy(cooling_steps=(1, 5), heating_steps=(3, 5)),
        mode_commissioning=dict(wall_modes=tuple(range(6)), checked_at=now - 1,
            expires_at=now + 3600, restrictions='unlocked',
            physical_scope='independent', ventilation_hardware=True))


def sets(local):
    return [c for c in local.calls if len(c) == 3 and c[1] in
        (CMD_SET_FAN, CMD_SET_MODE, CMD_SET_POWER, CMD_SET_SETPOINT)]


def reply(room, command, objects, wrote):
    if command == CMD_SET_FAN:
        return {0x20: b'\x00', 0x21: b'\x00'}
    if command == CMD_SET_MODE:
        return {0x20: b'\x03'}
    if command == CMD_SET_SETPOINT:
        return synthetic_setpoint_ack()
    return objects


async def setup(local):
    local.states['room-1'][CMD_GET_FAN] = {0x20: b'\x03', 0x21: b'\x00', 0x97: b'\x12\x45'}
    local.fleet.configure_controls('room-1', combined_controls())
    local.reply_hook = reply
    manager = RoomManager(local.config.rooms, RoomStore(':memory:', mode='real'), offline_fleet=local.fleet)
    for room in manager.rooms:
        manager.room(room).backend = await local.fleet.ensure_room(room)
        await manager.operate(room)
    return manager


async def close(manager):
    await manager.close()
    manager.store.close()


def consent(manager):
    proposal, = manager.snapshot('room-1')['manual_controls']['setpoint_proposals']
    return SetpointConfirmation(token=proposal['token'], confirmed=True)


async def change(manager, action, value, **kwargs):
    return await manager.native_manual('room-1', action, value, str(uuid4()),
        'Synthetic coexistence operation', **kwargs)


def test_local_controls_reload_keeps_fan_mode_and_private_temperature_authority_separate(tmp_path):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            try:
                backend = manager.room('room-1').backend
                configured = backend.controls
                profile = backend.coordinated_setpoint_profile
                combined = LocalConfig(rooms=local.config.rooms,
                    controls={r: b.controls for r, b in local.fleet.backends.items()},
                    coordinated_setpoints=local.config.coordinated_setpoints)
                assert combined.controls['room-1'].fan_policy == configured.fan_policy
                assert combined.controls['room-1'].mode_commissioning == configured.mode_commissioning
                assert combined.coordinated_setpoints == {'room-1': profile}
                local.fleet.configure_controls('room-1', configured)
                local.fleet.reload_controls()
                replacement = local.fleet._backend(manager.room('room-1').config)
                assert replacement.controls == configured
                assert replacement.coordinated_setpoint_profile == profile
                sidecar = json.loads(local.fleet.settings_path.read_text())
                assert sidecar['rooms']['room-1']['controls']['fan_policy'] is not None
                assert sidecar['rooms']['room-1']['controls']['mode_commissioning'] is not None
                assert 'coordinated_setpoints' not in sidecar
                assert not sets(local)
            finally: await close(manager)
        asyncio.run(case())


def test_three_contracts_coexist_with_one_set_per_action_and_preserve_other_rooms(tmp_path):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            original = copy.deepcopy(local.states)
            try:
                fan = await change(manager, 'fan', 5, expected_mode=3)
                assert fan['last_operation']['status'] == 'confirmed'
                assert sets(local)[-1][2] == {0x20: b'\x05', 0x21: b'\x00'}
                assert local.states['room-1'][CMD_GET_SETPOINT] == original['room-1'][CMD_GET_SETPOINT]
                confirmation = consent(manager)
                temperature = await change(manager, 'setpoint', 25,
                    expected_mode=3, setpoint_confirmation=confirmation)
                assert temperature['last_operation']['before_state']['setpoint_authorization']['targets'] == [32, 33]
                assert local.states['room-1'][CMD_GET_SETPOINT][0x20] == temp_to_bytes(25)
                assert local.states['room-1'][CMD_GET_SETPOINT][0x21] == temp_to_bytes(24)
                assert local.states['room-1'][CMD_GET_FAN][0x21] == b'\x00'
                after = await change(manager, 'mode', 4, expected_mode=3)
                assert after['last_operation']['status'] == 'confirmed'
                assert after['state']['setpoint'] == 24 and after['state']['fan_id'] == 0
                assert after['manual_controls']['fans'] == [3, 5]
                assert after['manual_controls']['setpoint_proposals'] == []
                assert [c[1] for c in sets(local)] == [CMD_SET_FAN, CMD_SET_SETPOINT, CMD_SET_MODE]
                assert all(local.states[r] == original[r] for r in original if r != 'room-1')
            finally: await close(manager)
        asyncio.run(case())


@pytest.mark.parametrize('mutation', ['fan', 'mode', 'fan_permission'])
def test_fan_mode_or_permission_change_invalidates_prior_pair_consent(tmp_path, mutation):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            try:
                old = consent(manager)
                if mutation == 'fan': await change(manager, 'fan', 5, expected_mode=3)
                elif mutation == 'mode': await change(manager, 'mode', 4, expected_mode=3)
                else:
                    backend = manager.room('room-1').backend
                    payload = backend.controls.model_dump()
                    payload['fan_policy']['cooling_steps'] = (1,)
                    local.fleet.configure_controls('room-1', LocalControls.model_validate(payload))
                    await manager.operate('room-1')
                before = len(sets(local))
                with pytest.raises(ValueError): await change(manager, 'setpoint', 25, setpoint_confirmation=old)
                assert len(sets(local)) == before
                assert not local.budget.pending_operation('room-1')
            finally: await close(manager)
        asyncio.run(case())


@pytest.mark.parametrize('mode', [0, 1, 2, 5])
def test_non_cool_heat_modes_do_not_acquire_fan_or_temperature_permission(tmp_path, mode):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            try:
                await change(manager, 'mode', mode, expected_mode=3)
                view = manager.snapshot('room-1')
                assert view['state']['setpoint'] is None and view['state']['fan_id'] is None
                assert view['manual_controls']['setpoint'] is False
                assert view['manual_controls']['fans'] == []
                assert view['manual_controls']['setpoint_proposals'] == []
                count = len(sets(local))
                for action, value in [('fan', 5), ('setpoint', 25)]:
                    with pytest.raises(ValueError): await change(manager, action, value, expected_mode=mode)
                assert len(sets(local)) == count
            finally: await close(manager)
        asyncio.run(case())


@pytest.mark.parametrize('action,value,command', [('fan', 5, CMD_SET_FAN),
    ('mode', 4, CMD_SET_MODE), ('setpoint', 25, CMD_SET_SETPOINT)])
def test_unknown_ack_uses_correct_readback_and_preserves_action_specific_review(tmp_path, action, value, command):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            try:
                options = {'expected_mode': 3}
                if action == 'setpoint': options['setpoint_confirmation'] = consent(manager)
                def unknown(room, cmd, objects, wrote):
                    return {0x97: b'\x01'} if cmd == command else reply(room, cmd, objects, wrote)
                local.reply_hook = unknown
                with pytest.raises(RoomUnavailable): await change(manager, action, value, **options)
                assert local.budget.last_operation('room-1')['status'] == 'uncertain'
                assert len(sets(local)) == 1
                # The synthetic device applies the change; an unknown ACK can
                # never turn the matching fresh GET into a confirmed outcome.
                actual = local.states['room-1']
                if action == 'fan': assert actual[CMD_GET_FAN][0x20] == b'\x05'
                elif action == 'mode': assert actual[CMD_GET_MODE][0x20] == b'\x04'
                else:
                    assert actual[CMD_GET_SETPOINT][0x20] == temp_to_bytes(25)
                    assert actual[CMD_GET_SETPOINT][0x21] == temp_to_bytes(24)
                local.reply_hook = reply
                reviewed = await manager.native_recover('room-1', 'Synthetic GET-only review')
                assert len(sets(local)) == 1
                if action in ('fan', 'mode'):
                    assert reviewed['last_operation']['status'] == 'uncertain'
                    assert local.budget.pending_operation('room-1')
                    assert not reviewed['manual_controls']['fans'] and not reviewed['manual_controls']['modes']
                    assert not reviewed['manual_controls']['setpoint']
                else:
                    assert reviewed['last_operation']['status'] == 'reviewed_uncertain'
                    assert not local.budget.pending_operation('room-1')
                assert not any(local.budget.pending_operation(r) for r in local.states if r != 'room-1')
            finally: await close(manager)
        asyncio.run(case())


@pytest.mark.parametrize('action,value', [('fan', 5), ('mode', 4)])
def test_temperature_consent_cannot_authorize_fan_or_mode(tmp_path, action, value):
    with closed_office(tmp_path, local=True, coordinated_room='room-1') as local:
        async def case():
            manager = await setup(local)
            try:
                confirmation = consent(manager)
                with pytest.raises(ValueError):
                    await change(manager, action, value, expected_mode=3,
                                 setpoint_confirmation=confirmation)
                assert not sets(local) and local.budget.last_operation('room-1') is None
            finally: await close(manager)
        asyncio.run(case())
