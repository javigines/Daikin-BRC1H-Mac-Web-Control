"""Integration regressions for explicit setup and quarantined offline ownership."""
import asyncio
import gc
import weakref

import pytest

from madoka_protocol import CMD_GET_POWER, CMD_SET_POWER
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.backends.daikin import (
    BackendBusy, CleanupUncertain, DaikinBackend, SessionBudget, SessionState,
)
from madoka_multiroom.backends.offline_port import Reply, ScriptedController


def controller(**kwargs):
    return ScriptedController({CMD_GET_POWER: [Reply(objects={0x20: b"\x01"})]}, **kwargs)


async def prepared(**kwargs):
    port = controller(**kwargs)
    budget = SessionBudget()
    backend = DaikinBackend(port, budget, timeout=.1, cleanup_timeout=.1)
    await backend.prepare()
    return backend, port, budget


def test_operations_cannot_implicitly_prepare_or_claim_a_session():
    async def case():
        port = controller()
        backend = DaikinBackend(port, SessionBudget())
        for operation in (backend.snapshot(("power",)), backend.apply("power", False)):
            with pytest.raises(BackendUnavailable, match="Explicit preparation"):
                await operation
        assert not port.calls and port.setup_calls == 0
        assert not backend.claimed and not backend.stopped
        await backend.prepare()
        assert (await backend.snapshot(("power",)))["power_on"]
        await backend.close()
    asyncio.run(case())


def test_preparation_can_outlast_operation_deadline_and_is_idempotent():
    async def case():
        port = controller(setup=Reply(delay=.2))
        backend = DaikinBackend(port, SessionBudget(), timeout=.1, setup_timeout=1)
        # The default setup allowance covers the legacy 12+20+20s phases with margin.
        assert DaikinBackend(controller(), SessionBudget()).setup_timeout >= 60
        await backend.prepare()
        await backend.prepare()
        assert port.setup_calls == 1 and not port.calls
        assert backend.state is SessionState.READY
        assert (await backend.snapshot(("power",)))["power_on"]
        await backend.close()
    asyncio.run(case())


@pytest.mark.parametrize("failure", ["timeout", "exception", "cancellation"])
def test_setup_failure_stops_without_retry_and_retains_ownership(failure):
    async def case():
        reply = Reply(error=ConnectionError("setup fault")) if failure == "exception" else Reply(delay=1)
        port = controller(setup=reply)
        budget = SessionBudget()
        backend = DaikinBackend(port, budget, setup_timeout=.1)
        task = asyncio.create_task(backend.prepare())
        if failure == "cancellation":
            while port.setup_calls == 0:
                await asyncio.sleep(0)
            task.cancel()
        expected = {"exception": ConnectionError, "timeout": TimeoutError,
                    "cancellation": asyncio.CancelledError}[failure]
        with pytest.raises(expected):
            await task
        assert backend.state is SessionState.STOPPED and backend.claimed
        assert len(budget.owners) == 1 and not port.calls
        for operation in (backend.prepare(), backend.snapshot(("power",))):
            with pytest.raises(BackendUnavailable):
                await operation
        assert port.setup_calls == 1
        await backend.close()
        assert not budget.owners
    asyncio.run(case())


def test_setup_deadline_includes_wait_for_operation_capacity():
    async def case():
        budget = SessionBudget()
        await budget.operations.acquire()
        port = controller()
        backend = DaikinBackend(port, budget, setup_timeout=.1)
        with pytest.raises(TimeoutError):
            await backend.prepare()
        assert port.setup_calls == 0 and backend.stopped and backend.claimed
        budget.operations.release()
        await backend.close()
    asyncio.run(case())


def test_lost_ready_session_never_reenters_setup_or_sends_a_write():
    async def case():
        backend, port, budget = await prepared()
        port.ready = False
        with pytest.raises(BackendUnavailable, match="no lazy setup"):
            await backend.apply("power", False)
        assert port.setup_calls == 1 and not port.calls
        assert backend.stopped and backend.latest == {}
        assert CMD_SET_POWER not in [cmd for cmd, _ in port.calls]
        await backend.close()
    asyncio.run(case())


def test_close_during_preparation_prevents_operations_after_setup():
    async def case():
        port = controller(setup=Reply(delay=.05))
        backend = DaikinBackend(port, SessionBudget())
        setup = asyncio.create_task(backend.prepare())
        while port.setup_calls == 0:
            await asyncio.sleep(0)
        close = asyncio.create_task(backend.close())
        await asyncio.sleep(0)
        with pytest.raises(BackendUnavailable):
            await setup
        await close
        assert not port.calls and port.disconnect_calls == 1
        assert backend.state is SessionState.CLOSED and not backend.budget.owners
    asyncio.run(case())


def test_cleanup_has_its_own_deadline_longer_than_an_ordinary_operation():
    async def case():
        port = controller(disconnect=Reply(delay=.2))
        backend = DaikinBackend(port, SessionBudget(), timeout=.1, cleanup_timeout=1)
        await backend.prepare()
        await backend.close()
        assert backend.state is SessionState.CLOSED and backend._cleanly_closed
        assert port.disconnect_calls == 1 and not backend.budget.owners
    asyncio.run(case())


@pytest.mark.parametrize("failure", ["exception", "timeout", "cancellation"])
def test_uncertain_cleanup_quarantines_capacity_and_repeated_close_cannot_succeed(failure):
    async def case():
        reply = Reply(error=ConnectionError("disconnect fault")) if failure == "exception" else Reply(delay=1)
        backend, port, budget = await prepared(disconnect=reply)
        task = asyncio.create_task(backend.close())
        if failure == "cancellation":
            while port.disconnect_calls == 0:
                await asyncio.sleep(0)
            task.cancel()
        expected = {"exception": ConnectionError, "timeout": TimeoutError,
                    "cancellation": asyncio.CancelledError}[failure]
        with pytest.raises(expected):
            await task
        error = backend.cleanup_error
        assert backend.closed and backend.stopped and backend.claimed
        assert backend.state is SessionState.CLEANUP_UNCERTAIN
        assert len(budget.owners) == len(budget.quarantined) == 1
        assert not backend._cleanly_closed and not backend.latest
        # A later successful plan must not turn repeated close into a retry.
        port.disconnect_reply = Reply()
        for _ in range(2):
            with pytest.raises(CleanupUncertain):
                await backend.close()
        assert port.disconnect_calls == 1 and backend.cleanup_error == error
        assert not backend._cleanly_closed and backend.claimed
        replacement = DaikinBackend(controller(), budget)
        duplicate = DaikinBackend(port, budget)
        for contender in (replacement, duplicate):
            with pytest.raises(BackendBusy):
                await contender.prepare()
            await contender.close()
        assert port.disconnect_calls == 1
        with pytest.raises(CleanupUncertain):
            budget.release(port, backend)
        with pytest.raises(BackendUnavailable, match="owner mismatch"):
            budget.release(port, object())
        assert len(budget.owners) == 1
    asyncio.run(case())


def test_cancelling_close_while_waiting_for_lock_quarantines_without_disconnect():
    async def case():
        backend, port, budget = await prepared()
        port.plan[CMD_GET_POWER][0] = Reply(objects={0x20: b"\x01"}, delay=.05)
        operation = asyncio.create_task(backend.snapshot(("power",)))
        while not port.calls:
            await asyncio.sleep(0)
        close = asyncio.create_task(backend.close())
        await asyncio.sleep(0)
        close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close
        assert backend.state is SessionState.CLEANUP_UNCERTAIN
        with pytest.raises(BackendUnavailable):
            backend.resolve_offline_cleanup(reason="operator review", evidence="in-flight operation")
        with pytest.raises(BackendUnavailable):
            await operation
        assert port.disconnect_calls == 0 and backend.claimed
        with pytest.raises(CleanupUncertain):
            await backend.close()
        assert len(budget.quarantined) == 1 and not backend._cleanly_closed
    asyncio.run(case())


def test_concurrent_close_callers_cannot_retry_or_release_failed_disconnect():
    async def case():
        backend, port, budget = await prepared(disconnect=Reply(delay=.03, error=ConnectionError("fault")))
        results = await asyncio.gather(backend.close(), backend.close(), return_exceptions=True)
        assert isinstance(results[0], ConnectionError)
        assert isinstance(results[1], CleanupUncertain)
        assert port.disconnect_calls == 1 and len(budget.quarantined) == 1
        assert backend.state is SessionState.CLEANUP_UNCERTAIN
    asyncio.run(case())


def test_supervised_offline_resolution_records_evidence_without_claiming_clean_close():
    async def case():
        backend, port, budget = await prepared(disconnect=Reply(error=ConnectionError("fault")))
        with pytest.raises(BackendUnavailable):
            backend.resolve_offline_cleanup(reason="premature", evidence="no quarantine")
        with pytest.raises(ConnectionError):
            await backend.close()
        for reason, evidence in [("", "proof"), ("valid", " "), (None, "proof")]:
            with pytest.raises(ValueError):
                backend.resolve_offline_cleanup(reason=reason, evidence=evidence)
        assert backend.claimed and len(budget.quarantined) == 1
        resolution = backend.resolve_offline_cleanup(
            reason="Offline operator reviewed the injected failure", evidence="Scripted session only; no physical I/O")
        assert budget.resolutions == (resolution,) and not budget.owners
        assert backend.state is SessionState.RELEASED_OFFLINE
        assert not backend._cleanly_closed and backend.closed and backend.stopped
        with pytest.raises(BackendUnavailable):
            backend.resolve_offline_cleanup(reason="replay", evidence="old review")
        with pytest.raises(CleanupUncertain):
            await backend.close()
        with pytest.raises(BackendUnavailable):
            await backend.snapshot(("power",))
        replacement = DaikinBackend(controller(), budget)
        await replacement.prepare()
        await replacement.close()
    asyncio.run(case())


def test_quarantined_budget_keeps_strong_session_and_owner_references():
    async def case():
        backend, port, budget = await prepared(disconnect=Reply(error=ConnectionError("fault")))
        with pytest.raises(ConnectionError):
            await backend.close()
        owner_ref, port_ref = weakref.ref(backend), weakref.ref(port)
        del backend, port
        gc.collect()
        assert owner_ref() is not None and port_ref() is not None
        assert len(budget.quarantined) == 1
    asyncio.run(case())


@pytest.mark.parametrize("argument,value", [("setup_timeout", True), ("setup_timeout", float("nan")),
    ("setup_timeout", .01), ("cleanup_timeout", float("inf")), ("cleanup_timeout", 0)])
def test_invalid_lifecycle_deadlines_are_rejected_without_setup(argument, value):
    port = controller()
    with pytest.raises(ValueError):
        DaikinBackend(port, SessionBudget(), **{argument: value})
    assert port.setup_calls == port.disconnect_calls == 0 and not port.calls


def test_manager_enforce_cannot_correct_even_a_prepared_offline_daikin_adapter(monkeypatch):
    from types import SimpleNamespace
    import madoka_multiroom.manager as module
    from madoka_multiroom.config import AppConfig, demo_config
    from madoka_multiroom.policies import PolicySpec
    from madoka_multiroom.storage import RoomStore

    async def case():
        now = [1000.0]
        monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[0]))
        data = demo_config().model_dump(mode="json")
        data["devices"][0]["queries"]["fields"] = ["power"]
        config = AppConfig.model_validate(data)
        store = RoomStore(":memory:")
        manager = module.RoomManager(config, store)
        room = manager.rooms["room-1"]
        await room.backend.close()
        port = controller()
        room.backend = DaikinBackend(port, SessionBudget())
        await room.backend.prepare()
        room.policy.clock = lambda: now[0]
        room.policy.configure(PolicySpec(mode="ENFORCE", protected=("power_on",),
            stabilization_seconds=1.0, cooldown_seconds=1.0))
        room.policy.desired({"power_on": False})
        await manager.operate("room-1")
        now[0] += 2
        with pytest.raises(module.RoomUnavailable):
            await manager.operate("room-1")
        assert room.policy.view()["stopped"] and room.failure
        assert room.stats["writes"] == 0
        assert all(command == CMD_GET_POWER for command, _ in port.calls)
        # A different simulator still works after the rejected correction.
        assert (await manager.operate("room-2"))["connected"]
        await manager.close()
        store.close()
    asyncio.run(case())
