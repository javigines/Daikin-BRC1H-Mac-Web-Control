"""Six scripted radios through the actual hardened Controller and application."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from madoka_protocol import (CMD_GET_POWER as POWER, CMD_GET_MODE as MODE,
    CMD_GET_FAN as FAN, CMD_GET_SETPOINT as SETPOINT, CMD_GET_SENSOR as SENSOR,
    CMD_SET_POWER, CMD_SET_MODE, CMD_SET_FAN, CMD_SET_SETPOINT, temp_to_bytes)
from madoka_multiroom.api import build_demo_app
from madoka_multiroom.config import demo_config, AppConfig
from madoka_multiroom.manager import RoomManager, RoomUnavailable, RoomBusy
from madoka_multiroom.storage import RoomStore
from madoka_multiroom.policies import PolicySpec, DesiredPatch, ResetBudget
from madoka_multiroom.backends.daikin import BackendBusy, CleanupUncertain, PhysicalBackendDisabled
from madoka_multiroom.backends.controller_bridge import HardenedControllerBridge, ScriptedRadio, WireReply
from madoka_multiroom.backends.physical_sessions import (
    ConnectionPlan, PhysicalSessionBudget, OfflineRecoveryEvidence)
from madoka_multiroom.backends.physical_fleet import (
    ScriptedPhysicalFleet, PreparedDaikinBackend, DeviceCapabilities)


class Harness:
    def __init__(self, path, *, active=6, operations=2, strategy="managed", options=None,
                 config=None, capabilities=None, initial=()):
        self.config = config or demo_config()
        self.path = path / "sessions.sqlite"
        self.budget = PhysicalSessionBudget(self.path, ConnectionPlan(strategy, active, operations))
        self.radios = {d.id: ScriptedRadio(d.address, **(options or {}).get(d.id, {})) for d in self.config.devices}
        self.fleet = ScriptedPhysicalFleet(self.config, self.budget, self.radios,
                                          capabilities=capabilities, initial_admissions=initial)
        self.store = RoomStore(str(path / "rooms.sqlite"))
        self.manager = RoomManager(self.config, self.store, offline_fleet=self.fleet)

    async def admit(self):
        for rid in self.radios:
            await self.manager.prepare_room(rid)

    async def close(self):
        try:
            await self.manager.close()
        except RuntimeError:
            pass  # Explicitly expected uncertainty, never simulated native proof.
        self.store.close()
        self.budget.close_storage()


def evidence(row, **changes):
    return replace(OfflineRecoveryEvidence(row["session_id"], row["digest"], row["revision"],
        "operator-a", "reviewer-b", "offline-scripted-proof-1", True, True, True), **changes)


def frame(command, objects):
    body = b"".join(bytes((k, len(v))) + v for k, v in objects)
    return bytes((4 + len(body), 0)) + command.to_bytes(2, "big") + body


@pytest.mark.parametrize("strategy", ["managed", "retained"])
def test_six_independent_rooms_four_manual_commands_readback_histories_and_shutdown(tmp_path, strategy):
    async def case():
        h = Harness(tmp_path, strategy=strategy)
        try:
            await h.admit()
            await asyncio.gather(*(h.manager.operate(rid) for rid in h.radios))
            for i, rid in enumerate(h.radios):
                for action, value in (("power", False), ("setpoint", 23 + i % 2), ("mode", 4), ("fan", 5)):
                    result = await h.manager.operate(rid, action, value)
                    assert result["hardware_enabled"] is False and result["physical_enforce_enabled"] is False
                assert h.radios[rid].setup_calls[:2] == [("discover", 15), ("connect", 120)]
                commands = [cmd for cmd, args in h.radios[rid].calls]
                assert all(cmd in commands for cmd in (CMD_SET_POWER, CMD_SET_MODE, CMD_SET_SETPOINT, CMD_SET_FAN))
                assert len(h.store.history(rid, 0)["samples"]) == 5
                assert h.manager.snapshot(rid)["state"]["fan_id"] == 5
            assert len({r["digest"] for r in h.budget.records()}) == 6
            assert len(h.budget.owners) == 6
            assert h.manager.rooms["room-1"].policy.view()["desired"]["setpoint"] == 23
            assert h.manager.rooms["room-2"].policy.view()["desired"]["setpoint"] == 24
            with pytest.raises(RuntimeError, match="uncertain"):
                await h.manager.close()
            assert len(h.budget.quarantined) == 6
            assert all(r.cleanup_calls == ["stop_notify", "disconnect"] for r in h.radios.values())
            assert all(b.port.client is r for b, r in zip(h.fleet.backends.values(), h.radios.values()))
        finally:
            await h.close()
    asyncio.run(case())


def test_default_one_session_no_rotation_and_explicit_offline_resolution(tmp_path):
    async def case():
        h = Harness(tmp_path, active=1, operations=1)
        try:
            await h.manager.prepare_room("room-1")
            await h.manager.operate("room-1")
            for rid in list(h.radios)[1:]:
                with pytest.raises(BackendBusy):
                    await h.manager.prepare_room(rid)
                assert not h.radios[rid].setup_calls and h.manager.rooms[rid].failure is None
            with pytest.raises(CleanupUncertain):
                await h.fleet.backends["room-1"].close()
            with pytest.raises(BackendBusy):
                await h.manager.prepare_room("room-2")
            row = h.budget.records()[0]
            for bad in (evidence(row, revision=0), evidence(row, identity_digest="wrong"),
                        evidence(row, independent_reviewer="operator-a"), evidence(row, native_disconnected=False)):
                with pytest.raises((ValueError, CleanupUncertain)):
                    h.budget.resolve_offline(bad)
            h.budget.resolve_offline(evidence(row))
            await h.manager.prepare_room("room-2")
            assert h.radios["room-1"].cleanup_calls == ["stop_notify", "disconnect"]
            assert h.radios["room-2"].is_connected
            with pytest.raises(PhysicalBackendDisabled):
                h.budget.resolve_physical(evidence(row))
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.parametrize("stage", ["discover", "connect", "subscribe"])
@pytest.mark.parametrize("failure", ["timeout", "cancel", "error"])
def test_preparation_failure_cancellation_quarantines_without_retry_and_isolates_peer(tmp_path, stage, failure):
    async def case():
        key = {"discover": "setup", "connect": "connect", "subscribe": "subscribe"}[stage]
        options = {key + ("_error" if failure == "error" else "_delay"): RuntimeError("injected") if failure == "error" else 1}
        h = Harness(tmp_path, options={"room-1": options})
        try:
            backend = h.fleet.backends["room-1"]
            backend.setup_timeout = .1
            task = asyncio.create_task(h.manager.prepare_room("room-1"))
            if failure == "cancel":
                while not any(call[0] == stage for call in h.radios["room-1"].setup_calls):
                    await asyncio.sleep(0)
                task.cancel()
            with pytest.raises(asyncio.CancelledError if failure == "cancel" else TimeoutError if failure == "timeout" else RuntimeError):
                await task
            assert len(h.budget.quarantined) == 1 and backend.stopped
            count = len(h.radios["room-1"].setup_calls)
            with pytest.raises((RoomBusy, RoomUnavailable)):
                await h.manager.prepare_room("room-1")
            assert len(h.radios["room-1"].setup_calls) == count
            await h.manager.prepare_room("room-2")
            assert (await h.manager.operate("room-2"))["connected"]
        finally:
            await h.close()
    asyncio.run(case())


def test_setup_outside_short_manager_operation_deadline(tmp_path):
    async def case():
        data = demo_config().model_dump(mode="json")
        for d in data["devices"]: d["queries"]["timeout_seconds"] = .1
        h = Harness(tmp_path, config=AppConfig.model_validate(data), options={"room-1": {"connect_delay": .15}})
        try:
            await h.manager.prepare_room("room-1")
            assert (await h.manager.operate("room-1"))["connected"]
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.parametrize("failure", ["timeout", "cancel", "disconnect", "error"])
def test_request_failures_stop_one_room_never_reconnect(tmp_path, failure):
    async def case():
        reply = WireReply(delay=1 if failure in ("timeout", "cancel") else 0,
            disconnect=failure == "disconnect", error=RuntimeError("injected") if failure == "error" else None)
        h = Harness(tmp_path, options={"room-1": {"plan": {POWER: [reply]}}})
        try:
            await h.admit()
            h.fleet.backends["room-1"].timeout = .1
            task = asyncio.create_task(h.manager.operate("room-1"))
            if failure == "cancel":
                while not h.radios["room-1"].calls: await asyncio.sleep(0)
                task.cancel()
            with pytest.raises(asyncio.CancelledError if failure == "cancel" else RoomUnavailable):
                await task
            assert len(h.budget.quarantined) == 1
            before = len(h.radios["room-1"].calls)
            for _ in range(3):
                with pytest.raises(RoomUnavailable): await h.manager.operate("room-1")
            assert len(h.radios["room-1"].calls) == before
            assert (await h.manager.operate("room-6"))["connected"]
            assert len(h.radios["room-1"].setup_calls) == 3
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.parametrize("kind", ["short", "index", "header", "duplicate", "wrong_command", "duplicate_first", "bad_tlv"])
def test_actual_controller_bridge_strict_notification_stop(tmp_path, kind):
    async def case():
        payload = frame(POWER, [(0x20, b"\x01")])
        chunks = {
            "short": (b"\x00",), "index": (b"\x01" + payload,),
            "header": (b"\x00" + payload[:1] + b"\x01" + payload[2:],),
            "duplicate": (b"\x00" + frame(POWER, [(0x20,b"\x01"),(0x20,b"\x00")]),),
            "wrong_command": (b"\x00" + frame(MODE, [(0x20,b"\x03")]),),
            "duplicate_first": (b"\x00" + payload[:4], b"\x00" + payload),
            "bad_tlv": (b"\x00\x07\x00\x00\x20\x20\x02\x01",),
        }[kind]
        h = Harness(tmp_path, options={"room-1": {"plan": {POWER: [WireReply(chunks=chunks)]}}})
        try:
            await h.manager.prepare_room("room-1")
            with pytest.raises(RoomUnavailable): await h.manager.operate("room-1")
            assert h.manager.rooms["room-1"].latest == {} and len(h.budget.quarantined) == 1
            assert [cmd for cmd,args in h.radios["room-1"].calls] == [MODE, POWER]
        finally:
            await h.close()
    asyncio.run(case())


@pytest.mark.parametrize("objects,success", [({}, False), ({0x20:b""}, False), ({0x20:b"\x02"}, False), ({0x20:b"\x01\x00"}, False), ({0x20:b"\x01"}, True)])
def test_missing_unsupported_and_invalid_required_fields(tmp_path, objects, success):
    async def case():
        h = Harness(tmp_path, options={"room-1": {"plan": {POWER: [WireReply(objects=objects)]}}})
        try:
            await h.manager.prepare_room("room-1")
            if success: assert (await h.manager.operate("room-1"))["state"]["power_on"] is True
            else:
                with pytest.raises(RoomUnavailable): await h.manager.operate("room-1")
        finally:
            await h.close()
    asyncio.run(case())


def test_unsupported_optional_sensor_remains_explicit(tmp_path):
    async def case():
        h = Harness(tmp_path, options={"room-1": {"plan": {SENSOR: [WireReply(objects={0x40:b""})]}}})
        try:
            await h.manager.prepare_room("room-1")
            state = (await h.manager.operate("room-1"))["state"]
            assert state["room_temp"] is None and state["field_status"]["room_temp"] == "unsupported"
        finally: await h.close()
    asyncio.run(case())


@pytest.mark.parametrize("method", ["stop_error", "disconnect_error"])
@pytest.mark.parametrize("error", [RuntimeError("injected"), asyncio.CancelledError()])
def test_cleanup_failures_and_direct_backend_cancellation_preserve_quarantine(tmp_path, method, error):
    async def case():
        h = Harness(tmp_path, options={"room-1": {method: error}})
        try:
            await h.manager.prepare_room("room-1")
            b = h.fleet.backends["room-1"]
            with pytest.raises(Exception): await b.close()
            assert h.radios["room-1"].cleanup_calls == ["stop_notify", "disconnect"]
            assert len(h.budget.quarantined) == 1 and b.port.client is h.radios["room-1"]
            with pytest.raises(CleanupUncertain): await b.close()
            assert len(h.radios["room-1"].cleanup_calls) == 2
        finally: await h.close()
    asyncio.run(case())


@pytest.mark.parametrize("action,value", [("power",False),("setpoint",24),("mode",4),("fan",5)])
def test_ack_without_effect_never_confirms_manual_command(tmp_path, action, value):
    async def case():
        h = Harness(tmp_path, options={"room-1": {"apply_writes": False}})
        try:
            await h.manager.prepare_room("room-1")
            await h.manager.operate("room-1")
            with pytest.raises(RoomUnavailable): await h.manager.operate("room-1", action, value)
            assert len(h.budget.quarantined) == 1
            assert h.manager.rooms["room-1"].policy.record["desired"]
        finally: await h.close()
    asyncio.run(case())


@pytest.mark.parametrize("action", ["fan", "setpoint"])
def test_both_written_fields_must_confirm_without_rounding(tmp_path, action):
    async def case():
        h = Harness(tmp_path)
        try:
            await h.manager.prepare_room("room-1")
            await h.manager.operate("room-1")
            radio = h.radios["room-1"]
            if action == "fan":
                radio.plan[FAN] = __import__("collections").deque([WireReply(objects={0x20:b"\x05", 0x21:b"\x03"})])
                value = 5
            else:
                obj = radio.observations(SETPOINT)
                bad = {**obj, 0x20:temp_to_bytes(23.875), 0x21:temp_to_bytes(24)}
                radio.plan[SETPOINT] = __import__("collections").deque([WireReply(objects=obj), WireReply(objects=bad)])
                value = 24
            with pytest.raises(RoomUnavailable): await h.manager.operate("room-1", action, value)
        finally: await h.close()
    asyncio.run(case())


def test_manual_capabilities_and_no_enforce_or_simulator_recovery_paths(tmp_path):
    async def case():
        h = Harness(tmp_path, capabilities={"room-1": DeviceCapabilities(modes=(3,),fan_speeds=(3,))})
        try:
            await h.manager.prepare_room("room-1")
            with pytest.raises(RoomUnavailable): await h.manager.operate("room-1", "power", True)
            await h.manager.operate("room-1")
            for action, value in (("mode",4),("fan",5)):
                with pytest.raises(ValueError): await h.manager.operate("room-1",action,value)
            assert h.manager.rooms["room-1"].policy.record["desired"] == {}
            before = len(h.radios["room-1"].calls)
            room = h.manager.rooms["room-1"]
            # A persisted legacy ENFORCE record still cannot produce SETs.
            room.policy.configure(PolicySpec(mode="ENFORCE", protected=("power_on",), allow_auto_on=True))
            room.policy.desired({"power_on":False})
            for _ in range(4): await h.manager.operate("room-1")
            assert all(cmd not in (CMD_SET_POWER,CMD_SET_MODE,CMD_SET_SETPOINT,CMD_SET_FAN)
                       for cmd,args in h.radios["room-1"].calls[before:])
            for operation in (h.manager.simulate("room-1","online"),
                              h.manager.external_change("room-1",{"power_on":False}),
                              h.manager.administer("room-1","policy",PolicySpec(mode="ENFORCE"))):
                with pytest.raises(RoomUnavailable): await operation
            assert room.policy.record["desired"] == {"power_on":False}
        finally: await h.close()
    asyncio.run(case())


def test_capacity_concurrency_bounded_per_room_and_shutdown_cancels_work(tmp_path):
    async def case():
        h = Harness(tmp_path, operations=1, options={"room-1":{"plan":{POWER:[WireReply(delay=.2)]}}})
        try:
            await h.admit()
            tasks = [asyncio.create_task(h.manager.operate(rid)) for rid in h.radios]
            await asyncio.sleep(.01)
            assert h.budget.operations._value == 0
            with pytest.raises(RoomBusy): await h.manager.operate("room-1")
            assert all(not r.calls for rid,r in h.radios.items() if rid != "room-1")
            await asyncio.gather(*tasks)
            task = asyncio.create_task(h.manager.operate("room-1"))
            await asyncio.sleep(.01)
            with pytest.raises(RuntimeError): await h.manager.close()
            assert task.cancelled() and len(h.budget.quarantined) == 6
        finally: await h.close()
    asyncio.run(case())


def test_restart_crash_marker_and_session_capacity_are_durable(tmp_path):
    source = """
import asyncio, os, sys
from madoka_multiroom.config import demo_config
from madoka_multiroom.backends.physical_sessions import PhysicalSessionBudget
from madoka_multiroom.backends.physical_fleet import ScriptedPhysicalFleet
from madoka_multiroom.backends.controller_bridge import ScriptedRadio
config = demo_config()
budget = PhysicalSessionBudget(sys.argv[1])
fleet = ScriptedPhysicalFleet(config,budget,{d.id:ScriptedRadio(d.address) for d in config.devices})
asyncio.run(fleet.backends['room-1'].prepare())
os._exit(99)
"""
    result = subprocess.run([sys.executable,"-c",source,str(tmp_path/"sessions.sqlite")],capture_output=True,text=True)
    assert result.returncode == 99, result.stderr
    async def case():
        h = Harness(tmp_path, active=1, operations=1)
        try:
            row = h.budget.records()[0]
            assert row["state"] == "quarantined"
            for rid in ("room-1","room-2"):
                with pytest.raises(BackendBusy): await h.manager.prepare_room(rid)
                assert not h.radios[rid].setup_calls
            with pytest.raises(BlockingIOError): PhysicalSessionBudget(h.path)
            h.budget.resolve_offline(evidence(row))
            with pytest.raises(CleanupUncertain): h.budget.resolve_offline(evidence(row))
            await h.manager.prepare_room("room-1")
            assert (await h.manager.operate("room-1"))["connected"]
            rows = h.budget.records()
            assert len(rows) == 2 and rows[0]["session_id"] != rows[1]["session_id"]
            events = [r[0] for r in h.budget.db.execute("SELECT kind FROM session_events")]
            assert "restart_quarantine" in events and "released_offline_not_native_proof" in events
        finally: await h.close()
    asyncio.run(case())


def test_stable_bindings_cannot_be_reassigned_after_resolution(tmp_path):
    async def case():
        h = Harness(tmp_path, active=1, operations=1)
        try:
            await h.manager.prepare_room("room-1")
            with pytest.raises(CleanupUncertain): await h.fleet.backends["room-1"].close()
            h.budget.resolve_offline(evidence(h.budget.records()[0]))
            alternate = PreparedDaikinBackend("room-1", h.fleet.backends["room-2"].port, h.budget)
            with pytest.raises(PhysicalBackendDisabled): await alternate.prepare()
            duplicate = PreparedDaikinBackend("other-room", HardenedControllerBridge(h.config.devices[0].address,
                ScriptedRadio(h.config.devices[0].address)), h.budget)
            with pytest.raises(PhysicalBackendDisabled): await duplicate.prepare()
        finally: await h.close()
    asyncio.run(case())


def test_real_factories_wrong_bindings_corrupt_ledgers_and_capacities_fail_closed(tmp_path):
    for factory in (HardenedControllerBridge.from_physical, ScriptedPhysicalFleet.from_physical,
                    PreparedDaikinBackend.from_physical_controller):
        with pytest.raises((RoomUnavailable, PhysicalBackendDisabled)): factory("private identity")
    for plan in (("rotation",1,1),("managed",0,1),("retained",7,1),("managed",1,2)):
        with pytest.raises(ValueError): ConnectionPlan(*plan)
    with pytest.raises(ValueError): PhysicalSessionBudget(":memory:")
    path=tmp_path/"bad.sqlite"
    path.write_bytes(b"not a sqlite database")
    path.chmod(0o600)
    with pytest.raises(Exception): PhysicalSessionBudget(path)
    config=demo_config()
    with pytest.raises(RoomUnavailable): HardenedControllerBridge(config.devices[0].address, object())


def test_application_http_reads_commands_history_and_desired_through_wire_bridge(tmp_path):
    config=demo_config()
    budget=PhysicalSessionBudget(tmp_path/"http-sessions.sqlite",ConnectionPlan("retained",6,2))
    radios={d.id:ScriptedRadio(d.address) for d in config.devices}
    fleet=ScriptedPhysicalFleet(config,budget,radios,initial_admissions=tuple(radios))
    app=build_demo_app(config,str(tmp_path/"http-rooms.sqlite"),offline_fleet=fleet)
    try:
        with pytest.raises(RuntimeError,match="uncertain"):
            with TestClient(app,base_url="http://127.0.0.1") as client:
                manager=app.state.manager
                # Complete observations deterministically before issuing HTTP commands.
                client.portal.call(lambda: asyncio.gather(*(manager.operate(rid) for rid in radios)))
                rooms=client.get("/api/controllers").json()["controllers"]
                assert len(rooms)==6 and all(r["transport"]=="offline-controller-contract" for r in rooms)
                session=client.get("/api/session").json()
                assert session["hardware_enabled"] is False
                headers={"X-Demo-Token":session["token"]}
                for action,body in (("power",{"on":False}),("setpoint",{"temp":24}),("mode",{"mode":4}),("fan",{"speed":5})):
                    response=client.post(f"/api/controllers/room-1/{action}",json=body,headers=headers)
                    assert response.status_code==200,response.text
                state=client.get("/api/controllers/room-1").json()
                assert state["protection"]["desired"]=={"power_on":False,"setpoint":24,"mode_id":4,"fan_id":5}
                assert client.get("/api/controllers/room-2").json()["protection"]["desired"]=={}
                assert client.get("/api/controllers/room-1/history").json()["samples"]
                assert client.post("/api/controllers/room-1/external-change",json={"power_on":True},headers=headers).status_code==503
    finally:
        budget.close_storage()


def test_new_import_graph_and_cli_real_guard_forbid_ble_activation():
    source="""
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname,path=None,target=None):
  if fullname.startswith('bleak'): raise AssertionError('BLE_FORBIDDEN')
sys.meta_path.insert(0,Guard())
import madoka_multiroom.backends.controller_bridge
import madoka_multiroom.backends.physical_fleet
import madoka_multiroom.backends.physical_sessions
"""
    result=subprocess.run([sys.executable,"-c",source],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    result=subprocess.run([sys.executable,"multiroom_app.py","--real"],capture_output=True,text=True)
    assert result.returncode!=0 and "disabled" in (result.stdout+result.stderr).lower()


def test_bridge_cannot_reinterpret_failed_cleanup_as_success(tmp_path):
    async def case():
        h = Harness(tmp_path, options={"room-1":{"stop_error":RuntimeError("injected")}})
        try:
            await h.manager.prepare_room("room-1")
            bridge=h.fleet.backends["room-1"].port
            with pytest.raises(RuntimeError): await bridge.disconnect()
            with pytest.raises(RoomUnavailable, match="Previous cleanup"):
                await bridge.disconnect()
            assert bridge.cleanup_failed and h.radios["room-1"].cleanup_calls==["stop_notify","disconnect"]
        finally: await h.close()
    asyncio.run(case())


@pytest.mark.parametrize("stage", ["stop", "disconnect"])
def test_cleanup_timeout_retains_ownership_and_still_attempts_disconnect(tmp_path, monkeypatch, stage):
    import ble_lifecycle
    monkeypatch.setattr(ble_lifecycle, "CLEANUP_TIMEOUT", .02)
    async def case():
        h=Harness(tmp_path, options={"room-1":{stage+"_delay":1}})
        try:
            await h.manager.prepare_room("room-1")
            b=h.fleet.backends["room-1"]
            with pytest.raises(RuntimeError): await b.close()
            assert h.radios["room-1"].cleanup_calls==["stop_notify","disconnect"]
            assert len(h.budget.quarantined)==1
            with pytest.raises(CleanupUncertain): await b.close()
        finally: await h.close()
    asyncio.run(case())


def test_direct_backend_contention_does_not_stop_current_owner(tmp_path):
    async def case():
        h=Harness(tmp_path, options={"room-1":{"plan":{POWER:[WireReply(delay=.05)]}}})
        try:
            b=h.fleet.backends["room-1"]
            await b.prepare()
            operation=asyncio.create_task(b.snapshot(("power",)))
            while not h.radios["room-1"].calls: await asyncio.sleep(0)
            with pytest.raises(BackendBusy): await b.prepare()
            assert (await operation)["power_on"] is True
            assert not b.stopped and not h.budget.quarantined
        finally: await h.close()
    asyncio.run(case())


def test_ledger_commit_failure_prevents_any_discovery(tmp_path):
    async def case():
        h=Harness(tmp_path)
        try:
            h.budget.db.execute("PRAGMA query_only=ON")
            with pytest.raises(Exception): await h.manager.prepare_room("room-1")
            assert not h.radios["room-1"].setup_calls
            assert not h.fleet.backends["room-1"].claimed
        finally: await h.close()
    asyncio.run(case())


def test_no_polling_or_startup_implicitly_admits_any_device(tmp_path):
    async def case():
        h=Harness(tmp_path, active=1, operations=1)
        try:
            await h.manager.start()
            await asyncio.sleep(.02)
            assert all(not r.setup_calls for r in h.radios.values())
            assert not h.budget.owners
        finally: await h.close()
    asyncio.run(case())


def test_asynchronous_link_loss_is_visible_and_cannot_be_prepared_again(tmp_path):
    async def case():
        h=Harness(tmp_path)
        try:
            await h.manager.prepare_room("room-1")
            await h.manager.operate("room-1")
            radio=h.radios["room-1"]
            radio.is_connected=False
            radio.disconnected_callback(radio)
            snapshot=h.manager.snapshot("room-1")
            assert snapshot["stale"] and not snapshot["connected"] and not snapshot["controls_enabled"]
            with pytest.raises(RoomUnavailable): await h.fleet.backends["room-1"].prepare()
            assert len(radio.setup_calls)==3 and len(h.budget.quarantined)==1
        finally: await h.close()
    asyncio.run(case())
