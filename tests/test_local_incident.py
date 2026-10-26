"""Incident regressions: actual framing shapes against six closed SDK clients."""
import asyncio
from contextlib import asynccontextmanager
import json
import time
from uuid import uuid4

import pytest

from integration.office_mock import closed_office
from madoka_protocol import CMD_SET_POWER, CMD_GET_POWER, CMD_GET_SENSOR, build_command
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.manager import RoomManager, RoomUnavailable
from madoka_multiroom.storage import RoomStore


@pytest.fixture
def local(tmp_path):
    with closed_office(tmp_path, local=True) as runtime:
        yield runtime


async def manager_for(local):
    manager = RoomManager(local.config.rooms, RoomStore(':memory:', mode='real'), offline_fleet=local.fleet)
    for room in manager.rooms.values():
        room.backend = await local.fleet.ensure_room(room.config.id)
        await manager.operate(room.config.id)
    return manager


def sets(local):
    return [c for c in local.calls if len(c) == 3 and c[1] == CMD_SET_POWER]


@pytest.mark.parametrize('ack', [{0x20: b'\x00'}, {0x20: b'\x01'}, {}, {0: b''}])
def test_power_tlv_response_is_not_effect_confirmation(local, ack):
    async def case():
        local.states['room-6'][CMD_GET_POWER][0x20] = b'\x00'
        manager = await manager_for(local)
        local.ack = ack
        result = await manager.native_manual('room-6', 'power', True, str(uuid4()), 'Local browser action')
        assert result['last_operation']['status'] == 'confirmed'
        assert result['state']['power_on'] is True
        assert len(sets(local)) == 1
        assert all(manager.snapshot(r)['connected'] for r in manager.rooms)
        await manager.close(); manager.store.close()
    asyncio.run(case())


def test_power_wire_bytes_match_reference():
    assert bytes(build_command(CMD_SET_POWER, [(0x20, b'\x01')])[0]).hex() == '0007004020200101'
    assert bytes(build_command(CMD_SET_POWER, [(0x20, b'\x00')])[0]).hex() == '0007004020200100'


def test_power_settles_through_gets_without_repeating_set(local):
    async def case():
        manager = await manager_for(local)
        old_reads = []
        def lag(room, command, objects, wrote):
            if room == 'room-6' and command == CMD_GET_POWER and wrote:
                old_reads.append(command)
                if len(old_reads) < 3: return {0x20: b'\x01'}
            return objects
        local.reply_hook = lag
        result = await manager.native_manual('room-6', 'power', False, str(uuid4()), 'Local browser action')
        assert result['last_operation']['status'] == 'confirmed'
        assert len(sets(local)) == 1 and len(old_reads) == 3
        await manager.close(); manager.store.close()
    asyncio.run(case())


def test_uncertain_effect_does_not_invent_bluetooth_loss_or_stop_gets(local):
    async def case():
        manager = await manager_for(local)
        local.apply_writes = False
        with pytest.raises(RoomUnavailable):
            await manager.native_manual('room-6', 'power', False, str(uuid4()), 'Local browser action')
        backend = manager.room('room-6').backend
        assert backend.port.ready and not backend.stopped
        state = await manager.operate('room-6')
        assert state['connected'] and not state['stale']
        assert state['last_operation']['status'] == 'uncertain'
        assert not state['manual_controls']['power']
        reads = manager.room('room-6').stats['reads']
        manager.pollers = [asyncio.create_task(manager.poll('room-6', 0))]
        deadline = time.monotonic() + 1
        while manager.room('room-6').stats['reads'] == reads:
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        assert local.budget.pending_operation('room-6')
        with pytest.raises(RoomUnavailable):
            await manager.native_manual('room-6', 'power', False, str(uuid4()), 'Local browser action')
        assert len(sets(local)) == 1
        assert all(manager.snapshot(r)['connected'] for r in manager.rooms)
        await manager.close(); manager.store.close()
    asyncio.run(case())


def test_stale_observation_is_not_a_native_disconnect(local):
    async def case():
        manager = await manager_for(local)
        manager.room('room-1').updated_monotonic -= manager.config.stale_seconds + 1
        view = manager.snapshot('room-1')
        assert view['connected'] and view['stale'] and not view['controls_enabled']
        assert view['connection_status'] == 'Conectado · lectura pendiente'
        await manager.close(); manager.store.close()
    asyncio.run(case())


@pytest.mark.parametrize('ack', [{0x20: b'\xff'}, {0x20: b'\x00\x01'}, {0x99: b'\x01'}, {0x20: b'\x01', 0x21: b'\x00'}])
def test_unknown_ack_is_uncertain_with_exact_stage_and_no_replay(local, ack):
    async def case():
        manager = await manager_for(local)
        local.ack = ack
        with pytest.raises(RoomUnavailable):
            await manager.native_manual('room-4', 'power', False, str(uuid4()), 'Local browser action')
        view = manager.snapshot('room-4')
        failure = view['last_operation']['diagnostics'][-1]
        assert failure['stage'] == 'failed'
        assert failure['details']['failed_stage'] == 'ack_validate'
        assert failure['details']['error_class'] == 'WriteNotConfirmed'
        assert view['connected'] and not view['manual_controls']['power']
        assert len(sets(local)) == 1
        await manager.close(); manager.store.close()
    asyncio.run(case())


@pytest.mark.parametrize('fault', ['response_timeout', 'disconnect', 'readback', 'timeout_and_failed_cleanup'])
def test_transport_failures_keep_uncertain_stage_and_other_rooms_alive(local, monkeypatch, fault):
    async def case():
        manager = await manager_for(local)
        local.ack = {0x20: b'\x00'}
        if fault in ('response_timeout', 'timeout_and_failed_cleanup'):
            local.mute_reply_for.add(CMD_SET_POWER)
            real_timeout = asyncio.wait_for
            async def short_timeout(task, timeout):
                return await real_timeout(task, .02 if timeout == 6 else timeout)
            monkeypatch.setattr(asyncio, 'wait_for', short_timeout)
        if fault == 'timeout_and_failed_cleanup': local.stop_error = True
        if fault == 'disconnect': local.disconnect_after_set = True
        if fault == 'readback': local.fail_readback = True
        with pytest.raises(RoomUnavailable):
            await manager.native_manual('room-4', 'power', False, str(uuid4()), 'Local browser action')
        operation = local.budget.last_operation('room-4')
        assert operation['status'] == 'uncertain' and len(sets(local)) == 1
        failure = operation['diagnostics'][-1]['details']
        assert failure['failed_stage'] in ('set_exchange', 'readback_get')
        if fault == 'timeout_and_failed_cleanup':
            assert failure['error_class'] == 'TimeoutError'
            assert failure['exchange']['cleanup_error_class'] == 'RuntimeError'
        if fault == 'response_timeout': assert failure['exchange']['stage'] == 'response'
        assert all(manager.snapshot(r)['connected'] for r in manager.rooms if r != 'room-4')
        assert not manager.snapshot('room-4')['connected']
        local.disconnect_after_set = local.fail_readback = False
        local.mute_reply_for.clear()
        if fault == 'timeout_and_failed_cleanup':
            with pytest.raises(RuntimeError): await manager.close()
            assert local.budget.quarantined
        else:
            local.fleet.next_attempt['room-4'] = 0
            manager.room('room-4').backend = await local.fleet.ensure_room('room-4')
            state = await manager.operate('room-4')
            assert state['state']['power_on'] is False
            assert not state['manual_controls']['power']
            assert len(sets(local)) == 1
            await manager.close()
        manager.store.close()
    asyncio.run(case())


def test_three_power_clicks_with_six_serialized_rooms_get_complete_deadlines(local, monkeypatch):
    async def case():
        for i in (6, 5, 4): local.states[f'room-{i}'][CMD_GET_POWER][0x20] = b'\x00'
        manager = await manager_for(local)
        # Observe real timeout boundaries instead of compressing execution to
        # subsecond wall time (runner load must not decide correctness).
        real_timeout = asyncio.timeout
        active, entered = {}, {}
        queued = asyncio.Event()
        @asynccontextmanager
        async def traced_timeout(seconds):
            task = asyncio.current_task()
            async with real_timeout(seconds):
                active.setdefault(task, []).append(seconds)
                entered.setdefault(task, []).append(seconds)
                if sum(steps == [30] for steps in entered.values()) == 3:
                    queued.set()
                try:
                    yield
                finally:
                    active[task].pop()
        monkeypatch.setattr(asyncio, 'timeout', traced_timeout)
        def check_execution_window(room, command, objects, wrote):
            assert active[asyncio.current_task()] == [90]
            return objects
        local.reply_hook = check_execution_window
        local.ack = {0x20: b'\x00'}
        manager.capacity = asyncio.Semaphore(1)
        local.budget.operations = asyncio.Semaphore(1)
        await manager.capacity.acquire()
        tasks = [asyncio.create_task(manager.native_manual(f'room-{i}', 'power', True, str(uuid4()),
            'Local browser action')) for i in (6, 5, 4)]
        await asyncio.wait_for(queued.wait(), 10)
        assert not sets(local)
        assert all(active[t] == [30] for t in tasks)
        manager.capacity.release()
        results = await asyncio.gather(*tasks)
        assert all(entered[t] == [30, 90] and active[t] == [] for t in tasks)
        assert all(r['last_operation']['status'] == 'confirmed' for r in results)
        assert len(sets(local)) == 3 and len(local.clients) == 6
        assert all(manager.snapshot(r)['connected'] for r in manager.rooms)
        local.reply_hook = None
        await manager.close(); manager.store.close()
    asyncio.run(case())


def test_shutdown_quarantines_record_shutdown_cause_without_claiming_six_link_drops(local):
    async def case():
        manager = await manager_for(local)
        local.stop_error = True
        with pytest.raises(RuntimeError): await manager.close()
        events = local.budget.db.execute("SELECT details FROM session_events WHERE kind='quarantined'").fetchall()
        assert len(events) == 6
        assert all(json.loads(e['details'])['cause'] == 'application_shutdown' for e in events)
        assert all(json.loads(e['details'])['cleanup_error_class'] == 'RuntimeError' for e in events)
        assert len(local.budget.quarantined) == 6 and not sets(local)
        manager.store.close()
    asyncio.run(case())


@pytest.mark.parametrize('confirmed', [False, True])
def test_full_manual_deadline_can_expire_after_effect_before_complete_readback(local, monkeypatch, confirmed):
    async def case():
        manager = await manager_for(local)
        backend = manager.room('room-4').backend
        # Capture the real whole-operation timeout, then expire it at the first
        # post-SET GET. A 10ms wall clock could expire during baseline/fsync on
        # CI and never exercise the intended after-effect uncertainty boundary.
        backend.manual_timeout = 73
        original_timeout, deadline = asyncio.timeout, None
        def capture_timeout(delay):
            nonlocal deadline
            context = original_timeout(delay)
            if delay == backend.manual_timeout:
                assert deadline is None
                deadline = context
            return context
        monkeypatch.setattr(asyncio, 'timeout', capture_timeout)
        def reply(room, command, objects, wrote):
            if room == 'room-4' and command == CMD_GET_POWER and wrote and not confirmed:
                assert local.budget.last_operation(room)['status'] == 'pending'
                assert local.states[room][CMD_GET_POWER][0x20] == b'\x00'
                assert len(sets(local)) == 1 and deadline is not None
                deadline.reschedule(asyncio.get_running_loop().time())
            return objects
        local.reply_hook = reply
        if confirmed:
            result = await manager.native_manual('room-4', 'power', False, str(uuid4()), 'Local browser action')
            assert result['last_operation']['status'] == 'confirmed'
        else:
            with pytest.raises(RoomUnavailable):
                await manager.native_manual('room-4', 'power', False, str(uuid4()), 'Local browser action')
            assert local.budget.last_operation('room-4')['status'] == 'uncertain'
        assert deadline is not None and deadline.expired() is not confirmed
        assert local.states['room-4'][CMD_GET_POWER][0x20] == b'\x00'
        assert len(sets(local)) == 1
        local.reply_hook = None
        await manager.close(); manager.store.close()
    asyncio.run(case())


@pytest.mark.parametrize('fault', ['sensor', 'baseline', 'cancel', 'collateral'])
def test_read_failure_stage_is_sanitized_and_does_not_replay(local, fault):
    async def case():
        manager = await manager_for(local)
        private = 'private-identifier-must-not-reach-diagnostics'
        if fault == 'baseline':
            local.stage, local.room, local.error = 'query', 'room-4', RuntimeError(private)
        elif fault == 'cancel':
            local.stage, local.room, local.error = 'write', 'room-4', asyncio.CancelledError(private)
        else:
            def fault_reply(room, command, objects, wrote):
                if room == 'room-4' and wrote:
                    if fault == 'sensor' and command == CMD_GET_SENSOR: raise RuntimeError(private)
                    if fault == 'collateral' and command == CMD_GET_POWER: return {0x20: b'\x00'}
                    if fault == 'collateral':
                        from madoka_protocol import CMD_GET_MODE
                        if command == CMD_GET_MODE: return {0x20: b'\x04'}
                return objects
            local.reply_hook = fault_reply
        op_id = str(uuid4())
        with pytest.raises(RoomUnavailable):
            await manager.native_manual('room-4', 'power', False, op_id, 'Local browser action')
        events = local.budget.operation_events(op_id)
        failure = events[-1]['details']
        assert failure['failed_stage'] == {'baseline':'baseline_get', 'cancel':'set_exchange',
            'sensor':'readback_get', 'collateral':'readback_validate'}[fault]
        assert private not in json.dumps(events)
        if fault == 'baseline': assert not sets(local) and not local.budget.pending_operation('room-4')
        else: assert len(sets(local)) == 1 and local.budget.pending_operation('room-4')
        if fault == 'sensor': assert failure['exchange']['command'] == '0x0110'
        local.error = None; local.reply_hook = None
        await manager.close(); manager.store.close()
    asyncio.run(case())
