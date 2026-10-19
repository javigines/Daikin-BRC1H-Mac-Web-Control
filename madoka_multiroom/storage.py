"""Versioned multiroom SQLite, append-only samples and explicit legacy import.

Short synchronous transactions run on the app's event-loop thread. No automatic
retention/delete. Legacy tables remain untouched; never use this DB in old code.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

from .config import DeviceConfig


class RoomStore:
    def __init__(self, path: str, *, mode: str = "demo", legacy_id: str | None = None):
        if mode not in {"demo", "real"}:
            raise ValueError("invalid storage mode")
        self.db = sqlite3.connect(path, timeout=2, check_same_thread=False)
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("BEGIN IMMEDIATE")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 2):
                raise ValueError("unsupported database version")
            tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "samples" in tables and (mode != "real" or not legacy_id):
                raise ValueError("legacy database requires explicit real import and logical identity")
            self.db.execute("CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = self.db.execute("SELECT value FROM app_meta WHERE key='mode'").fetchone()
            if row and row[0] != mode:
                raise ValueError("demo/real database mismatch")
            self.db.execute("INSERT OR IGNORE INTO app_meta VALUES ('mode',?)", (mode,))
            self.db.execute("CREATE TABLE IF NOT EXISTS rooms (id TEXT PRIMARY KEY, name TEXT NOT NULL, binding TEXT)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS room_samples (
                sample_id INTEGER PRIMARY KEY AUTOINCREMENT,
                controller_id TEXT NOT NULL REFERENCES rooms(id),
                captured_at_ms INTEGER NOT NULL,
                state_json TEXT NOT NULL)""")
            self.db.execute("CREATE INDEX IF NOT EXISTS room_time ON room_samples(controller_id,captured_at_ms,sample_id)")
            self.db.execute("CREATE TABLE IF NOT EXISTS room_info (controller_id TEXT PRIMARY KEY REFERENCES rooms(id), info_json TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS room_control (controller_id TEXT PRIMARY KEY REFERENCES rooms(id), record_json TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS room_observed (controller_id TEXT PRIMARY KEY REFERENCES rooms(id), captured_at_ms INTEGER NOT NULL, state_json TEXT NOT NULL)")
            self.db.execute("""CREATE TABLE IF NOT EXISTS control_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, controller_id TEXT NOT NULL REFERENCES rooms(id),
                created_at_ms INTEGER NOT NULL, kind TEXT NOT NULL, details_json TEXT NOT NULL)""")
            self.db.execute("CREATE INDEX IF NOT EXISTS event_room ON control_events(controller_id,event_id)")
            imported = self.db.execute("SELECT value FROM app_meta WHERE key='legacy_id'").fetchone()
            if imported and legacy_id and imported[0] != legacy_id:
                raise ValueError("legacy identity cannot be reassigned")
            if "samples" in tables and not imported:
                # Validate identity without assigning any guessed physical address.
                import re
                if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", legacy_id):
                    raise ValueError("invalid legacy identity")
                self.db.execute("INSERT INTO rooms VALUES (?,?,NULL)", (legacy_id, "Imported legacy controller"))
                cols = {r[1] for r in self.db.execute("PRAGMA table_info(samples)")}
                required = {"ts", "power", "mode", "cooling_setpoint", "heating_setpoint", "room_temp", "outdoor_temp", "fan"}
                if not required <= cols:
                    raise ValueError("unsupported legacy schema")
                names = ["ts", "power", "mode", "cooling_setpoint", "heating_setpoint", "room_temp", "outdoor_temp", "fan"]
                if "filter_dirty" in cols:
                    names.append("filter_dirty")
                for row in self.db.execute("SELECT " + ",".join(names) + " FROM samples ORDER BY ts"):
                    old = dict(zip(names, row))
                    self.db.execute("INSERT INTO room_samples(controller_id,captured_at_ms,state_json) VALUES (?,?,?)",
                                    (legacy_id, old["ts"] * 1000, json.dumps(old, allow_nan=False)))
                if "device_info" in tables:
                    self.db.row_factory = sqlite3.Row
                    info = self.db.execute("SELECT * FROM device_info WHERE id=1").fetchone()
                    self.db.row_factory = None
                    if info:
                        self.db.execute("INSERT INTO room_info VALUES (?,?)", (legacy_id, json.dumps(dict(info))))
                self.db.execute("INSERT INTO app_meta VALUES ('legacy_id',?)", (legacy_id,))
            self.db.execute("PRAGMA user_version=2")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def register(self, devices: tuple[DeviceConfig, ...]):
        with self.db:
            for device in devices:
                binding = hashlib.sha256(device.address.encode()).hexdigest()
                row = self.db.execute("SELECT binding FROM rooms WHERE id=?", (device.id,)).fetchone()
                if row and row[0] is not None and row[0] != binding:
                    raise ValueError("logical id cannot be rebound to another device")
                self.db.execute("""INSERT INTO rooms(id,name,binding) VALUES (?,?,?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,binding=excluded.binding""",
                                (device.id, device.name, binding))

    def append(self, room_id: str, captured_at_ms: int, state: dict):
        encoded = json.dumps(state, allow_nan=False)
        with self.db:
            self.db.execute("INSERT INTO room_samples(controller_id,captured_at_ms,state_json) VALUES (?,?,?)",
                            (room_id, captured_at_ms, encoded))
            self.db.execute("""INSERT INTO room_observed VALUES (?,?,?) ON CONFLICT(controller_id)
                DO UPDATE SET captured_at_ms=excluded.captured_at_ms,state_json=excluded.state_json""",
                            (room_id, captured_at_ms, encoded))

    def control(self, room_id: str):
        row = self.db.execute("SELECT record_json FROM room_control WHERE controller_id=?", (room_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_control(self, room_id: str, record: dict, kind: str, details: dict, at: float):
        with self.db:
            self.db.execute("""INSERT INTO room_control VALUES (?,?) ON CONFLICT(controller_id)
                DO UPDATE SET record_json=excluded.record_json""", (room_id, json.dumps(record, allow_nan=False)))
            self.db.execute("INSERT INTO control_events(controller_id,created_at_ms,kind,details_json) VALUES (?,?,?,?)",
                            (room_id, int(at * 1000), kind, json.dumps(details, allow_nan=False)))

    def events(self, room_id: str, *, limit: int = 100, before_id: int | None = None):
        if not 1 <= limit <= 1000:
            raise ValueError("event limit out of range")
        rows = self.db.execute("""SELECT event_id,created_at_ms,kind,details_json FROM control_events
            WHERE controller_id=? AND (? IS NULL OR event_id<?) ORDER BY event_id DESC LIMIT ?""",
                               (room_id, before_id, before_id, limit + 1)).fetchall()
        return {"events": [{"id": r[0], "created_at_ms": r[1], "kind": r[2], "details": json.loads(r[3])}
                           for r in rows[:limit]], "next_before_id": rows[limit - 1][0] if len(rows) > limit else None}

    def history(self, room_id: str, since_ms: int, *, limit: int = 200, before_id: int | None = None):
        if not 1 <= limit <= 1000:
            raise ValueError("history limit out of range")
        rows = self.db.execute("""SELECT sample_id,captured_at_ms,state_json FROM room_samples
            WHERE controller_id=? AND captured_at_ms>=? AND (? IS NULL OR sample_id<?)
            ORDER BY sample_id DESC LIMIT ?""", (room_id, since_ms, before_id, before_id, limit + 1)).fetchall()
        page = rows[:limit]
        return {"samples": [{"id": r[0], "captured_at_ms": r[1], "state": json.loads(r[2])} for r in page],
                "next_before_id": page[-1][0] if len(rows) > limit else None}

    def close(self):
        self.db.close()


def migrate_copy(source: str, destination: str, legacy_id: str):
    """SQLite online backup to a NEW file; source is read-only and never edited."""
    target = Path(destination)
    with target.open("xb"):
        pass
    try:
        uri = Path(source).resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as original:
            backup = sqlite3.connect(target)
            try:
                original.backup(backup)
            finally:
                backup.close()
        store = RoomStore(str(target), mode="real", legacy_id=legacy_id)
        store.close()
    except BaseException:
        # Preserve failed copies for inspection; never overwrite or delete source.
        raise
