"""Attended, identity-bound authorization for the dedicated read-only runtime.

Importing this module neither imports Bleak nor consumes a physical attempt.
The canary's permanent record is deliberately not opened or modified here.
"""
import getpass
import json
from importlib.metadata import version
import os
from pathlib import Path
import pwd
import stat
import sys
import warnings

from madoka_readonly_canary import (
    PreflightError, canonical_uuid, private_inventory, verify_checkout,
    REVIEWED_BLEAK_VERSION, ROOT,
)

_SEAL = object()


class NativeAuthorization:
    """Capability minted only after the operator preflight; one fleet per grant."""
    def __init__(self, seal, identity, room_id, reviewed_sha, reference):
        if seal is not _SEAL:
            raise PreflightError("Attended native authorization required")
        self.identity, self.room_id = identity, room_id
        self.reviewed_sha, self.reference = reviewed_sha, reference
        self.used = False


def require_authorization(grant):
    if type(grant) is not NativeAuthorization or os.environ.get("MADOKA_OFFLINE_ONLY"):
        raise PreflightError("Native execution disabled or authorization absent")
    return grant


def authorize(*, inventory, controller_id, reviewed_sha, authorization_reference):
    """Fresh owner GO is required even after the previous successful canary."""
    if (os.environ.get("MADOKA_OFFLINE_ONLY") or os.environ.get("CI")
            or sys.platform != "darwin" or os.geteuid() == 0
            or not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise PreflightError("Use an attended macOS user terminal with fresh owner authorization")
    if os.environ.get("PYTHONPATH") or version("bleak") != REVIEWED_BLEAK_VERSION:
        raise PreflightError("Reviewed Bleak 3.0.2 without PYTHONPATH required")
    if (not isinstance(authorization_reference, str)
            or not 3 <= len(authorization_reference.strip()) <= 160):
        raise PreflightError("Fresh owner authorization reference required")
    verify_checkout(reviewed_sha)
    identity = private_inventory(inventory, controller_id)
    print("STOP unless the owner authorized this NEW web attempt and verified wall controls and excluded other clients.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        answer = getpass.getpass("Type READ ONLY WEB followed by the selected UUID (hidden): ")
    if not answer.startswith("READ ONLY WEB ") or canonical_uuid(answer[14:]) != identity:
        raise PreflightError("Explicit web identity confirmation did not match")
    return NativeAuthorization(_SEAL, identity, controller_id, reviewed_sha, authorization_reference.strip())


def native_state_directory():
    """Fixed account location; HOME overrides cannot bypass unresolved sessions."""
    directory = Path(pwd.getpwuid(os.geteuid()).pw_dir) / ".madoka-readonly-web"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise PreflightError("Native state directory must be private and not a symlink")
    for path in (directory, directory.parent):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return directory


def load_private_config(path):
    from .config import AppConfig
    source = Path(path)
    if not source.is_absolute() or source.resolve().is_relative_to(ROOT):
        raise PreflightError("Native configuration must be outside the checkout")
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise PreflightError("Native configuration must be owner-only")
        raw = os.read(fd, 65537)
        if len(raw) > 65536:
            raise PreflightError("Native configuration too large")
    finally:
        os.close(fd)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PreflightError("Duplicate native configuration key")
            result[key] = value
        return result
    return AppConfig.model_validate(json.loads(raw, object_pairs_hook=unique))
