"""Age is not revocation: offline mode menu, baseline and SET agree."""
import asyncio
import copy
import time
from uuid import uuid4

import pytest

from integration.office_mock import closed_office
from madoka_multiroom.local_modes import LocalControls, selectable_modes
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.backends.daikin import WriteNotConfirmed
from madoka_protocol import CMD_GET_MODE, CMD_SET_MODE
from tests.test_local_modes import commissioned, sets


def expired(controls):
    data = controls.model_dump()
    data['mode_commissioning'].update(checked_at=time.time()-7200, expires_at=time.time()-3600)
    return LocalControls.model_validate(data)


@pytest.mark.parametrize('source', range(6))
@pytest.mark.parametrize('target', (2, 3, 4))
def test_expiry_keeps_legacy_menu_baseline_and_real_action(tmp_path, source, target):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', commissioned())
            backend = await local.fleet.ensure_room('room-1')
            backend.controls = expired(backend.controls)
            local.states['room-1'][CMD_GET_MODE][0x20] = bytes([source])
            others = copy.deepcopy({r:s for r,s in local.states.items() if r != 'room-1'})
            try:
                await backend.snapshot(('mode',))
                assert backend.control_view()['modes'] == [2, 3, 4]
                local.ack = {0x20:bytes([source])}
                operation = str(uuid4())
                observed = await backend.manual('mode', target, operation, 'Expired inventory legacy action',
                                                lambda *a: None, expected_mode=source)
                assert observed['mode_id'] == target
                assert local.budget.operation(operation)['status'] == 'confirmed'
                assert sets(local) == ([] if source == target else [('room-1', CMD_SET_MODE, {0x20:bytes([target])})])
                assert {r:s for r,s in local.states.items() if r != 'room-1'} == others
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('target', (0, 1, 5))
@pytest.mark.parametrize('source', (1, 3))
def test_expired_new_targets_never_journal_or_set(tmp_path, source, target):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', commissioned())
            backend = await local.fleet.ensure_room('room-1')
            backend.controls = expired(backend.controls)
            local.states['room-1'][CMD_GET_MODE][0x20] = bytes([source])
            try:
                await backend.snapshot(('mode',))
                assert target not in backend.control_view()['modes']
                with pytest.raises(ValueError):
                    await backend.manual('mode', target, str(uuid4()), 'No expired new authority', lambda *a: pytest.fail('Authorized'))
                assert not sets(local) and not local.budget.last_operation('room-1')
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('target', (0, 1, 5))
def test_persistent_equipment_authority_survives_age_reload_and_fresh_get(tmp_path, target):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            controls = commissioned(checked_at=time.time()-60*86400, expires_at=None)
            local.fleet.configure_controls('room-1', controls)
            local.fleet.reload_controls()
            backend = await local.fleet.ensure_room('room-1')
            try:
                await backend.snapshot(('mode',))
                assert backend.controls == controls
                assert backend.control_view()['modes'] == list(range(6))
                local.ack = {0x20:b'\x03'}
                await backend.manual('mode', target, str(uuid4()), 'Persistent verified equipment', lambda *a: None)
                assert sets(local) == [('room-1', CMD_SET_MODE, {0x20:bytes([target])})]
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('restriction', ({'revoked':True}, {'restrictions':'locked'}, {'physical_scope':'linked'}))
@pytest.mark.parametrize('source', (1, 3))
def test_known_restriction_persists_and_blocks_legacy_and_new_actions(tmp_path, restriction, source):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            controls = commissioned(expires_at=None)
            local.fleet.configure_controls('room-1', controls)
            data = controls.model_dump()
            data['mode_commissioning'].update(restriction)
            blocked = LocalControls.model_validate(data)
            local.fleet.configure_controls('room-1', blocked)
            local.fleet.reload_controls()
            backend = await local.fleet.ensure_room('room-1')
            local.states['room-1'][CMD_GET_MODE][0x20] = bytes([source])
            try:
                await backend.snapshot(('mode',))
                assert backend.control_view()['modes'] == []
                for target in range(6):
                    with pytest.raises(ValueError):
                        await backend.manual('mode', target, str(uuid4()), 'Known block no bypass', lambda *a: pytest.fail('Authorized'))
                assert not sets(local) and not local.budget.last_operation('room-1')
                # Neither dropping inventory nor replaying the old unblocked
                # record can erase a known restriction through settings.
                with pytest.raises(ValueError):
                    local.fleet.configure_controls('room-1', LocalControls(modes=(2,3,4)))
                with pytest.raises(ValueError): local.fleet.configure_controls('room-1', controls)
            finally: await backend.close()
        asyncio.run(case())


def test_reconfiguration_retains_expiry_until_explicit_new_persistent_attestation(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        local.fleet.configure_controls('room-1', commissioned())
        backend = local.fleet.backends['room-1']
        backend.controls = expired(backend.controls)
        evidence = backend.controls.mode_commissioning
        local.fleet.configure_controls('room-1', backend.controls.model_copy(update={'modes':(2,3,4), 'fans':(1,3)}))
        local.fleet.reload_controls()
        assert backend.controls.mode_commissioning == evidence
        assert selectable_modes(backend.controls) == [2,3,4]
        with pytest.raises(ValueError):
            local.fleet.configure_controls('room-1', backend.controls.model_copy(update={'modes':tuple(range(6))}))
        local.fleet.configure_controls('room-1', commissioned(expires_at=None))
        assert selectable_modes(backend.controls) == list(range(6))


def test_additional_inventory_does_not_remove_unlisted_configured_legacy_modes():
    controls = commissioned(wall_modes=(0,1,5), expires_at=None)
    assert selectable_modes(controls) == list(range(6))
    assert selectable_modes(expired(controls)) == [2,3,4]


def test_legacy_room_can_record_revocation_without_inventing_wall_inventory(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        blocked = LocalControls(modes=(2,3,4), mode_commissioning={
            'wall_modes':[], 'checked_at':time.time()-1, 'revoked':True})
        local.fleet.configure_controls('room-1', blocked)
        local.fleet.reload_controls()
        assert selectable_modes(local.fleet.backends['room-1'].controls) == []
        with pytest.raises(ValueError):
            local.fleet.configure_controls('room-1', LocalControls(modes=(2,3,4)))
        assert not sets(local)


@pytest.mark.parametrize('pending', (True, False))
def test_new_attestation_never_rearms_pending_or_uncertain_mode_write(tmp_path, pending):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', commissioned(expires_at=None))
            backend = await local.fleet.ensure_room('room-1')
            try:
                await backend.snapshot(('mode',))
                local.ack = {0x20:b'\xff'}
                operation = str(uuid4())
                if pending:
                    local.budget.begin_operation(backend.port, backend, operation, 'mode', 1, {'synthetic':True})
                else:
                    with pytest.raises(WriteNotConfirmed):
                        await backend.manual('mode', 1, operation, 'Unknown mode ACK', lambda *a: None)
                local.fleet.configure_controls('room-1', commissioned(expires_at=None))
                local.fleet.reload_controls()
                assert backend.control_view()['modes'] == []
                with pytest.raises(BackendUnavailable):
                    await backend.manual('mode', 3, str(uuid4()), 'Never rearm by settings', lambda *a: pytest.fail('Authorized'))
                assert len(sets(local)) == (0 if pending else 1) and local.budget.pending_operation('room-1')
            finally: await backend.close()
        asyncio.run(case())


def test_known_revocation_requires_new_inspection_to_restore_authority(tmp_path):
    with closed_office(tmp_path, local=True) as local:
        checked = time.time()-10
        local.fleet.configure_controls('room-1', commissioned(checked_at=checked, expires_at=None))
        controls = local.fleet.backends['room-1'].controls
        revoked = controls.model_dump()
        revoked['mode_commissioning']['revoked'] = True
        local.fleet.configure_controls('room-1', LocalControls.model_validate(revoked))
        with pytest.raises(ValueError): local.fleet.configure_controls('room-1', controls)
        renewed = commissioned(checked_at=time.time()-1, expires_at=None)
        local.fleet.configure_controls('room-1', renewed)
        local.fleet.reload_controls()
        assert selectable_modes(local.fleet.backends['room-1'].controls) == list(range(6))
        assert not sets(local)


@pytest.mark.parametrize('fault', ('expired', 'locked', 'revoked', 'linked'))
@pytest.mark.parametrize('target', (0, 4))
def test_authority_change_during_fresh_get_is_rechecked_before_journal(tmp_path, fault, target):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', commissioned())
            backend = await local.fleet.ensure_room('room-1')
            try:
                await backend.snapshot(('mode',))
                def changed(room, cmd, objects, wrote):
                    if room == 'room-1' and cmd == CMD_GET_MODE and not wrote:
                        data = backend.controls.model_dump()
                        patch = ({'checked_at':time.time()-7200, 'expires_at':time.time()-3600} if fault == 'expired'
                                 else {'restrictions':'locked'} if fault == 'locked'
                                 else {'revoked':True} if fault == 'revoked' else {'physical_scope':'linked'})
                        data['mode_commissioning'].update(patch)
                        backend.controls = LocalControls.model_validate(data)
                    return objects
                local.reply_hook = changed
                local.ack = {0x20:b'\x03'}
                if fault == 'expired' and target == 4:
                    after = await backend.manual('mode', target, str(uuid4()), 'Age does not revoke legacy', lambda *a: None)
                    assert after['mode_id'] == 4 and len(sets(local)) == 1
                else:
                    with pytest.raises(ValueError):
                        await backend.manual('mode', target, str(uuid4()), 'Recheck changed authority', lambda *a: pytest.fail('Authorized'))
                    assert not sets(local) and not local.budget.last_operation('room-1')
            finally: await backend.close()
        asyncio.run(case())


@pytest.mark.parametrize('source', (0,1,5))
def test_return_to_legacy_does_not_infer_unknown_extra_source_scope(tmp_path, source):
    with closed_office(tmp_path, local=True) as local:
        async def case():
            local.fleet.configure_controls('room-1', LocalControls(modes=(2,3,4)))
            backend = await local.fleet.ensure_room('room-1')
            try:
                local.states['room-1'][CMD_GET_MODE][0x20] = bytes([source])
                await backend.snapshot(('mode',))
                with pytest.raises(ValueError):
                    await backend.manual('mode', 4, str(uuid4()), 'No inferred physical scope', lambda *a: pytest.fail('Authorized'))
                assert not sets(local) and not local.budget.last_operation('room-1')
            finally: await backend.close()
        asyncio.run(case())
