import asyncio
import time

import pytest

from madoka_multiroom.config import AppConfig, demo_config
from madoka_multiroom.manager import RoomBusy, RoomManager, RoomUnavailable
from madoka_multiroom.storage import RoomStore


def manager(**changes):
    config = demo_config().model_dump(mode="json")
    config.update(changes)
    store = RoomStore(":memory:")
    return RoomManager(AppConfig.model_validate(config), store)


def run(case):
    asyncio.run(case())


def test_all_rooms_independent_and_deep_copies():
    async def case():
        m = manager()
        for i, rid in enumerate(m.rooms):
            await m.operate(rid, "setpoint", 20 + i)
        assert [m.snapshot(r)["state"]["setpoint"] for r in m.rooms] == [20, 21, 22, 23, 24, 25]
        snap = m.snapshot("room-1")
        snap["state"]["setpoint"] = 99
        snap["stats"]["writes"] = 99
        assert m.snapshot("room-1")["state"]["setpoint"] == 20
        assert m.snapshot("room-1")["stats"]["writes"] == 1
        assert all(len(m.store.history(r, 0)["samples"]) == 1 for r in m.rooms)
        await m.close()
        m.store.close()
    run(case)


def test_concurrency_limit_and_different_latencies():
    async def case():
        m = manager(max_concurrent_operations=2)
        active = peak = 0
        for i, room in enumerate(m.rooms.values()):
            original = room.backend.snapshot
            async def snapshot(fields, original=original, delay=(i + 1) * .002):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                try:
                    await asyncio.sleep(delay)
                    return await original(fields)
                finally:
                    active -= 1
            room.backend.snapshot = snapshot
        results = await asyncio.gather(*(m.operate(r) for r in m.rooms))
        assert peak == 2 and all(r["connected"] for r in results)
        await m.close()
        m.store.close()
    run(case)


def test_busy_rejects_same_room_without_blocking_other_rooms():
    async def case():
        m = manager()
        m.rooms["room-3"].backend.delay_seconds = .03
        slow = asyncio.create_task(m.operate("room-3"))
        await asyncio.sleep(0)
        with pytest.raises(RoomBusy):
            await m.operate("room-3")
        assert (await m.operate("room-1"))["connected"]
        assert (await slow)["connected"]
        await m.close()
        m.store.close()
    run(case)


@pytest.mark.parametrize("fault", ["error", "disconnected", "timeout"])
def test_failure_latches_only_one_room_and_demo_recovery_is_explicit(fault):
    async def case():
        data = demo_config().model_dump(mode="json")
        data["devices"][2]["queries"]["timeout_seconds"] = .1
        m = RoomManager(AppConfig.model_validate(data), RoomStore(":memory:"))
        await m.operate("room-3")
        if fault == "timeout":
            m.rooms["room-3"].backend.delay_seconds = .2
            with pytest.raises(RoomUnavailable):
                await m.operate("room-3")
        else:
            await m.simulate("room-3", fault)
        failed = m.snapshot("room-3")
        assert failed["stale"] and not failed["controls_enabled"]
        for rid in m.rooms:
            if rid != "room-3":
                assert (await m.operate(rid))["connected"]
        with pytest.raises(RoomUnavailable):
            await m.operate("room-3")
        assert (await m.simulate("room-3", "online"))["connected"]
        await m.close()
        m.store.close()
    run(case)


def test_concurrent_cancellation_releases_capacity_and_locks():
    async def case():
        m = manager(max_concurrent_operations=1)
        tasks = []
        for rid in ["room-1", "room-2", "room-3"]:
            m.rooms[rid].backend.delay_seconds = 2
            tasks.append(asyncio.create_task(m.operate(rid)))
        await asyncio.sleep(.01)
        for task in tasks:
            task.cancel()
        assert all(isinstance(r, asyncio.CancelledError) for r in await asyncio.gather(*tasks, return_exceptions=True))
        assert all(not r.lock.locked() for r in m.rooms.values())
        assert not m.operations
        assert (await m.operate("room-6"))["connected"]
        await m.close()
        m.store.close()
    run(case)


def test_staleness_uses_monotonic_read_time_and_failed_read_does_not_refresh_it():
    async def case():
        m = manager(stale_seconds=5.0)
        await m.operate("room-1")
        room = m.rooms["room-1"]
        room.updated_monotonic = time.monotonic() - 6
        snap = m.snapshot("room-1")
        assert snap["stale"] and not snap["controls_enabled"]
        previous = room.updated_at
        await m.simulate("room-1", "error")
        assert m.snapshot("room-1")["updated_at"] == previous
        await m.close()
        m.store.close()
    run(case)


def test_poll_stagger_disabled_rooms_and_shutdown():
    async def case():
        data = demo_config().model_dump(mode="json")
        data["devices"][0]["enabled"] = False
        data["stagger_seconds"] = .01
        m = RoomManager(AppConfig.model_validate(data), RoomStore(":memory:"))
        await m.start()
        await asyncio.sleep(.1)
        assert m.rooms["room-1"].stats["reads"] == 0
        assert all(r.stats["reads"] == 1 for rid, r in m.rooms.items() if rid != "room-1")
        await m.close()
        assert all(t.done() for t in m.pollers)
        assert all(r.backend.closed for r in m.rooms.values())
        assert not m.operations
        with pytest.raises(RoomUnavailable):
            await m.operate("room-2")
        m.store.close()
    run(case)


def test_query_selection_and_rejected_temperature_do_not_corrupt_session():
    async def case():
        data = demo_config().model_dump(mode="json")
        data["devices"][0]["queries"]["fields"] = ["sensor"]
        m = RoomManager(AppConfig.model_validate(data), RoomStore(":memory:"))
        snap = await m.operate("room-1")
        assert set(snap["state"]) == {"room_temp"}
        with pytest.raises(ValueError):
            await m.operate("room-1", "setpoint", 35)
        assert m.rooms["room-1"].failure is None
        await m.close()
        m.store.close()
    run(case)
