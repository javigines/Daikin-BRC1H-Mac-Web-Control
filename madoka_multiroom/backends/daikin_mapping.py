"""Strict, pure mapping of Controller.request object dictionaries.

Required objects must exist and have the documented width and accepted value.
Only explicitly unsupported optional sensors become None, with field status.
These widths/ranges are offline assumptions, not firmware certification.
"""
from madoka_protocol import (CMD_GET_POWER, CMD_GET_MODE, CMD_GET_SETPOINT,
                             CMD_GET_SENSOR, CMD_GET_FAN)
from .base import BackendUnavailable

GROUPS = {"power": CMD_GET_POWER, "mode": CMD_GET_MODE, "setpoint": CMD_GET_SETPOINT,
          "sensor": CMD_GET_SENSOR, "fan": CMD_GET_FAN}


class InvalidSnapshot(BackendUnavailable):
    """Required response data is absent, unsupported or invalid."""


def selected_fields(fields):
    fields = tuple(fields)
    if not fields or len(set(fields)) != len(fields) or any(f not in GROUPS for f in fields):
        raise ValueError("Invalid snapshot field selection")
    return fields


def commands_for(fields):
    fields = selected_fields(fields)
    commands = []
    if any(f in fields for f in ("mode", "setpoint", "fan")):
        commands.append(CMD_GET_MODE)
    for field in fields:
        cmd = GROUPS[field]
        if cmd not in commands:
            commands.append(cmd)
    return commands


def decode_snapshot(responses, fields, *, strict_fan=False):
    fields = selected_fields(fields)
    for cmd in commands_for(fields):
        objects = responses.get(cmd)
        if not isinstance(objects, dict) or any(type(k) is not int or type(v) is not bytes for k,v in objects.items()):
            raise InvalidSnapshot("Invalid or missing response objects")
    state, status = {}, {}
    def raw(cmd, oid, width, *, optional=False, name=None):
        objects = responses[cmd]
        if oid not in objects:
            raise InvalidSnapshot("Required object missing")
        value = objects[oid]
        if value == b"" and optional:
            status[name] = "unsupported"
            return None
        if len(value) != width:
            raise InvalidSnapshot("Unsupported or invalid required object width")
        if name:
            status[name] = "available"
        return value
    def integer(cmd, oid, lo, hi, *, name=None):
        value = raw(cmd, oid, 1, name=name)[0]
        if not lo <= value <= hi:
            raise InvalidSnapshot("Invalid enumerated value")
        return value
    mode = None
    if CMD_GET_MODE in commands_for(fields):
        mode = integer(CMD_GET_MODE, 0x20, 0, 5)
    if "mode" in fields:
        state["mode_id"], status["mode_id"] = mode, "available"
    if "power" in fields:
        state["power_on"] = bool(integer(CMD_GET_POWER, 0x20, 0, 1, name="power_on"))
    if "fan" in fields:
        state["fan_id"] = integer(CMD_GET_FAN, 0x21 if mode == 4 else 0x20, 1, 5, name="fan_id")
    if "fan" in fields and strict_fan:
        state["cooling_fan_id"] = integer(CMD_GET_FAN, 0x20, 1, 5)
        state["heating_fan_id"] = integer(CMD_GET_FAN, 0x21, 1, 5)
    if "setpoint" in fields:
        for name,oid in (("cooling_setpoint",0x20),("heating_setpoint",0x21),
                         ("min_setpoint",0xA3 if mode == 4 else 0xA2),
                         ("max_setpoint",0xB3 if mode == 4 else 0xB2)):
            # Preserve wire precision: display rounding cannot prove a write.
            value = int.from_bytes(raw(CMD_GET_SETPOINT, oid, 2, name=name), "big") / 128.0
            if not 10 <= value <= 40:
                raise InvalidSnapshot("Invalid temperature or unverified zero range")
            state[name] = value
        state["setpoint"] = state["heating_setpoint" if mode == 4 else "cooling_setpoint"]
        status["setpoint"] = "available"
        if not state["min_setpoint"] <= state["setpoint"] <= state["max_setpoint"]:
            raise InvalidSnapshot("Inconsistent setpoint window")
    if "sensor" in fields:
        value = raw(CMD_GET_SENSOR, 0x40, 1, optional=True, name="room_temp")
        temp = int.from_bytes(value,"big",signed=True) if value is not None else None
        if temp is not None and not -40 <= temp <= 80:
            raise InvalidSnapshot("Invalid room temperature")
        state["room_temp"] = temp
    state["field_status"] = status
    return state
