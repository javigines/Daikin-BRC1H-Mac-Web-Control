"""Offline policy invariants: no hardware imports, clocks or long sleeps needed."""
import asyncio
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from madoka_multiroom.config import demo_config
from madoka_multiroom.manager import RoomManager, RoomUnavailable
from madoka_multiroom.policies import AdminChange, DesiredPatch, PolicyEngine, PolicySpec, ResetBudget
from madoka_multiroom.storage import RoomStore


class Clock:
    def __init__(self):
        self.now = 1000.0
    def __call__(self):
        return self.now
    def advance(self, delta=2):
        self.now += delta
        return self.now


def engine(path=":memory:", room="room-1"):
    store = RoomStore(str(path))
    store.register(demo_config().devices)
    clock = Clock()
    return PolicyEngine(store, room, clock), clock, store


def spec(**kwargs):
    return PolicySpec(mode="ENFORCE", protected=("setpoint",), stabilization_seconds=1.0,
                      cooldown_seconds=1.0, **kwargs)


def observe(p, c, state, delta=2, **kwargs):
    return p.observe(state, c.advance(delta), **kwargs)


@pytest.mark.parametrize("mode", ["FREE", "MONITOR", "ENFORCE"])
def test_desired_never_comes_from_observed_and_modes(mode):
    p, c, s = engine()
    p.configure(spec().model_copy(update={"mode": mode}))
    assert observe(p, c, {"setpoint": 22}) is None
    assert p.view()["desired"] == {}
    p.desired({"setpoint": 23})
    assert observe(p, c, {"setpoint": 22}) is None
    result = observe(p, c, {"setpoint": 22})
    assert result == (("setpoint", 23) if mode == "ENFORCE" else None)
    assert p.view()["desired"] == {"setpoint": 23}
    assert p.view()["notification"] == (mode != "FREE")
    assert p.view()["deviations"]["setpoint"]["origin"] == "unknown"
    s.close()


@pytest.mark.parametrize("field,desired,external", [("setpoint", 23.0, 20.0),
    ("mode_id", 4, 3), ("fan_id", 1, 5), ("power_on", False, True)])
def test_each_protected_field_independent(field, desired, external):
    p, c, s = engine()
    p.configure(spec().model_copy(update={"protected": (field,)}))
    values = {"setpoint": 23.0, "mode_id": 4, "fan_id": 1, "power_on": False}
    state = {"setpoint": 20.0, "mode_id": 3, "fan_id": 5, "power_on": True}
    p.desired(values)
    assert observe(p, c, state) is None
    assert observe(p, c, state) == (field, desired)
    assert set(p.view()["deviations"]) == set(values)
    s.close()


def test_no_protected_fields_means_no_writes():
    p, c, s = engine()
    p.configure(PolicySpec(mode="ENFORCE"))
    p.desired({"setpoint": 24})
    for _ in range(20):
        assert observe(p, c, {"setpoint": 20}) is None
    assert p.view()["attempts"] == 0
    s.close()


def test_confirmations_stability_repeated_timestamp_and_flapping():
    p, c, s = engine()
    p.configure(spec())
    p.desired({"setpoint": 24})
    assert observe(p, c, {"setpoint": 20}) is None
    assert p.observe({"setpoint": 20}, c()) is None
    assert observe(p, c, {"setpoint": 21}) is None
    assert observe(p, c, {"setpoint": 20}) is None
    assert observe(p, c, {"setpoint": 20}, .5) is None
    assert observe(p, c, {"setpoint": 20}, .6) == ("setpoint", 24)
    s.close()


def test_unprotected_and_missing_observations_do_not_correct():
    p, c, s = engine()
    p.configure(spec())
    p.desired({"setpoint": 24, "fan_id": 1})
    assert observe(p, c, {"setpoint": 20}) is None
    assert observe(p, c, {"fan_id": 5}) is None
    assert observe(p, c, {"setpoint": 20}) is None
    assert observe(p, c, {"setpoint": 20}) == ("setpoint", 24)
    s.close()


def test_budget_stops_infinite_fight_survives_restart_and_requires_manual_reset(tmp_path):
    path = tmp_path / "policies.db"
    p, c, s = engine(path)
    p.configure(spec(max_attempts=2))
    p.desired({"setpoint": 24})
    intents = []
    for _ in range(30):
        intent = observe(p, c, {"setpoint": 20})
        if intent:
            intents.append(intent)
    assert len(intents) == 2
    assert p.view()["stopped"] and p.view()["attempts"] == 2
    s.close()
    p, c, s = engine(path)
    assert p.view()["desired"] == {"setpoint": 24}
    assert p.view()["stopped"] and p.view()["attempts"] == 2
    p.configure(spec(max_attempts=5))  # policy change does not silently rearm STOP
    assert observe(p, c, {"setpoint": 20}) is None
    p.reset("owner reviewed repeated interference")
    assert observe(p, c, {"setpoint": 20}) is None
    assert observe(p, c, {"setpoint": 20}) == ("setpoint", 24)
    kinds = [e["kind"] for e in s.events("room-1")["events"]]
    assert "stop_budget_exhausted" in kinds and "budget_reset" in kinds
    s.close()


def test_restart_drops_confirmations_but_preserves_cooldown_and_attempts(tmp_path):
    path = tmp_path / "restart.db"
    p, c, s = engine(path)
    p.configure(spec().model_copy(update={"cooldown_seconds": 60.0}))
    p.desired({"setpoint": 24})
    observe(p, c, {"setpoint": 20})
    assert observe(p, c, {"setpoint": 20})
    s.close()
    p, c, s = engine(path)
    assert observe(p, c, {"setpoint": 20}) is None
    assert observe(p, c, {"setpoint": 20}) is None
    # Clock rollback cannot bypass cooldown.
    assert observe(p, c, {"setpoint": 20}, 1) is None
    assert observe(p, c, {"setpoint": 20}, 61) == ("setpoint", 24)
    assert p.view()["attempts"] == 2
    s.close()


def test_maintenance_suspension_expiry_and_traceability():
    p, c, s = engine()
    p.configure(spec())
    p.desired({"setpoint": 24})
    for change in [AdminChange(maintenance=True, reason="service"),
                   AdminChange(suspend_seconds=20, reason="temporary override")]:
        p.administrative(change)
        for _ in range(3):
            assert observe(p, c, {"setpoint": 20}) is None
    c.advance(20)
    assert observe(p, c, {"setpoint": 20}) is None
    assert observe(p, c, {"setpoint": 20}) == ("setpoint", 24)
    kinds = [e["kind"] for e in s.events("room-1")["events"]]
    assert "restrictions_changed" in kinds and "suspension_expired" in kinds
    s.close()


def test_auto_on_default_off_and_stronger_explicit_guard():
    p, c, s = engine()
    p.configure(PolicySpec(mode="ENFORCE", protected=("power_on",)))
    p.desired({"power_on": True})
    for _ in range(10):
        assert observe(p, c, {"power_on": False}, 30) is None
    assert p.view()["attempts"] == 0
    p.configure(PolicySpec(mode="ENFORCE", protected=("power_on",), allow_auto_on=True))
    assert observe(p, c, {"power_on": False}) is None
    assert observe(p, c, {"power_on": False}, 30) is None  # only 2 readings
    assert observe(p, c, {"power_on": False}) == ("power_on", True)
    assert observe(p, c, {"power_on": True}, allow_correction=False) is None
    for _ in range(10):
        assert observe(p, c, {"power_on": False}, 60) is None
    assert p.view()["attempts"] == 1 and p.view()["auto_on_used"]
    assert "correction_observed" in [e["kind"] for e in s.events("room-1")["events"]]
    s.close()


@pytest.mark.parametrize("model,body", [
    (PolicySpec, {"mode": "WRONG"}), (PolicySpec, {"protected": ["setpoint", "setpoint"]}),
    (PolicySpec, {"allow_auto_on": True}), (PolicySpec, {"confirmations": 1}),
    (PolicySpec, {"max_attempts": 6}), (PolicySpec, {"cooldown_seconds": float("nan")}),
    (DesiredPatch, {}), (DesiredPatch, {"setpoint": None}), (DesiredPatch, {"setpoint": "22"}),
    (DesiredPatch, {"fan_id": True}), (DesiredPatch, {"power_on": 1}),
    (DesiredPatch, {"mode_id": 6}), (AdminChange, {"reason": ""}),
    (AdminChange, {"suspend_seconds": 3601, "reason": "owner"}), (ResetBudget, {"reason": "x"})])
def test_policy_validation(model, body):
    with pytest.raises(ValidationError):
        model.model_validate(body)


def test_manager_six_devices_different_policies_external_changes_and_error_stop(monkeypatch):
    async def case():
        c = Clock()
        import madoka_multiroom.manager as module
        monkeypatch.setattr(module, "time", SimpleNamespace(time=c, monotonic=c))
        store = RoomStore(":memory:")
        m = RoomManager(demo_config(), store)
        for i, (rid, room) in enumerate(m.rooms.items()):
            room.policy.clock = c
            room.policy.configure(spec().model_copy(update={"mode": ["FREE", "MONITOR", "ENFORCE"][i % 3]}))
            await m.operate(rid, "setpoint", 24)
        c.advance()
        await asyncio.gather(*(m.external_change(rid, {"setpoint": 20, "fan_id": 5}) for rid in m.rooms))
        c.advance()
        await asyncio.gather(*(m.operate(rid) for rid in m.rooms))
        assert [m.snapshot(r)["state"]["setpoint"] for r in m.rooms] == [20, 20, 24, 20, 20, 24]
        assert all(m.snapshot(r)["state"]["fan_id"] == 5 for r in m.rooms)
        assert all(m.snapshot(r)["protection"]["desired"] == {"setpoint": 24} for r in m.rooms)
        await m.simulate("room-3", "error")
        assert m.snapshot("room-3")["protection"]["stopped"]
        await m.simulate("room-3", "online")
        await m.external_change("room-3", {"setpoint": 20})
        c.advance(100)
        await m.operate("room-3")
        assert m.snapshot("room-3")["state"]["setpoint"] == 20
        assert m.snapshot("room-6")["protection"]["stopped"] is False
        observed = store.db.execute("SELECT state_json FROM room_observed WHERE controller_id='room-3'").fetchone()[0]
        assert '20' in observed and store.control("room-3")["desired"] == {"setpoint": 24}
        await m.close()
        store.close()
    asyncio.run(case())


def test_correction_error_is_latched_without_retries(monkeypatch):
    async def case():
        c = Clock()
        import madoka_multiroom.manager as module
        monkeypatch.setattr(module, "time", SimpleNamespace(time=c, monotonic=c))
        store = RoomStore(":memory:")
        m = RoomManager(demo_config(), store)
        room = m.rooms["room-1"]
        room.policy.clock = c
        room.policy.configure(spec())
        room.policy.desired({"setpoint": 24})
        await m.operate("room-1")
        c.advance()
        async def fail(*args):
            raise RuntimeError("injected simulated write failure")
        room.backend.apply = fail
        with pytest.raises(RoomUnavailable):
            await m.operate("room-1")
        assert room.policy.view()["attempts"] == 1 and room.policy.view()["stopped"]
        for _ in range(10):
            with pytest.raises(RoomUnavailable):
                await m.operate("room-1")
        assert room.policy.view()["attempts"] == 1
        assert (await m.operate("room-2"))["connected"]
        await m.close()
        store.close()
    asyncio.run(case())


def test_capacity_wait_included_in_deadline_no_late_write():
    async def case():
        data = demo_config().model_dump(mode="json")
        data["max_concurrent_operations"] = 1
        data["devices"][1]["queries"]["timeout_seconds"] = .1
        from madoka_multiroom.config import AppConfig
        store = RoomStore(":memory:")
        m = RoomManager(AppConfig.model_validate(data), store)
        m.rooms["room-1"].backend.delay_seconds = .2
        first = asyncio.create_task(m.operate("room-1"))
        await asyncio.sleep(.01)
        with pytest.raises(RoomUnavailable):
            await m.operate("room-2", "setpoint", 30)
        await first
        assert m.rooms["room-2"].backend.state["setpoint"] == 22
        assert m.rooms["room-2"].stats["writes"] == 0
        assert m.rooms["room-2"].policy.view()["desired"] == {"setpoint": 30}
        assert m.rooms["room-2"].policy.view()["stopped"]
        await m.close()
        store.close()
    asyncio.run(case())


@pytest.mark.parametrize("mutation", ["policy", "reset", "restrictions", "desired"])
def test_failed_persistence_rolls_back_administration(monkeypatch, mutation):
    import sqlite3
    p, c, s = engine()
    p.desired({"setpoint": 22})
    p.administrative(AdminChange(maintenance=True, reason="service in progress"))
    p.stop("operator test")
    before = p.view()
    original = s.save_control
    def fail(*args):
        raise sqlite3.OperationalError("injected full disk")
    monkeypatch.setattr(s, "save_control", fail)
    calls = {"policy": lambda: p.configure(spec()), "reset": lambda: p.reset("reviewed"),
             "restrictions": lambda: p.administrative(AdminChange(reason="resume")),
             "desired": lambda: p.desired({"setpoint": 30})}
    with pytest.raises(sqlite3.OperationalError):
        calls[mutation]()
    assert p.view() == before
    monkeypatch.setattr(s, "save_control", original)
    for _ in range(10):
        assert observe(p, c, {"setpoint": 20}) is None
    assert s.control("room-1")["policy"]["mode"] == "FREE"
    assert s.control("room-1")["stopped"] is True
    s.close()


def test_audit_failure_does_not_kill_poller_and_stop_survives_recovery(monkeypatch):
    import sqlite3
    async def case():
        store = RoomStore(":memory:")
        m = RoomManager(demo_config(), store)
        original = store.save_control
        def fail(*args):
            raise sqlite3.OperationalError("injected disk failure")
        monkeypatch.setattr(store, "save_control", fail)
        m.rooms["room-1"].backend.status = "error"
        poller = asyncio.create_task(m.poll("room-1", 0))
        await asyncio.sleep(.01)
        assert not poller.done()
        assert m.rooms["room-1"].policy.view()["stopped"]
        monkeypatch.setattr(store, "save_control", original)
        await m.simulate("room-1", "online")
        assert not poller.done()
        assert m.snapshot("room-1")["connected"]
        assert m.snapshot("room-1")["protection"]["stopped"]
        assert store.control("room-1")["stopped"]
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
        await m.close()
        store.close()
    asyncio.run(case())


def test_repeated_disk_failures_during_stop_recovery_and_reset_keep_stop(monkeypatch):
    import sqlite3
    async def case():
        store = RoomStore(":memory:")
        m = RoomManager(demo_config(), store)
        room = m.rooms["room-1"]
        room.policy.desired({"setpoint": 24})
        room.policy.configure(spec())
        original = store.save_control
        def fail(*args):
            raise sqlite3.OperationalError("disk still broken")
        monkeypatch.setattr(store, "save_control", fail)
        await m.simulate("room-1", "error")
        assert room.policy.view()["stopped"]
        for _ in range(3):
            with pytest.raises(RoomUnavailable):
                await m.simulate("room-1", "online")
            assert room.failure and room.policy.view()["stopped"]
            with pytest.raises(RoomUnavailable):
                await m.administer("room-1", "reset", ResetBudget(reason="reviewed"))
            assert room.policy.view()["stopped"]
        monkeypatch.setattr(store, "save_control", original)
        await m.simulate("room-1", "online")
        assert room.policy.view()["stopped"] and store.control("room-1")["stopped"]
        await m.administer("room-1", "reset", ResetBudget(reason="storage repaired and reviewed"))
        assert not room.policy.view()["stopped"]
        await m.close()
        store.close()
    asyncio.run(case())
