"""Private commissioning of one observed transition, never capability discovery.

No profile is supplied by default or editable through the ordinary controls UI.
The wire fingerprint is deliberately opaque: byte 0x32 is not decoded as °C.
"""
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .config import StrictModel


class CoordinatedSetpointProfile(StrictModel):
    profile: Literal["cool-lower-one-v1"]
    identity_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    cooling_before: int = Field(ge=11, le=40)
    heating_before: int = Field(ge=11, le=39)

    @model_validator(mode="after")
    def observed_pair(self):
        if self.cooling_before != self.heating_before + 1:
            raise ValueError("Only an explicitly commissioned one-degree pair is supported")
        return self


class SetpointConfirmation(StrictModel):
    token: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_boolean(cls, value):
        if value is not True:
            raise ValueError("Explicit affirmative consent required")
        return value
