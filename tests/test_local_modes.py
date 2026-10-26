"""Closed SDK mode contracts; every identity and payload is synthetic."""
import asyncio
import copy
import time
from uuid import uuid4

import pytest
from pydantic import ValidationError

from integration.office_mock import closed_office
from madoka_multiroom.local_modes import LocalControls, selectable_modes
from madoka_multiroom.office_access import Controls
from madoka_multiroom.backends.daikin import WriteNotConfirmed
from madoka_multiroom.manager import RoomManager, RoomUnavailable
from madoka_multiroom.storage import RoomStore
from madoka_protocol import (CMD_GET_MODE, CMD_GET_POWER, CMD_GET_SETPOINT, CMD_GET_FAN,
                             CMD_SET_MODE, CMD_SET_POWER, CMD_SET_SETPOINT, CMD_SET_FAN)


def commissioned(*, modes=tuple(range(6)), **changes):
    now = time.time()
    evidence = dict(wall_modes=modes, checked_at=now-1, expires_at=now+3600,
                    restrictions="unlocked", physical_scope="independent", ventilation_hardware=5 in modes)
    evidence.update(changes)
    return LocalControls(power=True, setpoint=True, modes=modes, fans=(1, 2, 3, 4, 5),
                         mode_commissioning=evidence)


def sets(local):
    return [c for c in local.calls if len(c) == 3 and c[1] in
            (CMD_SET_MODE, CMD_SET_POWER, CMD_SET_SETPOINT, CMD_SET_FAN)]


@pytest.mark.parametrize("patch", [dict(wall_modes=(0, 1, 2, 3, 4)), dict(wall_modes=(0, 0, 1)),
    dict(expires_at=time.time()-90000), dict(checked_at=float('nan'))])
def test_invalid_commissioning(patch):
    with pytest.raises(ValidationError): commissioned(**patch)


@pytest.mark.parametrize("patch", [dict(physical_scope="unknown"), dict(physical_scope="linked"),
    dict(restrictions="locked"), dict(restrictions="unknown"), dict(ventilation_hardware=False),
    dict(revoked=True)])
def test_unverified_or_revoked_inventory_never_grants_new_modes(tmp_path, patch):
    controls = commissioned(**patch)
    with closed_office(tmp_path, local=True) as local:
        with pytest.raises(ValueError): local.fleet.configure_controls('room-1', controls)
        assert not sets(local)


@pytest.mark.parametrize("mode", [0, 1, 5])
def test_enum_is_never_permission_and_office_stays_restricted(mode):
    with pytest.raises(ValidationError): LocalControls(modes=(mode,))
    with pytest.raises(ValidationError): Controls(modes=(mode,))


def test_fan_dry_do_not_require_ventilation_and_expiry_only_disables_extra_targets():
    controls = commissioned(modes=(0, 1, 2, 3, 4), ventilation_hardware=False)
    assert selectable_modes(controls) == [0, 1, 2, 3, 4]
    saved = controls.model_dump()
    saved['mode_commissioning'].update(checked_at=time.time()-7200, expires_at=time.time()-3600)
    assert selectable_modes(LocalControls.model_validate(saved)) == [2, 3, 4]


@pytest.mark.parametrize("source", range(6))
@pytest.mark.parametrize("target", range(6))
def test_every_transition_preserves_raw_fields_and_recomputes_active_controls(tmp_path, source, target):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', commissioned())
            local.states['room-1'][CMD_GET_MODE][0x20] = bytes([source])
            backend = await local.fleet.ensure_room('room-1')
            try:
                before = await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                raw = copy.deepcopy(local.states)
                local.ack = {0x20: bytes([source])}
                operation = str(uuid4())
                after = await backend.manual('mode', target, operation, 'Synthetic mode pilot', lambda *a: None)
                assert after['mode_id'] == target
                assert after['cooling_setpoint'] == before['cooling_setpoint']
                assert after['heating_setpoint'] == before['heating_setpoint']
                assert after['setpoint'] == (21 if target == 4 else 22 if target == 3 else None)
                assert after['fan_id'] == (2 if target == 4 else 3 if target == 3 else None)
                assert backend.control_view()['setpoint'] == (target in (3, 4))
                assert bool(backend.control_view()['fans']) == (target in (3, 4))
                assert bool(backend.control_view()['power']) == (target not in (1, 5))
                assert local.budget.operation(operation)['status'] == 'confirmed'
                assert sets(local) == ([] if source == target else [('room-1', CMD_SET_MODE, {0x20: bytes([target])})])
                for cmd in (CMD_GET_POWER, CMD_GET_SETPOINT, CMD_GET_FAN):
                    assert local.states['room-1'][cmd] == raw['room-1'][cmd]
                assert all(local.states[r] == raw[r] for r in local.states if r != 'room-1')
                if target not in (3, 4):
                    for action in ('fan', 'setpoint'):
                        with pytest.raises(ValueError): backend.validate_action(action, 3 if action == 'fan' else 23)
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize("ack", [{0x20: b'\x03'}, {0x20: b'\x01'}])
def test_status_ack_does_not_need_to_echo_target(tmp_path, ack):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', commissioned())
            backend = await local.fleet.ensure_room('room-1')
            try:
                await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                local.ack = ack
                await backend.manual('mode', 0, str(uuid4()), 'Synthetic ACK', lambda *a: None)
                calls = [c[1] for c in local.calls if len(c) == 3]
                assert calls[calls.index(CMD_SET_MODE)+1] == CMD_GET_MODE
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize("fault", ['ack', 'no_effect', 'power', 'cool_target', 'heat_target',
    'fan', 'inactive_fan', 'extension', 'metadata', 'mode_extension', 'fan_extension'])
def test_unknown_effect_durable_stop_survives_get_review_without_replay(tmp_path, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', commissioned())
            store = RoomStore(':memory:', mode='real')
            manager = RoomManager(local.config.rooms, store, offline_fleet=local.fleet)
            backend = await local.fleet.ensure_room('room-1')
            manager.room('room-1').backend = backend
            try:
                await manager.operate('room-1')
                local.ack = {0x20: b'\x03'}
                if fault == 'ack': local.ack = {0x20: b'\xff'}
                if fault == 'no_effect': local.apply_writes = False
                def hook(room, cmd, obj, wrote):
                    if room != 'room-1' or not wrote: return obj
                    if fault == 'power' and cmd == CMD_GET_POWER: obj[0x20] = b'\x00'
                    if fault in ('cool_target', 'heat_target') and cmd == CMD_GET_SETPOINT:
                        obj[0x20 if fault == 'cool_target' else 0x21] = b'\x0c\x00'
                    if fault in ('fan', 'inactive_fan') and cmd == CMD_GET_FAN:
                        obj[0x20 if fault == 'fan' else 0x21] = b'\x00'
                    if fault == 'extension' and cmd == CMD_GET_SETPOINT: obj[0x99] = b'\x01'
                    if fault == 'metadata' and cmd == CMD_GET_SETPOINT: obj[0x32] = b'\x02'
                    if fault == 'mode_extension' and cmd == CMD_GET_MODE: obj[0x99] = b'\x01'
                    if fault == 'fan_extension' and cmd == CMD_GET_FAN: obj[0x99] = b'\x01'
                    return obj
                local.reply_hook = hook
                operation = str(uuid4())
                with pytest.raises(RoomUnavailable):
                    await manager.native_manual('room-1', 'mode', 1, operation, 'Synthetic divergent effect')
                assert local.budget.operation(operation)['status'] == 'uncertain'
                await manager.native_recover('room-1', 'Synthetic observation only')
                assert local.budget.pending_operation('room-1')
                with pytest.raises(RoomUnavailable):
                    await manager.native_manual('room-1', 'mode', 3, str(uuid4()), 'No silent restore')
                await local.fleet.ensure_room('room-2')
                assert len(sets(local)) == 1
                assert local.budget.operation(operation)['status'] == 'uncertain'
                if fault == 'ack':
                    assert any(c[1] == CMD_GET_MODE for c in local.calls[local.calls.index(sets(local)[0])+1:] if len(c) == 3)
            finally:
                await manager.close(); store.close()
        asyncio.run(case())


@pytest.mark.parametrize('effect', ['preserve', 'auto_both'])
def test_dry_only_allows_exact_commissioned_fan_effect(tmp_path, effect):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', commissioned(dry_fan_effect=effect))
            backend = await local.fleet.ensure_room('room-1')
            try:
                await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                local.ack = {0x20: b'\x03'}
                def hook(room, cmd, objects, wrote):
                    if wrote and cmd == CMD_GET_FAN: objects.update({0x20:b'\x00', 0x21:b'\x00'})
                    return objects
                local.reply_hook = hook
                if effect == 'preserve':
                    with pytest.raises(WriteNotConfirmed):
                        await backend.manual('mode', 1, str(uuid4()), 'Synthetic Dry profile', lambda *a: None)
                else:
                    state = await backend.manual('mode', 1, str(uuid4()), 'Synthetic Dry profile', lambda *a: None)
                    assert state['cooling_fan_id'] == state['heating_fan_id'] == 0 and state['fan_id'] is None
                assert len(sets(local)) == 1
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('fault', ['mode_changed', 'expired', 'future', 'fan_missing', 'metadata_missing', 'expected_mode'])
def test_fresh_baseline_rejects_before_journal_or_set(tmp_path, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', commissioned())
            backend = await local.fleet.ensure_room('room-1')
            try:
                await backend.snapshot(("power", "mode", "setpoint", "sensor", "fan"))
                if fault == 'mode_changed': local.states['room-1'][CMD_GET_MODE][0x20] = b'\x04'
                if fault in ('expired', 'future'):
                    evidence = backend.controls.mode_commissioning.model_copy(update={
                        'expires_at' if fault == 'expired' else 'checked_at': time.time()+(-1 if fault == 'expired' else 100)})
                    backend.controls = backend.controls.model_copy(update={'mode_commissioning': evidence})
                if fault == 'fan_missing': local.states['room-1'][CMD_GET_FAN].pop(0x21)
                if fault == 'metadata_missing': local.states['room-1'][CMD_GET_SETPOINT].pop(0xA0)
                with pytest.raises(ValueError):
                    await backend.manual('mode', 0, str(uuid4()), 'Synthetic stale baseline', lambda *a: pytest.fail('Authorized'),
                                         expected_mode=4 if fault == 'expected_mode' else 3)
                assert not sets(local) and not local.budget.last_operation('room-1') and not backend.stopped
            finally: await backend.close()
        asyncio.run(case())


def test_room_configuration_identity_reload_and_expiry_isolation(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        controls = commissioned(modes=(0, 1, 2, 3, 4), ventilation_hardware=False)
        local.fleet.configure_controls('room-2', controls)
        assert all(b.controls.modes == (2, 3, 4) for r, b in local.fleet.backends.items() if r != 'room-2')
        local.fleet.reload_controls()
        assert local.fleet.backends['room-2'].controls == controls
        assert not sets(local)
        expired = controls.mode_commissioning.model_copy(update={
            'checked_at': time.time()-7200, 'expires_at': time.time()-3600})
        retained = controls.model_copy(update={'mode_commissioning': expired})
        local.fleet.backends['room-2'].controls = retained
        local.fleet.configure_controls('room-2', retained.model_copy(update={'fans': (1,3)}))
        assert local.fleet.backends['room-2'].controls.mode_commissioning == expired
        assert selectable_modes(local.fleet.backends['room-2'].controls) == [2, 3, 4]
        assert local.fleet.backends['room-2'].controls.fans == (1,3)
        import json
        data = json.loads(local.fleet.settings_path.read_text())
        data['rooms']['room-2']['identity'] = 'wrong-controller'
        local.fleet.settings_path.write_text(json.dumps(data))
        with pytest.raises(ValueError): local.fleet.reload_controls()


@pytest.mark.parametrize('ack', [{}, {0:b''}, {0x20:b''}, {0x20:b'\x06'}, {0x20:b'\x00\x01'},
                               {0x21:b'\x01'}, {0x20:b'\x00', 0x99:b'\x01'}])
def test_commissioned_ack_variants_never_confirm_from_get_alone(tmp_path, ack):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', commissioned())
            backend = await local.fleet.ensure_room('room-1')
            try:
                await backend.snapshot(('mode',))
                local.ack = ack
                operation = str(uuid4())
                with pytest.raises(WriteNotConfirmed):
                    await backend.manual('mode', 1, operation, 'Synthetic unknown ACK', lambda *a: None)
                assert local.states['room-1'][CMD_GET_MODE][0x20] == b'\x01'
                assert local.budget.operation(operation)['status'] == 'uncertain'
                assert len(sets(local)) == 1
            finally: await backend.close()
        asyncio.run(case())


def test_six_live_synthetic_backends_keep_independent_state_and_commissioning(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            backends = [await local.fleet.ensure_room(r) for r in local.states]
            try:
                for backend in backends: await backend.snapshot(('mode',))
                snapshots = [copy.deepcopy(b.latest) for b in backends]
                local.fleet.configure_controls('room-2', commissioned(modes=(0,1,2,3,4), ventilation_hardware=False))
                local.ack = {0x20:b'\x00'}
                await backends[1].manual('mode', 0, str(uuid4()), 'Synthetic isolated mode', lambda *a: None)
                assert all(b.latest == snapshots[i] for i,b in enumerate(backends) if i != 1)
                assert all(0 not in b.control_view()['modes'] for i,b in enumerate(backends) if i != 1)
                assert len(sets(local)) == 1 and sets(local)[0][0] == 'room-2'
            finally:
                for backend in backends: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('fault', ['wrong_command', 'duplicate_object'])
def test_mode_wire_correlation_and_duplicate_tlv_cannot_confirm(tmp_path, fault):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', commissioned())
            backend = await local.fleet.ensure_room('room-1')
            try:
                await backend.snapshot(('mode',))
                body = b'\x20\x01\x00'
                if fault == 'duplicate_object': body += body
                command = CMD_SET_POWER if fault == 'wrong_command' else CMD_SET_MODE
                local.raw_replies[CMD_SET_MODE] = bytes([4+len(body),0])+command.to_bytes(2,'big')+body
                operation = str(uuid4())
                with pytest.raises(Exception):
                    await backend.manual('mode', 0, operation, 'Synthetic bad wire', lambda *a: None)
                assert local.budget.operation(operation)['status'] == 'uncertain'
                assert len(sets(local)) == 1
            finally: await backend.close()
        asyncio.run(case())
