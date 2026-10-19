"""Independent simulated rooms, bounded coordination and staggered polling.

The optional fleet accepts closed wire models or explicit native read-only admission.
The backend boundary is an async snapshot/apply/close interface, not BLE itself.
"""
import asyncio
import copy
from contextlib import asynccontextmanager
import logging
import time
from dataclasses import dataclass, field

from madoka_protocol import round_setpoint
from .config import AppConfig, DeviceConfig
from .storage import RoomStore
from .policies import PolicyEngine, FIELDS, ACTIONS
from .backends.base import Backend, BackendUnavailable as RoomUnavailable
from .backends.simulator import Simulator

log = logging.getLogger(__name__)


class RoomBusy(Exception):
    pass




@dataclass
class Room:
    config: DeviceConfig
    backend: Backend
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    latest: dict = field(default_factory=dict)
    updated_at: float | None = None
    updated_monotonic: float | None = None
    failure: str | None = None
    manual_error: str | None = None
    policy: PolicyEngine | None = None
    stats: dict = field(default_factory=lambda: {"reads": 0, "writes": 0, "errors": 0,
                                               "cancellations": 0, "last_duration_ms": None})


class RoomManager:
    def __init__(self, config: AppConfig, store: RoomStore, *, offline_fleet=None):
        self.offline_fleet = offline_fleet
        self.native_readonly = False
        self.native_office = False
        if offline_fleet is not None:
            from .backends.physical_fleet import ScriptedPhysicalFleet
            from .backends.native_fleet import NativeReadonlyFleet
            from .backends.office_native import OfficeFleet
            from .local_runtime import LocalFleet
            self.native_office = type(offline_fleet) in (OfficeFleet, LocalFleet)
            self.native_readonly = type(offline_fleet) in (NativeReadonlyFleet, OfficeFleet, LocalFleet)
            if type(offline_fleet) not in (ScriptedPhysicalFleet, NativeReadonlyFleet, OfficeFleet, LocalFleet):
                raise ValueError("Only the closed offline physical fleet is accepted")
            offline_fleet.validate_config(config)
        self.local = bool(offline_fleet and getattr(offline_fleet, "local", False))
        self.config = config
        self.store = store
        store.register(config.devices)
        self.rooms = {d.id: Room(d, offline_fleet.backends[d.id] if offline_fleet else Simulator(i))
                      for i, d in enumerate(config.devices)}
        for room_id, room in self.rooms.items():
            room.policy = PolicyEngine(store, room_id)
        self.capacity = asyncio.Semaphore(offline_fleet.budget.plan.max_operations
            if offline_fleet else config.max_concurrent_operations)
        self.pollers = []
        self.operations = set()
        self.closing = False

    def room(self, room_id: str) -> Room:
        return self.rooms[room_id]

    def snapshot(self, room_id: str) -> dict:
        room = self.room(room_id)
        age = None if room.updated_monotonic is None else max(0, time.monotonic() - room.updated_monotonic)
        link_unavailable = bool(self.offline_fleet and room.backend.claimed and not room.backend.port.ready)
        stale = link_unavailable or age is None or age > self.config.stale_seconds or bool(room.failure) or self.closing
        enabled = room.config.enabled
        last_operation = self.offline_fleet.budget.last_operation(room_id) if self.native_office else None
        connected = enabled and not self.closing and (
            bool(room.backend.claimed and room.backend.port.ready) if self.local else not stale)
        return {"id": room.config.id, "name": room.config.name, "enabled": enabled,
                "connected": connected, "stale": stale,
                "updated_at": room.updated_at, "age_seconds": age,
                "stale_after_seconds": self.config.stale_seconds,
                "error": room.failure or room.manual_error or (("Se ha perdido la conexión; reconectando sin repetir órdenes" if self.local else "Prepared link unavailable; review required") if link_unavailable else None), "state": copy.deepcopy(room.latest),
                "info": dict(room.backend.info), "stats": dict(room.stats),
                "controls_enabled": (not self.native_readonly or self.native_office) and enabled and not stale and not room.lock.locked(),
                "manual_controls": room.backend.control_view() if self.native_office else None,
                "last_operation": last_operation,
                "queries": room.config.queries.model_dump(), "simulated": not self.native_readonly,
                "transport": "native-local" if self.local else "native-office" if self.native_office else "native-readonly" if self.native_readonly else "offline-controller-contract" if self.offline_fleet else "simulator",
                "session_state": room.backend.state.value if self.offline_fleet else "simulated",
                **({"local_controls": room.backend.controls.model_dump(mode="json"),
                    "diagnostic": room.backend.last_manual_diagnostic,
                    "connection_status": "Aplicando cambio" if last_operation and last_operation["status"] == "pending" and room.lock.locked() else
                    "Conectado · lectura pendiente" if connected and stale else
                    "Conectado" if connected else "Conectando" if room.lock.locked() else "Desconectado"}
                    if self.local else {}),
                "physical_enforce_enabled": False,
                "hardware_enabled": self.native_readonly and self.offline_fleet.admitted(room_id),
                "protection": room.policy.view()}

    def stop_policy(self, room, reason):
        try:
            room.policy.stop(reason)
        except Exception:
            log.exception("Could not persist STOP for logical id %s", room.config.id)
            # stop() keeps the in-memory latch even if SQLite is unavailable.

    @asynccontextmanager
    async def operation_window(self, seconds):
        # Another room can be pairing for 120s. Waiting for its global slot is
        # not this ready room's wire operation and must not trip its deadline.
        if self.native_office:
            async with self.capacity:
                async with asyncio.timeout(seconds):
                    yield
        else:
            async with asyncio.timeout(seconds):
                async with self.capacity:
                    yield

    async def operate(self, room_id: str, action: str | None = None, value=None):
        if self.native_readonly and action is not None:
            raise RoomUnavailable("Native runtime is read-only")
        room = self.room(room_id)
        if self.closing or not room.config.enabled or room.failure:
            raise RoomUnavailable("Controller disabled, stopped or failed")
        if room.lock.locked():
            raise RoomBusy("Controller has an operation in progress")
        if self.offline_fleet and not self.offline_fleet.admitted(room_id):
            raise RoomUnavailable("Explicit session admission required; no lazy connection")
        if action and self.offline_fleet:
            if self.snapshot(room_id)["stale"]:
                raise RoomUnavailable("Fresh observed state required for manual commands")
            room.backend.validate_action(action, value)
        if action == "setpoint":
            value = round_setpoint(value)
            if not room.latest.get("min_setpoint", 16.0) <= value <= room.latest.get("max_setpoint", 32.0):
                raise ValueError("Setpoint outside simulated limits")
        async with room.lock:
            task = asyncio.current_task()
            self.operations.add(task)
            start = time.monotonic()
            try:
                if action:
                    room.policy.desired({FIELDS[action]: value})
                async with self.operation_window(30 if self.native_readonly else room.config.queries.timeout_seconds):
                    if self.closing:
                        raise RoomUnavailable("Application closing")
                    async def exchange():
                        if action:
                            await room.backend.apply(action, value)
                            room.stats["writes"] += 1
                        return await room.backend.snapshot(room.config.queries.fields)
                    state = await exchange()
                    intent = room.policy.observe(state, time.monotonic(), allow_correction=self.offline_fleet is None)
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
                if self.native_office:
                    self.offline_fleet.note_healthy(room_id)
                self.store.append(room_id, int(room.updated_at * 1000), state)
            except asyncio.CancelledError:
                room.stats["cancellations"] += 1
                room.failure = "Operation cancelled; recover manually"
                self.stop_policy(room, "operation_cancelled")
                raise
            except Exception as exc:
                room.stats["errors"] += 1
                if self.local:
                    from .local_runtime import local_error
                    room.failure = local_error(exc)
                else:
                    room.failure = "Operation failed; recover manually"
                self.stop_policy(room, "operation_failed")
                log.exception("Room operation failed for logical id %s", room_id)
                raise RoomUnavailable(room.failure) from None
            finally:
                room.stats["last_duration_ms"] = round((time.monotonic() - start) * 1000, 1)
                self.operations.discard(task)
        return self.snapshot(room_id)

    async def prepare_room(self, room_id):
        """Explicit admission outside ordinary request deadlines; never a retry."""
        if self.offline_fleet is None or self.closing:
            raise RoomUnavailable("No offline contract fleet available")
        room = self.room(room_id)
        if not room.config.enabled or room.failure or room.lock.locked():
            raise RoomBusy("Room unavailable for admission")
        if self.native_readonly and not self.native_office:
            self.offline_fleet.consume_admission(room_id)
        async with room.lock:
            task = asyncio.current_task()
            self.operations.add(task)
            try:
                await room.backend.prepare()
            except BaseException:
                if room.backend.claimed:
                    room.failure = "Session preparation failed; supervised review required"
                    self.stop_policy(room, "session_preparation_failed")
                raise
            finally:
                self.operations.discard(task)

    async def simulate(self, room_id: str, status: str, delay_seconds: float = 0):
        room = self.room(room_id)
        if self.offline_fleet and type(room.backend) is not Simulator:
            raise RoomUnavailable("Simulator administration unavailable for Controller contracts")
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
        if self.native_readonly and not self.native_office:
            raise RoomUnavailable("Native administration and desired-state changes are disabled")
        room = self.room(room_id)
        if self.closing or not room.config.enabled:
            raise RoomUnavailable("Controller disabled or closed")
        if room.lock.locked():
            raise RoomBusy("Controller busy")
        async with room.lock:
            try:
                if self.offline_fleet and change == "policy" and (body.mode == "ENFORCE" or body.allow_auto_on):
                    raise RoomUnavailable("Physical contract ENFORCE and auto-on are disabled")
                if self.native_office and change in ("desired", "reset"):
                    raise RoomUnavailable("Native intent requires a manual operation; automatic corrections remain disabled")
                if change == "policy":
                    room.policy.configure(body)
                elif change == "desired":
                    if self.offline_fleet:
                        for key, value in body.values().items():
                            room.backend.validate_action(ACTIONS[key], value)
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
        if self.offline_fleet:
            raise RoomUnavailable("Simulator mutations unavailable for Controller contracts")
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

    def manual_failure(self, room):
        message = ("No se pudo confirmar el cambio. No se repetirá; consulta el estado antes de otra orden."
            if self.local else "STOP: physical outcome uncertain; inspect recorded operation and GET observations")
        if self.local:
            room.manual_error = message
        else:
            room.failure = message
        self.stop_policy(room, "native_manual_stopped")
        return RoomUnavailable(message)

    @asynccontextmanager
    async def manual_window(self):
        if self.local:
            # Expire only an unstarted waiter. Once admitted, the backend gets
            # its whole 90-second execution budget, irrespective of queue age.
            async with asyncio.timeout(30):
                await self.capacity.acquire()
            try:
                yield
            finally:
                self.capacity.release()
        else:
            async with self.capacity:
                yield

    async def native_manual(self, room_id, action, value, operation_id, reason):
        if not self.native_office:
            raise RoomUnavailable("Office native control unavailable")
        room = self.room(room_id)
        if (self.closing or room.failure or not room.config.enabled or self.snapshot(room_id)["stale"]
                or not self.offline_fleet.admitted(room_id)):
            raise RoomUnavailable((room.failure or "Todavía no hay una lectura reciente del Daikin; espera a que conecte")
                if self.local else "Fresh admitted native observations required")
        if room.lock.locked():
            raise RoomBusy("Native room busy")
        if self.offline_fleet.budget.pending_operation(room_id):
            raise RoomUnavailable("No se repetirá el cambio pendiente; consulta el estado" if self.local else "Unresolved write; no replay")
        room.backend.validate_action(action, value)
        if self.offline_fleet.budget.operation(operation_id):
            raise RoomBusy("Operation identity already consumed; inspect its recorded outcome")
        async with room.lock:
            task = asyncio.current_task()
            self.operations.add(task)
            try:
                async with self.manual_window():
                    state = await room.backend.manual(action, value, operation_id, reason,
                        lambda field, desired: room.policy.desired({field: desired}))
                room.latest, room.updated_at, room.updated_monotonic = state, time.time(), time.monotonic()
                room.stats["writes"] += 1
                self.store.append(room_id, int(room.updated_at * 1000), state)
                room.policy.observe(state, time.monotonic(), allow_correction=False)
                room.policy.save("manual_native_confirmed", {"operation_id": operation_id, "action": action})
            except TimeoutError:
                if self.local and not self.offline_fleet.budget.operation(operation_id):
                    raise RoomBusy("Las comunicaciones están ocupadas; no se ha enviado el cambio") from None
                raise self.manual_failure(room) from None
            except ValueError:
                if self.local and not self.offline_fleet.budget.operation(operation_id):
                    # Fresh pre-read rejected a value, with no SET or journal.
                    room.latest = copy.deepcopy(room.backend.latest)
                    room.updated_at, room.updated_monotonic = time.time(), time.monotonic()
                    raise
                raise self.manual_failure(room) from None
            except (Exception, asyncio.CancelledError):
                raise self.manual_failure(room) from None
            finally:
                self.operations.discard(task)
        return self.snapshot(room_id)

    async def native_recover(self, room_id, reason):
        """Explicit read-only reconciliation of uncertain intent; never replay SET."""
        if not self.native_office or self.closing or not 3 <= len(reason.strip()) <= 160:
            raise RoomUnavailable("Explicit office recovery required")
        room = self.room(room_id)
        if not room.config.enabled or room.lock.locked():
            raise RoomBusy("Room unavailable")
        async with room.lock:
            task = asyncio.current_task()
            self.operations.add(task)
            try:
                async with self.preparation_window():
                    try:
                        await self.offline_fleet.ensure_room(room_id, explicit=True)
                    finally:
                        room.backend = self.offline_fleet.backends[room_id]
                async with self.capacity:
                    state = await room.backend.snapshot(room.config.queries.fields)
                self.offline_fleet.budget.acknowledge_operations(room_id, state, reason)
                room.policy.save("native_outcome_reviewed", {"reason": reason, "source": "authorized_operator", "state": state})
                room.latest, room.failure, room.manual_error = state, None, None
                room.updated_at, room.updated_monotonic = time.time(), time.monotonic()
                self.store.append(room_id, int(room.updated_at * 1000), state)
            finally:
                self.operations.discard(task)
        return self.snapshot(room_id)

    @asynccontextmanager
    async def preparation_window(self):
        if self.local:
            yield  # Local backend owns its separate bounded setup semaphore.
        else:
            async with self.capacity:
                yield

    async def poll(self, room_id: str, offset: float):
        room = self.room(room_id)
        await asyncio.sleep(offset)
        while not self.closing:
            try:
                if self.local and room.failure and self.offline_fleet.admitted(room_id) and not self.offline_fleet.budget.pending_operation(room_id):
                    room.failure = None
                if self.native_office and not self.offline_fleet.admitted(room_id):
                    if room.lock.locked():
                        raise RoomBusy("Room busy")
                    async with room.lock:
                        async with self.preparation_window():
                            try:
                                await self.offline_fleet.ensure_room(room_id)
                            finally:
                                room.backend = self.offline_fleet.backends[room_id]
                            room.failure = None
                if not room.failure and (self.offline_fleet is None or self.offline_fleet.admitted(room_id)):
                    await self.operate(room_id)
            except (RoomBusy, RoomUnavailable) as exc:
                if self.native_office and not self.offline_fleet.admitted(room_id):
                    room.failure = str(exc) if self.local else "Connection stopped or backing off; review native ownership"
            await asyncio.sleep(room.config.queries.interval_seconds)

    async def start(self):
        if self.offline_fleet:
            for room_id in self.offline_fleet.initial_admissions:
                await self.prepare_room(room_id)
        self.pollers = [asyncio.create_task(self.poll(d.id, i * self.config.stagger_seconds))
                        for i, d in enumerate(self.config.devices) if d.enabled]

    async def close(self):
        self.closing = True
        backends = list(self.offline_fleet.backends.values() if self.native_office else (r.backend for r in self.rooms.values()))
        if self.native_office:
            for backend in backends:
                backend.lifecycle_context = 'application_shutdown'
        tasks = set(self.pollers) | self.operations
        tasks.discard(asyncio.current_task())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        results = await asyncio.gather(*(b.close() for b in backends), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise RuntimeError("Some backend cleanup remains uncertain; session records retained")
