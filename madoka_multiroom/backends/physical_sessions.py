"""Durable physical-session lifecycle model, exercised only with closed radios.

No session is freed by disconnect return, restart, elapsed time or a new budget.
Offline evidence resolution is deliberately incapable of certifying hardware.
"""
import asyncio
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time
from typing import Literal

from .daikin import BackendBusy, CleanupUncertain, PhysicalBackendDisabled
from .controller_bridge import HardenedControllerBridge


@dataclass(frozen=True)
class ConnectionPlan:
    strategy: Literal["retained", "managed"] = "managed"
    max_active: int = 1
    max_operations: int = 1

    def __post_init__(self):
        if (self.strategy not in ("retained", "managed")
                or type(self.max_active) is not int or not 1 <= self.max_active <= 6
                or type(self.max_operations) is not int or not 1 <= self.max_operations <= self.max_active):
            raise ValueError("Invalid connection strategy/capacity")


@dataclass(frozen=True)
class OfflineRecoveryEvidence:
    session_id: str
    identity_digest: str
    revision: int
    operator: str
    independent_reviewer: str
    evidence_reference: str
    native_disconnected: bool
    wall_responsive: bool
    other_clients_excluded: bool

    def validate(self):
        texts = (self.operator, self.independent_reviewer, self.evidence_reference)
        if (any(not isinstance(v, str) or not 3 <= len(v.strip()) <= 160 for v in texts)
                or self.operator.strip() == self.independent_reviewer.strip()
                or any(v is not True for v in (self.native_disconnected, self.wall_responsive, self.other_clients_excluded))):
            raise ValueError("Independent identity-bound evidence required")


class PhysicalSessionBudget:
    """One ledger owner per process and durable per-UUID reservations.

    All unresolved sessions count toward capacity. Explicit offline resolution
    models the evidence workflow; no route accepts these attestations as real
    hardware evidence. Storage must be private local trustworthy durable media.
    """
    def __init__(self, path, plan=ConnectionPlan()):
        if type(plan) is not ConnectionPlan or str(path) == ":memory:":
            raise ValueError("Durable ledger and explicit connection plan required")
        self.plan, self.max_active = plan, plan.max_active
        self.operations = asyncio.Semaphore(plan.max_operations)
        self._reservations = {}
        self.owner_token = secrets.token_hex(24)
        self.db = None
        self.lock_fd = None
        source = Path(path).absolute()
        try:
            self.lock_fd = os.open(str(source) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(source, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise ValueError("Ledger must be a private regular file")
                os.fsync(fd)
            finally:
                os.close(fd)
            directory_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self.db = sqlite3.connect(source, timeout=0, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA journal_mode=DELETE")
            tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and "session_meta" not in tables:
                raise ValueError("Not a physical-session ledger")
            with self.db:
                self.db.execute("CREATE TABLE IF NOT EXISTS session_meta (kind TEXT PRIMARY KEY)")
                rows = self.db.execute("SELECT kind FROM session_meta").fetchall()
                if rows and [r[0] for r in rows] != ["physical-contract-v1"]:
                    raise ValueError("Unknown session schema")
                self.db.execute("INSERT OR IGNORE INTO session_meta VALUES ('physical-contract-v1')")
                self.db.execute("CREATE TABLE IF NOT EXISTS bindings (room TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL)")
                self.db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, room TEXT NOT NULL, digest TEXT NOT NULL,
                    owner TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL)""")
                self.db.execute("""CREATE TABLE IF NOT EXISTS session_events (
                    event_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
                    at REAL NOT NULL, kind TEXT NOT NULL, details TEXT NOT NULL)""")
                for row in self.db.execute("SELECT * FROM sessions WHERE state!='released_offline'").fetchall():
                    self.db.execute("UPDATE sessions SET state='quarantined',revision=revision+1 WHERE session_id=?", (row["session_id"],))
                    self._event(row["session_id"], "restart_quarantine", {})
        except BaseException:
            self.close_storage()
            raise

    def _event(self, session_id, kind, details):
        self.db.execute("INSERT INTO session_events(session_id,at,kind,details) VALUES (?,?,?,?)",
                        (session_id, time.time(), kind, json.dumps(details)))

    @property
    def owners(self):
        return frozenset(r[0] for r in self.db.execute("SELECT session_id FROM sessions WHERE state!='released_offline'"))

    @property
    def quarantined(self):
        return frozenset(r[0] for r in self.db.execute("SELECT session_id FROM sessions WHERE state='quarantined'"))

    def records(self):
        return [dict(r) for r in self.db.execute("SELECT session_id,room,digest,state,revision FROM sessions")]

    def claim(self, port, owner):
        if type(port) is not HardenedControllerBridge or id(port) in self._reservations:
            raise PhysicalBackendDisabled("Only one closed offline bridge owner is permitted")
        room = owner.room_id
        digest = hashlib.sha256(port.address.encode()).hexdigest()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT digest FROM bindings WHERE room=?", (room,)).fetchone()
            if row and row[0] != digest:
                raise PhysicalBackendDisabled("Persistent room identity mismatch")
            self.db.execute("INSERT OR IGNORE INTO bindings VALUES (?,?)", (room, digest))
            bound = self.db.execute("SELECT room FROM bindings WHERE digest=?", (digest,)).fetchone()
            if bound[0] != room:
                raise PhysicalBackendDisabled("Identity already bound to another room")
            if self.db.execute("SELECT 1 FROM sessions WHERE digest=? AND state!='released_offline'", (digest,)).fetchone():
                raise BackendBusy("Identity has unresolved session ownership")
            if len(self.owners) >= self.max_active:
                raise BackendBusy("Durable session capacity reached; no automatic rotation")
            sid = secrets.token_hex(24)
            self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,1)", (sid, room, digest, self.owner_token, "reserved"))
            self._event(sid, "reserved_before_prepare", {"strategy": self.plan.strategy})
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        self._reservations[id(port)] = (port, owner, sid)

    def _owned(self, port, owner):
        entry = self._reservations.get(id(port))
        if entry is None or entry[0] is not port or entry[1] is not owner:
            raise PhysicalBackendDisabled("Session owner mismatch")
        row = self.db.execute("SELECT * FROM sessions WHERE session_id=?", (entry[2],)).fetchone()
        if row is None or row["owner"] != self.owner_token or row["state"] == "released_offline":
            raise PhysicalBackendDisabled("Durable owner mismatch")
        return row

    def transition(self, port, owner, state):
        row = self._owned(port, owner)
        if state not in ("ready", "quarantined") or (state == "ready" and row["state"] != "reserved"):
            raise CleanupUncertain("Invalid session transition")
        with self.db:
            changed = self.db.execute("UPDATE sessions SET state=?,revision=revision+1 WHERE session_id=? AND owner=? AND revision=?",
                (state, row["session_id"], self.owner_token, row["revision"]))
            if changed.rowcount != 1:
                raise CleanupUncertain("Session revision mismatch")
            self._event(row["session_id"], state, {"native_state": "unverified"})

    def quarantine(self, port, owner):
        self.transition(port, owner, "quarantined")

    def release(self, port, owner):
        self.quarantine(port, owner)
        raise CleanupUncertain("Public disconnect does not prove native release; review required")

    def resolve_offline(self, evidence):
        if type(evidence) is not OfflineRecoveryEvidence:
            raise ValueError("Typed offline evidence required")
        evidence.validate()
        row = self.db.execute("SELECT * FROM sessions WHERE session_id=?", (evidence.session_id,)).fetchone()
        if (row is None or row["state"] != "quarantined" or row["digest"] != evidence.identity_digest
                or row["revision"] != evidence.revision):
            raise CleanupUncertain("Stale or mismatched evidence")
        with self.db:
            changed = self.db.execute("UPDATE sessions SET state='released_offline',revision=revision+1 WHERE session_id=? AND revision=? AND state='quarantined'",
                                      (evidence.session_id, evidence.revision))
            if changed.rowcount != 1:
                raise CleanupUncertain("Evidence revision mismatch")
            self._event(evidence.session_id, "released_offline_not_native_proof", {
                "operator": evidence.operator, "reviewer": evidence.independent_reviewer,
                "evidence_reference": evidence.evidence_reference})

    def resolve_physical(self, *args, **kwargs):
        raise PhysicalBackendDisabled("Physical recovery verifier is not enabled")

    def close_storage(self):
        # No lifecycle transitions or capacity release: durable records survive.
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
