"""Offline-only preparation for the hardened Controller request boundary.

No driver import, scan, connect, reconnect, address or physical factory exists.
Only our closed data-only ScriptedController is accepted in this iteration.
"""
import asyncio
import copy
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from madoka_protocol import (CMD_SET_POWER, CMD_SET_MODE, CMD_SET_FAN,
                             CMD_SET_SETPOINT, build_setpoint_args)
from ..policies import DesiredPatch
from .base import BackendUnavailable
from .daikin_mapping import commands_for, decode_snapshot, selected_fields
from .offline_port import ScriptedController


class ControllerPort(Protocol):
    """Prepared-session boundary; raw Controller.request is NOT compatible.

    Preparation must run before coordinator request deadlines. exchange_ready
    must fail on link loss and never initiate setup, scanning or reconnection.
    A future physical bridge needs separate review; none exists here.
    """
    ready: bool
    async def prepare(self) -> None: ...
    async def exchange_ready(self, cmd_id: int, args=None, timeout=6.0) -> dict: ...
    async def disconnect(self) -> None: ...


class PhysicalBackendDisabled(BackendUnavailable):
    pass


class BackendBusy(BackendUnavailable):
    pass


class WriteNotConfirmed(BackendUnavailable):
    pass


class CleanupUncertain(BackendUnavailable):
    """Cleanup is unproven; ordinary close cannot release or retry the session."""


class SessionState(str, Enum):
    NEW = "new"
    PREPARING = "preparing"
    READY = "ready"
    STOPPED = "stopped"
    CLOSING = "closing"
    CLOSED = "closed"
    CLEANUP_UNCERTAIN = "cleanup_uncertain"
    RELEASED_OFFLINE = "released_after_offline_review"


@dataclass
class _Reservation:
    port: ScriptedController
    owner: object
    quarantined: bool = False


@dataclass(frozen=True)
class OfflineResolution:
    reason: str
    evidence: str
    outcome: str = "offline_capacity_release_not_clean_disconnect"

    def __post_init__(self):
        for value in (self.reason, self.evidence):
            if not isinstance(value, str) or not 3 <= len(value.strip()) <= 240:
                raise ValueError("Supervised resolution requires a reason and evidence")


class SessionBudget:
    """Admission bound for retained sessions, separate from exchange capacity."""
    def __init__(self, max_active=1, max_operations=1):
        if type(max_active) is not int or type(max_operations) is not int or not 1 <= max_active <= 6 or not 1 <= max_operations <= 6:
            raise ValueError("Capacity must be an integer between one and six")
        self.max_active = max_active
        self.operations = asyncio.Semaphore(max_operations)
        # Strong references prevent id reuse and retain uncertain ownership.
        self._reservations = {}
        self.resolutions = ()

    @property
    def owners(self):
        return frozenset(self._reservations)

    @property
    def quarantined(self):
        return frozenset(key for key, value in self._reservations.items() if value.quarantined)

    def claim(self, port, owner):
        if type(port) is not ScriptedController:
            raise PhysicalBackendDisabled("Only offline session reservations are supported")
        if id(port) in self._reservations:
            raise BackendBusy("Controller double already owned")
        if len(self._reservations) >= self.max_active:
            raise BackendBusy("Active-session capacity reached")
        self._reservations[id(port)] = _Reservation(port, owner)

    def _owned(self, port, owner):
        reservation = self._reservations.get(id(port))
        if reservation is None or reservation.port is not port or reservation.owner is not owner:
            raise BackendUnavailable("Session reservation owner mismatch")
        return reservation

    def quarantine(self, port, owner):
        self._owned(port, owner).quarantined = True

    def release(self, port, owner):
        if self._owned(port, owner).quarantined:
            raise CleanupUncertain("Quarantined capacity requires supervised resolution")
        del self._reservations[id(port)]

    def resolve_offline(self, port, owner, resolution):
        if type(port) is not ScriptedController:
            raise PhysicalBackendDisabled("Physical recovery is not implemented")
        if not self._owned(port, owner).quarantined:
            raise BackendUnavailable("Only quarantined offline reservations can be resolved")
        if type(resolution) is not OfflineResolution:
            raise ValueError("Offline resolution evidence is required")
        self.resolutions += (resolution,)
        del self._reservations[id(port)]


class DaikinBackend:
    def __init__(self, controller: ControllerPort, budget: SessionBudget, *, timeout=5.0,
                 setup_timeout=60.0, cleanup_timeout=25.0):
        if type(controller) is not ScriptedController:
            raise PhysicalBackendDisabled("Only the data-only offline Controller double is permitted")
        for name, value, upper in (("operation", timeout, 30), ("setup", setup_timeout, 180),
                                   ("cleanup", cleanup_timeout, 60)):
            if type(value) not in (int, float) or not .1 <= value <= upper:
                raise ValueError(f"Invalid {name} timeout")
        self.port, self.budget, self.timeout = controller, budget, timeout
        self.setup_timeout, self.cleanup_timeout = setup_timeout, cleanup_timeout
        self.lock = asyncio.Lock()
        self.claimed = self.closed = self.stopped = False
        self.state = SessionState.NEW
        self.cleanup_error = None
        self.latest = {}
        self._cleanly_closed = False
        self.info = {"model":None,"controller_version":None,"comm_version":None,
                     "backend":"offline-daikin-adapter","hardware_enabled":False}

    @classmethod
    def from_physical_controller(cls, *args, **kwargs):
        raise PhysicalBackendDisabled("Physical adapter activation requires a separately reviewed change")

    def admit(self, *, preparing=False):
        if self.closed or self.stopped:
            raise BackendUnavailable("Adapter stopped; operator review required")
        if self.lock.locked():
            raise BackendBusy("Adapter operation in progress")
        if not preparing and self.state is not SessionState.READY:
            raise BackendUnavailable("Explicit preparation is required before operations")
        if not self.claimed:
            self.budget.claim(self.port, self)
            self.claimed = True

    async def prepare(self):
        """One explicit setup phase, outside adapter and manager operation timers.

        Sixty seconds is an offline planning default for 12+20+20s setup, not
        native CoreBluetooth certification. Caller cancellation still stops it.
        """
        self.admit(preparing=True)
        async with self.lock:
            if self.state is SessionState.READY:
                return
            self.state = SessionState.PREPARING
            try:
                async with asyncio.timeout(self.setup_timeout):
                    async with self.budget.operations:
                        if self.closed:
                            raise BackendUnavailable("Adapter closing before preparation")
                        await self.port.prepare()
                        if self.closed or not self.port.ready:
                            raise BackendUnavailable("Preparation did not establish a ready session")
                        self.state = SessionState.READY
            except (Exception, asyncio.CancelledError):
                self.stopped = True
                if not self.closed:
                    self.state = SessionState.STOPPED
                self.latest = {}
                raise

    async def _request(self, cmd, args=None):
        if self.closed or self.stopped:
            raise BackendUnavailable("Adapter stopped before exchange")
        if self.state is not SessionState.READY or not self.port.ready:
            raise BackendUnavailable("Prepared session unavailable; no lazy setup allowed")
        result = await self.port.exchange_ready(cmd,args,timeout=self.timeout)
        if self.closed or self.stopped or not self.port.ready:
            raise BackendUnavailable("Adapter stopped during exchange")
        return result

    async def _read(self, fields):
        responses = {}
        for cmd in commands_for(fields):
            responses[cmd] = await self._request(cmd)
        return decode_snapshot(responses, fields)

    async def _execute(self, operation):
        self.admit()
        async with self.lock:
            try:
                async with asyncio.timeout(self.timeout):
                    async with self.budget.operations:
                        if self.closed:
                            raise BackendUnavailable("Adapter closing")
                        result = await operation()
                        if self.closed:
                            raise BackendUnavailable("Adapter closed during operation")
                        return result
            except (Exception, asyncio.CancelledError):
                self.stopped = True
                if not self.closed:
                    self.state = SessionState.STOPPED
                self.latest = {}
                raise

    async def snapshot(self, fields):
        fields = selected_fields(fields)
        async def operation():
            state = await self._read(fields)
            self.latest = copy.deepcopy(state)
            return state
        return await self._execute(operation)

    async def apply(self, action, value):
        mapping = {"power":("power_on",CMD_SET_POWER,"power"),
                   "mode":("mode_id",CMD_SET_MODE,"mode"),
                   "fan":("fan_id",CMD_SET_FAN,"fan"),
                   "setpoint":("setpoint",CMD_SET_SETPOINT,"setpoint")}
        if action not in mapping:
            raise ValueError("Unsupported action")
        field,cmd,group = mapping[action]
        value = DesiredPatch.model_validate({field:value}).values()[field]
        async def operation():
            if action == "setpoint":
                before = await self._read(("setpoint",))
                if not before["min_setpoint"] <= value <= before["max_setpoint"]:
                    raise ValueError("Target outside reported limits")
                args = build_setpoint_args(value,value)
            elif action == "fan":
                args = [(0x20,bytes([value])),(0x21,bytes([value]))]
            else:
                args = [(0x20,bytes([int(value)]))]
            await self._request(cmd,args)
            observed = await self._read((group,))
            if observed.get(field) != value or (action == "setpoint" and any(observed[k] != value for k in ("cooling_setpoint","heating_setpoint"))):
                raise WriteNotConfirmed("Readback does not confirm requested effect")
            self.latest = copy.deepcopy(observed)
        await self._execute(operation)

    async def close(self):
        # Mark closed before waiting, so queued work cannot start new exchanges.
        self.closed = self.stopped = True
        self.latest = {}
        try:
            async with self.lock:
                if self.state in (SessionState.CLEANUP_UNCERTAIN, SessionState.RELEASED_OFFLINE):
                    raise CleanupUncertain("Cleanup remains unproven; no automatic retry")
                if self._cleanly_closed:
                    return
                self.state = SessionState.CLOSING
                if self.claimed:
                    async with asyncio.timeout(self.cleanup_timeout):
                        await self.port.disconnect()
                    # Only successful data-only cleanup releases ordinary capacity.
                    self.budget.release(self.port, self)
                    self.claimed = False
                self.state = SessionState.CLOSED
                self._cleanly_closed = True
        except (Exception, asyncio.CancelledError) as exc:
            if self.claimed:
                self.budget.quarantine(self.port, self)
                self.state = SessionState.CLEANUP_UNCERTAIN
                self.cleanup_error = self.cleanup_error or type(exc).__name__
            raise

    def resolve_offline_cleanup(self, *, reason, evidence):
        """Explicit simulation of supervised release; never a clean-close claim.

        This has no CLI/API route and cannot resolve physical sessions. A real
        recovery protocol needs durable quarantine and verified native evidence.
        """
        if self.lock.locked() or self.state is not SessionState.CLEANUP_UNCERTAIN:
            raise BackendUnavailable("No idle quarantined session to resolve")
        for text in (reason, evidence):
            if not isinstance(text, str) or not 3 <= len(text.strip()) <= 240:
                raise ValueError("Supervised resolution requires a reason and evidence")
        resolution = OfflineResolution(reason.strip(), evidence.strip())
        self.budget.resolve_offline(self.port, self, resolution)
        self.claimed = False
        self.state = SessionState.RELEASED_OFFLINE
        return resolution
