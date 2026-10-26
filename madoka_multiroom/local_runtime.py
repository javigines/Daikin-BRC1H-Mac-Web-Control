"""Loopback native runtime: no credentials, expiry, reviewer or SHA gate.

The legacy private inventory and native ledger are reused without replacement.
Native imports occur only after explicit local startup, never at module import.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from importlib.metadata import version

from pydantic import field_validator, model_validator
from .config import AppConfig, StrictModel
from .office_access import Controls, read_private
from .local_modes import LocalControls
from .native_access import native_state_directory
from .backends.base import BackendUnavailable
from .backends.office_native import OfficeFleet


class LocalSession:
    """Identity-bound transport context for the local application, not a grant."""
    local = True

    def __init__(self, identity, room_id, boot_time):
        self.identity, self.room_id, self.boot_time = identity, room_id, boot_time


class LocalConfig(StrictModel):
    rooms: AppConfig
    controls: dict[str, LocalControls]

    @field_validator("controls", mode="before")
    @classmethod
    def legacy_controls(cls, value):
        if isinstance(value, dict):
            return {room: control.model_dump() if isinstance(control, Controls) else control
                    for room, control in value.items()}
        return value

    @model_validator(mode="after")
    def scope(self):
        if not self.rooms.devices or set(self.controls) != {d.id for d in self.rooms.devices}:
            raise ValueError("La configuración debe contener las salas y sus controles")
        if self.rooms.max_concurrent_operations > self.rooms.max_active_devices:
            raise ValueError("La concurrencia supera el número de conexiones")
        return self


def default_config_path():
    import pwd
    return Path(pwd.getpwuid(os.geteuid()).pw_dir) / "madoka-test/madoka-office-private/office.json"


def load_local_config(path):
    """Read legacy office configuration; leave certificates and original bytes alone."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("La configuración contiene claves duplicadas")
            result[key] = value
        return result
    data = json.loads(read_private(Path(path).absolute()), object_pairs_hook=unique)
    allowed = {"rooms", "controls", "host", "port", "authorities", "certificate", "private_key"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("Formato de configuración local desconocido")
    rooms = AppConfig.model_validate(data["rooms"])
    controls = {d.id: LocalControls.model_validate(data.get("controls", {}).get(d.id, {})) for d in rooms.devices}
    return LocalConfig(rooms=rooms, controls=controls)


def native_environment():
    if os.environ.get("MADOKA_OFFLINE_ONLY") or os.environ.get("CI"):
        raise ValueError("Bluetooth real está deshabilitado en DEMO y en las pruebas")
    if sys.platform != "darwin" or os.geteuid() == 0:
        raise ValueError("Ejecuta la aplicación con tu usuario de macOS, sin sudo")
    if os.environ.get("PYTHONPATH"):
        raise ValueError("Inicia sin PYTHONPATH para cargar el controlador instalado")
    if version("bleak") != "3.0.2":
        raise ValueError("Instala la versión compatible de Bleak: 3.0.2")


async def recover_local_room(budget, session, probe=None):
    """Recover one old owner only after exact identity and stable native absence.

    The exclusive existing ledger lock excludes other application processes.
    Legacy rows additionally need a real boot newer than their native events.
    Rows created by this local runtime can recover after process death in the
    same boot: no old coroutine survives the process/lock boundary. Neither
    elapsed time nor a public disconnect return establishes native absence.
    """
    from .native_recovery import probe_absence
    probe = probe or probe_absence
    digest = hashlib.sha256(session.identity.encode()).hexdigest()
    rows = [r for r in budget.records() if r["room"] == session.room_id and r["state"] == "quarantined"]
    if not rows:
        return
    if any(r["digest"] != digest for r in rows):
        raise BackendUnavailable("La identidad de la sala no coincide con su historial; revisa la configuración")
    active = {entry[2] for entry in budget._reservations.values()}
    for row in rows:
        if row["session_id"] in active:
            raise BackendUnavailable("No se pudo cerrar la conexión de esta sala; cierra la aplicación y vuelve a abrirla")
        events = budget.db.execute("SELECT kind,at,details FROM session_events WHERE session_id=? AND kind!='restart_quarantine'", (row["session_id"],)).fetchall()
        latest = max((e["at"] for e in events), default=None)
        local = [json.loads(e["details"]) for e in events if e["kind"] == "reserved_before_prepare"]
        same_boot_local = bool(local and local[0].get("runtime") == "local" and local[0].get("boot_time") == session.boot_time)
        if latest is None or latest > time.time() or (not same_boot_local and not latest < session.boot_time <= time.time()):
            raise BackendUnavailable("La conexión anterior no tiene evidencia de cierre o reinicio; las demás salas pueden funcionar")
    try:
        await probe(session.identity)
    except asyncio.CancelledError:
        raise
    except Exception:
        raise BackendUnavailable("Bluetooth no confirma la desconexión de esta sala; comprueba Bluetooth y otras aplicaciones") from None
    with budget.db:
        for row in rows:
            changed = budget.db.execute("UPDATE sessions SET state='released_native',revision=revision+1 WHERE session_id=? AND revision=? AND state='quarantined'",
                (row["session_id"], row["revision"]))
            if changed.rowcount != 1:
                raise BackendUnavailable("El estado de conexión cambió durante la recuperación")
            budget._event(row["session_id"], "local_native_absence_recovery", {
                "boot_time": session.boot_time, "identity_matched": True,
                "exclusive_process_lock": True, "native_absence_observed": True,
                "prior_process_ended": same_boot_local, "scope": "local_application_ownership",
                "global_radio_release": "not_proven"})


class LocalFleet(OfficeFleet):
    """Six independent native rooms with bounded automatic read-only recovery."""
    local = True

    def __init__(self, config, budget, sessions, *, settings_path=None):
        if any(type(session) is not LocalSession for session in sessions.values()):
            raise ValueError("Local mode uses device sessions, not legacy authorizations")
        self.sessions = sessions
        self.settings_path = Path(settings_path) if settings_path else None
        self.errors = {}
        super().__init__(config, budget, sessions)
        self.reload_controls()

    def reload_controls(self):
        if not self.settings_path or not self.settings_path.exists():
            return
        data = json.loads(read_private(self.settings_path))
        if data.get("schema") != 1:
            raise ValueError("Formato de ajustes locales desconocido")
        for room, saved in data.get("rooms", {}).items():
            if room in self.sessions:
                digest = hashlib.sha256(self.sessions[room].identity.encode()).hexdigest()
                if saved.get("identity") != digest:
                    raise ValueError("Los ajustes pertenecen a otro controlador")
                self.backends[room].controls = LocalControls.model_validate(saved["controls"])
                self.backends[room].local_configured = True

    def configure_controls(self, room_id, controls):
        controls = LocalControls.model_validate(controls.model_dump())
        evidence = controls.mode_commissioning
        current = self.backends[room_id].controls
        changes_mode_authority = (evidence != getattr(current, "mode_commissioning", None)
                                  or bool(set(controls.modes) - set(current.modes)))
        if evidence and changes_mode_authority and any(not evidence.permits(mode) for mode in controls.modes):
            raise ValueError("Revisa el inventario del mando; la evidencia ha caducado o está bloqueada")
        if not self.settings_path:
            raise ValueError("No se pueden guardar los ajustes locales")
        data = {"schema": 1, "rooms": {room: {
            "identity": hashlib.sha256(self.sessions[room].identity.encode()).hexdigest(),
            "controls": (controls if room == room_id else backend.controls).model_dump(mode="json")}
            for room, backend in self.backends.items()}}
        import tempfile
        fd, name = tempfile.mkstemp(prefix="local-controls-", suffix=".tmp", dir=self.settings_path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w") as target:
                json.dump(data, target)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.settings_path)
            fd = os.open(self.settings_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            temporary.unlink(missing_ok=True)
        self.backends[room_id].controls = controls
        self.backends[room_id].local_configured = True

    def _backend(self, room):
        backend = super()._backend(room)
        if hasattr(self, "backends") and room.id in self.backends:
            backend.controls = self.backends[room.id].controls
            backend.local_configured = getattr(self.backends[room.id], "local_configured", False)
        backend.info["backend"] = "native-local"
        return backend

    async def ensure_room(self, room_id, *, explicit=False):
        if self.admitted(room_id):
            return self.backends[room_id]
        # Three attempts per ten minutes, then a bounded new episode. Uncertain
        # writes never replay; reconnecting for observations is independent.
        now = time.monotonic()
        if not explicit and now < self.next_attempt[room_id]:
            raise BackendUnavailable(self.errors.get(room_id, "Esperando para volver a conectar"))
        if self.budget.recent_admissions(room_id) >= 3:
            self.next_attempt[room_id] = now + 60
            raise BackendUnavailable("Bluetooth no está disponible; se volverá a comprobar sin repetir órdenes")
        old = self.backends[room_id]
        if old.claimed:
            try:
                await old.close()
            except Exception:
                raise BackendUnavailable("No se ha confirmado el cierre de esta sala; cierra la aplicación y vuelve a abrirla") from None
        try:
            await recover_local_room(self.budget, self.sessions[room_id])
            backend = self._backend(next(d for d in self.config.devices if d.id == room_id))
            self.backends[room_id] = backend
            self.retries[room_id] = self.retries[room_id] % 3 + 1
            self.next_attempt[room_id] = now + (10, 30, 120)[self.retries[room_id] - 1]
            await backend.prepare()
            self.healthy_since[room_id] = time.monotonic()
            self.errors.pop(room_id, None)
            return backend
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.next_attempt[room_id] = max(self.next_attempt[room_id], now + 30)
            self.errors[room_id] = local_error(exc)
            raise BackendUnavailable(self.errors[room_id]) from None


def local_error(exc, phase=None):
    """Only known safe messages reach the browser; raw SDK details stay private."""
    if isinstance(exc, BackendUnavailable) and any(word in str(exc) for word in ("sala", "Bluetooth", "identidad", "conexión", "historial")):
        return str(exc)
    reason = getattr(getattr(exc, "reason", None), "name", "")
    if isinstance(exc, PermissionError) or reason in ("DENIED_BY_USER", "DENIED_BY_SYSTEM"):
        return "macOS no permite usar Bluetooth; revisa el permiso de la aplicación en Ajustes del Sistema"
    if reason in ("POWERED_OFF", "NO_BLUETOOTH"):
        return "Bluetooth está apagado o no está disponible en este Mac"
    if phase == "discovery":
        return "No se encuentra este Daikin; comprueba su alcance y el permiso Bluetooth de macOS"
    if phase == "connection":
        return "No se pudo conectar con este Daikin; comprueba si otra aplicación lo está utilizando"
    if isinstance(exc, TimeoutError):
        return "Bluetooth ha tardado demasiado; se volverá a conectar sin repetir órdenes"
    return "No se pudo comunicar con el Daikin; comprueba que esté cerca y Bluetooth tenga permiso en macOS"
