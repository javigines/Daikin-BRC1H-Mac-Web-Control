import asyncio
import json
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from madoka_multiroom.api import build_demo_app
from madoka_multiroom.config import AppConfig, demo_config


@pytest.fixture
def client(tmp_path):
    data = demo_config().model_dump(mode="json")
    data["stagger_seconds"] = 0
    app = build_demo_app(AppConfig.model_validate(data), str(tmp_path / "demo.db"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["X-Demo-Token"] = c.get("/api/session").json()["token"]
        # Real startup pollers, no sleeps needed to establish initial data.
        async def ready():
            while any(not r.latest for r in app.state.manager.rooms.values()):
                await asyncio.sleep(.001)
        c.portal.call(ready)
        yield c


def test_full_six_room_http_isolation_and_histories(client):
    rooms = client.get("/api/controllers").json()["controllers"]
    assert len(rooms) == 6 and all(r["simulated"] for r in rooms)
    assert "address" not in json.dumps(rooms)
    for i, room in enumerate(rooms):
        prefix = f'/api/controllers/{room["id"]}'
        for action, body in [("setpoint", {"temp": 20 + i}), ("power", {"on": i % 2 == 0}),
                             ("mode", {"mode": i % 6}), ("fan", {"speed": i % 5 + 1})]:
            assert client.post(prefix + "/" + action, json=body).status_code == 200
        hist = client.get(prefix + "/history").json()["samples"]
        assert hist[0]["state"]["setpoint"] == 20 + i
    final = client.get("/api/controllers").json()["controllers"]
    assert [r["state"]["setpoint"] for r in final] == [20, 21, 22, 23, 24, 25]


@pytest.mark.parametrize("action,body", [
    ("power", {"on": "true"}), ("power", {"on": 1}), ("power", {"on": True, "id": "room-2"}),
    ("setpoint", {"temp": "22"}), ("setpoint", {"temp": 10}), ("setpoint", {"temp": 33}),
    ("fan", {"speed": True}), ("fan", {"speed": 1.5}), ("fan", {"speed": 6}),
    ("mode", {"mode": -1}), ("mode", {"mode": 6}), ("mode", {"mode": "3"}),
    ("simulation", {"status": "restart-real"}), ("simulation", {"status": "error", "delay_ms": 2001})])
def test_strict_mutation_validation(client, action, body):
    before = client.get("/api/controllers/room-1").json()["stats"]["writes"]
    result = client.post("/api/controllers/room-1/" + action, json=body)
    assert result.status_code == 422
    assert client.get("/api/controllers/room-1").json()["stats"]["writes"] == before


@pytest.mark.parametrize("query", ["hours=nan", "hours=inf", "hours=-1", "hours=745", "limit=1001", "before_id=0"])
def test_history_limits(client, query):
    assert client.get("/api/controllers/room-1/history?" + query).status_code == 422


def test_csrf_host_json_and_body_limits(client):
    url = "/api/controllers/room-1/power"
    assert client.post(url, json={"on": True}, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post(url, json={"on": True}, headers={"Host": "evil.example"}).status_code == 400
    assert client.post(url, json={"on": True}, headers={"X-Demo-Token": "wrong"}).status_code == 403
    assert client.post(url, json={"on": True}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post(url, content='{"on":true}', headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post(url, content=" " * 16385, headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post(url, json={"on": True}, headers={"Origin": "http://127.0.0.1"}).status_code == 200
    response = client.get("/api/controllers", headers={"Origin": "https://attacker.example"})
    assert "access-control-allow-origin" not in response.headers
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_error_recovery_unknown_ids_and_retired_legacy_api(client):
    url = "/api/controllers/room-3"
    assert client.post(url + "/simulation", json={"status": "error"}).json()["stale"]
    assert client.post(url + "/setpoint", json={"temp": 24}).status_code == 503
    assert client.post("/api/controllers/room-1/setpoint", json={"temp": 25}).status_code == 200
    assert client.post(url + "/simulation", json={"status": "online"}).json()["connected"]
    assert client.post(url + "/power", json={"on": False}).status_code == 200
    assert client.get("/api/controllers/no-such-room").status_code == 404
    assert client.post("/api/controllers/no-such-room/power", json={"on": True}).status_code == 404
    assert client.post("/api/setpoint", json={"temp": 22}).status_code == 410


@pytest.mark.parametrize("count", [0, 1, 6])
def test_startup_shutdown_and_no_orphan_tasks(tmp_path, count):
    data = demo_config().model_dump(mode="json")
    data["devices"] = data["devices"][:count]
    app = build_demo_app(AppConfig.model_validate(data), str(tmp_path / "demo.db"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert len(client.get("/api/controllers").json()["controllers"]) == count
    m = app.state.manager
    assert m.closing and all(r.backend.closed for r in m.rooms.values())
    assert all(t.done() for t in m.pollers)
    with pytest.raises(sqlite3.ProgrammingError):
        m.store.history("room-1", 0)


def test_duplicate_process_database_ownership(tmp_path):
    path = str(tmp_path / "demo.db")
    first = build_demo_app(demo_config(), path)
    second = build_demo_app(demo_config(), path)
    with TestClient(first, base_url="http://127.0.0.1"):
        with pytest.raises(RuntimeError, match="already owned"):
            with TestClient(second, base_url="http://127.0.0.1"):
                pass
    # Closing the first process makes the lock available again.
    with TestClient(second, base_url="http://127.0.0.1"):
        pass


def test_local_rate_limit(client):
    statuses = [client.get("/api/session").status_code for _ in range(601)]
    assert 429 in statuses


@pytest.mark.parametrize("command", [
    ["multiroom_app.py", "--real"], ["multiroom_app.py"],
    ["multiroom_app.py", "--demo", "--host", "0.0.0.0"],
    ["daikin_server.py", "--address", "fake"], ["brc1h_spike.py", "--scan"]])
def test_cli_fails_closed_without_importing_bleak(command):
    code = """
import importlib.abc, runpy, sys
class NoBLE(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('bleak'):
            raise RuntimeError('FORBIDDEN_BLE_IMPORT')
sys.meta_path.insert(0, NoBLE())
sys.argv = ARGS
runpy.run_path(sys.argv[0], run_name='__main__')
""".replace("ARGS", repr(command))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 2
    assert "FORBIDDEN_BLE_IMPORT" not in result.stderr


def test_demo_import_has_no_legacy_or_bleak_dependency():
    code = """
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('bleak') or fullname in {'daikin_server','brc1h_spike'}:
            raise RuntimeError('forbidden import: ' + fullname)
sys.meta_path.insert(0, Guard())
from madoka_multiroom.api import build_demo_app
from madoka_multiroom.config import demo_config
from fastapi.testclient import TestClient
with TestClient(build_demo_app(demo_config()),base_url='http://127.0.0.1') as c:
    assert len(c.get('/api/controllers').json()['controllers']) == 6
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("endpoint,body", [
    ("policy", {"mode": "MONITOR", "protected": ["setpoint"]}),
    ("desired", {"setpoint": 24}),
    ("restrictions", {"maintenance": True, "reason": "service"}),
    ("reset-budget", {"reason": "reviewed"}),
    ("external-change", {"setpoint": 20})])
def test_policy_admin_requires_token_and_is_room_scoped(client, endpoint, body):
    prefix = "/api/controllers/room-1/"
    assert client.post(prefix + endpoint, json=body, headers={"X-Demo-Token": "bad"}).status_code == 403
    before = client.get("/api/controllers/room-2").json()["protection"]
    assert client.post(prefix + endpoint, json=body).status_code == 200
    assert client.get("/api/controllers/room-2").json()["protection"] == before


def test_api_desired_observed_separation_events_and_restart(tmp_path):
    path = str(tmp_path / "persistent.db")
    data = demo_config().model_dump(mode="json")
    data["stagger_seconds"] = 0
    config = AppConfig.model_validate(data)
    old_token = None
    for iteration in range(2):
        app = build_demo_app(config, path)
        with TestClient(app, base_url="http://127.0.0.1") as c:
            token = c.get("/api/session").json()["token"]
            c.headers["X-Demo-Token"] = token
            prefix = "/api/controllers/room-1/"
            if iteration == 0:
                old_token = token
                assert c.post(prefix + "policy", json={"mode": "MONITOR", "protected": ["setpoint"]}).status_code == 200
                assert c.post(prefix + "desired", json={"setpoint": 24}).status_code == 200
                assert c.post(prefix + "external-change", json={"setpoint": 20}).status_code == 200
                assert c.post(prefix + "restrictions", json={"maintenance": True, "reason": "service"}).status_code == 200
            else:
                assert token != old_token
                assert c.post(prefix + "desired", json={"setpoint": 28}, headers={"X-Demo-Token": old_token}).status_code == 403
            status = c.get(prefix[:-1]).json()
            assert status["protection"]["desired"] == {"setpoint": 24}
            assert status["protection"]["policy"]["mode"] == "MONITOR"
            assert status["protection"]["maintenance"]
            events = c.get(prefix + "events?limit=2").json()
            assert len(events["events"]) == 2 and events["next_before_id"]
            next_page = c.get(prefix + "events?before_id=" + str(events["next_before_id"])).json()
            assert not ({e["id"] for e in events["events"]} & {e["id"] for e in next_page["events"]})


def test_http_concurrency_is_bounded_per_room(client):
    m = client.app.state.manager
    m.rooms["room-3"].backend.delay_seconds = .1
    started = client.portal.start_task_soon(m.operate, "room-3")
    async def locked():
        while not m.rooms["room-3"].lock.locked():
            await asyncio.sleep(0)
    client.portal.call(locked)
    assert client.post("/api/controllers/room-3/power", json={"on": True}).status_code == 409
    assert client.post("/api/controllers/room-1/power", json={"on": True}).status_code == 200
    assert started.result(timeout=2)["connected"]


def test_failed_admin_storage_is_sanitized_and_latched(client, monkeypatch):
    m = client.app.state.manager
    def fail(*args):
        raise sqlite3.OperationalError("private path should never be echoed")
    monkeypatch.setattr(m.store, "save_control", fail)
    response = client.post("/api/controllers/room-1/policy", json={"mode": "ENFORCE", "protected": ["setpoint"]})
    assert response.status_code == 503
    assert "private" not in response.text
    status = client.get("/api/controllers/room-1").json()
    assert status["protection"]["policy"]["mode"] == "FREE"
    assert status["protection"]["stopped"] and status["stale"]
