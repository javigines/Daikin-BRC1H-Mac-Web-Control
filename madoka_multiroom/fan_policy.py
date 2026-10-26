"""Owner-commissioned fan steps, separate from protocol observations.

No device I/O, defaults, enum expansion or unknown-field interpretation here.
Pair ACK recognition is grounded in pinned pymadoka; physical effect still
requires a fresh GET. Auto selection is intentionally not commissioned.
"""
from typing import Literal

from pydantic import field_validator, model_validator

from .config import StrictModel


class FanPolicy(StrictModel):
    cooling_steps: tuple[int, ...] = ()
    heating_steps: tuple[int, ...] = ()
    ack_profile: Literal["pair_v1"] = "pair_v1"

    @field_validator("cooling_steps", "heating_steps", mode="before")
    @classmethod
    def tuples(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def steps(self):
        for steps in (self.cooling_steps, self.heating_steps):
            if len(set(steps)) != len(steps) or any(type(v) is not int or v not in range(1, 6) for v in steps):
                raise ValueError("Only independently commissioned manual fan codes are allowed")
        return self

    def choices(self, mode):
        return self.cooling_steps if mode == 3 else self.heating_steps if mode == 4 else ()


def recognized_fan_pair(objects):
    return all(type(objects.get(k)) is bytes and len(objects[k]) == 1
               and objects[k][0] in range(6) for k in (0x20, 0x21))


def fan_block(objects, mode, value):
    """Only two SET TLVs; preserve the exact inactive byte, including Auto=0."""
    if mode not in (3, 4) or not recognized_fan_pair(objects):
        raise ValueError("Fan requires a complete recognized Cool/Heat bank pair")
    if type(value) is not int or value not in range(1, 6):
        raise ValueError("Auto selection is not commissioned")
    target = 0x20 if mode == 3 else 0x21
    return [(k, bytes([value]) if k == target else objects[k]) for k in (0x20, 0x21)]
