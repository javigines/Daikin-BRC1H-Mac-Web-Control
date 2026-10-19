"""Actual native factory against a closed fake SDK, with no physical evidence."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from integration.native_mock import closed_native, SHA
from madoka_multiroom import native_access as access
from madoka_multiroom.api import build_native_readonly_app, build_demo_app
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.backends.daikin import CleanupUncertain, BackendBusy, PhysicalBackendDisabled
from madoka_multiroom.backends.physical_sessions import PhysicalSessionBudget, ConnectionPlan
from madoka_multiroom.manager import RoomManager, RoomUnavailable, RoomBusy
from madoka_multiroom.storage import RoomStore
from madoka_protocol import CMD_SET_POWER, CMD_GET_FAN, WRITE_CHAR, build_command


@pytest.fixture
def native(tmp_path):
    with closed_native(tmp_path) as runtime:
        yield runtime


async def close_uncertain(backend):
    with pytest.raises(CleanupUncertain):
        await backend.close()


def test_native_factory_ready_only_no_startup_connection_and_four_gets(native):
    async def case():
        backend = native.fleet.backends["room-1"]
        assert native.calls == [] and native.fleet.initial_admissions == ()
        with pytest.raises(BackendUnavailable):
            await backend.snapshot(("power",))
        assert native.calls == []
        await backend.prepare()
        assert backend.setup_timeout == 170 and native.calls == ["discovery", "connect", "subscribe"]
        state = await backend.snapshot(("power", "mode", "setpoint", "sensor"))
        assert state["room_temp"] == 23 and state["setpoint"] == 22 and state["power_on"] is True
        assert "min_setpoint" not in state and state["field_status"]["fan_id"] == "not_queried"
        assert [v for v in native.calls if type(v) is int] == [0x20, 0x30, 0x40, 0x110]
        assert not native.fleet.admitted("room-2")
        await close_uncertain(backend)
        assert len(native.budget.owners) == len(native.budget.quarantined) == 1
        assert native.calls[-2:] == ["stop_notify", "disconnect"]
        with pytest.raises(CleanupUncertain):
            await backend.close()
        assert native.calls.count("disconnect") == 1
    asyncio.run(case())


@pytest.mark.parametrize("stage", ["discovery", "connect", "subscribe"])
def test_native_setup_is_outside_short_operation_deadlines(native, stage):
    async def case():
        backend = native.fleet.backends["room-1"]
        backend.timeout = .01
        native.stage, native.delay = stage, .04
        await backend.prepare()
        assert backend.port.ready and native.calls == ["discovery", "connect", "subscribe"]
        await close_uncertain(backend)
    asyncio.run(case())


def test_native_concurrent_admissions_and_reads_are_bounded(native):
    async def case():
        store = RoomStore(":memory:", mode="real")
        manager = RoomManager(native.config, store, offline_fleet=native.fleet)
        native.stage, native.delay = "connect", .04
        first = asyncio.create_task(manager.prepare_room("room-1"))
        await asyncio.sleep(.01)
        with pytest.raises(RoomBusy): await manager.prepare_room("room-1")
        with pytest.raises(BackendUnavailable): await manager.prepare_room("room-2")
        await first
        native.stage, native.delay = "query", .01
        first = asyncio.create_task(manager.operate("room-1"))
        await asyncio.sleep(.005)
        with pytest.raises(RoomBusy): await manager.operate("room-1")
        with pytest.raises(RoomUnavailable): await manager.operate("room-2")
        await first
        assert native.calls.count("connect") == 1
        with pytest.raises(RuntimeError): await manager.close()
        store.close()
    asyncio.run(case())


@pytest.mark.parametrize("stage", ["connect", "query"])
def test_native_shutdown_cancels_inflight_work_and_retains_quarantine(native, stage):
    async def case():
        store = RoomStore(":memory:", mode="real")
        manager = RoomManager(native.config, store, offline_fleet=native.fleet)
        if stage == "query": await manager.prepare_room("room-1")
        native.stage, native.delay = stage, 10
        task = asyncio.create_task(manager.prepare_room("room-1") if stage == "connect" else manager.operate("room-1"))
        while stage not in native.calls:
            await asyncio.sleep(0)
        with pytest.raises(RuntimeError): await manager.close()
        assert task.cancelled() and native.budget.quarantined
        assert native.calls.count("stop_notify") == native.calls.count("disconnect") == 1
        store.close()
    asyncio.run(case())


@pytest.mark.parametrize("stage", ["discovery", "connect", "subscribe", "query"])
@pytest.mark.parametrize("failure", ["error", "cancel", "timeout"])
def test_native_failure_or_cancellation_never_reconnects(native, stage, failure):
    async def case():
        native.stage = stage
        native.failure = asyncio.CancelledError() if failure == "cancel" else RuntimeError(native.grant.identity)
        backend = native.fleet.backends["room-1"]
        if failure == "timeout":
            native.failure, native.delay = None, 10
            if stage == "query": backend.timeout = .02
            else: backend.setup_timeout = .02
        with pytest.raises((BackendUnavailable, asyncio.CancelledError, TimeoutError)) as caught:
            await backend.prepare()
            await backend.snapshot(("power",))
        assert native.grant.identity not in str(caught.value)
        assert native.budget.quarantined
        with pytest.raises(BackendUnavailable): await backend.prepare()
        with pytest.raises((CleanupUncertain, BackendUnavailable)): await backend.close()
        if native.clients:
            assert native.calls.count("stop_notify") == native.calls.count("disconnect") == 1
        assert native.calls.count("discovery") == 1
    asyncio.run(case())


@pytest.mark.parametrize("operation", ["power", "fan", "frame", "characteristic", "response"])
def test_native_bridge_and_wire_block_every_unapproved_write(native, operation):
    async def case():
        backend = native.fleet.backends["room-1"]
        await backend.prepare()
        port = backend.port
        before = list(native.calls)
        with pytest.raises(BackendUnavailable):
            if operation == "power": await port.exchange_ready(CMD_SET_POWER, [(0x20, b"\x01")])
            elif operation == "fan": await port.exchange_ready(CMD_GET_FAN)
            else:
                from madoka_protocol import build_query, CMD_GET_POWER
                data = bytes(build_command(CMD_SET_POWER, [(0x20,b"\x01")])[0]) if operation == "frame" else bytes(build_query(CMD_GET_POWER)[0])
                await port.radio.write_gatt_char("invalid" if operation == "characteristic" else WRITE_CHAR,
                    data, response=operation == "response")
        assert native.calls == before
        await close_uncertain(backend)
    asyncio.run(case())


@pytest.mark.parametrize("method", ["stop_error", "disconnect_error"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_native_cleanup_faults_remain_uncertain(native, method, cancelled):
    async def case():
        backend = native.fleet.backends["room-1"]
        await backend.prepare()
        setattr(native, method, asyncio.CancelledError() if cancelled else RuntimeError(native.grant.identity))
        with pytest.raises((BackendUnavailable, RuntimeError)): await backend.close()
        assert native.calls[-2:] == ["stop_notify", "disconnect"]
        assert native.budget.quarantined
        with pytest.raises(CleanupUncertain): await backend.close()
        assert native.calls.count("disconnect") == 1
    asyncio.run(case())


def test_restart_and_offline_evidence_cannot_release_native_ownership(native, tmp_path):
    async def case():
        await native.fleet.backends["room-1"].prepare()
    asyncio.run(case())
    native.budget.close_storage()  # Simulate crash without disconnect.
    budget = PhysicalSessionBudget(tmp_path / "sessions.sqlite", native_authorization=native.grant)
    try:
        assert len(budget.owners) == len(budget.quarantined) == 1
        with pytest.raises(PhysicalBackendDisabled): budget.resolve_offline(None)
        with pytest.raises(PhysicalBackendDisabled): budget.resolve_physical(None)
        # A new grant/fleet cannot erase capacity; use the same exact bridge owner
        # only to exercise the durable admission rejection after process restart.
        backend = native.fleet.backends["room-1"]
        with pytest.raises(BackendBusy): budget.claim(backend.port, backend)
    finally:
        budget.close_storage()
    with pytest.raises(ValueError): PhysicalSessionBudget(tmp_path / "sessions.sqlite")


def test_native_offline_ledger_separation_and_capacity(native, tmp_path):
    with pytest.raises(ValueError):
        PhysicalSessionBudget(tmp_path / "larger.sqlite", ConnectionPlan("managed", 2, 1), native_authorization=native.grant)
    other = PhysicalSessionBudget(tmp_path / "offline.sqlite")
    try:
        with pytest.raises(PhysicalBackendDisabled):
            other.claim(native.fleet.backends["room-1"].port, native.fleet.backends["room-1"])
    finally: other.close_storage()
    with pytest.raises(ValueError): build_demo_app(native.config, offline_fleet=native.fleet)


def test_asynchronous_link_loss_is_immediately_stale_and_cannot_readmit(native):
    async def case():
        store = RoomStore(":memory:", mode="real")
        manager = RoomManager(native.config, store, offline_fleet=native.fleet)
        try:
            await manager.prepare_room("room-1")
            await manager.operate("room-1")
            assert manager.snapshot("room-1")["connected"]
            client = native.clients[0]
            client.is_connected = False
            client.callback(client)
            assert manager.snapshot("room-1")["stale"]
            with pytest.raises(RoomUnavailable): await manager.operate("room-1")
            with pytest.raises((RoomUnavailable, RoomBusy)): await manager.prepare_room("room-1")
        finally:
            with pytest.raises(RuntimeError): await manager.close()
            store.close()
    asyncio.run(case())


@pytest.mark.parametrize("values", [{}, {0x20:b"\x02"}, {0x20:b"\x01\x00"}])
def test_invalid_native_semantics_stop_before_next_get(native, values):
    async def case():
        native.values[0x20] = values
        backend = native.fleet.backends["room-1"]
        await backend.prepare()
        with pytest.raises(BackendUnavailable): await backend.snapshot(("power",))
        assert [v for v in native.calls if type(v) is int] == [0x20]
        assert native.budget.quarantined
        await close_uncertain(backend)
    asyncio.run(case())


def test_native_http_auth_csrf_readonly_routes_and_no_startup_ble(native, tmp_path):
    password = "a" * 43
    app = build_native_readonly_app(native.config, str(tmp_path / "history.db"), fleet=native.fleet, password=password)
    # No admission in this case, so shutdown is ordinary and BLE-free.
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for path in ("/", "/assets/app.js", "/api/session", "/api/controllers"):
            assert client.get(path).status_code == 401
        client.auth = ("operator", password)
        session = client.get("/api/session").json()
        assert session["mode"] == "native-readonly" and not session["admission_attempted"]
        assert client.post("/api/physical/admit", json={}).status_code == 403
        token = {"X-Demo-Token": session["token"]}
        assert client.post("/api/physical/admit", json={}, headers={**token, "Origin":"http://evil.test"}).status_code == 403
        for action in ("power", "setpoint", "mode", "fan", "policy", "desired", "restrictions", "reset-budget", "external-change", "simulation"):
            assert client.post("/api/controllers/room-1/" + action, json={}, headers=token).status_code == 403
        rooms = client.get("/api/controllers").json()["controllers"]
        assert len(rooms) == 6 and all(r["stale"] and not r["controls_enabled"] for r in rooms)
        assert native.calls == []


def test_native_http_admission_and_history_are_selected_and_readonly(native, tmp_path):
    app = build_native_readonly_app(native.config, str(tmp_path / "history.db"), fleet=native.fleet, password="b"*43)
    with pytest.raises(RuntimeError, match="cleanup remains uncertain"):
        with TestClient(app, base_url="http://127.0.0.1") as client:
            client.auth = ("operator", "b"*43)
            headers = {"X-Demo-Token": client.get("/api/session").json()["token"]}
            assert client.post("/api/physical/admit", json={}, headers=headers).status_code == 200
            client.portal.call(app.state.manager.operate, "room-1")
            rooms = client.get("/api/controllers").json()["controllers"]
            assert rooms[0]["connected"] and not rooms[0]["controls_enabled"]
            assert all(not room["connected"] for room in rooms[1:])
            assert client.get("/api/controllers/room-1/history").json()["samples"]
            assert not client.get("/api/controllers/room-2/history").json()["samples"]
            assert client.post("/api/physical/admit", json={}, headers=headers).status_code == 503
            for change in ("policy", "desired", "restrictions", "reset"):
                with pytest.raises(RoomUnavailable):
                    client.portal.call(app.state.manager.administer, "room-1", change, None)
    assert native.budget.quarantined and native.calls.count("connect") == 1


@pytest.mark.parametrize("arguments", [[], ["--selftest"], ["--serve-read-only"]])
def test_native_cli_defaults_and_nonattended_gates_forbid_driver(arguments):
    code = """
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname == 'bleak' or fullname.startswith('bleak.'):
   raise RuntimeError('FORBIDDEN_DRIVER')
sys.meta_path.insert(0,Guard())
import madoka_readonly_web
raise SystemExit(madoka_readonly_web.main(ARGS))
""".replace("ARGS", repr(arguments))
    result = subprocess.run([sys.executable, "-c", code], text=True, capture_output=True)
    assert result.returncode == (2 if arguments == ["--serve-read-only"] else 0)
    assert "FORBIDDEN_DRIVER" not in result.stdout + result.stderr


@pytest.mark.parametrize("gate", ["ci", "offline", "platform", "tty", "root", "version", "pythonpath", "sha", "confirmation", "reference"])
def test_fresh_native_authorization_gates(native, monkeypatch, gate):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    reference = "fresh offline reference"
    if gate == "ci": monkeypatch.setenv("CI", "true")
    if gate == "offline": monkeypatch.setenv("MADOKA_OFFLINE_ONLY", "1")
    if gate == "platform": monkeypatch.setattr(sys, "platform", "linux")
    if gate == "tty": monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    if gate == "root": monkeypatch.setattr(os, "geteuid", lambda: 0)
    if gate == "version": monkeypatch.setattr(access, "version", lambda name: "wrong")
    if gate == "pythonpath": monkeypatch.setenv("PYTHONPATH", "unreviewed")
    if gate == "sha":
        def reject(sha): raise access.PreflightError("Unreviewed checkout")
        monkeypatch.setattr(access, "verify_checkout", reject)
    if gate == "confirmation": monkeypatch.setattr(access.getpass, "getpass", lambda prompt: "READ ONLY " + native.grant.identity)
    if gate == "reference": reference = ""
    with pytest.raises(access.PreflightError):
        access.authorize(inventory=str(native.inventory), controller_id="room-1", reviewed_sha=SHA,
                         authorization_reference=reference)
    assert not native.calls


def test_native_state_fixed_location_fsync_and_canary_marker_preserved(native, tmp_path, monkeypatch):
    monkeypatch.setattr(access.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(tmp_path)))
    marker = tmp_path / ".madoka-readonly-canary"
    marker.mkdir()
    (marker / "attempt.jsonl").write_text("consumed evidence")
    calls = []
    real_fsync = os.fsync
    def fsync(fd):
        calls.append(os.fstat(fd).st_ino)
        return real_fsync(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    assert access.native_state_directory() == tmp_path / ".madoka-readonly-web"
    assert calls[-1] == tmp_path.stat().st_ino and len(calls) == 2
    assert (marker / "attempt.jsonl").read_text() == "consumed evidence"


def test_private_native_config_rejects_shared_file_and_symlink(native, tmp_path):
    path = tmp_path / "native-config.json"
    path.write_text(native.config.model_dump_json())
    path.chmod(0o600)
    assert access.load_private_config(str(path)) == native.config
    path.chmod(0o644)
    with pytest.raises(access.PreflightError): access.load_private_config(str(path))
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError): access.load_private_config(str(link))


@pytest.mark.parametrize("interrupt", [False, True])
def test_actual_native_web_entrypoint_uses_fixed_state_and_authenticated_mock_app(native, tmp_path, monkeypatch, capfd, interrupt):
    import sqlite3
    import madoka_readonly_web as web
    config = tmp_path / "config.json"
    config.write_text(native.config.model_dump_json())
    config.chmod(0o600)
    state = tmp_path / "entrypoint"
    monkeypatch.setattr(access.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(state)))
    state.mkdir(mode=0o700)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    uvicorn = ModuleType("uvicorn")
    def run(app, **kwargs):
        assert kwargs["host"] == "127.0.0.1" and kwargs["workers"] == 1
        assert not kwargs["access_log"] and not kwargs["proxy_headers"]
        assert kwargs["loop"] == "asyncio" and kwargs["timeout_graceful_shutdown"] == 30
        password = next(m.kwargs["password"] for m in app.user_middleware if "password" in m.kwargs)
        with pytest.raises(RuntimeError, match="cleanup remains uncertain"):
            with TestClient(app, base_url="http://127.0.0.1") as client:
                assert native.calls == []
                native.budget = app.state.manager.offline_fleet.budget
                assert client.get("/api/session").status_code == 401
                client.auth = ("operator", password)
                token = client.get("/api/session").json()["token"]
                print(native.grant.identity)
                os.write(2, native.grant.identity.encode())
                assert client.post("/api/physical/admit", json={}, headers={"X-Demo-Token":token}).status_code == 200
        if interrupt:
            raise KeyboardInterrupt  # Uvicorn re-raises SIGINT after draining.
    uvicorn.run = run
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    args = ["--serve-read-only", "--config", str(config), "--inventory", str(native.inventory),
            "--controller-id", "room-1", "--reviewed-sha", SHA,
            "--authorization-reference", "fresh offline fake GO"]
    assert web.main(args) == 3
    out, err = capfd.readouterr()
    assert native.grant.identity not in out + err
    assert out.count("Ephemeral password") == 1 and not err
    assert native.calls.count("connect") == native.calls.count("disconnect") == 1
    assert web.main(args) == 2  # Native unresolved ownership blocks before server.
    assert native.calls.count("connect") == 1
    with sqlite3.connect(state / ".madoka-readonly-web" / "native-sessions.sqlite") as db:
        assert db.execute("SELECT state FROM sessions").fetchone()[0] == "quarantined"
