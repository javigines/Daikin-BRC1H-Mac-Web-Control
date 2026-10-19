"""Six native rooms using the existing serialized Controller and durable ledger.

Writes are explicit, journaled before transmission and never replayed. A normal
verified close can release APP ownership. Unproven cleanup remains quarantined.
"""
import copy
import math
import time
from uuid import UUID

from madoka_protocol import (
    CMD_GET_POWER, CMD_GET_MODE, CMD_GET_SETPOINT, CMD_GET_FAN, CMD_GET_SENSOR,
    CMD_SET_POWER, CMD_SET_MODE, CMD_SET_SETPOINT, CMD_SET_FAN,
    SETPOINT_EXTRA_FIELDS, temp_to_bytes,
)
from madoka_readonly_canary import observation
from ..native_access import require_native_context, manual_transport
from .base import BackendUnavailable
from .controller_bridge import HardenedControllerBridge
from .physical_fleet import PreparedDaikinBackend
from .physical_sessions import PhysicalSessionBudget
from .daikin import SessionState, WriteNotConfirmed


def setpoint_block(objects, mode, value, controls):
    """Preserve every range/mode/limit byte; never use legacy zero defaults."""
    widths = {0x20: 2, 0x21: 2, **{oid: size for oid, size, _ in SETPOINT_EXTRA_FIELDS}}
    if (set(objects) != set(widths) or any(type(objects[k]) is not bytes or len(objects[k]) != size
                                         for k, size in widths.items())):
        raise BackendUnavailable("Unrecognized setpoint schema; commissioning evidence required")
    # Unknown flags/symbols are rejected, not normalized into installer settings.
    if (objects[0x30][0] not in (0, 1) or objects[0x31][0] not in (0, 1, 2)
            or any(objects[k][0] not in (0, 1) for k in (0xA4, 0xA5, 0xB4, 0xB5))):
        raise BackendUnavailable("Unsupported setpoint flags")
    if mode not in (3, 4) or type(value) not in (float, int) or not math.isfinite(value) or value != int(value):
        raise ValueError("Native setpoints require whole degrees and cooling/heating mode")
    target = 0x21 if mode == 4 else 0x20
    lo, hi = (0xA3, 0xB3) if mode == 4 else (0xA2, 0xB2)
    lower, upper = (int.from_bytes(objects[k], "big") / 128 for k in (lo, hi))
    # Zero/absent ranges are not replaced with guessed device capabilities.
    if not 10 <= lower <= upper <= 40:
        raise BackendUnavailable("Device setpoint limits unverified")
    if not max(lower, controls.minimum) <= value <= min(upper, controls.maximum):
        raise ValueError("Setpoint exceeds device/owner limits")
    return [(k, temp_to_bytes(value) if k == target else objects[k]) for k in widths]


class OfficeBackend(PreparedDaikinBackend):
    def __init__(self, room, port, budget, controls):
        self.controls = controls
        self.raw = {}
        super().__init__(room.id, port, budget, timeout=30, setup_timeout=170)
        self.info.update(backend="native-office", hardware_enabled=True,
                         supported_fields=["power", "mode", "setpoint", "sensor", "fan"])

    async def _request(self, cmd, args=None):
        if self.closed or self.stopped or self.state is not SessionState.READY:
            raise BackendUnavailable("Native session stopped")
        result = await self.port.exchange_ready(cmd, args, timeout=6)
        if not self.port.ready:
            raise BackendUnavailable("Link lost during native operation")
        return result

    async def _read(self, fields):
        state, raw = {}, {}
        commands = (CMD_GET_POWER, CMD_GET_MODE, CMD_GET_SETPOINT, CMD_GET_SENSOR)
        for command in commands:
            raw[command] = await self._request(command)
            group = observation(command, raw[command])
            if any(v["status"] != "available" for v in group.values()):
                raise BackendUnavailable("Required native observation unavailable")
            state.update({k: v["value"] for k, v in group.items()})
        mode = state["mode_id"]
        state["setpoint"] = state["heating_setpoint" if mode == 4 else "cooling_setpoint"]
        state["room_temp"] = state.pop("indoor_temperature")
        state["fan_id"] = None
        if self.controls.fans:
            fans = raw[CMD_GET_FAN] = await self._request(CMD_GET_FAN)
            valid = all(k in fans and type(fans[k]) is bytes and len(fans[k]) == 1 and fans[k][0] in range(1, 6) for k in (0x20, 0x21))
            if not valid and not getattr(self.port.radio.grant, "local", False):
                raise BackendUnavailable("Unsupported fan observations")
            if valid:
                state.update(cooling_fan_id=fans[0x20][0], heating_fan_id=fans[0x21][0])
                state["fan_id"] = fans[0x21 if mode == 4 else 0x20][0]
        state["field_status"] = {key: "available" if value is not None else "not_queried" for key, value in state.items()}
        for key, oid in (("min_setpoint", 0xA3 if mode == 4 else 0xA2), ("max_setpoint", 0xB3 if mode == 4 else 0xB2)):
            value = raw[CMD_GET_SETPOINT].get(oid)
            if value and len(value) == 2 and 10 <= int.from_bytes(value, "big") / 128 <= 40:
                state[key] = int.from_bytes(value, "big") / 128
        if "min_setpoint" in state:
            state["device_min_setpoint"] = state["min_setpoint"]
            state["min_setpoint"] = max(state["min_setpoint"], self.controls.minimum)
        if "max_setpoint" in state:
            state["device_max_setpoint"] = state["max_setpoint"]
            state["max_setpoint"] = min(state["max_setpoint"], self.controls.maximum)
        self.raw = raw
        if getattr(self.port.radio.grant, "local", False) and not getattr(self, "local_configured", False):
            # A valid power GET proves the boolean schema; a full setpoint GET
            # proves its active target/limits. Mode/fan allowlists remain local
            # configuration because an enum alone does not prove firmware support.
            controls = self.controls.model_copy(update={"power": True, "setpoint": True})
            self.controls = controls
        return state

    def control_view(self):
        stopped = self.stopped or self.closed or self.budget.pending_operation(self.room_id)
        limits = self.latest
        setpoint = self.controls.setpoint and limits.get("mode_id") in (3, 4)
        if setpoint:
            try:
                candidate = max(self.controls.minimum, min(self.controls.maximum, limits["setpoint"]))
                setpoint_block(self.raw.get(CMD_GET_SETPOINT, {}), limits["mode_id"], candidate, self.controls)
            except (ValueError, BackendUnavailable):
                setpoint = False
        return {"power": self.controls.power and not stopped, "setpoint": bool(setpoint and not stopped),
                "modes": list(self.controls.modes) if not stopped else [],
                "fans": list(self.controls.fans) if not stopped and limits.get("mode_id") in (3, 4)
                    and (not getattr(self.port.radio.grant, "local", False) or limits.get("fan_id") is not None) else []}

    def validate_action(self, action, value):
        if action == "power" and self.controls.power and type(value) is bool:
            return value
        if action == "mode" and type(value) is int and value in self.controls.modes:
            return value
        if action == "fan" and type(value) is int and value in self.controls.fans:
            if self.latest.get("mode_id") not in (3, 4) or (getattr(self.port.radio.grant, "local", False) and self.latest.get("fan_id") is None):
                raise ValueError("Fan control requires cooling/heating mode")
            return value
        if action == "setpoint" and self.controls.setpoint:
            if type(value) not in (int, float) or not math.isfinite(value) or value != int(value):
                raise ValueError("Whole-degree native setpoint required")
            if (not self.control_view()["setpoint"] or not self.latest.get("min_setpoint", 41) <= value <= self.latest.get("max_setpoint", 9)):
                raise ValueError("Setpoint outside commissioned device/owner window")
            return value
        raise ValueError("Control not commissioned for this room")

    async def apply(self, action, value):
        raise BackendUnavailable("Native writes require explicit durable operation identity")

    async def manual(self, action, value, operation_id, reason, authorize_desired):
        if self.stopped or self.closed or self.budget.pending_operation(self.room_id):
            raise BackendUnavailable("Native write stopped or unresolved; no replay")
        value = self.validate_action(action, value)
        if str(UUID(operation_id)) != operation_id or not 3 <= len(reason.strip()) <= 160:
            raise ValueError("Explicit operation identity and reason required")
        if self.budget.operation(operation_id) or self.budget.pending_operation(self.room_id):
            raise BackendUnavailable("Duplicate or unresolved write; STOP, no replay")
        async def operation():
            journaled = False
            try:
                before = await self._read(())
                raw = copy.deepcopy(self.raw)
                if getattr(self.port.radio.grant, "local", False):
                    self.latest = copy.deepcopy(before)
                    try:
                        self.validate_action(action, value)
                    except (ValueError, BackendUnavailable):
                        return {"_local_rejected": True}
                    if before[{"power": "power_on", "mode": "mode_id", "fan": "fan_id", "setpoint": "setpoint"}[action]] == value:
                        field = {"power": "power_on", "mode": "mode_id", "fan": "fan_id", "setpoint": "setpoint"}[action]
                        self.budget.begin_operation(self.port, self, operation_id, action, value,
                            {"state": before, "reason": reason, "already_observed": True,
                             "wire": {str(c): {str(k): v.hex() for k, v in obj.items()} for c, obj in raw.items()}})
                        journaled = True
                        authorize_desired(field, value)
                        self.budget.finish_operation(operation_id, "confirmed", before)
                        return before
                if action == "setpoint":
                    args = setpoint_block(raw[CMD_GET_SETPOINT], before["mode_id"], value, self.controls)
                    command = CMD_SET_SETPOINT
                elif action == "fan":
                    if before["mode_id"] not in (3, 4):
                        raise ValueError("Fan control requires commissioned cooling/heating mode")
                    target = 0x21 if before["mode_id"] == 4 else 0x20
                    args = [(k, bytes([value]) if k == target else raw[CMD_GET_FAN][k]) for k in (0x20, 0x21)]
                    command = CMD_SET_FAN
                else:
                    command = CMD_SET_POWER if action == "power" else CMD_SET_MODE
                    args = [(0x20, bytes([int(value)]))]
                field = {"power": "power_on", "mode": "mode_id", "fan": "fan_id", "setpoint": "setpoint"}[action]
                if before[field] == value:
                    raise ValueError("Requested value already observed; no SET necessary")
                saved = {"state": before, "wire": {str(cmd): {str(k): v.hex() for k, v in objects.items()} for cmd, objects in raw.items()}, "reason": reason}
                self.budget.begin_operation(self.port, self, operation_id, action, value, saved)
                journaled = True
                # An authorized API operation is the only native desired-state
                # source. Both durable stores must succeed before any SET byte.
                authorize_desired(field, value)
                self.port.radio.arm_write(command, args)
                ack = await self._request(command, args)
                if ack not in ({}, {0: b""}):
                    raise WriteNotConfirmed("Unrecognized SET acknowledgement; STOP")
                observed = await self._read(())
                expected = dict(before)
                expected[field] = value
                if action == "setpoint":
                    expected["heating_setpoint" if before["mode_id"] == 4 else "cooling_setpoint"] = value
                    # All accompanying flags and limits must be unchanged.
                    expected_raw = dict(args)
                    if self.raw[CMD_GET_SETPOINT] != expected_raw:
                        raise WriteNotConfirmed("Setpoint block readback mismatch")
                if action == "fan":
                    expected["heating_fan_id" if before["mode_id"] == 4 else "cooling_fan_id"] = value
                # Room temperature can move naturally. Mode/power and both
                # inactive targets/fans must remain unchanged unless requested.
                keys = ("power_on", "mode_id", "cooling_setpoint", "heating_setpoint", "cooling_fan_id", "heating_fan_id")
                if observed.get(field) != value or any(k in expected and observed.get(k) != expected[k] for k in keys):
                    raise WriteNotConfirmed("GET readback did not confirm exactly the intended change")
                self.budget.finish_operation(operation_id, "confirmed", observed)
                self.latest = copy.deepcopy(observed)
                return observed
            except BaseException:
                if journaled:
                    self.budget.finish_operation(operation_id, "uncertain")
                raise
            finally:
                self.port.radio.write_frames.clear()
        result = await self._execute(operation)
        if result.get("_local_rejected"):
            raise ValueError("El estado del Daikin ha cambiado; consulta la lectura antes de ajustar este control")
        return result


class OfficeFleet:
    """Persistent independent rooms; bounded reconnect ONLY after verified close."""
    def __init__(self, office, budget, grants):
        if type(budget) is not PhysicalSessionBudget or not budget.office or budget.native_grants is not grants:
            raise ValueError("Matching office ledger and exact scoped grants required")
        if set(grants) != {d.id for d in office.rooms.devices}:
            raise ValueError("Exact configured office identities required")
        self.office, self.config, self.budget, self.grants = office, office.rooms, budget, grants
        self.initial_admissions = ()
        self.backends = {d.id: self._backend(d) for d in self.config.devices}
        self.retries = {d.id: 0 for d in self.config.devices}
        self.next_attempt = {d.id: 0 for d in self.config.devices}
        self.healthy_since = {d.id: None for d in self.config.devices}

    def note_healthy(self, room_id):
        since = self.healthy_since[room_id]
        if self.admitted(room_id) and since is not None and time.monotonic() - since >= 60:
            self.retries[room_id] = 0

    def _backend(self, room):
        grant = require_native_context(self.grants[room.id])
        if not manual_transport(grant) or grant.identity != room.address:
            raise ValueError("Office identity binding mismatch")
        return OfficeBackend(room, HardenedControllerBridge.from_native_readonly(grant), self.budget, self.office.controls[room.id])

    def validate_config(self, config):
        if config != self.config:
            raise ValueError("Exact office configuration required")

    def admitted(self, room):
        backend = self.backends[room]
        return backend.state is SessionState.READY and not backend.stopped and not backend.closed and backend.port.ready

    async def ensure_room(self, room_id, *, explicit=False):
        if self.admitted(room_id):
            return self.backends[room_id]
        old = self.backends[room_id]
        # Never reconnect after a pending/uncertain SET. Never clear quarantine
        # on retry, a wall-controller observation, or a public disconnect return.
        if (not explicit and (self.budget.pending_operation(room_id) or self.retries[room_id] >= 3
                or self.budget.recent_admissions(room_id) >= 3
                or time.monotonic() < self.next_attempt[room_id])):
            raise BackendUnavailable("Native recovery requires operator review or backoff")
        if old.claimed:
            await old.close()  # A failed native proof retains capacity and stops here.
        self.retries[room_id] = min(3, self.retries[room_id] + 1)
        self.healthy_since[room_id] = None
        self.next_attempt[room_id] = time.monotonic() + (10, 30, 120)[self.retries[room_id] - 1]
        backend = self._backend(next(d for d in self.config.devices if d.id == room_id))
        self.backends[room_id] = backend
        await backend.prepare()
        self.healthy_since[room_id] = time.monotonic()
        return backend
