import hashlib
import sqlite3

import pytest

from madoka_multiroom.config import demo_config
from madoka_multiroom.storage import RoomStore, migrate_copy


def make_legacy(path, *, filter_column=False):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY,power INTEGER,mode INTEGER,cooling_setpoint REAL,heating_setpoint REAL,room_temp INTEGER,outdoor_temp INTEGER,fan INTEGER" + (",filter_dirty INTEGER" if filter_column else "") + ")")
        db.execute("INSERT INTO samples VALUES (10,1,3,24,24,23,NULL,3" + (",1" if filter_column else "") + ")")
        db.execute("CREATE TABLE device_info (id INTEGER PRIMARY KEY,model TEXT)")
        db.execute("INSERT INTO device_info VALUES (1,'Old model')")


def test_same_millisecond_and_different_rooms_never_overwrite(tmp_path):
    store = RoomStore(str(tmp_path / "demo.db"))
    store.register(demo_config().devices)
    for room in demo_config().devices:
        store.append(room.id, 100, {"setpoint": 20})
        store.append(room.id, 100, {"setpoint": 21})
        rows = store.history(room.id, 0)["samples"]
        assert len(rows) == 2 and rows[0]["state"]["setpoint"] == 21
    assert store.db.execute("SELECT COUNT(*) FROM room_samples").fetchone()[0] == 12
    store.close()


@pytest.mark.parametrize("filter_column", [False, True])
def test_migration_preserves_source_and_original_rows_and_is_idempotent(tmp_path, filter_column):
    source, target = tmp_path / "old.db", tmp_path / "new.db"
    make_legacy(source, filter_column=filter_column)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    migrate_copy(str(source), str(target), "legacy-room")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    for _ in range(2):
        store = RoomStore(str(target), mode="real", legacy_id="legacy-room")
        rows = store.history("legacy-room", 0)["samples"]
        assert len(rows) == 1 and rows[0]["captured_at_ms"] == 10000
        assert rows[0]["state"]["cooling_setpoint"] == 24
        assert store.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
        assert "Old model" in store.db.execute("SELECT info_json FROM room_info").fetchone()[0]
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        store.close()
    with pytest.raises(ValueError, match="cannot be reassigned"):
        RoomStore(str(target), mode="real", legacy_id="wrong-room")
    with pytest.raises(FileExistsError):
        migrate_copy(str(source), str(target), "legacy-room")


def test_legacy_import_requires_identity_and_demo_never_reads_it(tmp_path):
    path = tmp_path / "old.db"
    make_legacy(path)
    before = path.read_bytes()
    for mode, identity in [("demo", None), ("demo", "old"), ("real", None)]:
        with pytest.raises(ValueError, match="explicit real import"):
            RoomStore(str(path), mode=mode, legacy_id=identity)
    assert path.read_bytes() == before


def test_migration_failure_rolls_back_schema_and_rows(tmp_path):
    path = tmp_path / "broken.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE samples(ts INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO samples VALUES (42)")
    with pytest.raises(ValueError, match="unsupported legacy schema"):
        RoomStore(str(path), mode="real", legacy_id="old")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM samples").fetchall() == [(42,)]
        assert db.execute("SELECT name FROM sqlite_master WHERE name='rooms'").fetchall() == []
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0


def test_mode_and_binding_cannot_be_reused_for_different_devices(tmp_path):
    path = str(tmp_path / "demo.db")
    devices = demo_config().devices
    store = RoomStore(path)
    store.register(devices)
    store.append(devices[0].id, 1, {"power_on": True})
    renamed = devices[0].model_copy(update={"name": "New name"})
    store.register((renamed,))
    assert len(store.history(renamed.id, 0)["samples"]) == 1
    rebound = renamed.model_copy(update={"address": devices[1].address})
    with pytest.raises(ValueError, match="rebound"):
        store.register((rebound,))
    store.close()
    with pytest.raises(ValueError, match="mismatch"):
        RoomStore(path, mode="real")


def test_pagination_and_restart_are_lossless(tmp_path):
    path = str(tmp_path / "demo.db")
    store = RoomStore(path)
    store.register(demo_config().devices)
    for i in range(5):
        store.append("room-1", 100, {"value": i})
    store.close()
    store = RoomStore(path)
    first = store.history("room-1", 0, limit=2)
    second = store.history("room-1", 0, limit=2, before_id=first["next_before_id"])
    third = store.history("room-1", 0, limit=2, before_id=second["next_before_id"])
    rows = first["samples"] + second["samples"] + third["samples"]
    assert [r["state"]["value"] for r in rows] == [4, 3, 2, 1, 0]
    assert third["next_before_id"] is None
    assert store.history("room-2", 0)["samples"] == []
    assert store.history("room-1", 101)["samples"] == []
    store.close()
