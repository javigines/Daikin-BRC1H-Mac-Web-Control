"""Local wall commissioning and conservative mode-transition contracts.

No GET, enum membership or BLE lock establishes physical capability/isolation.
These attestations are private, identity-bound by LocalFleet, and expire.
"""
import time
from typing import ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from .config import StrictModel
from .office_access import Controls


class ModeCommissioning(StrictModel):
    wall_modes: tuple[int, ...]
    checked_at: float = Field(strict=True, gt=0, allow_inf_nan=False)
    expires_at: float = Field(strict=True, gt=0, allow_inf_nan=False)
    restrictions: Literal["unknown", "unlocked", "locked"] = "unknown"
    physical_scope: Literal["unknown", "independent", "linked"] = "unknown"
    ventilation_hardware: bool = False
    # Only use auto_both after a supervised variant established this exact effect.
    dry_fan_effect: Literal["preserve", "auto_both"] = "preserve"

    @field_validator("wall_modes", mode="before")
    @classmethod
    def wall_tuple(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def evidence(self):
        if (not self.wall_modes or len(set(self.wall_modes)) != len(self.wall_modes)
                or any(type(v) is not int or v not in range(6) for v in self.wall_modes)
                or not 0 < self.expires_at - self.checked_at <= 86400):
            raise ValueError("Wall inventory must be explicit and valid for at most 24 hours")
        return self

    def permits(self, mode, now=None):
        now = time.time() if now is None else now
        return (self.checked_at <= now < self.expires_at and mode in self.wall_modes
                and self.restrictions == "unlocked" and self.physical_scope == "independent"
                and (mode != 5 or self.ventilation_hardware))


class LocalControls(Controls):
    allowed_modes: ClassVar[tuple[int, ...]] = tuple(range(6))
    mode_commissioning: ModeCommissioning | None = None

    @model_validator(mode="after")
    def commissioned_modes(self):
        extra = set(self.modes) & {0, 1, 5}
        if extra and (self.mode_commissioning is None or any(
                not self.mode_commissioning.permits(m, self.mode_commissioning.checked_at) for m in self.modes)):
            raise ValueError("New modes require fresh unlocked wall inventory and proven independent equipment")
        if self.mode_commissioning and not set(self.modes) <= set(self.mode_commissioning.wall_modes):
            raise ValueError("Configured modes must occur in this room's wall inventory")
        return self


def selectable_modes(controls):
    evidence = getattr(controls, "mode_commissioning", None)
    if evidence is not None:
        return [mode for mode in controls.modes if evidence.permits(mode)]
    return [mode for mode in controls.modes if mode in (2, 3, 4)]


def validate_mode_baseline(controls, before, raw):
    from madoka_protocol import CMD_GET_MODE, CMD_GET_POWER, CMD_GET_SETPOINT, CMD_GET_FAN
    from .backends.office_native import setpoint_fields
    from .backends.base import BackendUnavailable
    mode = raw.get(CMD_GET_MODE, {}).get(0x20)
    power = raw.get(CMD_GET_POWER, {}).get(0x20)
    if (type(mode) is not bytes or len(mode) != 1 or mode[0] not in range(6)
            or mode[0] != before.get("mode_id") or power not in (b"\x00", b"\x01")):
        raise ValueError("Mode/power baseline unavailable")
    try:
        setpoint_fields(raw.get(CMD_GET_SETPOINT, {}))
    except BackendUnavailable as exc:
        raise ValueError("Stored setpoint block is not verified for a mode transition") from exc
    fans = raw.get(CMD_GET_FAN, {})
    if any(type(fans.get(k)) is not bytes or len(fans[k]) != 1 or fans[k][0] not in range(6)
           for k in (0x20, 0x21)):
        raise ValueError("Both stored fan banks must be verified before a mode SET")
    evidence = getattr(controls, "mode_commissioning", None)
    if mode[0] in (0, 1, 5) and evidence is None:
        raise ValueError("Current mode requires explicit wall commissioning")
    if evidence and not evidence.permits(mode[0]):
        raise ValueError("Current mode is outside the fresh wall inventory")


def validate_mode_effect(controls, target, before_raw, after_raw):
    """Target-mode matrix: preserve all bytes except explicit, bounded effects.

    Every target preserves power, both stored targets, all setpoint metadata,
    and opaque extensions. Dry may set both fan banks to Auto ONLY with the
    separately commissioned exact profile. No guessed active bank in 0/1/2/5.
    Sensor temperature is naturally variable; derived UI fields may change.
    """
    from madoka_protocol import CMD_GET_MODE, CMD_GET_POWER, CMD_GET_SETPOINT, CMD_GET_FAN
    from .backends.daikin import WriteNotConfirmed
    expected = {cmd: dict(before_raw[cmd]) for cmd in
                (CMD_GET_POWER, CMD_GET_MODE, CMD_GET_SETPOINT, CMD_GET_FAN)}
    expected[CMD_GET_MODE][0x20] = bytes([target])
    evidence = getattr(controls, "mode_commissioning", None)
    if target == 1 and evidence and evidence.dry_fan_effect == "auto_both":
        expected[CMD_GET_FAN].update({0x20: b"\x00", 0x21: b"\x00"})
    groups = {CMD_GET_POWER:"power", CMD_GET_MODE:"mode", CMD_GET_SETPOINT:"stored_setpoints", CMD_GET_FAN:"stored_fans"}
    mismatches = [groups[cmd] for cmd, block in expected.items() if after_raw.get(cmd) != block]
    if mismatches:
        error = WriteNotConfirmed("Mode effect or preserved raw fields diverged; STOP")
        error.reason, error.fields = "mode_state_mismatch", mismatches
        raise error


MODE_NOTICES = {
    0: "Ventilador: circula aire sin frío/calor. Banco de velocidad activo sin verificar.",
    1: "Seco: temperatura y ventilador automáticos. No apagar inmediatamente; revisar drenaje en el mando.",
    2: "Auto: sin objetivo ni banco de ventilador activo verificados para ajuste manual.",
    3: "Frío: objetivo de refrigeración activo.",
    4: "Calor: objetivo de calefacción activo.",
    5: "Ventilación: requiere equipo compatible. Sin controles de ventilador interior ni temperatura. No apagar durante el arranque.",
}
