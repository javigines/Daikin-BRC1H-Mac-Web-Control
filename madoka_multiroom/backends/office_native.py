"""Six native rooms using the existing serialized Controller and durable ledger.

Writes are explicit, journaled before transmission and never replayed. A normal
verified close can release APP ownership. Unproven cleanup remains quarantined.
"""
import asyncio
import copy
import hashlib
import json
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
from ..fan_policy import recognized_fan_pair, fan_block
from .base import BackendUnavailable
from .controller_bridge import HardenedControllerBridge
from .physical_fleet import PreparedDaikinBackend
from .physical_sessions import PhysicalSessionBudget
from .daikin import SessionState, WriteNotConfirmed
from ..local_modes import selectable_modes, validate_mode_baseline, validate_mode_effect, MODE_NOTICES


def manual_fan_pair(objects):
    """The pre-existing write gate; Auto/partial observations grant no SET."""
    return all(k in objects and type(objects[k]) is bytes and len(objects[k]) == 1
               and objects[k][0] in range(1, 6) for k in (0x20, 0x21))


def local_fan_observation(objects, mode):
    """Decode the two GET banks independently; never normalize raw objects.

    Public sources recognize 0 as Auto and 1..5 as manual protocol codes.
    Observation proves neither distinct physical speeds nor write capability.
    Only Cool/Heat have an established active-bank mapping in this application.
    """
    values, statuses = {}, {}
    for field, oid in (("cooling_fan_id", 0x20), ("heating_fan_id", 0x21)):
        value = objects.get(oid)
        valid = type(value) is bytes and len(value) == 1 and value[0] in range(6)
        values[field] = value[0] if valid else None
        statuses[field] = "available" if valid else "unknown" if oid in objects else "not_observed"
    active = {3: "cooling_fan_id", 4: "heating_fan_id"}.get(mode)
    values["fan_id"] = values[active] if active else None
    statuses["fan_id"] = statuses[active] if active else "not_applicable"
    return values, statuses


SETPOINT_WIDTHS = {0x20: 2, 0x21: 2, **{oid: size for oid, size, _ in SETPOINT_EXTRA_FIELDS}}
# ACK payloads are opaque: these are response profiles, not GET value schemas.
# Public source rationale and the limits of this classification are in
# docs/setpoint-ack-verification.md. Presence alone never implies acceptance.
SETPOINT_ACK_BYTE_FIELDS = (0x30, 0x31, 0x32, 0xA0, 0xA1, 0xA4, 0xA5, 0xB0, 0xB1, 0xB4, 0xB5)
SETPOINT_ACK_VARIABLE_FIELDS = (0x20, 0x21, 0xA2, 0xA3, 0xB2, 0xB3)
SETPOINT_ACK_IDS = frozenset(SETPOINT_ACK_BYTE_FIELDS + SETPOINT_ACK_VARIABLE_FIELDS)
SETPOINT_ACK_PROFILES = {
    "full_fields": {**{k: 1 for k in SETPOINT_ACK_BYTE_FIELDS},
                    **{k: 2 for k in SETPOINT_ACK_VARIABLE_FIELDS}},
    "compact_fields": {k: 1 for k in SETPOINT_ACK_IDS},
}


class SetpointUnconfirmed(WriteNotConfirmed):
    def __init__(self, reason):
        self.reason = reason  # Fixed code only, never payload or exception text.
        super().__init__(f"Setpoint not confirmed: {reason}")


class UnknownSetpointAck(SetpointUnconfirmed):
    pass


class UnknownModeAck(WriteNotConfirmed):
    reason = "mode_ack_schema"


def mode_ack_schema(objects, *, require_status=False):
    if not require_status and objects in ({}, {0: b""}):
        return "legacy_empty"
    if (set(objects) == {0x20} and type(objects[0x20]) is bytes
            and len(objects[0x20]) == 1 and objects[0x20][0] in range(6)):
        return "mode_status_v1"
    raise UnknownModeAck("Unrecognized SET_MODE acknowledgement schema")


class FanUnconfirmed(WriteNotConfirmed):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(f"Fan not confirmed: {reason}")


class UnknownFanAck(FanUnconfirmed):
    pass


def fan_ack_schema(objects, *, commissioned=False):
    """Source-shaped pair ACK; values only authorize the following GET."""
    if set(objects) == {0x20, 0x21} and recognized_fan_pair(objects):
        return "pair_v1"
    if not commissioned and objects in ({}, {0: b""}):
        return "legacy_empty"
    raise UnknownFanAck("ack_profile_unknown")


def validate_fan_readback(before, args, observed):
    if not recognized_fan_pair(observed):
        raise FanUnconfirmed("readback_schema")
    expected = {**before, **dict(args)}
    changed = [k for k, v in args if before[k] != v]
    if len(changed) != 1:
        raise FanUnconfirmed("invalid_expected_change")
    target = changed[0]
    if observed[target] != expected[target]:
        raise FanUnconfirmed("no_effect" if observed[target] == before[target] else "active_target_mismatch")
    if any(observed.get(k) != v for k, v in expected.items()):
        raise FanUnconfirmed("preserved_fan_field_mismatch")


def setpoint_ack_schema(objects):
    """Recognize only bounded, correlated response profiles; decode no values.

    pymadoka's Feature.update parses TLVs, then SetPointStatus.set_values uses
    variable-width integers. OpenHAB's SET handler does not interpret the body.
    We support full-width and uniform one-byte profiles, not arbitrary widths,
    error codes, extensions, mixed-width variants or zero-length field values.
    Framing, duplicate IDs and correlation must already have passed the bridge.
    """
    if objects == {} or objects == {0: b""}:
        return "empty"
    if set(objects) != SETPOINT_ACK_IDS:
        raise UnknownSetpointAck("ack_object_ids")
    if any(type(v) is not bytes for v in objects.values()):
        raise UnknownSetpointAck("ack_value_type")
    for name, widths in SETPOINT_ACK_PROFILES.items():
        if all(len(objects[k]) == width for k, width in widths.items()):
            return name
    raise UnknownSetpointAck("ack_field_widths")


def setpoint_fields(objects, *, extensions=True):
    """Validate the documented block; classify other GET TLVs as read-only.

    Widths and SET argument membership come from pymadoka SetPointStatus.
    Symbol bytes have no documented boolean domain. Keep them opaque, as with
    the differential/configuration bytes; do not decode/re-encode or default.
    """
    if (not SETPOINT_WIDTHS.keys() <= objects.keys()
            or any(type(objects[k]) is not bytes or len(objects[k]) != size
                   for k, size in SETPOINT_WIDTHS.items())):
        raise BackendUnavailable("Unrecognized setpoint schema; required fields missing or malformed")
    extra = {k: v for k, v in objects.items() if k not in SETPOINT_WIDTHS}
    if (extra and not extensions) or any(
            type(k) is not int or not 0 <= k <= 255 or type(v) is not bytes or len(v) > 255
            for k, v in extra.items()):
        raise BackendUnavailable("Unrecognized setpoint extension schema")
    if objects[0x30][0] not in (0, 1) or objects[0x31][0] not in (0, 1, 2):
        raise BackendUnavailable("Unsupported setpoint flags")
    return {k: objects[k] for k in SETPOINT_WIDTHS}, extra


def validate_set_ack(command, objects):
    """A correlated SET response permits GET verification, never proves effect.

    pymadoka documents status-shaped SET replies whose value need not equal the
    requested state. ACK schemas deliberately do not use setpoint_fields().
    See docs/setpoint-ack-verification.md for pinned schema evidence.
    """
    if command == CMD_SET_SETPOINT:
        return setpoint_ack_schema(objects)
    if command == CMD_SET_FAN:
        return fan_ack_schema(objects)
    if command == CMD_SET_MODE:
        return mode_ack_schema(objects)
    if objects in ({}, {0: b""}):
        return
    values = range(2) if command == CMD_SET_POWER else range(6) if command == CMD_SET_MODE else ()
    if (set(objects) == {0x20} and type(objects[0x20]) is bytes
            and len(objects[0x20]) == 1 and objects[0x20][0] in values):
        return
    raise WriteNotConfirmed("Unrecognized SET acknowledgement schema")


def setpoint_block(objects, mode, value, controls):
    """Build only documented SET arguments, preserving every untouched byte."""
    fields, _ = setpoint_fields(objects)
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
    return [(k, temp_to_bytes(value) if k == target else v) for k, v in fields.items()]


def validate_setpoint_readback(before, args, observed, *, authorized_targets=None, active_target=None):
    """Allow newly reported GET objects, but preserve the whole baseline.

    Every known byte (including the modified target) and every pre-existing
    read-only extension must match. Additional GET-only TLVs are never SET args.
    """
    try:
        setpoint_fields(observed)
    except BackendUnavailable as exc:
        raise SetpointUnconfirmed("readback_schema") from exc
    arguments = dict(args)
    if len(arguments) != len(args) or arguments.keys() != SETPOINT_WIDTHS.keys():
        raise SetpointUnconfirmed("invalid_expected_change")
    changed = {k for k, v in args if v != before[k]}
    if authorized_targets is None:
        # Existing callers authorize exactly one active target, never a pair.
        authorized_targets = changed if len(changed) == 1 else set()
    authorized = set(authorized_targets)
    if not authorized or not authorized <= {0x20, 0x21} or changed != authorized:
        raise SetpointUnconfirmed("invalid_expected_change")
    if active_target is None and len(authorized) == 1:
        active_target = next(iter(authorized))
    if active_target not in authorized:
        raise SetpointUnconfirmed("invalid_expected_change")
    if any(observed[k] != arguments[k] for k in authorized):
        if all(observed[k] == before[k] for k in authorized):
            raise SetpointUnconfirmed("no_effect")
        raise SetpointUnconfirmed("active_target_mismatch" if observed[active_target] != arguments[active_target]
                                 else "inactive_target_mismatch")
    expected = {**before, **arguments}
    if any(observed.get(k) != v for k, v in expected.items()):
        raise SetpointUnconfirmed("preserved_field_mismatch")


class OfficeBackend(PreparedDaikinBackend):
    def __init__(self, room, port, budget, controls):
        self.controls = controls
        self.raw = {}
        self.manual_context = None
        self.last_manual_diagnostic = None
        self.manual_timeout = 90
        super().__init__(room.id, port, budget, timeout=30, setup_timeout=170)
        self.info.update(backend="native-office", hardware_enabled=True,
                         supported_fields=["power", "mode", "setpoint", "sensor", "fan"])

    async def _request(self, cmd, args=None):
        if self.closed or self.stopped or self.state is not SessionState.READY:
            raise BackendUnavailable("Native session stopped")
        if self.manual_context:
            self._trace(self.manual_context['phase'], command=f"0x{cmd:04x}")
        result = await self.port.exchange_ready(cmd, args, timeout=6)
        if not self.port.ready:
            raise BackendUnavailable("Link lost during native operation")
        return result

    def _trace(self, stage, **details):
        context = self.manual_context
        if context:
            context['stage'] = stage
            self.last_manual_diagnostic = {"operation_id": context['id'], "stage": stage,
                "elapsed_ms": round((time.monotonic() - context['start']) * 1000), **details}
            self.budget.operation_event(context['id'], stage, self.last_manual_diagnostic)

    async def _execute(self, operation, *, timeout=None):
        # A well-framed reply with an unconfirmed effect does not establish
        # link loss. Keep GETs alive; the durable unresolved operation blocks SET.
        async def local_operation():
            try:
                return await operation()
            except WriteNotConfirmed as exc:
                if not getattr(self.budget, 'local', False) or not self.port.ready:
                    raise
                return exc
        result = await super()._execute(local_operation, timeout=timeout)
        if isinstance(result, WriteNotConfirmed):
            raise result
        return result

    async def _read(self, fields, *, initial=None):
        state, raw = {}, {}
        commands = (CMD_GET_POWER, CMD_GET_MODE, CMD_GET_SETPOINT, CMD_GET_SENSOR)
        for command in commands:
            raw[command] = initial[command] if initial and command in initial else await self._request(command)
            group = observation(command, raw[command])
            if any(v["status"] != "available" for v in group.values()):
                raise BackendUnavailable("Required native observation unavailable")
            state.update({k: v["value"] for k, v in group.items()})
        mode = state["mode_id"]
        state["setpoint"] = state["heating_setpoint" if mode == 4 else "cooling_setpoint"]
        state["room_temp"] = state.pop("indoor_temperature")
        state["fan_id"] = None
        fan_status = {}
        local = getattr(self.port.radio.grant, "local", False)
        if local and mode not in (3, 4):
            state["setpoint"] = None
        if local or self.controls.fans:
            fans = raw[CMD_GET_FAN] = (initial[CMD_GET_FAN]
                if initial and CMD_GET_FAN in initial else await self._request(CMD_GET_FAN))
            if local:
                observed, fan_status = local_fan_observation(fans, mode)
                state.update(observed)
            elif manual_fan_pair(fans):
                state.update(cooling_fan_id=fans[0x20][0], heating_fan_id=fans[0x21][0])
                state["fan_id"] = fans[0x21 if mode == 4 else 0x20][0]
            else:
                raise BackendUnavailable("Unsupported fan observations")
        state["field_status"] = {key: "available" if value is not None else "not_queried" for key, value in state.items()}
        state["field_status"].update(fan_status)
        if local and mode not in (3, 4):
            state["field_status"]["setpoint"] = "not_applicable"
        if local:
            state["mode_notice"] = MODE_NOTICES.get(mode, "Modo desconocido: controles deshabilitados")
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

    def _fan_write_ready(self):
        if self.latest.get("mode_id") not in (3, 4):
            return False
        if not getattr(self.port.radio.grant, "local", False):
            return True
        if self.controls.fan_policy is not None:
            return bool(self.controls.fan_policy.choices(self.latest.get("mode_id"))) and recognized_fan_pair(self.raw.get(CMD_GET_FAN, {}))
        # Legacy rooms retain their old manual/manual permission.
        return self.latest.get("fan_id") is not None and manual_fan_pair(self.raw.get(CMD_GET_FAN, {}))

    def _fan_choices(self):
        if getattr(self.port.radio.grant, "local", False) and self.controls.fan_policy is not None:
            return [v for v in self.controls.fan_policy.choices(self.latest.get("mode_id")) if v in self.controls.fans]
        return list(self.controls.fans)

    def _setpoint_plan(self, value):
        """Recompute from observations; only a private exact profile permits two targets."""
        objects = self.raw.get(CMD_GET_SETPOINT, {})
        mode = self.latest["mode_id"]
        args = setpoint_block(objects, mode, value, self.controls)
        value = int(value)  # Canonical whole degrees for snapshot/API consent hashes.
        active = 0x21 if mode == 4 else 0x20
        profile = getattr(self, "coordinated_setpoint_profile", None)
        if (not getattr(self.port.radio.grant, "local", False) or profile is None or mode != 3
                or profile.identity_digest != hashlib.sha256(self.port.radio.grant.identity.encode()).hexdigest()
                or value != profile.cooling_before - 1
                or objects[0x20] != temp_to_bytes(profile.cooling_before)
                or objects[0x21] != temp_to_bytes(profile.heating_before)
                or tuple(objects[k] for k in (0x30, 0x31, 0x32)) != (b'\x00', b'\x00', b'\x01')):
            return args, (active,), None
        # Both banks, before and after, must fit their own effective limits.
        setpoint_block(objects, 3, profile.cooling_before, self.controls)
        setpoint_block(objects, 4, profile.heating_before, self.controls)
        heating = dict(setpoint_block(objects, 4, profile.heating_before - 1, self.controls))[0x21]
        args = [(k, heating if k == 0x21 else v) for k, v in args]
        proposal = {"value": value, "cooling_before": profile.cooling_before,
                    "cooling_after": value, "heating_before": profile.heating_before,
                    "heating_after": profile.heating_before - 1}
        # Bind consent to this room/identity, both targets, every baseline byte,
        # extensions, power/mode/fan banks and owner limits. No raw data is exposed.
        binding = {"room": self.room_id, "profile": profile.model_dump(mode="json"),
                   "controls": self.controls.model_dump(mode="json"), "proposal": proposal,
                   "wire": {str(c): {str(k): v.hex() for k, v in obj.items()}
                            for c, obj in self.raw.items() if c != CMD_GET_SENSOR}}
        proposal["token"] = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return args, (0x20, 0x21), proposal

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
        proposals = []
        if setpoint and not stopped and getattr(self, "coordinated_setpoint_profile", None):
            try:
                _, _, proposal = self._setpoint_plan(limits["setpoint"] - 1)
                if proposal:
                    proposals.append(proposal)
            except (ValueError, BackendUnavailable):
                pass  # No proposal when either target is outside its verified window.
        local = getattr(self.port.radio.grant, "local", False)
        safe_power = not (local and limits.get("power_on") and limits.get("mode_id") in (1, 5))
        return {"power": self.controls.power and not stopped and safe_power, "setpoint": bool(setpoint and not stopped),
                "setpoint_proposals": proposals,
                "modes": (selectable_modes(self.controls) if local else list(self.controls.modes)) if not stopped else [],
                "fans": self._fan_choices() if not stopped and self._fan_write_ready() else [],
                "fan_permission": "stopped" if stopped else "mode_not_commissioned" if limits.get("mode_id") not in (3, 4)
                    else "not_commissioned" if not self._fan_choices() else "ready" if self._fan_write_ready()
                    else "bank_profile_not_commissioned"}

    def validate_action(self, action, value):
        if action == "power" and self.controls.power and type(value) is bool:
            if (getattr(self.port.radio.grant, "local", False) and not value
                    and self.latest.get("mode_id") in (1, 5)):
                raise ValueError("Supervised wall stop required for Dry drain/ventilation start")
            return value
        if action == "mode" and type(value) is int and value in self.controls.modes:
            if getattr(self.port.radio.grant, "local", False) and value not in selectable_modes(self.controls):
                raise ValueError("Fresh unlocked wall commissioning required")
            return value
        if action == "fan" and type(value) is int and value in self._fan_choices():
            if not self._fan_write_ready():
                raise ValueError("Fan write requires commissioned Cool/Heat steps and a recognized bank pair")
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

    async def _manual_readback(self, action, value, before, raw, args, *, phase="readback_get", authorized_targets=None):
        self.manual_context['phase'] = phase
        initial = None
        if action == "mode" and getattr(self.budget, "local", False):
            initial = {CMD_GET_MODE: await self._request(CMD_GET_MODE)}
        if action == "setpoint":
            # First command after the ACK is the affected GET. No SET replay,
            # no read retry, and no interpretation of ACK bytes as observations.
            initial = {CMD_GET_SETPOINT: await self._request(CMD_GET_SETPOINT)}
        if action == "fan":
            initial = {CMD_GET_FAN: await self._request(CMD_GET_FAN)}
        if action == 'power' and getattr(self.budget, 'local', False):
            # Existing power settling contract; unrelated to setpoint ACKs.
            for attempt, delay in enumerate((0, .3, .6), 1):
                await asyncio.sleep(delay)
                reply = await self._request(CMD_GET_POWER)
                group = observation(CMD_GET_POWER, reply)['power_on']
                self._trace('power_readback', attempt=attempt, status=group['status'], value=group.get('value'))
                if group['status'] != 'available':
                    raise BackendUnavailable('Power readback schema unavailable')
                if group['value'] == value:
                    initial = {CMD_GET_POWER: reply}
                    break
            if initial is None:
                raise WriteNotConfirmed('Power did not settle within three GET observations')
        observed = await self._read((), initial=initial)
        self._trace('readback_validate')
        if action == "mode" and getattr(self.budget, "local", False):
            try:
                validate_mode_effect(self.controls, value, raw, self.raw)
            except WriteNotConfirmed as exc:
                self._trace('readback_validate', fields=exc.fields, reason=exc.reason)
                raise
            return observed
        field = {"power": "power_on", "mode": "mode_id", "fan": "fan_id", "setpoint": "setpoint"}[action]
        expected = dict(before)
        expected[field] = value
        if action == "setpoint":
            active = 0x21 if before["mode_id"] == 4 else 0x20
            authorized = authorized_targets or (active,)
            for target in authorized:
                expected["cooling_setpoint" if target == 0x20 else "heating_setpoint"] = int.from_bytes(dict(args)[target], "big") / 128
            validate_setpoint_readback(raw[CMD_GET_SETPOINT], args, self.raw[CMD_GET_SETPOINT],
                                      authorized_targets=authorized, active_target=active)
        if action == "fan":
            expected["heating_fan_id" if before["mode_id"] == 4 else "cooling_fan_id"] = value
            validate_fan_readback(raw[CMD_GET_FAN], args, self.raw[CMD_GET_FAN])
            for command in (CMD_GET_POWER, CMD_GET_MODE, CMD_GET_SETPOINT):
                if any(self.raw[command].get(k) != v for k, v in raw[command].items()):
                    raise FanUnconfirmed("preserved_configuration_mismatch")
        # Room temperature can move naturally. Preserve mode/power and both
        # inactive targets/fans. Diagnostics contain field names, never values.
        keys = ("power_on", "mode_id", "cooling_setpoint", "heating_setpoint", "cooling_fan_id", "heating_fan_id")
        mismatches = sorted(k for k in set(keys) | {field} if k in expected and observed.get(k) != expected[k])
        if mismatches:
            self._trace('readback_validate', fields=mismatches)
            if action == "setpoint":
                raise SetpointUnconfirmed("state_mismatch")
            if action == "fan":
                raise FanUnconfirmed("state_mismatch")
            raise WriteNotConfirmed("GET readback did not confirm exactly the intended change")
        return observed

    async def _observe_unknown_ack(self, error, value, before, raw, args, *, action="setpoint", authorized_targets=None):
        """One GET observation on a healthy correlated stream; never confirm.

        A malformed/uncorrelated wire reply cannot enter here: the bridge stops
        that session before returning an ACK. Any GET failure is diagnostic
        only; it cannot replace the unknown ACK or clear durable uncertainty.
        """
        try:
            await self._manual_readback(action, value, before, raw, args, phase="unknown_ack_get",
                                        authorized_targets=authorized_targets)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._trace('unknown_ack_observation', result="not_verified",
                        reason=getattr(exc, 'reason', 'get_unavailable'), error_class=type(exc).__name__)
        else:
            self._trace('unknown_ack_observation', result="requested_state_observed",
                        reason="unknown_ack_still_blocks")
        self._trace('ack_validate', result="unknown", reason=error.reason)
        raise error

    async def manual(self, action, value, operation_id, reason, authorize_desired, *, expected_mode=None, setpoint_confirmation=None):
        if self.stopped or self.closed or self.budget.pending_operation(self.room_id):
            raise BackendUnavailable("Native write stopped or unresolved; no replay")
        value = self.validate_action(action, value)
        if setpoint_confirmation is not None and (action != "setpoint" or not getattr(self.budget, "local", False)):
            raise ValueError("Coordinated confirmation requires a local setpoint action")
        if str(UUID(operation_id)) != operation_id or not 3 <= len(reason.strip()) <= 160:
            raise ValueError("Explicit operation identity and reason required")
        if self.budget.operation(operation_id) or self.budget.pending_operation(self.room_id):
            raise BackendUnavailable("Duplicate or unresolved write; STOP, no replay")
        async def operation():
            journaled = False
            presented_mode = self.latest.get("mode_id") if expected_mode is None else expected_mode
            self.manual_context = {'id': operation_id, 'start': time.monotonic(), 'stage': 'baseline', 'phase': 'baseline_get'}
            try:
                before = await self._read(())
                raw = copy.deepcopy(self.raw)
                authorized_targets, proposal = None, None
                if getattr(self.port.radio.grant, "local", False):
                    self.latest = copy.deepcopy(before)
                    try:
                        self.validate_action(action, value)
                        if action == "fan":
                            if before["mode_id"] != presented_mode:
                                raise ValueError("Fan mode changed since presentation")
                            if await self._request(CMD_GET_MODE) != raw[CMD_GET_MODE]:
                                raise ValueError("Fan mode changed during baseline")
                            setpoint_fields(raw[CMD_GET_SETPOINT])
                            self.validate_action(action, value)
                            if self.budget.pending_operation(self.room_id):
                                raise ValueError("Unresolved write appeared during fan baseline")
                        if action == "mode":
                            if before["mode_id"] != presented_mode:
                                raise ValueError("Mode changed since presentation")
                            validate_mode_baseline(self.controls, before, raw, value)
                        if action == "setpoint":
                            args, authorized_targets, proposal = self._setpoint_plan(value)
                            if proposal is not None:
                                if (setpoint_confirmation is None or setpoint_confirmation.confirmed is not True
                                        or setpoint_confirmation.token != proposal["token"]):
                                    raise ValueError("Explicit confirmation of both current targets required")
                            elif setpoint_confirmation is not None:
                                raise ValueError("Coordinated proposal no longer applies")
                    except (ValueError, BackendUnavailable):
                        return {"_local_rejected": True}
                    if before[{"power": "power_on", "mode": "mode_id", "fan": "fan_id", "setpoint": "setpoint"}[action]] == value:
                        field = {"power": "power_on", "mode": "mode_id", "fan": "fan_id", "setpoint": "setpoint"}[action]
                        self.budget.begin_operation(self.port, self, operation_id, action, value,
                            {"state": before, "reason": reason, "already_observed": True,
                             "wire": {str(c): {str(k): v.hex() for k, v in obj.items()} for c, obj in raw.items()}})
                        journaled = True
                        authorize_desired(field, value)
                        self._trace('confirmed_noop')
                        self.budget.finish_operation(operation_id, "confirmed", before)
                        return before
                if action == "setpoint":
                    if authorized_targets is None:
                        args = setpoint_block(raw[CMD_GET_SETPOINT], before["mode_id"], value, self.controls)
                        authorized_targets = (0x21 if before["mode_id"] == 4 else 0x20,)
                    command = CMD_SET_SETPOINT
                elif action == "fan":
                    if before["mode_id"] not in (3, 4):
                        raise ValueError("Fan control requires commissioned cooling/heating mode")
                    args = fan_block(raw[CMD_GET_FAN], before["mode_id"], value)
                    command = CMD_SET_FAN
                else:
                    command = CMD_SET_POWER if action == "power" else CMD_SET_MODE
                    args = [(0x20, bytes([int(value)]))]
                field = {"power": "power_on", "mode": "mode_id", "fan": "fan_id", "setpoint": "setpoint"}[action]
                if before[field] == value:
                    raise ValueError("Requested value already observed; no SET necessary")
                saved = {"state": before, "wire": {str(cmd): {str(k): v.hex() for k, v in objects.items()} for cmd, objects in raw.items()}, "reason": reason}
                if action == "mode" and getattr(self.budget, "local", False):
                    evidence = getattr(self.controls, "mode_commissioning", None)
                    saved["mode_contract"] = {"from_mode": before["mode_id"], "to_mode": value,
                                              "commissioning": evidence.model_dump(mode="json") if evidence else None}
                if action == "setpoint":
                    saved["setpoint_authorization"] = {"targets": list(authorized_targets),
                        "expected": {str(k): dict(args)[k].hex() for k in authorized_targets},
                        "coordinated_confirmation": proposal}
                commissioned_fan = bool(getattr(self.port.radio.grant, "local", False) and self.controls.fan_policy is not None)
                if action == "fan":
                    saved["fan_commissioning"] = self.controls.fan_policy.model_dump(mode="json") if commissioned_fan else None
                    saved["fan_request_mode"] = presented_mode
                self._trace('persist_baseline')
                self.budget.begin_operation(self.port, self, operation_id, action, value, saved)
                journaled = True
                # An authorized API operation is the only native desired-state
                # source. Both durable stores must succeed before any SET byte.
                self._trace('persist_desired')
                authorize_desired(field, value)
                self.port.radio.arm_write(command, args)
                self.manual_context['phase'] = 'set_exchange'
                ack = await self._request(command, args)
                self._trace('ack_validate')
                try:
                    if (action == "mode" and getattr(self.budget, "local", False)
                            and getattr(self.controls, "mode_commissioning", None) is not None):
                        schema = mode_ack_schema(ack, require_status=True)
                    elif action == "fan":
                        schema = fan_ack_schema(ack, commissioned=commissioned_fan)
                    else:
                        schema = validate_set_ack(command, ack)
                except UnknownSetpointAck as exc:
                    self._trace('ack_validate', result="unknown", reason=exc.reason)
                    await self._observe_unknown_ack(exc, value, before, raw, args, authorized_targets=authorized_targets)
                except UnknownModeAck as exc:
                    await self._observe_unknown_ack(exc, value, before, raw, args, action="mode")
                except UnknownFanAck as exc:
                    await self._observe_unknown_ack(exc, value, before, raw, args, action="fan")
                self._trace('ack_validate', result="recognized", schema=schema or "legacy")
                observed = await self._manual_readback(action, value, before, raw, args, authorized_targets=authorized_targets)
                self._trace('confirmed')
                self.budget.finish_operation(operation_id, "confirmed", observed)
                self.latest = copy.deepcopy(observed)
                return observed
            except BaseException as exc:
                if journaled:
                    self.budget.finish_operation(operation_id, "uncertain")
                try:
                    exchange = getattr(self.port, 'last_exchange', None) or {}
                    fallback = ("reply_framing_or_correlation" if 'notification_error_class' in exchange
                        else "get_unavailable" if self.manual_context['stage'] in ('readback_get', 'unknown_ack_get')
                        else "operation_interrupted")
                    self._trace('failed', failed_stage=self.manual_context['stage'], error_class=type(exc).__name__,
                        reason=getattr(exc, 'reason', fallback),
                        journaled=journaled, transport_ready=self.port.ready,
                        exchange=exchange)
                except Exception:
                    # A storage error must not replace the originating failure.
                    # The in-memory diagnostic remains available in room detail.
                    pass
                raise
            finally:
                self.port.radio.write_frames.clear()
                self.manual_context = None
        # Eleven ordinary exchanges can each take six seconds. The old shared
        # thirty-second clock could cancel a SET/readback after a slow baseline.
        result = await self._execute(operation,
            timeout=self.manual_timeout if getattr(self.budget, 'local', False) else None)
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
