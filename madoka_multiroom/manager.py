"""Independent simulated rooms, bounded coordination and staggered polling.

There is deliberately no factory for physical controllers in this iteration.
The backend boundary is an async snapshot/apply/close interface, not BLE itself.
"""
import asyncio
import copy
import logging
import time
from dataclasses import dataclass, field

from madoka_protocol import round_setpoint
from .config import AppConfig, DeviceConfig
from .storage import RoomStore
from .policies import PolicyEngine, FIELDS, ACTIONS

log = logging.getLogger(__name__)


class RoomUnavailable(Exception):
    pass


class RoomBusy(Exception):
    pass


class Simulator:
    def __init__(self, index: int):
        self.status = "online"
        self.delay_seconds = 0.0
        self.closed = False
        self.state = {"power_on": index % 3 != 0, "mode_id": 3, "setpoint": 22.0,
                      "room_temp": 22.0 + index * 0.5, "fan_id": 3,
                      "cooling_setpoint": 22.0, "heating_setpoint": 22.0,
                      "min_setpoint": 16.0, "max_setpoint": 32.0}
        self.info = {"model": "BRC1H · SIMULADO", "controller_version": "demo-1",
                     "comm_version": "demo-1"}

    async def snapshot(self, fields):
        await asyncio.sleep(self.delay_seconds)
        if self.closed or self.status != "online":
            raise RoomUnavailable("Simulated controller unavailable")
        keys = {"power": ["power_on"], "mode": ["mode_id"],
                "setpoint": ["setpoint", "cooling_setpoint", "heating_setpoint", "min_setpoint", "max_setpoint"],
                "sensor": ["room_temp"], "fan": ["fan_id"]}
        return {key: self.state[key] for group in fields for key in keys[group]}

    async def apply(self, action: str, value):
        await asyncio.sleep(self.delay_seconds)
        if self.closed or self.status != "online":
            raise RoomUnavailable("Simulated controller unavailable")
        if action == "setpoint":
            self.state.update(setpoint=value, cooling_setpoint=value, heating_setpoint=value)
        else:
            self.state[{"power": "power_on", "mode": "mode_id", "fan": "fan_id"}[action]] = value

    async def close(self):
        self.closed = True


@dataclass
class Room:
    config: DeviceConfig
    backend: Simulator
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    latest: dict = field(default_factory=dict)
    updated_at: float | None = None
    updated_monotonic: float | None = None
    failure: str | None = None
    policy: PolicyEngine | None = None
    stats: dict = field(default_factory=lambda: {"reads": 0, "writes": 0, "errors": 0,
                                               "cancellations": 0, "last_duration_ms": None})


class RoomManager:
    def __init__(self, config: AppConfig, store: RoomStore):
        self.config = config
        self.store = store
        store.register(config.devices)
        self.rooms = {d.id: Room(d, Simulator(i)) for i, d in enumerate(config.devices)}
        for room_id, room in self.rooms.items():
            room.policy = PolicyEngine(store, room_id)
        self.capacity = asyncio.Semaphore(config.max_concurrent_operations)
        self.pollers = []
        self.operations = set()
        self.closing = False

    def room(self, room_id: str) -> Room:
        return self.rooms[room_id]

    def snapshot(self, room_id: str) -> dict:
        room = self.room(room_id)
        age = None if room.updated_monotonic is None else max(0, time.monotonic() - room.updated_monotonic)
        stale = age is None or age > self.config.stale_seconds or bool(room.failure) or self.closing
        enabled = room.config.enabled
        return {"id": room.config.id, "name": room.config.name, "enabled": enabled,
                "connected": enabled and not stale, "stale": stale,
                "updated_at": room.updated_at, "age_seconds": age,
                "stale_after_seconds": self.config.stale_seconds,
                "error": room.failure, "state": copy.deepcopy(room.latest),
                "info": dict(room.backend.info), "stats": dict(room.stats),
                "controls_enabled": enabled and not stale and not room.lock.locked(),
                "queries": room.config.queries.model_dump(), "simulated": True,
                "protection": room.policy.view()}

    def stop_policy(self, room, reason):
        try:
            room.policy.stop(reason)
        except Exception:
            log.exception("Could not persist STOP for logical id %s", room.config.id)
            # stop() keeps the in-memory latch even if SQLite is unavailable.

    async def operate(self, room_id: str, action: str | None = None, value=None):
        room = self.room(room_id)
        if self.closing or not room.config.enabled or room.failure:
            raise RoomUnavailable("Controller disabled, stopped or failed")
        if room.lock.locked():
            raise RoomBusy("Controller has an operation in progress")
        if action == "setpoint":
            value = round_setpoint(value)
            if not room.backend.state["min_setpoint"] <= value <= room.backend.state["max_setpoint"]:
                raise ValueError("Setpoint outside simulated limits")
        async with room.lock:
            task = asyncio.current_task()
            self.operations.add(task)
            start = time.monotonic()
            try:
                if action:
                    room.policy.desired({FIELDS[action]: value})
                async with asyncio.timeout(room.config.queries.timeout_seconds):
                    async with self.capacity:
                        if self.closing:
                            raise RoomUnavailable("Application closing")
                        async def exchange():
                            if action:
                                await room.backend.apply(action, value)
                                room.stats["writes"] += 1
                            return await room.backend.snapshot(room.config.queries.fields)
                        state = await exchange()
                        intent = room.policy.observe(state, time.monotonic())
                        if intent:
                            self.store.append(room_id, int(time.time() * 1000), state)
                            # The only automatic correction path is physically incapable
                            # of reaching BLE. Never accept another backend here.
                            if type(room.backend) is not Simulator:
                                raise RoomUnavailable("Automatic correction backend rejected")
                            key, desired = intent
                            await room.backend.apply(ACTIONS[key], desired)
                            room.stats["writes"] += 1
                            state = await room.backend.snapshot(room.config.queries.fields)
                            room.policy.observe(state, time.monotonic(), allow_correction=False)
                room.latest = state
                room.updated_at = time.time()
                room.updated_monotonic = time.monotonic()
                room.stats["reads"] += 1
                self.store.append(room_id, int(room.updated_at * 1000), state)
            except asyncio.CancelledError:
                room.stats["cancellations"] += 1
                room.failure = "Simulated operation cancelled; recover manually"
                self.stop_policy(room, "operation_cancelled")
                raise
            except Exception:
                room.stats["errors"] += 1
                room.failure = "Simulated operation failed; recover manually"
                self.stop_policy(room, "operation_failed")
                log.exception("Room operation failed for logical id %s", room_id)
                raise RoomUnavailable(room.failure) from None
            finally:
                room.stats["last_duration_ms"] = round((time.monotonic() - start) * 1000, 1)
                self.operations.discard(task)
        return self.snapshot(room_id)

    async def simulate(self, room_id: str, status: str, delay_seconds: float = 0):
        room = self.room(room_id)
        if self.closing or not room.config.enabled:
            raise RoomUnavailable("Controller disabled or application closed")
        if room.lock.locked():
            raise RoomBusy("Controller busy")
        async with room.lock:
            room.backend.status = status
            room.backend.delay_seconds = delay_seconds
            room.updated_monotonic = None
            if status != "online":
                room.failure = f"Simulated {status}; recover manually"
                self.stop_policy(room, "simulated_" + status)
            else:
                try:
                    room.policy.save("simulation_recovered", {"source": "authorized_local_api"})
                except Exception:
                    room.failure = "Recovery could not be persisted; review storage"
                    self.stop_policy(room, "recovery_persistence_failed")
                    raise RoomUnavailable(room.failure) from None
                room.failure = None
        if status == "online":
            return await self.operate(room_id)
        return self.snapshot(room_id)

    async def administer(self, room_id: str, change, body):
        room = self.room(room_id)
        if self.closing or not room.config.enabled:
            raise RoomUnavailable("Controller disabled or closed")
        if room.lock.locked():
            raise RoomBusy("Controller busy")
        async with room.lock:
            try:
                if change == "policy":
                    room.policy.configure(body)
                elif change == "desired":
                    room.policy.desired(body.values())
                elif change == "restrictions":
                    room.policy.administrative(body)
                elif change == "reset":
                    room.policy.reset(body.reason)
            except Exception:
                room.failure = "Administration could not be persisted; recover manually"
                self.stop_policy(room, "administration_persistence_failed")
                raise RoomUnavailable(room.failure) from None
        return self.snapshot(room_id)

    async def external_change(self, room_id: str, values: dict):
        room = self.room(room_id)
        if self.closing or not room.config.enabled:
            raise RoomUnavailable("Controller disabled or closed")
        if room.lock.locked():
            raise RoomBusy("Controller busy")
        async with room.lock:
            for key, value in values.items():
                if key == "setpoint":
                    room.backend.state.update(setpoint=value, cooling_setpoint=value, heating_setpoint=value)
                else:
                    room.backend.state[key] = value
            try:
                room.policy.save("external_change_simulated", {"fields": list(values), "origin": "unknown"})
            except Exception:
                room.failure = "Simulated change could not be audited; review storage"
                self.stop_policy(room, "external_change_persistence_failed")
                raise RoomUnavailable(room.failure) from None
        # Observe through the same coordinator; no update to desired.
        if room.failure:
            return self.snapshot(room_id)
        return await self.operate(room_id)

    async def poll(self, room_id: str, offset: float):
        room = self.room(room_id)
        await asyncio.sleep(offset)
        while not self.closing:
            try:
                if not room.failure:
                    await self.operate(room_id)
            except (RoomBusy, RoomUnavailable):
                pass
            await asyncio.sleep(room.config.queries.interval_seconds)

    async def start(self):
        self.pollers = [asyncio.create_task(self.poll(d.id, i * self.config.stagger_seconds))
                        for i, d in enumerate(self.config.devices) if d.enabled]

    async def close(self):
        self.closing = True
        tasks = set(self.pollers) | self.operations
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        results = await asyncio.gather(*(r.backend.close() for r in self.rooms.values()), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise RuntimeError("Some simulated backends could not close")
