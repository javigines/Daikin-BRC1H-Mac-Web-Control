"""Offline-only preparation for the hardened Controller request boundary.

No driver import, scan, connect, reconnect, address or physical factory exists.
Only our closed data-only ScriptedController is accepted in this iteration.
"""
import asyncio
import copy
from typing import Protocol

from madoka_protocol import (CMD_SET_POWER, CMD_SET_MODE, CMD_SET_FAN,
                             CMD_SET_SETPOINT, build_setpoint_args)
from ..policies import DesiredPatch
from .base import BackendUnavailable
from .daikin_mapping import commands_for, decode_snapshot, selected_fields
from .offline_port import ScriptedController


class ControllerPort(Protocol):
    """Future bridge shape, matching Controller from PR #1; not an activation API."""
    async def request(self, cmd_id: int, args=None, timeout=6.0) -> dict: ...
    async def disconnect(self) -> None: ...


class PhysicalBackendDisabled(BackendUnavailable):
    pass


class BackendBusy(BackendUnavailable):
    pass


class WriteNotConfirmed(BackendUnavailable):
    pass


class SessionBudget:
    """Admission bound for retained sessions, separate from exchange capacity."""
    def __init__(self, max_active=1, max_operations=1):
        if type(max_active) is not int or type(max_operations) is not int or not 1 <= max_active <= 6 or not 1 <= max_operations <= 6:
            raise ValueError("Capacity must be an integer between one and six")
        self.max_active = max_active
        self.operations = asyncio.Semaphore(max_operations)
        self.owners = set()

    def claim(self, port):
        if id(port) in self.owners:
            raise BackendBusy("Controller double already owned")
        if len(self.owners) >= self.max_active:
            raise BackendBusy("Active-session capacity reached")
        self.owners.add(id(port))

    def release(self, port):
        self.owners.discard(id(port))


class DaikinBackend:
    def __init__(self, controller: ControllerPort, budget: SessionBudget, *, timeout=5.0):
        if type(controller) is not ScriptedController:
            raise PhysicalBackendDisabled("Only the data-only offline Controller double is permitted")
        if type(timeout) not in (int,float) or not .1 <= timeout <= 30:
            raise ValueError("Invalid operation timeout")
        self.port, self.budget, self.timeout = controller, budget, timeout
        self.lock = asyncio.Lock()
        self.claimed = self.closed = self.stopped = False
        self.latest = {}
        self._cleanly_closed = False
        self.info = {"model":None,"controller_version":None,"comm_version":None,
                     "backend":"offline-daikin-adapter","hardware_enabled":False}

    @classmethod
    def from_physical_controller(cls, *args, **kwargs):
        raise PhysicalBackendDisabled("Physical adapter activation requires a separately reviewed change")

    def admit(self):
        if self.closed or self.stopped:
            raise BackendUnavailable("Adapter stopped; operator review required")
        if self.lock.locked():
            raise BackendBusy("Adapter operation in progress")
        if not self.claimed:
            self.budget.claim(self.port)
            self.claimed = True

    async def _request(self, cmd, args=None):
        if self.closed or self.stopped:
            raise BackendUnavailable("Adapter stopped before exchange")
        result = await self.port.request(cmd,args,timeout=self.timeout)
        if self.closed or self.stopped:
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
        async with self.lock:
            if self._cleanly_closed:
                return
            if not self.claimed:
                self._cleanly_closed = True
                return
            try:
                async with asyncio.timeout(self.timeout):
                    await self.port.disconnect()
                    self._cleanly_closed = True
            finally:
                if self.claimed:
                    self.budget.release(self.port)
                    self.claimed = False
                self.latest = {}
