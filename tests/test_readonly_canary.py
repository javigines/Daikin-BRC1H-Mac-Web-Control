"""Exercise the real canary entrypoint against a closed mocked Bleak boundary."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import warnings
from types import ModuleType, SimpleNamespace

import pytest

import madoka_readonly_canary as c
from madoka_protocol import build_query, split_in_chunks

IDENTITY = "11111111-2222-4333-8444-555555555555"
OTHER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
SHA = "f5267721a6936e53a0d1461e58ba0c5c9b547ab5"


def reply(command, objects):
    body = b"".join(bytes((key, len(value))) + value for key, value in objects.items())
    return bytes((4 + len(body), 0)) + command.to_bytes(2, "big") + body


@pytest.fixture
def harness(monkeypatch, tmp_path):
    inventory = tmp_path / "private.json"
    inventory.write_text(json.dumps({"controllers": {"chosen": {"uuid": IDENTITY}, "other": {"uuid": OTHER}}}))
    inventory.chmod(0o600)
    monkeypatch.setattr(c, "state_directory", lambda: tmp_path / "state")
    monkeypatch.setattr(c.sys, "platform", "darwin")
    monkeypatch.setattr(c.os, "geteuid", lambda: os.stat(inventory).st_uid)
    monkeypatch.setattr(c.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(c.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(c, "version", lambda name: "3.0.2")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setattr(c, "verify_checkout", lambda sha: None)
    monkeypatch.setattr(c.getpass, "getpass", lambda prompt: "READ ONLY " + IDENTITY)
    options = SimpleNamespace(discoveries=[], clients=[], writes=[], stage=None,
        stop_error=None, disconnect_error=None, malformed=None, returned=IDENTITY,
        values={}, answer=None, cancel_between=False, stop_delay=0, disconnect_delay=0)
    module = ModuleType("bleak")

    async def boundary(stage):
        if options.stage == stage + "_timeout":
            await asyncio.sleep(10)
        if options.stage == stage + "_cancel":
            raise asyncio.CancelledError()
        if options.stage == stage + "_error":
            raise RuntimeError(IDENTITY + " private backend error")

    class Scanner:
        @staticmethod
        async def find_device_by_address(address, timeout):
            marker = (tmp_path / "state" / "attempt.jsonl").read_text()
            assert "attempt_consumed" in marker and IDENTITY not in marker
            options.discoveries.append((address, timeout))
            await boundary("discovery")
            return None if options.returned is None else SimpleNamespace(address=options.returned)

    class Client:
        def __init__(self, device, disconnected_callback, timeout):
            assert device.address == options.returned
            assert timeout == c.PAIRING_SECONDS
            self.callback = disconnected_callback
            self.is_connected = False
            self.connects = self.subscriptions = self.stops = self.disconnects = 0
            options.clients.append(self)

        async def connect(self):
            self.connects += 1
            await boundary("connect")
            self.is_connected = True

        async def start_notify(self, characteristic, callback):
            assert characteristic == c.NOTIFY_CHAR
            self.subscriptions += 1
            self.notify = callback
            await boundary("subscribe")

        async def write_gatt_char(self, characteristic, data, response):
            command = int.from_bytes(data[3:5], "big")
            assert characteristic == c.WRITE_CHAR and response is False
            assert command in c.GET_COMMANDS and data == bytes(build_query(command)[0])
            options.writes.append(command)
            await boundary("query")
            if options.cancel_between and len(options.writes) == 2:
                self.is_connected = False
                self.callback(self)
                raise ConnectionError(IDENTITY)
            defaults = {0x20: {0x20: b"\x01"}, 0x30: {0x20: b"\x03"},
                0x40: {0x20: (22*128).to_bytes(2,"big"), 0x21: (22*128).to_bytes(2,"big")},
                0x110: {0x40: b"\x17"}}
            payload = reply(command, options.values.get(command, defaults[command]))
            if options.malformed is not None:
                for chunk in options.malformed(command, payload):
                    self.notify(None, chunk)
            else:
                for chunk in split_in_chunks(bytearray(payload)):
                    self.notify(None, chunk)
            # Test both Python output and C-style fd writes are suppressed.
            print(IDENTITY)
            os.write(2, IDENTITY.encode())

        async def stop_notify(self, characteristic):
            self.stops += 1
            await asyncio.sleep(options.stop_delay)
            if options.stop_error:
                raise options.stop_error
            self.is_connected = False

        async def disconnect(self):
            self.disconnects += 1
            await asyncio.sleep(options.disconnect_delay)
            if options.disconnect_error:
                raise options.disconnect_error
            self.is_connected = False
            self.callback(self)

    module.BleakScanner, module.BleakClient = Scanner, Client
    monkeypatch.setitem(sys.modules, "bleak", module)
    monkeypatch.setattr(c, "DISCOVERY_SECONDS", .05)
    monkeypatch.setattr(c, "PAIRING_SECONDS", .08)
    monkeypatch.setattr(c, "QUERY_SECONDS", .03)
    options.inventory = inventory
    options.root = tmp_path
    options.run = lambda: c.main(["--physical-read-only", "--inventory", str(inventory),
        "--controller-id", "chosen", "--reviewed-sha", SHA])
    options.result = lambda: json.loads((tmp_path / "state" / "result.json").read_text())
    return options


@pytest.mark.parametrize("arguments", [[], ["--selftest"]])
def test_default_and_selftest_forbid_ble_import_in_fresh_process(arguments):
    code = """
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith('bleak') or fullname in {'daikin_server','madoka_multiroom.manager','madoka_multiroom.api'}:
   raise RuntimeError('FORBIDDEN_HARDWARE_OR_WEB_IMPORT')
sys.meta_path.insert(0, Guard())
import madoka_readonly_canary
raise SystemExit(madoka_readonly_canary.main(ARGS))
""".replace("ARGS", repr(arguments))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "FORBIDDEN" not in result.stderr


def test_actual_entrypoint_uses_exactly_selected_device_four_gets_and_sanitized_results(harness, capfd, monkeypatch):
    monkeypatch.setattr(c.sys.stdout, "isatty", lambda: True)
    assert harness.run() == 0
    assert harness.discoveries == [(IDENTITY, c.DISCOVERY_SECONDS)]
    assert harness.writes == list(c.GET_COMMANDS) and len(harness.clients) == 1
    client = harness.clients[0]
    assert (client.connects, client.subscriptions, client.stops, client.disconnects) == (1,1,1,1)
    result = harness.result()
    assert result["hardware_state"] == "UNVERIFIED" and result["attempt"] == "CONSUMED_REVIEW_REQUIRED"
    assert result["readings"][0]["fields"]["power_on"] == {"status": "available", "value": True}
    assert result["readings"][2]["object_widths"] == {"0x20": 2, "0x21": 2}
    out, err = capfd.readouterr()
    assert IDENTITY not in out + err + json.dumps(result)
    assert not err
    assert harness.run() == 2
    assert len(harness.discoveries) == len(harness.clients) == 1


@pytest.mark.parametrize("typed", [OTHER, "not-a-uuid", "00000000-0000-0000-0000-000000000000", "{"+IDENTITY+"}"])
def test_invalid_or_mismatched_interactive_uuid_never_imports_or_consumes(harness, monkeypatch, typed):
    monkeypatch.setattr(c.getpass, "getpass", lambda prompt: "READ ONLY " + typed)
    assert harness.run() == 2
    assert not harness.discoveries and not (harness.root / "state").exists()


@pytest.mark.parametrize("gate", ["platform", "root", "tty", "version", "pythonpath", "sha"])
def test_physical_preflight_gates_precede_driver_and_record(harness, monkeypatch, gate):
    if gate == "platform": monkeypatch.setattr(c.sys,"platform","linux")
    if gate == "root": monkeypatch.setattr(c.os,"geteuid",lambda:0)
    if gate == "tty": monkeypatch.setattr(c.sys.stdin,"isatty",lambda:False)
    if gate == "version": monkeypatch.setattr(c,"version",lambda name:"unreviewed")
    if gate == "pythonpath": monkeypatch.setenv("PYTHONPATH","unreviewed")
    if gate == "sha":
        def reject(sha): raise c.PreflightError("Reviewed checkout rejected")
        monkeypatch.setattr(c,"verify_checkout",reject)
    assert harness.run() == 2
    assert not harness.discoveries and not (harness.root / "state").exists()


@pytest.mark.parametrize("returned", [None, OTHER, "invalid"])
def test_discovery_mismatch_never_constructs_client_and_attempt_is_consumed(harness, returned):
    harness.returned = returned
    assert harness.run() == 4
    assert len(harness.discoveries) == 1 and not harness.clients
    assert harness.run() == 2 and len(harness.discoveries) == 1


@pytest.mark.parametrize("stage", ["discovery", "connect", "subscribe", "query"])
@pytest.mark.parametrize("failure,code", [("timeout",4),("cancel",130),("error",4)])
def test_failures_and_cancellation_attempt_cleanup_without_retry(harness, stage, failure, code):
    harness.stage = stage + "_" + failure
    assert harness.run() == code
    result = harness.result()
    assert result["error"]["phase"] == {"discovery":"discovery","connect":"connect_subscribe", "subscribe":"connect_subscribe", "query":"read"}[stage]
    if harness.clients:
        client = harness.clients[0]
        assert client.connects == client.stops == client.disconnects == 1
    assert len(harness.writes) <= 1
    assert harness.run() == 2 and len(harness.discoveries) == 1


@pytest.mark.parametrize("objects,status", [({},"missing"),({0x20:b""},"unsupported")])
def test_missing_unsupported_fields_are_explicit_and_never_default_success(harness, objects, status):
    harness.values[0x20] = objects
    assert harness.run() == 3
    field = harness.result()["readings"][0]["fields"]["power_on"]
    assert field == {"status":status} and harness.writes == list(c.GET_COMMANDS)


@pytest.mark.parametrize("objects", [{0x20:b"\x02"},{0x20:b"\x01\x00"}])
def test_invalid_semantics_or_width_stop_before_next_get(harness, objects):
    harness.values[0x20] = objects
    assert harness.run() == 4
    result = harness.result()
    assert result["readings"][0]["fields"]["power_on"] == {"status":"invalid"}
    assert result["error"]["category"] == "invalid_reply" and harness.writes == [0x20]
    assert harness.clients[0].disconnects == 1


@pytest.mark.parametrize("malformation", ["short", "index", "header", "tlv", "duplicate", "wrong_command"])
def test_malformed_frames_headers_and_tlvs_stop_without_more_reads(harness, malformation):
    def chunks(command, payload):
        if malformation == "short": return [b"\x00"]
        if malformation == "index": return [b"\x01" + payload]
        if malformation == "header": payload = payload[:1] + b"\x01" + payload[2:]
        if malformation == "wrong_command": payload = payload[:2] + b"\x00\x30" + payload[4:]
        if malformation == "tlv": payload = b"\x07\x00\x00\x20\x20\x02\x01"
        if malformation == "duplicate": payload = b"\x0a\x00\x00\x20\x20\x01\x01\x20\x01\x00"
        return [b"\x00" + payload]
    harness.malformed = chunks
    assert harness.run() == 4
    assert harness.writes == [0x20] and harness.clients[0].disconnects == 1
    assert harness.result()["error"]["category"] == "invalid_reply"


def test_disconnection_between_reads_stops_single_session(harness):
    harness.cancel_between = True
    assert harness.run() == 4
    assert harness.writes == [0x20,0x30]
    assert harness.result()["error"]["category"] == "disconnected_or_unavailable"
    assert harness.clients[0].connects == harness.clients[0].disconnects == 1


@pytest.mark.parametrize("method", ["stop_error", "disconnect_error"])
@pytest.mark.parametrize("error", [RuntimeError(IDENTITY), asyncio.CancelledError()])
def test_cleanup_failures_or_boundary_cancellation_still_attempt_both_and_block_rerun(harness, method, error):
    setattr(harness, method, error)
    assert harness.run() == 5
    client = harness.clients[0]
    assert client.stops == client.disconnects == 1
    assert harness.result()["cleanup_uncertain"] is True
    assert harness.run() == 2


def test_partial_crash_record_blocks_every_future_identity(harness):
    marker = c.Attempt(c.canonical_uuid(IDENTITY.upper()), SHA)
    marker.close()  # Simulate process exit before any result or cleanup is recorded.
    record = json.loads((harness.root/"state"/"attempt.jsonl").read_text())
    assert record["identity_sha256"] == hashlib.sha256(IDENTITY.encode()).hexdigest()
    assert harness.run() == 2 and not harness.discoveries
    with pytest.raises(FileExistsError):
        c.Attempt(OTHER, SHA)


def test_initial_fsync_failure_consumes_marker_without_touching_ble(harness, monkeypatch):
    def fail(fd): raise OSError("fsync fault")
    monkeypatch.setattr(c.os,"fsync",fail)
    assert harness.run() == 2
    assert (harness.root/"state"/"attempt.jsonl").exists() and not harness.discoveries
    assert harness.run() == 2


def test_inventory_requires_explicit_selection_private_permissions_and_no_symlink(harness):
    with pytest.raises(c.PreflightError): c.private_inventory(str(harness.inventory), "unknown")
    harness.inventory.chmod(0o644)
    with pytest.raises(c.PreflightError): c.private_inventory(str(harness.inventory), "chosen")
    harness.inventory.chmod(0o600)
    link = harness.root/"link.json"
    link.symlink_to(harness.inventory)
    with pytest.raises(OSError): c.private_inventory(str(link), "chosen")


def test_duplicate_inventory_keys_are_rejected_without_selection(harness):
    harness.inventory.write_text('{"controllers":{},"controllers":{}}')
    assert harness.run() == 2 and not harness.discoveries


def test_query_boundary_cannot_transmit_set_command():
    async def case():
        session = c.ReadSession()
        client = SimpleNamespace(is_connected=True)
        with pytest.raises(ValueError, match="Only approved GET"):
            await session.query(client,0x4020)
    asyncio.run(case())


def test_checkout_verification_checks_exact_head_and_untracked_code(monkeypatch):
    results = iter([SHA+"\n", "?? unreviewed.py\n"])
    monkeypatch.setattr(c.subprocess,"check_output",lambda *a,**k:next(results))
    with pytest.raises(c.PreflightError): c.verify_checkout(SHA)
    with pytest.raises(c.PreflightError): c.verify_checkout("bad")


def test_parent_directory_fsync_failure_prevents_driver_import(harness, monkeypatch):
    original = c.os.fsync
    calls = []
    def fail_parent(fd):
        calls.append(fd)
        if len(calls) == 3:
            raise OSError("parent durability fault")
        original(fd)
    monkeypatch.setattr(c.os, "fsync", fail_parent)
    assert harness.run() == 2 and len(calls) == 3
    assert not harness.discoveries and (harness.root/"state"/"attempt.jsonl").exists()


def test_state_path_uses_account_home_not_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(c.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(tmp_path)))
    monkeypatch.setenv("HOME", str(tmp_path/"different-home"))
    assert c.state_directory() == tmp_path/".madoka-readonly-canary"
    monkeypatch.setenv("HOME", str(tmp_path/"another-home"))
    assert c.state_directory() == tmp_path/".madoka-readonly-canary"


@pytest.mark.parametrize("command,objects,code,status", [
    (0x30,{0x20:b"\xff"},4,"invalid"),
    (0x40,{0x20:b"\x16",0x21:b"\x0b\x00"},4,"invalid"),
    (0x40,{0x20:b"\x00\x00",0x21:b"\x0b\x00"},4,"invalid"),
    (0x110,{0x40:b"\x00\x17"},4,"invalid"),
    (0x110,{0x40:b"\x7f"},4,"invalid"),
    (0x110,{0x40:b""},3,"unsupported"),
    (0x110,{},3,"missing"),
])
def test_semantic_statuses_for_all_query_groups(harness, command, objects, code, status):
    harness.values[command] = objects
    assert harness.run() == code
    reading = next(r for r in harness.result()["readings"] if r["command"] == f"0x{command:04x}")
    assert any(field["status"] == status for field in reading["fields"].values())
    if code == 4:
        assert harness.writes[-1] == command


@pytest.mark.parametrize("method", ["stop_delay", "disconnect_delay"])
def test_cleanup_deadlines_still_attempt_both_calls(harness, monkeypatch, method):
    import ble_lifecycle
    monkeypatch.setattr(ble_lifecycle,"CLEANUP_TIMEOUT",.02)
    setattr(harness,method,10)
    assert harness.run() == 5
    assert harness.clients[0].stops == harness.clients[0].disconnects == 1
    assert harness.result()["cleanup_uncertain"]
    assert harness.run() == 2


def test_process_crash_leaves_consumed_record_without_a_result(harness):
    code = """
import os
from pathlib import Path
import madoka_readonly_canary as c
c.state_directory = lambda: Path(STATE)
c.Attempt(IDENTITY, SHA)
os._exit(99)
""".replace("STATE",repr(str(harness.root/"state"))).replace("IDENTITY",repr(IDENTITY)).replace("SHA",repr(SHA))
    child = subprocess.run([sys.executable,"-c",code],capture_output=True,text=True)
    assert child.returncode == 99,child.stderr
    assert not (harness.root/"state"/"result.json").exists()
    assert harness.run() == 2 and not harness.discoveries


def test_final_result_storage_failure_never_grants_a_retry(harness, monkeypatch, capfd):
    monkeypatch.setattr(c.sys.stdout,"isatty",lambda:True)
    def fail(self, result): raise OSError(IDENTITY)
    monkeypatch.setattr(c.Attempt,"save_result",fail)
    assert harness.run() == 5
    assert "record_update_failed" in capfd.readouterr().out
    assert harness.clients[0].disconnects == 1
    assert harness.run() == 2 and len(harness.discoveries) == 1


def test_record_update_failure_during_reads_still_cleans_up(harness, monkeypatch):
    original = c.Attempt.append
    def fail_query(self,stage,**details):
        if stage == "query": raise OSError(IDENTITY)
        return original(self,stage,**details)
    monkeypatch.setattr(c.Attempt,"append",fail_query)
    assert harness.run() == 4
    assert not harness.writes and harness.clients[0].disconnects == 1
    assert harness.run() == 2


def test_concurrent_attempt_creation_allows_only_one_consumed_marker(harness):
    from concurrent.futures import ThreadPoolExecutor
    # Create the private directory before the race so both open the same entry.
    (harness.root/"state").mkdir(mode=0o700)
    def consume(identity):
        try:
            attempt = c.Attempt(identity,SHA)
            attempt.close()
            return "consumed"
        except FileExistsError:
            return "blocked"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(consume,[IDENTITY,OTHER]))
    assert sorted(results) == ["blocked","consumed"]
    assert harness.run() == 2 and not harness.discoveries


def test_hidden_confirmation_never_falls_back_to_echoed_input(harness, monkeypatch, capfd):
    def fallback(prompt):
        warnings.warn("Echo controls unavailable", c.getpass.GetPassWarning)
        print(IDENTITY)  # A real getpass fallback would echo private input here.
        return "READ ONLY " + IDENTITY
    monkeypatch.setattr(c.getpass, "getpass", fallback)
    assert harness.run() == 2
    assert not harness.discoveries and not harness.clients
    assert not (harness.root / "state").exists()
    out, err = capfd.readouterr()
    assert IDENTITY not in out + err
