"""Explicit, expiring owner authorization for the unattended office runtime.

No Bluetooth import or operation occurs here. The fixed legacy ledger is reused;
an authorization file never releases an unresolved native session.
"""
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import time
from typing import ClassVar
from importlib.metadata import version

from pydantic import Field, field_validator, model_validator

from madoka_readonly_canary import PreflightError, verify_checkout, REVIEWED_BLEAK_VERSION
from .config import AppConfig, StrictModel
from .fan_policy import FanPolicy
from .native_access import NativeAuthorization, _SEAL, native_state_directory


class Controls(StrictModel):
    """Commissioned capabilities, not inferred from an enum or a GET success."""
    power: bool = False
    setpoint: bool = False
    modes: tuple[int, ...] = ()
    fans: tuple[int, ...] = ()
    fan_policy: FanPolicy | None = None
    minimum: int = Field(default=16, ge=16, le=32)
    maximum: int = Field(default=32, ge=16, le=32)
    allowed_modes: ClassVar[tuple[int, ...]] = (2, 3, 4)

    @field_validator("modes", "fans", mode="before")
    @classmethod
    def tuples(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def bounds(self):
        if (self.minimum > self.maximum or len(set(self.modes)) != len(self.modes)
                or len(set(self.fans)) != len(self.fans)
                or any(type(v) is not int or v not in self.allowed_modes for v in self.modes)
                or any(type(v) is not int or v not in range(1, 6) for v in self.fans)):
            raise ValueError("Invalid commissioned controls")
        if self.fan_policy and not set(self.fan_policy.cooling_steps + self.fan_policy.heating_steps) <= set(self.fans):
            raise ValueError("Mode-specific fan steps must be within the owner's fan choices")
        return self


class OfficeConfig(StrictModel):
    rooms: AppConfig
    controls: dict[str, Controls]
    host: str = "127.0.0.1"
    port: int = Field(default=8443, ge=1024, le=65535)
    certificate: str
    private_key: str
    # Exact HTTPS authorities; no wildcard, forwarded headers or Internet bind.
    authorities: tuple[str, ...]

    @field_validator("authorities", mode="before")
    @classmethod
    def authorities_tuple(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def scope(self):
        import ipaddress
        import re
        address = ipaddress.ip_address(self.host)
        if not address.is_private or address.is_unspecified or address.is_multicast:
            raise ValueError("Bind to a specific private or loopback address")
        if (set(self.controls) != {d.id for d in self.rooms.devices}
                or any(mode not in (2, 3, 4) for control in self.controls.values() for mode in control.modes)
                or not self.authorities or any(not re.fullmatch(r"[a-zA-Z0-9.-]+:[0-9]{4,5}", a) for a in self.authorities)):
            raise ValueError("Exact configured room capabilities and HTTPS authorities required")
        if self.rooms.max_concurrent_operations > self.rooms.max_active_devices:
            raise ValueError("Operation capacity exceeds connection capacity")
        return self


def read_private(path):
    source = Path(path)
    if not source.is_absolute():
        raise PreflightError("Absolute private path required")
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise PreflightError("Owner-only regular file required")
        raw = os.read(fd, 65537)
        if len(raw) > 65536:
            raise PreflightError("Private file exceeds size limit")
        return raw
    finally:
        os.close(fd)


def load_office_config(path):
    from madoka_readonly_canary import ROOT
    if Path(path).resolve().is_relative_to(ROOT):
        raise PreflightError("Private office configuration must be outside the checkout")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PreflightError("Duplicate private configuration key")
            result[key] = value
        return result
    return OfficeConfig.model_validate(json.loads(read_private(path), object_pairs_hook=unique))


def environment():
    if (os.environ.get("CI") or os.environ.get("MADOKA_OFFLINE_ONLY") or os.environ.get("PYTHONPATH")
            or sys.platform != "darwin" or os.geteuid() == 0 or version("bleak") != REVIEWED_BLEAK_VERSION):
        raise PreflightError("Reviewed macOS user runtime required; offline/CI native execution forbidden")


def config_digest(config):
    return hashlib.sha256(config.model_dump_json().encode()).hexdigest()


def authorize_service(config, sha, reference, days=7):
    """Attended source/config-bound authorization; no hardware operation."""
    environment()
    if (not sys.stdin.isatty() or not sys.stdout.isatty() or type(days) is not int or not 1 <= days <= 90
            or not isinstance(reference, str) or not 3 <= len(reference.strip()) <= 160):
        raise PreflightError("Attended bounded owner authorization required")
    verify_checkout(sha)
    if getpass.getpass("Type AUTHORIZE OFFICE followed by the reviewed SHA (hidden): ") != "AUTHORIZE OFFICE " + sha:
        raise PreflightError("Office authorization confirmation mismatch")
    data = {"schema": 1, "sha": sha, "config_digest": config_digest(config), "reference": reference.strip(),
            "expires": time.time() + days * 86400, "password": secrets.token_urlsafe(32)}
    path = native_state_directory() / "office-authorization.json"
    # Replacement requires another explicit attended authorization. Never touch
    # the session ledger or consumed canary marker when issuing credentials.
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as target:
            json.dump(data, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)
    return data["password"]


def service_grants(config):
    environment()
    data = json.loads(read_private(native_state_directory() / "office-authorization.json"))
    if (data.get("schema") != 1 or data.get("config_digest") != config_digest(config)
            or type(data.get("expires")) not in (float, int) or not time.time() < data["expires"] <= time.time() + 90 * 86400
            or not isinstance(data.get("password"), str) or len(data["password"]) < 40):
        raise PreflightError("Expired or mismatched office authorization")
    verify_checkout(data["sha"])
    grants = {}
    for room in config.rooms.devices:
        grant = NativeAuthorization(_SEAL, room.address, room.id, data["sha"], data["reference"])
        grant.office = True
        grant.expires = data["expires"]
        grants[room.id] = grant
    return grants, data["password"]
