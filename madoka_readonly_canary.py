#!/usr/bin/env python3
"""One-shot, explicitly opted-in read-only Madoka diagnostic. Default is offline.

Reuses the audited spike setup, strict protocol helpers and shielded cleanup.
No web application, coordinator, policy engine, SET operation or recovery path.
"""
import argparse
import asyncio
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import getpass
import hashlib
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import time
import uuid
import warnings

from brc1h_spike import connect_and_subscribe, selftest as protocol_selftest
from ble_lifecycle import cleanup_client
from madoka_protocol import (
    NOTIFY_CHAR, WRITE_CHAR, Reassembler, build_query, cmd_id_of, parse_objects,
    CMD_GET_POWER, CMD_GET_MODE, CMD_GET_SETPOINT, CMD_GET_SENSOR,
)

GET_COMMANDS = (CMD_GET_POWER, CMD_GET_MODE, CMD_GET_SETPOINT, CMD_GET_SENSOR)
DISCOVERY_SECONDS = 15.0
PAIRING_SECONDS = 120.0
QUERY_SECONDS = 6.0
REVIEWED_BLEAK_VERSION = "3.0.2"
ROOT = Path(__file__).resolve().parent


class PreflightError(Exception):
    """A sanitized preflight rejection, never an underlying exception message."""


def canonical_uuid(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value):
        raise PreflightError("Invalid CoreBluetooth UUID")
    parsed = uuid.UUID(value)
    if parsed.int == 0:
        raise PreflightError("Invalid CoreBluetooth UUID")
    return str(parsed)


def private_inventory(path, selected):
    if not path or not selected or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", selected):
        raise PreflightError("Explicit private inventory entry required")
    source = Path(path)
    if not source.is_absolute() or source.resolve().is_relative_to(ROOT):
        raise PreflightError("Inventory must be an absolute private path outside the checkout")
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise PreflightError("Inventory must be an owner-only regular file")
        raw = os.read(fd, 65537)
    finally:
        os.close(fd)
    if len(raw) > 65536:
        raise PreflightError("Inventory exceeds size limit")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PreflightError("Duplicate inventory key")
            result[key] = value
        return result
    data = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(data, dict) or set(data) != {"controllers"}:
        raise PreflightError("Invalid inventory schema")
    entries = data["controllers"]
    if not isinstance(entries, dict) or not 1 <= len(entries) <= 32 or selected not in entries:
        raise PreflightError("Exact inventory selection required")
    for value in entries.values():
        if not isinstance(value, dict) or set(value) != {"uuid"}:
            raise PreflightError("Invalid inventory entry")
        canonical_uuid(value["uuid"])
    return canonical_uuid(entries[selected]["uuid"])


def verify_checkout(expected):
    if not expected or not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise PreflightError("Exact reviewed SHA required")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT,
                                    stderr=subprocess.DEVNULL, text=True).strip()
    if head != expected or dirty:
        raise PreflightError("Reviewed SHA mismatch or modified checkout")


def state_directory():
    # HOME overrides must not bypass an attempt consumed by this macOS account.
    return Path(pwd.getpwuid(os.geteuid()).pw_dir) / ".madoka-readonly-canary"


class Attempt:
    """A consumed record, not a releasable lease. There is no reset operation."""
    def __init__(self, identity, reviewed_sha):
        self.directory = state_directory()
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise PreflightError("Attempt directory must be an owner-only real directory")
        self.fd = os.open(self.directory / "attempt.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.durable = False
        try:
            self.append("attempt_consumed", identity_sha256=hashlib.sha256(identity.encode()).hexdigest(), reviewed_sha=reviewed_sha)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            parent_fd = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)  # Commit the newly-created directory's name.
            finally:
                os.close(parent_fd)
            self.durable = True
        except BaseException:
            os.close(self.fd)
            # Preserve even an empty/partial marker: failure never grants a rerun.
            raise

    def append(self, stage, **details):
        data = (json.dumps({"stage": stage, "at_unix": time.time(), **details}, sort_keys=True) + "\n").encode()
        offset = 0
        while offset < len(data):
            written = os.write(self.fd, data[offset:])
            if written <= 0:
                raise OSError("Attempt record write failed")
            offset += written
        os.fsync(self.fd)

    def save_result(self, result):
        path = self.directory / "result.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def close(self):
        os.close(self.fd)


@contextmanager
def silence_backend_output():
    """Keep driver output/tracebacks out of shared diagnostics, including C stdio.

    OS Bluetooth logs remain local OS data; only result.json is shareable.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    saved = [os.dup(1), os.dup(2)]
    old_logging = logging.root.manager.disable
    try:
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            logging.disable(sys.maxsize)
            yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        for target, source in zip((1, 2), saved):
            os.dup2(source, target)
            os.close(source)
        logging.disable(old_logging)


def observation(command, objects):
    """Report explicit availability; no inferred defaults or display rounding."""
    fields = {
        CMD_GET_POWER: (("power_on", 0x20, 1, 0, 1),),
        CMD_GET_MODE: (("mode_id", 0x20, 1, 0, 5),),
        CMD_GET_SETPOINT: (("cooling_setpoint", 0x20, 2, 10, 40), ("heating_setpoint", 0x21, 2, 10, 40)),
        CMD_GET_SENSOR: (("indoor_temperature", 0x40, 1, -40, 80),),
    }
    result = {}
    for name, oid, width, low, high in fields[command]:
        raw = objects.get(oid)
        status = "missing" if raw is None else "unsupported" if raw == b"" else "available"
        item = {"status": status}
        if status == "available":
            if len(raw) != width:
                item["status"] = "invalid"
            else:
                value = int.from_bytes(raw, "big", signed=command == CMD_GET_SENSOR)
                if command == CMD_GET_SETPOINT:
                    value /= 128
                if not low <= value <= high:
                    item["status"] = "invalid"
                else:
                    item["value"] = bool(value) if command == CMD_GET_POWER else value
        result[name] = item
    return result


class ReadSession:
    """The only transmit path admits a fixed GET allowlist without arguments."""
    def __init__(self):
        self.reassembler = Reassembler()
        self.pending = None
        self.command = None
        self.disconnected = False
        self.fault = None
        self.active = True

    def fail(self, fault):
        self.fault = self.fault or fault
        if self.pending is not None and not self.pending.done():
            self.pending.set_exception(fault)

    def on_disconnect(self, _client):
        if self.active:
            self.disconnected = True
            self.fail(ConnectionError("Disconnected"))

    def on_notify(self, _sender, data):
        if not self.active or self.fault:
            return
        try:
            if self.pending is None or self.pending.done():
                raise ValueError("Unsolicited notification")
            if self.reassembler.chunks and data and data[0] == 0:
                raise ValueError("Ambiguous repeated first fragment")
            payload = self.reassembler.feed(bytearray(data))
            if payload is None:
                if not self.reassembler.chunks:
                    raise ValueError("Malformed fragment")
                return
            if payload[1] != 0 or cmd_id_of(payload) != self.command:
                raise ValueError("Unexpected command header")
            objects = parse_objects(payload)
            # Shared parser validates widths; reject duplicate TLVs before use.
            seen, offset = set(), 4
            while offset < len(payload):
                oid = payload[offset]
                if oid in seen:
                    raise ValueError("Duplicate response object")
                seen.add(oid)
                offset += 2 + payload[offset + 1]
            self.pending.set_result(objects)
        except Exception:
            self.fail(ValueError("Malformed or unexpected reply"))

    async def query(self, client, command):
        if command not in GET_COMMANDS:
            raise ValueError("Only approved GET commands are permitted")
        if self.fault or self.disconnected or not client.is_connected:
            raise self.fault or ConnectionError("Disconnected")
        self.command = command
        self.pending = asyncio.get_running_loop().create_future()
        self.reassembler.reset()
        try:
            async with asyncio.timeout(QUERY_SECONDS):
                for chunk in build_query(command):
                    if self.fault or self.disconnected or not client.is_connected:
                        raise self.fault or ConnectionError("Disconnected")
                    await client.write_gatt_char(WRITE_CHAR, bytes(chunk), response=False)
                result = await self.pending
                if self.fault or self.disconnected or not client.is_connected:
                    raise self.fault or ConnectionError("Disconnected")
                return result
        finally:
            if not self.pending.done():
                self.pending.cancel()
            elif not self.pending.cancelled():
                self.pending.exception()
            self.pending = None
            self.reassembler.reset()


class CleanupBoundary:
    """Ensure a backend-cancelled stop-notify cannot skip disconnect."""
    is_connected = True  # conservatively try both calls after partial setup

    def __init__(self, client, evidence):
        self.client, self.evidence = client, evidence

    async def _call(self, name, *args):
        start = time.monotonic()
        item = {"call": name, "status": "uncertain"}
        self.evidence.append(item)
        try:
            await getattr(self.client, name)(*args)
            item["status"] = "public_call_returned_native_unverified"
        except asyncio.CancelledError:
            raise RuntimeError("Cleanup boundary cancelled") from None
        finally:
            item["duration_seconds"] = round(time.monotonic() - start, 3)

    async def stop_notify(self, characteristic):
        await self._call("stop_notify", characteristic)

    async def disconnect(self):
        await self._call("disconnect")


async def run_physical(identity, attempt):
    if not attempt.durable:
        raise PreflightError("Durable attempt required before driver import")
    result = {"hardware_state": "UNVERIFIED", "attempt": "CONSUMED_REVIEW_REQUIRED", "readings": [], "cleanup": []}
    client, phase, command = None, "driver_import", None
    session = ReadSession()
    started = time.monotonic()
    exit_code = 4
    try:
        # The sole lazy driver import is reached only after main's physical gates.
        from bleak import BleakClient, BleakScanner
        phase = "discovery"
        attempt.append(phase)
        stage_start = time.monotonic()
        device = await asyncio.wait_for(BleakScanner.find_device_by_address(identity, timeout=DISCOVERY_SECONDS), DISCOVERY_SECONDS)
        result["discovery_seconds"] = round(time.monotonic() - stage_start, 3)
        if device is None or canonical_uuid(device.address) != identity:
            raise ConnectionError("Exact selected device unavailable")
        client = BleakClient(device, disconnected_callback=session.on_disconnect, timeout=PAIRING_SECONDS)
        phase = "connect_subscribe"
        attempt.append(phase)
        stage_start = time.monotonic()
        await connect_and_subscribe(client, session.on_notify, pair_window=PAIRING_SECONDS)
        result["setup_seconds"] = round(time.monotonic() - stage_start, 3)
        if session.fault or session.disconnected:
            raise session.fault or ConnectionError("Disconnected")
        phase = "read"
        for command in GET_COMMANDS:
            attempt.append("query", command=f"0x{command:04x}")
            start = time.monotonic()
            objects = await session.query(client, command)
            fields = observation(command, objects)
            result["readings"].append({"command": f"0x{command:04x}",
                "duration_seconds": round(time.monotonic() - start, 3),
                "object_widths": {f"0x{key:02x}": len(value) for key, value in objects.items()}, "fields": fields})
            if any(field["status"] == "invalid" for field in fields.values()):
                raise ValueError("Invalid field width or value; STOP")
        exit_code = 0 if all(f["status"] == "available" for r in result["readings"] for f in r["fields"].values()) else 3
    except asyncio.CancelledError:
        result["error"] = {"phase": phase, "category": "cancelled"}
        exit_code = 130
    except Exception as exc:
        category = "timeout" if isinstance(exc, TimeoutError) else "disconnected_or_unavailable" if isinstance(exc, ConnectionError) else "invalid_reply" if isinstance(exc, ValueError) else "backend_failure"
        result["error"] = {"phase": phase, "category": category}
    finally:
        if "error" in result and phase == "read" and command is not None:
            result["error"]["command"] = f"0x{command:04x}"
        session.active = False
        if client is not None:
            try:
                await cleanup_client(CleanupBoundary(client, result["cleanup"]))
            except (Exception, asyncio.CancelledError):
                result["cleanup_uncertain"] = True
                exit_code = 5 if exit_code != 130 else 130
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["exit_code"] = exit_code
        try:
            attempt.append("quarantined_review_required", exit_code=exit_code, native_state="unverified")
            attempt.save_result(result)
        except Exception:
            result["record_update_failed"] = True
            result["exit_code"] = 5
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Default OFFLINE; physical use requires separate owner authorization.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--selftest", action="store_true", help="offline protocol checks; never imports Bleak")
    mode.add_argument("--physical-read-only", action="store_true", help="ONE authorized physical attempt; consumes durable record")
    parser.add_argument("--inventory", help="absolute owner-only private JSON outside checkout")
    parser.add_argument("--controller-id", help="exact explicitly selected private entry")
    parser.add_argument("--reviewed-sha", help="exact reviewed clean checkout SHA")
    args = parser.parse_args(argv)
    if args.selftest:
        return protocol_selftest()
    if not args.physical_read_only:
        print("OFFLINE: no Bluetooth initialized. Use --selftest. Physical use requires separate authorization.")
        return 0
    attempt = None
    try:
        if sys.platform != "darwin" or os.geteuid() == 0 or not sys.stdin.isatty() or not sys.stdout.isatty():
            raise PreflightError("Physical use requires an attended macOS user terminal without sudo")
        if os.environ.get("PYTHONPATH") or version("bleak") != REVIEWED_BLEAK_VERSION:
            raise PreflightError("Use the reviewed Bleak version without PYTHONPATH overrides")
        verify_checkout(args.reviewed_sha)
        identity = private_inventory(args.inventory, args.controller_id)
        if (state_directory() / "attempt.jsonl").exists():
            raise PreflightError("A consumed attempt already exists; review required")
        print("STOP unless the owner authorized this exact controller/window and verified its wall controls and other clients.")
        with warnings.catch_warnings():
            # Abort before getpass can fall back to echoed private input.
            warnings.simplefilter("error", getpass.GetPassWarning)
            answer = getpass.getpass("Type READ ONLY followed by the exact inventory UUID (hidden): ")
        if not answer.startswith("READ ONLY ") or canonical_uuid(answer[len("READ ONLY "):]) != identity:
            raise PreflightError("Interactive identity confirmation did not match")
        attempt = Attempt(identity, args.reviewed_sha)
        with silence_backend_output():
            result = asyncio.run(run_physical(identity, attempt))
        print(json.dumps(result, sort_keys=True))
        print("STOP: attempt consumed. Review result.json and physically verify wall controls; do not run again.")
        return result["exit_code"]
    except PreflightError as exc:
        print(f"STOP: {exc}. No automatic retry.")
        return 2
    except (Exception, KeyboardInterrupt):
        # Never print inventory, exception text, UUIDs, backend tracebacks or argv.
        print("STOP: preflight, durable record or execution failed. No automatic retry; preserve the private attempt directory.")
        return 2 if attempt is None else 130
    finally:
        if attempt is not None:
            try:
                attempt.close()
            except OSError:
                pass  # The consumed marker remains; never imply capacity release.


if __name__ == "__main__":
    sys.exit(main())
