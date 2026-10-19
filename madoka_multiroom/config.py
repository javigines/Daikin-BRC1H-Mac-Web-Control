"""Validated local configuration; addresses are never sent to the browser."""
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class QueryConfig(StrictModel):
    interval_seconds: float = Field(default=10.0, ge=2, le=300, allow_inf_nan=False)
    timeout_seconds: float = Field(default=5.0, ge=0.1, le=30, allow_inf_nan=False)
    fields: tuple[Literal["power", "mode", "setpoint", "sensor", "fan"], ...] = (
        "power", "mode", "setpoint", "sensor", "fan")

    @field_validator("fields", mode="before")
    @classmethod
    def fields_tuple(cls, value):
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("fields")
    @classmethod
    def unique_fields(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError("queries must be nonempty and unique")
        return value


class DeviceConfig(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    name: str = Field(min_length=1, max_length=64)
    address: str
    enabled: bool = True
    queries: QueryConfig = Field(default_factory=QueryConfig)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("name must be trimmed printable text")
        return value

    @field_validator("address")
    @classmethod
    def corebluetooth_uuid(cls, value):
        return str(UUID(value))


class AppConfig(StrictModel):
    devices: tuple[DeviceConfig, ...] = Field(default=(), max_length=6)
    max_active_devices: int = Field(default=1, ge=1, le=6)
    max_concurrent_operations: int = Field(default=1, ge=1, le=6)
    stagger_seconds: float = Field(default=0.25, ge=0, le=5, allow_inf_nan=False)
    stale_seconds: float = Field(default=35.0, ge=5, le=900, allow_inf_nan=False)

    @field_validator("devices", mode="before")
    @classmethod
    def devices_tuple(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def distinct(self):
        for attr in ("id", "address"):
            values = [getattr(d, attr) for d in self.devices]
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate device {attr}")
        if sum(d.enabled for d in self.devices) > self.max_active_devices:
            raise ValueError("enabled devices exceed max_active_devices; no implicit rotation")
        return self


def load_config(path: str) -> AppConfig:
    source = Path(path)
    if source.stat().st_size > 64 * 1024:
        raise ValueError("configuration exceeds 64 KiB")
    return AppConfig.model_validate(json.loads(source.read_text(encoding="utf-8")))


def demo_config() -> AppConfig:
    names = ("Recepción", "Sala de reuniones", "Zona de trabajo", "Dirección", "Sala creativa", "Oficina norte")
    return AppConfig.model_validate({
        "max_active_devices": 6, "max_concurrent_operations": 2,
        "devices": [{"id": f"room-{i}", "name": name,
                     "address": f"00000000-0000-4000-8000-{i:012d}"}
                    for i, name in enumerate(names, 1)]})
