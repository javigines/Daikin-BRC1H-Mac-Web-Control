"""Six-room physical contract integration, with no executable hardware factory."""
from dataclasses import dataclass
import re

from .base import BackendUnavailable
from .daikin import DaikinBackend, PhysicalBackendDisabled, WriteNotConfirmed, SessionState
from .daikin_mapping import commands_for, decode_snapshot, selected_fields, GROUPS
from .controller_bridge import HardenedControllerBridge, ScriptedRadio
from .physical_sessions import PhysicalSessionBudget


@dataclass(frozen=True)
class DeviceCapabilities:
    fields: tuple = ("power", "mode", "setpoint", "fan", "sensor")
    modes: tuple = (0, 1, 2, 3, 4, 5)
    fan_speeds: tuple = (1, 2, 3, 4, 5)

    def __post_init__(self):
        selected_fields(self.fields)
        if (not self.modes or any(type(v) is not int or v not in range(6) for v in self.modes)
                or not self.fan_speeds or any(type(v) is not int or v not in range(1, 6) for v in self.fan_speeds)):
            raise ValueError("Invalid supported control values")


class PreparedDaikinBackend(DaikinBackend):
    def __init__(self, room_id, controller, budget, *, capabilities=DeviceCapabilities(), **kwargs):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", room_id) or type(capabilities) is not DeviceCapabilities:
            raise ValueError("Stable room identity and capabilities required")
        self.room_id, self.capabilities = room_id, capabilities
        kwargs.setdefault("setup_timeout", 140)
        super().__init__(controller, budget, **kwargs)
        self.info.update(backend="hardened-controller-contract-offline", hardware_enabled=False,
                         supported_fields=list(capabilities.fields), native_state="unverified")

    @staticmethod
    def validate_port(controller, budget):
        if type(controller) is not HardenedControllerBridge or type(budget) is not PhysicalSessionBudget:
            raise PhysicalBackendDisabled("Only the closed bridge and durable budget are accepted")

    async def prepare(self):
        already_ready = self.state is SessionState.READY
        if already_ready and not self.port.ready:
            self.stopped = True
            self.state = SessionState.STOPPED
            self.budget.quarantine(self.port, self)
            raise BackendUnavailable("Prepared link lost; no re-admission or reconnection")
        try:
            await super().prepare()
        except BaseException:
            if self.claimed and self.stopped:
                self.budget.quarantine(self.port, self)
            raise
        if not already_ready:
            try:
                self.budget.transition(self.port, self, "ready")
            except BaseException:
                self.stopped = True
                self.state = SessionState.STOPPED
                raise

    async def _execute(self, operation):
        try:
            return await super()._execute(operation)
        except BaseException:
            if self.claimed and self.stopped:
                self.budget.quarantine(self.port, self)
            raise

    async def _read(self, fields):
        responses = {}
        for cmd in commands_for(fields):
            responses[cmd] = await self._request(cmd)
            group = next(group for group, command in GROUPS.items() if command == cmd)
            decode_snapshot(responses, (group,), strict_fan=True)
        return decode_snapshot(responses, fields, strict_fan=True)

    async def snapshot(self, fields):
        fields = selected_fields(fields)
        if not set(fields) <= set(self.capabilities.fields):
            raise ValueError("Unsupported query fields")
        return await super().snapshot(fields)

    def validate_action(self, action, value):
        from ..policies import DesiredPatch, FIELDS
        if action not in FIELDS:
            raise ValueError("Unsupported manual action")
        value = DesiredPatch.model_validate({FIELDS[action]: value}).values()[FIELDS[action]]
        if action not in self.capabilities.fields or action == "sensor":
            raise ValueError("Unsupported manual command")
        if action == "mode" and value not in self.capabilities.modes:
            raise ValueError("Unsupported device mode")
        if action == "fan" and value not in self.capabilities.fan_speeds:
            raise ValueError("Unsupported device fan speed")
        return value

    async def apply(self, action, value):
        value = self.validate_action(action, value)
        await super().apply(action, value)

    def validate_readback(self, action, value, observed):
        if action == "fan" and any(observed[k] != value for k in ("cooling_fan_id", "heating_fan_id")):
            raise WriteNotConfirmed("Both fan fields must confirm the SET")


class ScriptedPhysicalFleet:
    """Explicit offline admission; no start/close operation rotates connections.

    Six bindings can exist with only one admitted session. Higher capacities
    evaluate the design only on scripted radios, never authorize hardware.
    """
    def __init__(self, config, budget, radios, *, capabilities=None, initial_admissions=()):
        if type(budget) is not PhysicalSessionBudget or set(radios) != {d.id for d in config.devices}:
            raise ValueError("Exact fleet bindings and durable budget required")
        self.config, self.budget = config, budget
        self.backends = {}
        for device in config.devices:
            radio = radios[device.id]
            if type(radio) is not ScriptedRadio or radio.identity != device.address:
                raise PhysicalBackendDisabled("Room UUID mismatch or non-scripted radio")
            caps = (capabilities or {}).get(device.id, DeviceCapabilities())
            if not set(device.queries.fields) <= set(caps.fields):
                raise ValueError("Configured reads exceed device capabilities")
            self.backends[device.id] = PreparedDaikinBackend(device.id,
                HardenedControllerBridge(device.address, radio), budget,
                capabilities=caps, timeout=device.queries.timeout_seconds)
        if len(set(initial_admissions)) != len(initial_admissions) or not set(initial_admissions) <= set(self.backends):
            raise ValueError("Invalid explicit initial admission list")
        self.initial_admissions = tuple(initial_admissions)

    def validate_config(self, config):
        if config != self.config:
            raise ValueError("Fleet must use the exact configured identities")

    @classmethod
    def from_physical(cls, *args, **kwargs):
        raise PhysicalBackendDisabled("Physical fleet activation is disabled")

    def admitted(self, room_id):
        backend = self.backends[room_id]
        return backend.state is SessionState.READY and not backend.closed and not backend.stopped
