"""Local mode acceptance against an explicitly closed CoreBluetooth SDK."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from integration.office_mock import closed_office
from madoka_multiroom.api import build_local_app
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.backends.physical_sessions import PhysicalSessionBudget, ConnectionPlan
from madoka_multiroom.local_runtime import LocalFleet, LocalSession, load_local_config, recover_local_room
from madoka_multiroom.manager import RoomManager, RoomUnavailable
from madoka_multiroom.office_access import Controls
from madoka_multiroom.storage import RoomStore
from madoka_protocol import CMD_GET_MODE, CMD_GET_FAN, CMD_GET_POWER, CMD_GET_SETPOINT, CMD_SET_SETPOINT, CMD_SET_POWER, CMD_SET_MODE, CMD_SET_FAN


@pytest.fixture
def local(tmp_path):
    with closed_office(tmp_path, local=True) as runtime:
        yield runtime


def sets(local):
    return [call for call in local.calls if len(call) == 3 and call[1] in (CMD_SET_POWER, CMD_SET_MODE, CMD_SET_SETPOINT, CMD_SET_FAN)]


async def wait_rooms(manager, rooms=None):
    deadline = time.monotonic() + 5
    while not all(manager.snapshot(room)['connected'] for room in (rooms or manager.rooms)):
        assert time.monotonic() < deadline
        await asyncio.sleep(.01)


def test_local_six_real_rooms_have_no_credentials_or_authorization(local, tmp_path):
    app = build_local_app(local.config, str(tmp_path/'history.db'), fleet=local.fleet)
    with TestClient(app, base_url='http://127.0.0.1:8090', client=('127.0.0.1',1234)) as client:
        client.portal.call(wait_rooms,app.state.manager)
        session = client.get('/api/session').json()
        assert session['mode'] == 'native-local' and session['hardware_enabled']
        rooms = client.get('/api/controllers').json()['controllers']
        assert len(rooms) == 6 and all(r['connected'] and not r['simulated'] for r in rooms)
        assert all(r['transport'] == 'native-local' for r in rooms)
        assert not (tmp_path/'.madoka-readonly-web/office-authorization.json').exists()
        assert all(type(g) is LocalSession for g in local.grants.values())
        assert client.get('/').status_code == 200
        assert not sets(local)
    assert not local.budget.owners


@pytest.mark.parametrize('action,value,field', [('power',False,'power_on'), ('mode',4,'mode_id'), ('mode',2,'mode_id'), ('setpoint',23,'setpoint'), ('fan',5,'fan_id')])
def test_local_direct_control_once_and_readback_without_reason(local,tmp_path,action,value,field):
    app = build_local_app(local.config,str(tmp_path/'history.db'),fleet=local.fleet)
    with TestClient(app,base_url='http://127.0.0.1:8090',client=('127.0.0.1',1234)) as client:
        client.portal.call(wait_rooms,app.state.manager)
        token={'X-Demo-Token':client.get('/api/session').json()['token']}
        payload={'operation_id':str(uuid4()),'action':action,'value':value}
        response=client.post('/api/controllers/room-1/manual',json=payload,headers=token)
        assert response.status_code == 200,response.text
        data=response.json()
        assert data['state'][field] == value and data['last_operation']['status']=='confirmed'
        assert data['last_operation']['before_state']['state'][field] != value
        assert client.post('/api/controllers/room-1/manual',json=payload,headers=token).status_code == 409
        assert len(sets(local)) == 1


def seed_legacy(local, *, at=100, digest=None, runtime=None):
    session=local.grants['room-1']
    digest=digest or hashlib.sha256(session.identity.encode()).hexdigest()
    with local.budget.db:
        local.budget.db.execute("INSERT INTO sessions VALUES ('historical','room-1',?,'dead-process','quarantined',1)",(digest,))
        local.budget._event('historical','reserved_before_prepare',runtime or {})
        local.budget.db.execute('UPDATE session_events SET at=? WHERE session_id=?',(at,'historical'))
    return session


def test_historical_quarantine_after_reboot_recovers_automatically_and_retains_all_rows(local):
    async def case():
        session=seed_legacy(local)
        original_events=local.budget.db.execute('SELECT COUNT(*) FROM session_events').fetchone()[0]
        backend=await local.fleet.ensure_room('room-1')
        records=local.budget.records()
        assert records[0]['state']=='released_native' and records[0]['session_id']=='historical'
        assert len(records)==2 and backend.port.ready
        assert local.budget.db.execute("SELECT COUNT(*) FROM session_events WHERE kind='local_native_absence_recovery'").fetchone()[0]==1
        assert local.budget.db.execute('SELECT COUNT(*) FROM session_events').fetchone()[0]>original_events
        assert not sets(local)
        await backend.close()
    asyncio.run(case())


@pytest.mark.parametrize('fault',['identity','newer_session','native_connected','native_connecting','unknown_native','clock_rollback'])
def test_inconclusive_recovery_blocks_only_affected_room(local,fault):
    async def case():
        session=seed_legacy(local,digest='wrong' if fault=='identity' else None,at=2000 if fault=='newer_session' else time.time()+30 if fault=='clock_rollback' else 100)
        if fault=='native_connected': local.external_connections.add(session.identity)
        if fault=='native_connecting': local.native_overrides[session.identity]=1
        if fault=='unknown_native': local.native_overrides[session.identity]=3
        with pytest.raises(BackendUnavailable): await recover_local_room(local.budget,session)
        assert local.budget.records()[0]['state']=='quarantined'
        other=await local.fleet.ensure_room('room-2')
        assert other.port.ready and not sets(local)
        await other.close()
    asyncio.run(case())


def test_same_boot_dead_local_process_recovers_without_reviewer_or_reboot(local):
    async def case():
        session=seed_legacy(local,at=2000,runtime={'runtime':'local','boot_time':1000})
        await recover_local_room(local.budget,session)
        assert not local.budget.owners
    asyncio.run(case())


def test_normal_disconnect_reconnect_is_bounded_and_never_sends_set(local):
    async def case():
        backend=await local.fleet.ensure_room('room-1')
        client=local.clients[-1]
        client.is_connected=False;client.callback(client)
        local.fleet.next_attempt['room-1']=0
        fresh=await local.fleet.ensure_room('room-1')
        assert fresh is not backend and fresh.port.ready
        assert len(local.budget.records())==2 and not sets(local)
        await fresh.close()
    asyncio.run(case())


def test_local_native_state_proof_not_public_return_or_callback_alone(local):
    async def case():
        backend=await local.fleet.ensure_room('room-1')
        local.no_callback=True
        await backend.close()
        assert not local.budget.owners
        local.fleet.next_attempt['room-1']=0
        backend=await local.fleet.ensure_room('room-1')
        local.native_state_override=3
        with pytest.raises(Exception): await backend.close()
        assert local.budget.quarantined
    asyncio.run(case())


def test_local_settings_persist_without_changing_private_office_config(local,tmp_path):
    async def case():
        controls=Controls(power=True,setpoint=True,modes=(3,4),fans=(1,3,5),minimum=20,maximum=25)
        local.fleet.configure_controls('room-1',controls)
        assert local.fleet.settings_path.stat().st_mode & 0o077 == 0
        replacement=LocalFleet(local.config,local.budget,local.grants,settings_path=local.fleet.settings_path)
        assert replacement.backends['room-1'].controls==controls
        backend=await replacement.ensure_room('room-1')
        await backend.snapshot(('power','mode','setpoint','sensor','fan'))
        assert backend.latest['min_setpoint']==20 and backend.latest['max_setpoint']==25
        assert backend.latest['heating_fan_id']==2 # inactive value need not be enabled for SET
        await backend.close()
    asyncio.run(case())


def test_local_reads_old_config_without_certificates_or_mutating_source(tmp_path):
    data=json.loads(Path('office.example.json').read_text())
    path=tmp_path/'office.json';path.write_text(json.dumps(data));path.chmod(0o600)
    before=path.read_bytes()
    config=load_local_config(path)
    assert len(config.rooms.devices)==6
    assert not any(c.power for c in config.controls.values())
    assert path.read_bytes()==before
    assert not Path(data['certificate']).exists()


def test_local_shares_exclusive_original_ledger_lock(local):
    with pytest.raises(BlockingIOError):
        PhysicalSessionBudget(local.budget.db.execute('PRAGMA database_list').fetchone()[2],
            ConnectionPlan('retained',6,2),local_sessions=local.grants)
    assert local.budget.db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0


@pytest.mark.parametrize('attack',['missing_token','cross_origin','null_origin','cross_site','host','remote_peer','legacy','body','non_json'])
def test_local_loopback_security(local,tmp_path,attack):
    app=build_local_app(local.config,str(tmp_path/'history.db'),fleet=local.fleet)
    with TestClient(app,base_url='http://127.0.0.1:8090',client=('127.0.0.1',1234)) as client:
        token=client.get('/api/session').json()['token']
        headers={'X-Demo-Token':token};path='/api/controllers/room-1/manual'
        payload={'operation_id':str(uuid4()),'action':'power','value':False}
        if attack=='missing_token': headers={}
        if attack=='cross_origin': headers['Origin']='http://evil.example'
        if attack=='null_origin': headers['Origin']='null'
        if attack=='cross_site': headers['Sec-Fetch-Site']='cross-site'
        if attack=='host': headers['Host']='evil.example:8090'
        if attack=='legacy': path='/api/controllers/room-1/power'
        if attack=='body': payload['extra']='x'*17000
        if attack=='remote_peer': client=TestClient(app,base_url='http://127.0.0.1:8090',client=('192.168.1.10',1234))
        if attack=='non_json': response=client.post(path,content='{}',headers=headers)
        else: response=client.post(path,json=payload,headers=headers)
        assert response.status_code in (403,413,415)
        assert not sets(local)


def test_default_local_start_is_blocked_under_offline_guard():
    result=subprocess.run([sys.executable,'madoka_local.py'],capture_output=True,text=True,
        env={**os.environ,'MADOKA_OFFLINE_ONLY':'1','PYTHONPATH':'ci'})
    assert result.returncode==2 and 'deshabilitado' in result.stdout


@pytest.mark.parametrize('fault',['no_effect','ack','timeout','cancel','disconnect','readback'])
def test_local_uncertain_write_reconnects_for_reads_but_never_replays(local,fault):
    async def case():
        manager=RoomManager(local.config.rooms,RoomStore(':memory:',mode='real'),offline_fleet=local.fleet)
        backend=await local.fleet.ensure_room('room-1')
        manager.room('room-1').backend=backend
        await manager.operate('room-1')
        if fault=='no_effect': local.apply_writes=False
        if fault=='ack': local.ack={0x99:b'\x01'}
        if fault in ('timeout','cancel','disconnect'):
            local.stage='write';local.room='room-1'
            local.delay=1 if fault=='timeout' else 0
            backend.timeout=.1 if fault=='timeout' else 30
            local.error=asyncio.CancelledError() if fault=='cancel' else ConnectionError('closed disconnect') if fault=='disconnect' else None
        if fault=='readback': local.fail_readback=True
        operation_id=str(uuid4())
        with pytest.raises(RoomUnavailable):
            await manager.native_manual('room-1','setpoint',23,operation_id,'Local browser action')
        assert local.budget.pending_operation('room-1') and len(sets(local))==1
        local.delay=0;local.error=None;local.fail_readback=False
        local.fleet.next_attempt['room-1']=0
        backend=await local.fleet.ensure_room('room-1')
        await backend.snapshot(('power','mode','setpoint','sensor','fan'))
        assert not backend.control_view()['power'] and not backend.control_view()['setpoint']
        assert len(sets(local))==1
        with pytest.raises(BackendUnavailable):
            await backend.manual('power',False,str(uuid4()),'Local browser action',lambda *args:None)
        await manager.close();manager.store.close()
    asyncio.run(case())


def test_local_pending_operation_and_history_survive_normal_restart(local,tmp_path):
    async def case():
        backend=await local.fleet.ensure_room('room-1')
        await backend.snapshot(('power','mode','setpoint','sensor','fan'))
        local.apply_writes=False
        with pytest.raises(BackendUnavailable):
            await backend.manual('setpoint',23,str(uuid4()),'Local browser action',lambda *args:None)
        await backend.close()
        ledger=local.budget.db.execute('PRAGMA database_list').fetchone()[2]
        original_id=local.budget.last_operation('room-1')['operation_id']
        local.budget.close_storage()
        local.budget=PhysicalSessionBudget(ledger,ConnectionPlan('retained',6,2),local_sessions=local.grants)
        fleet=LocalFleet(local.config,local.budget,local.grants,settings_path=local.fleet.settings_path)
        backend=await fleet.ensure_room('room-1')
        state=await backend.snapshot(('power','mode','setpoint','sensor','fan'))
        assert state['setpoint']==22 and not backend.control_view()['setpoint']
        assert local.budget.last_operation('room-1')['operation_id']==original_id
        assert len(sets(local))==1
        await backend.close()
    asyncio.run(case())


def test_local_crash_same_boot_recovers_only_when_actual_native_absence_is_proven(local):
    async def case():
        backend=await local.fleet.ensure_room('room-1')
        client=local.clients[-1]
        ledger=local.budget.db.execute('PRAGMA database_list').fetchone()[2]
        local.budget.close_storage()
        local.budget=PhysicalSessionBudget(ledger,ConnectionPlan('retained',6,2),local_sessions=local.grants)
        fleet=LocalFleet(local.config,local.budget,local.grants)
        with pytest.raises(BackendUnavailable): await fleet.ensure_room('room-1')
        assert local.budget.quarantined
        # Model native release after the crashed process has actually ended.
        # No public disconnect or callback is used as absence evidence.
        client.is_connected=False
        fleet.next_attempt['room-1']=0
        fresh=await fleet.ensure_room('room-1')
        assert fresh.port.ready and not sets(local)
        assert local.budget.records()[0]['state']=='released_native'
        await fresh.close()
    asyncio.run(case())


def test_local_slow_setup_does_not_hold_healthy_room_exchange_capacity(local):
    async def case():
        local.budget.setup_operations=asyncio.Semaphore(1)
        local.budget.operations=asyncio.Semaphore(1)
        backend=await local.fleet.ensure_room('room-1')
        await backend.snapshot(('power','mode','setpoint','sensor','fan'))
        local.stage='connect';local.room='room-2';local.delay=.3
        connecting=asyncio.create_task(local.fleet.ensure_room('room-2'))
        await asyncio.sleep(.02)
        async with asyncio.timeout(.1):
            await backend.manual('setpoint',23,str(uuid4()),'Local browser action',lambda *args:None)
        assert not connecting.done()
        other=await connecting
        await backend.close();await other.close()
    asyncio.run(case())


def test_local_noop_and_changed_preflight_do_not_stop_or_send_set(local):
    async def case():
        store=RoomStore(':memory:',mode='real')
        manager=RoomManager(local.config.rooms,store,offline_fleet=local.fleet)
        backend=await local.fleet.ensure_room('room-1');manager.room('room-1').backend=backend;await manager.operate('room-1')
        state=await manager.native_manual('room-1','power',True,str(uuid4()),'Local browser action')
        assert state['last_operation']['status']=='confirmed' and not sets(local)
        local.states['room-1'][CMD_GET_MODE][0x20]=b'\x02'
        with pytest.raises(ValueError):
            await manager.native_manual('room-1','setpoint',23,str(uuid4()),'Local browser action')
        assert not backend.stopped and not local.budget.pending_operation('room-1')
        assert manager.room('room-1').failure is None and not sets(local)
        await manager.close();store.close()
    asyncio.run(case())


def test_local_unsupported_fan_does_not_hide_room_temperature_or_allow_write(local):
    async def case():
        backend=await local.fleet.ensure_room('room-1')
        local.states['room-1'][CMD_GET_FAN]={0x20:b'\xff'}
        state=await backend.snapshot(('power','mode','setpoint','sensor','fan'))
        assert state['room_temp']==23 and state['fan_id'] is None
        assert backend.control_view()['fans']==[]
        with pytest.raises(ValueError): await backend.manual('fan',3,str(uuid4()),'Local browser action',lambda *args:None)
        assert not sets(local)
        await backend.close()
    asyncio.run(case())


def test_original_native_v1_schema_migrates_without_discarding_events(local):
    async def case():
        seed_legacy(local)
        ledger=local.budget.db.execute('PRAGMA database_list').fetchone()[2]
        # Model the original pre-manual-controls schema, in a temporary test DB.
        with local.budget.db: local.budget.db.execute('DROP TABLE native_operations')
        local.budget.close_storage()
        local.budget=PhysicalSessionBudget(ledger,ConnectionPlan('retained',6,2),local_sessions=local.grants)
        assert local.budget.db.execute("SELECT name FROM sqlite_master WHERE name='native_operations'").fetchone()
        await recover_local_room(local.budget,local.grants['room-1'])
        assert local.budget.records()[0]['session_id']=='historical'
        assert local.budget.db.execute("SELECT COUNT(*) FROM session_events WHERE kind='reserved_before_prepare'").fetchone()[0]==1
    asyncio.run(case())


def test_local_lost_callback_still_requires_native_absence_and_recovers_normally(local):
    async def case():
        backend=await local.fleet.ensure_room('room-1')
        local.clients[-1].is_connected=False
        local.no_callback=True
        local.fleet.next_attempt['room-1']=0
        fresh=await local.fleet.ensure_room('room-1')
        assert fresh.port.ready and not sets(local)
        await fresh.close()
    asyncio.run(case())


def test_local_setup_queue_does_not_consume_connection_deadline(local,monkeypatch):
    async def case():
        real_timeout=asyncio.timeout
        monkeypatch.setattr(asyncio,'timeout',lambda seconds:real_timeout(.02 if seconds==170 else seconds))
        semaphore=local.budget.setup_operations
        for _ in range(local.budget.plan.max_operations): await semaphore.acquire()
        task=asyncio.create_task(local.fleet.ensure_room('room-1'))
        await asyncio.sleep(.04)
        assert not task.done() and not local.clients
        for _ in range(local.budget.plan.max_operations): semaphore.release()
        backend=await task
        assert backend.port.ready
        await backend.close()
    asyncio.run(case())


def test_local_write_queue_expires_before_starting_a_late_set(local,monkeypatch):
    from madoka_multiroom.manager import RoomBusy
    async def case():
        store=RoomStore(':memory:',mode='real')
        manager=RoomManager(local.config.rooms,store,offline_fleet=local.fleet)
        backend=await local.fleet.ensure_room('room-1');manager.room('room-1').backend=backend
        await manager.operate('room-1')
        for _ in range(local.budget.plan.max_operations): await manager.capacity.acquire()
        real_timeout=asyncio.timeout
        monkeypatch.setattr(asyncio,'timeout',lambda seconds:real_timeout(.02 if seconds==35 else seconds))
        with pytest.raises(RoomBusy):
            await manager.native_manual('room-1','power',False,str(uuid4()),'Local browser action')
        assert not sets(local) and not local.budget.pending_operation('room-1') and not backend.stopped
        for _ in range(local.budget.plan.max_operations): manager.capacity.release()
        await manager.close();store.close()
    asyncio.run(case())


def test_local_error_messages_are_specific_and_do_not_echo_sdk_identifiers():
    from types import SimpleNamespace
    from madoka_multiroom.local_runtime import local_error
    class AdapterError(Exception): pass
    error=AdapterError('private-uuid-and-device-address')
    error.reason=SimpleNamespace(name='POWERED_OFF')
    assert 'apagado' in local_error(error)
    assert 'permiso' in local_error(PermissionError('private-path'))
    assert 'encuentra' in local_error(error:=RuntimeError('private-uuid'),phase='discovery')
    assert 'private' not in local_error(error,phase='connection')


def test_local_port_conflict_stops_before_opening_ledger_or_creating_native_clients(local,monkeypatch):
    import socket
    import madoka_local
    from madoka_multiroom import local_runtime
    class BusySocket:
        def setsockopt(self,*args): pass
        def bind(self,*args):
            import errno
            raise OSError(errno.EADDRINUSE,'in use')
        def close(self): pass
    monkeypatch.setattr(local_runtime,'native_environment',lambda:None)
    monkeypatch.setattr(socket,'socket',lambda *args:BusySocket())
    assert madoka_local.main([])==2
    assert not local.clients and not local.budget.records()


@pytest.mark.parametrize('started', [False, True])
def test_local_launcher_reports_lifespan_startup_failure(monkeypatch, capsys, started):
    import socket
    import uvicorn
    import madoka_local

    class Listener:
        closed = False
        def setsockopt(self, *args): pass
        def bind(self, *args): pass
        def listen(self, *args): pass
        def setblocking(self, *args): pass
        def close(self): self.closed = True

    class Server:
        def __init__(self, config): self.started = started
        async def serve(self, *, sockets):
            assert sockets == [listener]

    listener = Listener()
    original_socket = socket.socket
    monkeypatch.setattr(socket, 'socket', lambda *args, **kwargs:
        original_socket(*args, **kwargs) if len(args) > 3 or 'fileno' in kwargs else listener)
    monkeypatch.setattr(uvicorn, 'Server', Server)
    assert madoka_local.main(['--demo']) == (0 if started else 2)
    assert listener.closed
    assert ('No se pudo iniciar' in capsys.readouterr().out) == (not started)
