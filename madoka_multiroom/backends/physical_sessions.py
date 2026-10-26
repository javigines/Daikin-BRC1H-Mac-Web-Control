"""Durable ownership with separate closed-model and gated native schemas.

No native session is freed by disconnect return, restart or elapsed time.
Offline evidence resolution cannot certify or release native hardware.
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
    def __init__(self, path, plan=ConnectionPlan(), *, native_authorization=None, native_grants=None, local_sessions=None):
        if type(plan) is not ConnectionPlan or str(path) == ":memory:":
            raise ValueError("Durable ledger and explicit connection plan required")
        self.plan, self.max_active = plan, plan.max_active
        self.native_authorization = native_authorization
        self.local = local_sessions is not None
        if self.local and (native_authorization is not None or native_grants is not None):
            raise ValueError("Local sessions and legacy authorizations are separate runtimes")
        self.native_grants = local_sessions if self.local else native_grants
        self.office = self.local or bool(native_authorization and getattr(native_authorization, "office", False))
        self.native = self.local or native_authorization is not None
        if self.native:
            from ..native_access import require_native_context, manual_transport
            if not self.local:
                require_native_context(native_authorization)
            if not self.office and (plan.max_active != 1 or plan.max_operations != 1):
                raise ValueError("Native read-only capacity must remain one")
            if self.office and (not self.native_grants or any(not manual_transport(require_native_context(g))
                    or key != g.room_id for key, g in self.native_grants.items())):
                raise ValueError("Exact office grants required")
        schema = "native-readonly-v1" if self.native else "physical-contract-v1"
        self.operations = asyncio.Semaphore(plan.max_operations)
        if self.local:
            # Pairing/discovery cannot consume healthy rooms' exchange slots.
            self.setup_operations = asyncio.Semaphore(plan.max_operations)
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
                if rows and [r[0] for r in rows] != [schema]:
                    raise ValueError("Unknown session schema")
                self.db.execute("INSERT OR IGNORE INTO session_meta VALUES (?)", (schema,))
                self.db.execute("CREATE TABLE IF NOT EXISTS bindings (room TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL)")
                self.db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, room TEXT NOT NULL, digest TEXT NOT NULL,
                    owner TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL)""")
                self.db.execute("""CREATE TABLE IF NOT EXISTS session_events (
                    event_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
                    at REAL NOT NULL, kind TEXT NOT NULL, details TEXT NOT NULL)""")
                self.db.execute("""CREATE TABLE IF NOT EXISTS native_operations (
                    operation_id TEXT PRIMARY KEY, room TEXT NOT NULL, action TEXT NOT NULL,
                    value TEXT NOT NULL, before_state TEXT NOT NULL, status TEXT NOT NULL,
                    observed TEXT, at REAL NOT NULL)""")
                self.db.execute("""CREATE TABLE IF NOT EXISTS native_operation_events (
                    event_id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL,
                    at REAL NOT NULL, stage TEXT NOT NULL, details TEXT NOT NULL)""")
                for row in self.db.execute("SELECT * FROM sessions WHERE state NOT IN ('released_offline','released_native')").fetchall():
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
        return frozenset(r[0] for r in self.db.execute("SELECT session_id FROM sessions WHERE state NOT IN ('released_offline','released_native')"))

    @property
    def quarantined(self):
        return frozenset(r[0] for r in self.db.execute("SELECT session_id FROM sessions WHERE state='quarantined'"))

    def records(self):
        return [dict(r) for r in self.db.execute("SELECT session_id,room,digest,state,revision FROM sessions")]

    def claim(self, port, owner):
        if type(port) is not HardenedControllerBridge or id(port) in self._reservations:
            raise PhysicalBackendDisabled("Only one closed offline bridge owner is permitted")
        room = owner.room_id
        if port.native != self.native:
            raise PhysicalBackendDisabled("Native and offline ledgers cannot share sessions")
        if self.native:
            from ..native_access import require_native_context
            grant = require_native_context(self.native_grants.get(room) if self.office else self.native_authorization)
            if port.radio.grant is not grant or port.address != grant.identity or room != grant.room_id:
                raise PhysicalBackendDisabled("Native authorization binding mismatch")
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
            if self.db.execute("SELECT 1 FROM sessions WHERE digest=? AND state NOT IN ('released_offline','released_native')", (digest,)).fetchone():
                raise BackendBusy("Identity has unresolved session ownership")
            if len(self.owners) >= self.max_active:
                raise BackendBusy("Durable session capacity reached; no automatic rotation")
            sid = secrets.token_hex(24)
            self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,1)", (sid, room, digest, self.owner_token, "reserved"))
            self._event(sid, "reserved_before_prepare", {"strategy": self.plan.strategy,
                **({"runtime": "local", "boot_time": grant.boot_time}
                   if self.native and getattr(grant, "local", False) else
                   {"reviewed_sha": grant.reviewed_sha, "authorization_reference": grant.reference}
                   if self.native else {})})
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        self._reservations[id(port)] = (port, owner, sid)
        if self.native:
            port.radio.reserved = True

    def _owned(self, port, owner):
        entry = self._reservations.get(id(port))
        if entry is None or entry[0] is not port or entry[1] is not owner:
            raise PhysicalBackendDisabled("Session owner mismatch")
        row = self.db.execute("SELECT * FROM sessions WHERE session_id=?", (entry[2],)).fetchone()
        if row is None or row["owner"] != self.owner_token or row["state"] in ("released_offline", "released_native"):
            raise PhysicalBackendDisabled("Durable owner mismatch")
        return row

    def transition(self, port, owner, state, *, details=None):
        row = self._owned(port, owner)
        if state not in ("ready", "quarantined") or (state == "ready" and row["state"] != "reserved"):
            raise CleanupUncertain("Invalid session transition")
        with self.db:
            changed = self.db.execute("UPDATE sessions SET state=?,revision=revision+1 WHERE session_id=? AND owner=? AND revision=?",
                (state, row["session_id"], self.owner_token, row["revision"]))
            if changed.rowcount != 1:
                raise CleanupUncertain("Session revision mismatch")
            self._event(row["session_id"], state, {"native_state": "unverified", **(details or {})})

    def quarantine(self, port, owner):
        self.transition(port, owner, "quarantined", details={
            'cause': getattr(owner, 'lifecycle_context', 'operation_failure'),
            'cleanup_error_class': getattr(owner, 'cleanup_error', None),
            'exchange': getattr(port, 'last_exchange', None),
            'cleanup_calls': port.cleanup_report})

    def release(self, port, owner):
        if self.office and getattr(port.radio, "release_evidence", None) and not port.cleanup_failed:
            row = self._owned(port, owner)
            with self.db:
                self.db.execute("UPDATE sessions SET state='released_native',revision=revision+1 WHERE session_id=?",
                                (row["session_id"],))
                self._event(row["session_id"], "native_application_ownership_released", {
                    **port.radio.release_evidence, 'cause': getattr(owner, 'lifecycle_context', 'explicit_close')})
            del self._reservations[id(port)]
            port.radio.reserved = False
            return
        self.quarantine(port, owner)
        raise CleanupUncertain("Public disconnect does not prove native release; review required")

    def operation(self, operation_id):
        row = self.db.execute("SELECT * FROM native_operations WHERE operation_id=?", (operation_id,)).fetchone()
        return dict(row) if row else None

    def last_operation(self, room):
        row = self.db.execute("SELECT * FROM native_operations WHERE room=? ORDER BY rowid DESC LIMIT 1", (room,)).fetchone()
        if row is None:
            return None
        return {**dict(row), "before_state": json.loads(row["before_state"]),
                "diagnostics": self.operation_events(row['operation_id']),
                "value": json.loads(row["value"]), "observed": json.loads(row["observed"]) if row["observed"] else None}

    def operation_event(self, operation_id, stage, details):
        with self.db:
            self.db.execute('INSERT INTO native_operation_events(operation_id,at,stage,details) VALUES (?,?,?,?)',
                (operation_id, time.time(), stage, json.dumps(details)))

    def operation_events(self, operation_id):
        return [{**dict(row), 'details': json.loads(row['details'])} for row in self.db.execute(
            'SELECT at,stage,details FROM native_operation_events WHERE operation_id=? ORDER BY event_id', (operation_id,))]

    def recent_admissions(self, room, seconds=600):
        return self.db.execute("SELECT COUNT(*) FROM session_events e JOIN sessions s USING(session_id) WHERE s.room=? AND e.kind='reserved_before_prepare' AND e.at>?",
                               (room, time.time()-seconds)).fetchone()[0]

    def pending_operation(self, room):
        return bool(self.db.execute("SELECT 1 FROM native_operations WHERE room=? AND status IN ('pending','uncertain')", (room,)).fetchone())

    def begin_operation(self, port, owner, operation_id, action, value, before):
        self._owned(port, owner)
        if self.operation(operation_id) or self.pending_operation(owner.room_id):
            raise BackendBusy("Duplicate or unresolved native mutation; never replay")
        with self.db:
            self.db.execute("INSERT INTO native_operations VALUES (?,?,?,?,?,'pending',NULL,?)",
                (operation_id, owner.room_id, action, json.dumps(value), json.dumps(before), time.time()))

    def finish_operation(self, operation_id, status, observed=None):
        if status not in ("confirmed", "uncertain"):
            raise ValueError("Invalid native mutation outcome")
        with self.db:
            self.db.execute("UPDATE native_operations SET status=?,observed=? WHERE operation_id=? AND status='pending'",
                (status, json.dumps(observed) if observed is not None else None, operation_id))

    def acknowledge_operations(self, room, observed, reason):
        with self.db:
            self.db.execute("UPDATE native_operations SET status='reviewed_uncertain',observed=? WHERE room=? AND status IN ('pending','uncertain') AND NOT (action='mode' AND ?)",
                            (json.dumps({"state": observed, "reason": reason}), room, bool(getattr(self, 'local', False))))

    def resolve_offline(self, evidence):
        if self.native:
            raise PhysicalBackendDisabled("Offline evidence cannot release native ownership")
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
