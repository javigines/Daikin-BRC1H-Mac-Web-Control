"""Adapter contract and semantic decoding, using data-only doubles exclusively."""
import asyncio
import copy
import importlib.abc
import subprocess
import sys

import pytest

from madoka_protocol import (CMD_GET_POWER as POWER, CMD_GET_MODE as MODE,
    CMD_GET_SETPOINT as SETPOINT, CMD_GET_FAN as FAN, CMD_GET_SENSOR as SENSOR,
    CMD_SET_POWER, CMD_SET_MODE, CMD_SET_FAN, CMD_SET_SETPOINT, temp_to_bytes)
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.backends.daikin import (DaikinBackend, SessionBudget, BackendBusy,
    PhysicalBackendDisabled, WriteNotConfirmed)
from madoka_multiroom.backends.daikin_mapping import decode_snapshot, InvalidSnapshot
from madoka_multiroom.backends.offline_port import Reply, ScriptedController

FIELDS = ("power","mode","setpoint","fan","sensor")


def responses(mode=3, target=22):
    return {POWER:{0x20:b"\x01"},MODE:{0x20:bytes([mode])},
            SETPOINT:{0x20:temp_to_bytes(target),0x21:temp_to_bytes(target),
                      0xA2:temp_to_bytes(16),0xB2:temp_to_bytes(32),
                      0xA3:temp_to_bytes(18),0xB3:temp_to_bytes(30)},
            FAN:{0x20:b"\x03",0x21:b"\x04"},SENSOR:{0x40:b"\x17"}}


def port(data=None, **kwargs):
    return ScriptedController({cmd:[Reply(objects=objects,**kwargs)] for cmd,objects in (data or responses()).items()})


@pytest.mark.parametrize("mode", range(6))
def test_mapping_never_invents_values_and_selects_active_mode(mode):
    result = decode_snapshot(responses(mode),FIELDS)
    assert result["mode_id"] == mode and result["power_on"] is True
    assert result["fan_id"] == (4 if mode == 4 else 3)
    assert result["min_setpoint"] == (18 if mode == 4 else 16)
    assert result["setpoint"] == 22 and result["room_temp"] == 23
    assert all(v == "available" for v in result["field_status"].values())


@pytest.mark.parametrize("command,oid,bad", [
    (POWER,0x20,b""),(POWER,0x20,b"\x02"),(POWER,0x20,b"\x00\x01"),
    (MODE,0x20,b""),(MODE,0x20,b"\xff"),(FAN,0x20,b""),
    (FAN,0x20,b"\x00"),(FAN,0x20,b"\x06"),(SENSOR,0x40,b"\x00\x17"),
    (SENSOR,0x40,b"\x7f"),(SETPOINT,0x20,b""),(SETPOINT,0x20,b"\x16"),
    (SETPOINT,0xA2,b"\x00\x00"),(SETPOINT,0xB2,temp_to_bytes(20))])
def test_invalid_or_unsupported_required_objects_fail_closed(command,oid,bad):
    data = responses()
    data[command][oid] = bad
    with pytest.raises(InvalidSnapshot):
        decode_snapshot(data,FIELDS)


@pytest.mark.parametrize("command,oid", [(POWER,0x20),(MODE,0x20),(FAN,0x20),
    (SENSOR,0x40),(SETPOINT,0x20),(SETPOINT,0x21),(SETPOINT,0xA2),(SETPOINT,0xB2)])
def test_absent_required_objects_are_not_success(command,oid):
    data = responses()
    del data[command][oid]
    with pytest.raises(InvalidSnapshot):
        decode_snapshot(data,FIELDS)


def test_explicit_optional_sensor_unsupported_is_distinct_from_missing():
    data = responses()
    data[SENSOR][0x40] = b""
    result = decode_snapshot(data,FIELDS)
    assert result["room_temp"] is None
    assert result["field_status"]["room_temp"] == "unsupported"
    assert decode_snapshot({POWER:data[POWER]},("power",))["power_on"] is True
    for bad in [{}, {POWER:None}, {POWER:{0x20:True}}, {POWER:{"32":b"\x01"}}]:
        with pytest.raises(InvalidSnapshot):
            decode_snapshot(bad,("power",))


def test_physical_creation_is_unconditionally_disabled():
    class WouldConnect:
        def __getattribute__(self,name):
            raise AssertionError("Physical object must not be inspected or called")
    with pytest.raises(PhysicalBackendDisabled):
        DaikinBackend(WouldConnect(),SessionBudget())
    with pytest.raises(PhysicalBackendDisabled):
        DaikinBackend.from_physical_controller("private address")


@pytest.mark.parametrize("action,value,command,group", [
    ("power",False,CMD_SET_POWER,POWER),("mode",4,CMD_SET_MODE,MODE),
    ("fan",5,CMD_SET_FAN,FAN),("setpoint",24,CMD_SET_SETPOINT,SETPOINT)])
def test_explicit_apply_requires_readback_and_preserves_wire_fields(action,value,command,group):
    async def case():
        data = responses()
        changed = copy.deepcopy(data[group])
        if action == "setpoint":
            changed[0x20] = changed[0x21] = temp_to_bytes(value)
        elif action == "fan":
            changed[0x20] = changed[0x21] = bytes([value])
        else:
            changed[0x20] = bytes([int(value)])
        plan = {cmd:[Reply(objects=objects)] for cmd,objects in data.items()}
        plan[command] = [Reply(objects={})]  # ACK alone never supplies observed state.
        plan[group] = ([Reply(objects=data[group])] if action == "setpoint" else []) + [Reply(objects=changed)]
        controller = ScriptedController(plan)
        backend = DaikinBackend(controller,SessionBudget())
        await backend.apply(action,value)
        assert not backend.stopped
        assert any(cmd == command for cmd,args in controller.calls)
        assert backend.latest[{"power":"power_on","mode":"mode_id","fan":"fan_id","setpoint":"setpoint"}[action]] == value
        if action == "setpoint":
            args = next(args for cmd,args in controller.calls if cmd == command)
            assert len(args) == 17 and dict(args)[0x20] == dict(args)[0x21]
        await backend.close()
        await backend.close()
        assert controller.disconnect_calls == 1
    asyncio.run(case())


def test_ack_without_matching_observation_stops_and_never_retries():
    async def case():
        controller = port()
        controller.plan[CMD_SET_POWER] = __import__("collections").deque([Reply(objects={})])
        backend = DaikinBackend(controller,SessionBudget())
        with pytest.raises(WriteNotConfirmed):
            await backend.apply("power",False)
        before = len(controller.calls)
        for _ in range(5):
            with pytest.raises(BackendUnavailable):
                await backend.snapshot(FIELDS)
        assert len(controller.calls) == before and backend.latest == {}
        await backend.close()
    asyncio.run(case())


def test_six_sessions_concurrency_failure_isolation_and_snapshot_copies():
    async def case():
        budget = SessionBudget(max_active=6,max_operations=2)
        ports = [port(delay=.005*i) for i in range(6)]
        ports[2].plan[POWER] = __import__("collections").deque([Reply(error=ConnectionError("injected"))])
        backends = [DaikinBackend(p,budget) for p in ports]
        tasks = [asyncio.create_task(b.snapshot(FIELDS)) for b in backends]
        await asyncio.sleep(.001)
        assert budget.operations._value == 0
        results = await asyncio.gather(*tasks,return_exceptions=True)
        assert isinstance(results[2],ConnectionError)
        assert all(isinstance(r,dict) for i,r in enumerate(results) if i != 2)
        assert backends[2].stopped and all(not b.stopped for i,b in enumerate(backends) if i != 2)
        results[0]["setpoint"] = 99
        assert backends[0].latest["setpoint"] == 22
        await asyncio.gather(*(b.close() for b in backends))
        assert not budget.owners
    asyncio.run(case())


def test_active_capacity_duplicate_port_busy_and_close_release():
    async def case():
        budget = SessionBudget()
        first_port = port(delay=.01)
        a,b,c = DaikinBackend(first_port,budget),DaikinBackend(first_port,budget),DaikinBackend(port(),budget)
        task = asyncio.create_task(a.snapshot(FIELDS))
        await asyncio.sleep(0)
        for backend in (a,b,c):
            with pytest.raises(BackendBusy):
                await backend.snapshot(FIELDS)
        await task
        await a.close()
        assert (await c.snapshot(("power",)))["power_on"]
        await c.close()
    asyncio.run(case())


def test_timeout_includes_capacity_wait_and_cancellation_latches_only_owner():
    async def case():
        budget = SessionBudget(3,1)
        a = DaikinBackend(port(delay=.05),budget,timeout=2)
        waiting_port = port()
        b = DaikinBackend(waiting_port,budget,timeout=.1)
        first = asyncio.create_task(a.snapshot(FIELDS))
        await asyncio.sleep(.005)
        with pytest.raises(TimeoutError):
            await b.snapshot(FIELDS)
        assert not waiting_port.calls and b.stopped
        await first
        a.port.plan[POWER] = __import__("collections").deque([Reply(objects={0x20:b"\x01"},delay=1)])
        task = asyncio.create_task(a.snapshot(("power",)))
        await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert a.stopped and not a.lock.locked()
        c = DaikinBackend(port(),budget)
        assert (await c.snapshot(("power",)))["power_on"]
        await asyncio.gather(a.close(),b.close(),c.close())
    asyncio.run(case())


def test_invalid_local_input_does_not_open_session_or_poison_backend():
    async def case():
        p = port()
        b = DaikinBackend(p,SessionBudget())
        for action,value in [("unknown",1),("power",1),("mode",True),("setpoint",33),("fan",6)]:
            with pytest.raises(ValueError):
                await b.apply(action,value)
        with pytest.raises(ValueError):
            await b.snapshot(("unknown",))
        assert not p.calls and not b.claimed and not b.stopped
        assert (await b.snapshot(("power",)))["power_on"]
        await b.close()
    asyncio.run(case())


def test_adapter_import_and_demo_have_no_driver_or_legacy_import():
    code = """
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.startswith('bleak') or fullname in {'daikin_server','brc1h_spike'}:
            raise AssertionError('FORBIDDEN_HARDWARE_IMPORT')
sys.meta_path.insert(0,Guard())
from madoka_multiroom.backends.daikin import DaikinBackend, SessionBudget, PhysicalBackendDisabled
from madoka_multiroom.api import build_demo_app
from madoka_multiroom.config import demo_config
from fastapi.testclient import TestClient
with TestClient(build_demo_app(demo_config()),base_url='http://127.0.0.1') as c:
    assert len(c.get('/api/controllers').json()['controllers']) == 6
try:
    DaikinBackend.from_physical_controller(None)
except PhysicalBackendDisabled:
    pass
else:
    raise AssertionError('Physical factory did not reject')
"""
    result = subprocess.run([sys.executable,"-c",code],text=True,capture_output=True)
    assert result.returncode == 0,result.stderr


def test_close_during_setpoint_preread_prevents_all_subsequent_exchanges():
    async def case():
        controller = port(delay=.03)
        backend = DaikinBackend(controller,SessionBudget())
        operation = asyncio.create_task(backend.apply("setpoint",24))
        while not controller.calls:
            await asyncio.sleep(0)
        close = asyncio.create_task(backend.close())
        await asyncio.sleep(0)
        assert backend.closed
        with pytest.raises(BackendUnavailable):
            await operation
        await close
        assert [cmd for cmd,args in controller.calls] == [MODE]
        assert controller.disconnect_calls == 1
        assert backend.stopped and not backend.budget.owners
    asyncio.run(case())


def test_near_miss_setpoint_readback_must_not_be_rounded_to_success():
    async def case():
        data = responses()
        after = copy.deepcopy(data[SETPOINT])
        after[0x20] = after[0x21] = temp_to_bytes(23.875)
        plan = {cmd:[Reply(objects=objects)] for cmd,objects in data.items()}
        plan[SETPOINT] = [Reply(objects=data[SETPOINT]),Reply(objects=after)]
        plan[CMD_SET_SETPOINT] = [Reply(objects={})]
        b = DaikinBackend(ScriptedController(plan),SessionBudget())
        with pytest.raises(WriteNotConfirmed):
            await b.apply("setpoint",24)
        assert b.stopped and not b.latest
        await b.close()
    asyncio.run(case())
    data = responses(target=9.875)
    with pytest.raises(InvalidSnapshot):
        decode_snapshot(data,FIELDS)
    data = responses(target=23.875)
    assert decode_snapshot(data,FIELDS)["setpoint"] == 23.875


def test_closing_denied_duplicate_never_disconnects_owner():
    async def case():
        budget = SessionBudget()
        controller = port()
        owner = DaikinBackend(controller,budget)
        rejected = DaikinBackend(controller,budget)
        never_admitted = DaikinBackend(controller,budget)
        await owner.snapshot(("power",))
        with pytest.raises(BackendBusy):
            await rejected.snapshot(("power",))
        await rejected.close()
        await never_admitted.close()
        assert controller.disconnect_calls == 0 and not controller.closed
        assert (await owner.snapshot(("power",)))["power_on"]
        assert len(budget.owners) == 1
        await owner.close()
        assert controller.disconnect_calls == 1 and not budget.owners
    asyncio.run(case())
