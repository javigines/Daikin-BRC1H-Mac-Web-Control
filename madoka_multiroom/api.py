"""Loopback DEMO API. The local token is CSRF protection, not user identity."""
import fcntl
import base64
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field

from .config import AppConfig, StrictModel
from .manager import RoomBusy, RoomManager, RoomUnavailable
from .storage import RoomStore
from .policies import PolicySpec, DesiredPatch, AdminChange, ResetBudget

WEB = Path(__file__).resolve().parent / "web"


class PowerBody(StrictModel):
    on: bool


class SetpointBody(StrictModel):
    temp: float = Field(ge=16, le=32, allow_inf_nan=False)


class ModeBody(StrictModel):
    mode: int = Field(ge=0, le=5)


class FanBody(StrictModel):
    speed: int = Field(ge=1, le=5)


class ManualBody(StrictModel):
    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    action: Literal["power", "setpoint", "mode", "fan"]
    value: bool | int | float
    reason: str = Field(min_length=3, max_length=160)


class LocalManualBody(StrictModel):
    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    action: Literal["power", "setpoint", "mode", "fan"]
    value: bool | int | float


class ReviewBody(StrictModel):
    reason: str = Field(min_length=3, max_length=160)


class SimulationBody(StrictModel):
    status: Literal["online", "disconnected", "error"]
    delay_ms: int = Field(default=0, ge=0, le=2000)


class LocalBoundary:
    """ASGI body cap before JSON parsing; exact same-origin mutation checks."""
    def __init__(self, app, token: str, authorities=None):
        self.app = app
        self.token = token
        self.window_start = time.monotonic()
        self.requests = 0
        self.authorities = authorities
        self.client_windows = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode("latin-1")
        # Only explicit loopback hostnames; no proxy forwarded-header trust.
        import re
        valid_host = (host in self.authorities and scope.get("scheme") == "https") if self.authorities else bool(re.fullmatch(r"(127\.0\.0\.1|localhost)(:[0-9]{1,5})?", host))
        if not valid_host:
            return await JSONResponse({"detail": "Invalid host"}, 400)(scope, receive, send)
        now = time.monotonic()
        if self.authorities:
            # Direct TLS client address only; forwarded headers are never trusted.
            client = (scope.get("client") or ("unknown", 0))[0]
            self.client_windows = {key: value for key, value in self.client_windows.items() if now-value[0] < 60}
            if client not in self.client_windows and len(self.client_windows) >= 512:
                return await JSONResponse({"detail": "Client capacity reached"}, 429)(scope, receive, send)
            started, count = self.client_windows.get(client, (now, 0))
            self.client_windows[client] = (started, count+1)
            exceeded = count >= 1200
        else:
            if now - self.window_start >= 60:
                self.window_start, self.requests = now, 0
            self.requests += 1
            exceeded = self.requests > 600
        if exceeded:
            return await JSONResponse({"detail": "Local request limit exceeded"}, 429,
                                      headers={"Retry-After": "60"})(scope, receive, send)
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get(b"origin")
            if (origin is not None and origin != (("https://" if self.authorities else "http://") + host).encode()
                    or headers.get(b"sec-fetch-site") == b"cross-site"
                    or not secrets.compare_digest(headers.get(b"x-demo-token", b""), self.token.encode())):
                return await JSONResponse({"detail": "Local mutation token/origin required"}, 403)(scope, receive, send)
            if headers.get(b"content-type", b"").split(b";")[0].strip() != b"application/json":
                return await JSONResponse({"detail": "JSON required"}, 415)(scope, receive, send)
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 16384:
                    return await JSONResponse({"detail": "Request too large"}, 413)(scope, receive, send)
                if not message.get("more_body", False):
                    break
            async def replay():
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            receive = replay

        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message["headers"]) + [
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")]
            await send(message)
        await self.app(scope, receive, secured_send)


class OperatorBoundary:
    """Authenticate every native resource; reject all writes except admission.

    Basic authentication is confined to loopback HTTP. LAN access is not offered.
    CSRF/origin checks are still enforced independently by LocalBoundary.
    """
    def __init__(self, app, password, office=False):
        self.app = app
        self.expected = b"Basic " + base64.b64encode(("operator:" + password).encode())
        self.office = office

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            credentials = dict(scope["headers"]).get(b"authorization", b"")
            if not secrets.compare_digest(credentials, self.expected):
                return await JSONResponse({"detail": "Operator authentication required"}, 401,
                    headers={"WWW-Authenticate": 'Basic realm="Madoka read-only", charset="UTF-8"',
                             "Cache-Control": "no-store"})(scope, receive, send)
            import re
            approved = (bool(re.fullmatch(r"/api/controllers/[a-z][a-z0-9-]{0,31}/(manual|review-outcome|policy|restrictions)", scope["path"]))
                        if self.office else scope["path"] == "/api/physical/admit")
            if scope["method"] not in {"GET", "HEAD"} and not (scope["method"] == "POST" and approved):
                return await JSONResponse({"detail": "Native runtime is read-only"}, 403)(scope, receive, send)
        return await self.app(scope, receive, send)


class LocalOnlyBoundary:
    """Exact loopback authority/peer, no cross-origin access or legacy writes."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            import re
            headers = dict(scope["headers"])
            peer = (scope.get("client") or ("", 0))[0]
            if (peer != "127.0.0.1" or scope.get("scheme") != "http"
                    or headers.get(b"host") != b"127.0.0.1:8090"):
                return await JSONResponse({"detail": "Solo se permite acceso desde este Mac en 127.0.0.1:8090"}, 403)(scope, receive, send)
            if (headers.get(b"origin", b"http://127.0.0.1:8090") != b"http://127.0.0.1:8090"
                    or headers.get(b"sec-fetch-site") == b"cross-site"):
                return await JSONResponse({"detail": "Acceso de otro origen bloqueado"}, 403)(scope, receive, send)
            approved = bool(re.fullmatch(r"/api/controllers/[a-z][a-z0-9-]{0,31}/(manual|review-outcome|local-controls)", scope["path"]))
            if scope["method"] not in {"GET", "HEAD"} and not (scope["method"] == "POST" and approved):
                return await JSONResponse({"detail": "Operación no disponible en el modo local"}, 403)(scope, receive, send)
        return await self.app(scope, receive, send)


def build_local_app(config, db_path, *, fleet):
    from .local_runtime import LocalFleet
    if type(fleet) is not LocalFleet or str(db_path) == ":memory:":
        raise ValueError("Local fleet and durable real history required")
    return _build_app(config.rooms, db_path, offline_fleet=fleet, local=True)


def build_demo_app(config: AppConfig, db_path: str = ":memory:", *, offline_fleet=None) -> FastAPI:
    if offline_fleet is not None:
        from .backends.physical_fleet import ScriptedPhysicalFleet
        if type(offline_fleet) is not ScriptedPhysicalFleet:
            raise ValueError("DEMO accepts only the closed offline fleet")
    return _build_app(config, db_path, offline_fleet=offline_fleet)


def build_native_readonly_app(config, db_path, *, fleet, password):
    from .backends.native_fleet import NativeReadonlyFleet
    if type(fleet) is not NativeReadonlyFleet or not isinstance(password, str) or len(password) < 40:
        raise ValueError("Explicit native fleet and strong ephemeral credentials required")
    if str(db_path) == ":memory:":
        raise ValueError("Separate persistent native observation history required")
    return _build_app(config, db_path, offline_fleet=fleet, native_password=password)


def build_office_app(office, db_path, *, fleet, password):
    from .backends.office_native import OfficeFleet
    if type(fleet) is not OfficeFleet or len(password) < 40 or str(db_path) == ":memory:":
        raise ValueError("Scoped office fleet, durable history and strong credentials required")
    return _build_app(office.rooms, db_path, offline_fleet=fleet, native_password=password, office=office)


def _build_app(config, db_path, *, offline_fleet=None, native_password=None, office=None, local=False):
    token = secrets.token_urlsafe(32)
    native = native_password is not None or local
    runtime_mode = "native-local" if local else "native-office" if office else "native-readonly" if native else "demo"

    @asynccontextmanager
    async def lifespan(app):
        lock_file = None
        store = None
        manager = None
        try:
            if db_path != ":memory:":
                lock_file = open(str(Path(db_path).resolve()) + ".lock", "a")
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("Database already owned by another application process") from None
            store = RoomStore(db_path, mode="real" if native else "demo")
            manager = RoomManager(config, store, offline_fleet=offline_fleet)
            app.state.manager = manager
            await manager.start()
            yield
        finally:
            try:
                if manager:
                    await manager.close()
            finally:
                if store:
                    store.close()
                if lock_file:
                    lock_file.close()

    app = FastAPI(title="Madoka · DEMO", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalBoundary, token=token, authorities=office.authorities if office else None)
    if local:
        app.add_middleware(LocalOnlyBoundary)
    elif native:
        app.add_middleware(OperatorBoundary, password=native_password, office=bool(office))

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Do not echo submitted values, config UUIDs or exception strings.
        return JSONResponse({"detail": "Invalid request parameters"}, 422)

    @app.exception_handler(sqlite3.Error)
    async def storage_failed(request, exc):
        return JSONResponse({"detail": "Local storage unavailable; review before retrying"}, 503)

    @app.exception_handler(RoomBusy)
    async def busy(request, exc):
        return JSONResponse({"detail": "El Daikin está ocupado; espera a que termine la operación" if local else "Controller busy; wait for current operation"}, 409)

    @app.exception_handler(RoomUnavailable)
    async def unavailable(request, exc):
        return JSONResponse({"detail": str(exc) if local else "Controller unavailable; recover manually"}, 503)

    def lookup(request, controller_id):
        manager = request.app.state.manager
        if controller_id not in manager.rooms:
            raise HTTPException(404, "Unknown controller")
        return manager

    async def mutate(request, controller_id, action, value):
        manager = lookup(request, controller_id)
        snapshot = manager.snapshot(controller_id)
        if not snapshot["enabled"] or snapshot["error"]:
            raise RoomUnavailable()
        if snapshot["stale"]:
            raise HTTPException(409, "No fresh simulated state; wait for a poll")
        try:
            return await manager.operate(controller_id, action, value)
        except ValueError:
            raise HTTPException(422, "Setpoint outside configured limits") from None

    @app.get("/")
    async def index():
        if local:
            from fastapi.responses import HTMLResponse
            page = (WEB / "index.html").read_text().replace("DEMO · Bluetooth inactivo", "Bluetooth local")
            page = page.replace("Control local de seis salas simuladas, sin Bluetooth.", "Control local de seis Daikin Madoka.")
            for view in ("admin", "lab"):
                page = page.replace(f'data-view="{view}"', f'data-view="{view}" hidden')
            return HTMLResponse(page)
        return FileResponse(WEB / "index.html")

    @app.get("/assets/{filename}")
    async def asset(filename: Literal["app.js", "style.css"]):
        return FileResponse(WEB / filename)

    @app.get("/api/session")
    async def session():
        return {"mode": runtime_mode, "hardware_enabled": bool(native and any(offline_fleet.admitted(r.id) for r in config.devices)), "token": token,
                **({"selected_id": offline_fleet.selected_id,
                    "admission_attempted": offline_fleet.attempted} if native and not office and not local else {})}

    @app.post("/api/physical/admit")
    async def admit(request: Request, body: StrictModel):
        if not native or office or local:
            raise HTTPException(403, "Native admission unavailable in DEMO")
        manager = request.app.state.manager
        try:
            await manager.prepare_room(offline_fleet.selected_id)
            return {"status": "ready", "native_release": "unverified"}
        except Exception:
            raise HTTPException(503, "Admission stopped; preserve records and obtain supervised review") from None

    @app.get("/api/controllers")
    async def controllers(request: Request):
        manager = request.app.state.manager
        return {"mode": runtime_mode, "controllers": [manager.snapshot(rid) for rid in manager.rooms]}

    @app.get("/api/controllers/{controller_id}")
    async def status(request: Request, controller_id: str):
        return lookup(request, controller_id).snapshot(controller_id)

    @app.get("/api/controllers/{controller_id}/history")
    async def history(request: Request, controller_id: str,
                      hours: float = Query(default=24, gt=0, le=744, allow_inf_nan=False),
                      limit: int = Query(default=200, ge=1, le=1000),
                      before_id: int | None = Query(default=None, ge=1)):
        manager = lookup(request, controller_id)
        return manager.store.history(controller_id, int((time.time() - hours * 3600) * 1000),
                                     limit=limit, before_id=before_id)

    @app.post("/api/controllers/{controller_id}/power")
    async def power(request: Request, controller_id: str, body: PowerBody):
        return await mutate(request, controller_id, "power", body.on)

    @app.post("/api/controllers/{controller_id}/setpoint")
    async def setpoint(request: Request, controller_id: str, body: SetpointBody):
        return await mutate(request, controller_id, "setpoint", body.temp)

    @app.post("/api/controllers/{controller_id}/mode")
    async def mode(request: Request, controller_id: str, body: ModeBody):
        return await mutate(request, controller_id, "mode", body.mode)

    @app.post("/api/controllers/{controller_id}/fan")
    async def fan(request: Request, controller_id: str, body: FanBody):
        return await mutate(request, controller_id, "fan", body.speed)

    if local:
        from .office_access import Controls

        @app.post("/api/controllers/{controller_id}/manual")
        async def local_manual(request: Request, controller_id: str, body: LocalManualBody):
            try:
                return await lookup(request, controller_id).native_manual(controller_id,
                    body.action, body.value, body.operation_id, "Local browser action")
            except ValueError:
                raise HTTPException(422, "Este control o valor no está disponible en este Daikin") from None

        @app.post("/api/controllers/{controller_id}/review-outcome")
        async def local_observe(request: Request, controller_id: str, body: StrictModel):
            return await lookup(request, controller_id).native_recover(controller_id, "User requested current observations")

        @app.post("/api/controllers/{controller_id}/local-controls")
        async def local_controls(request: Request, controller_id: str, body: Controls):
            manager = lookup(request, controller_id)
            room = manager.room(controller_id)
            if room.lock.locked():
                raise RoomBusy()
            async with room.lock:
                offline_fleet.configure_controls(controller_id, body)
            return manager.snapshot(controller_id)
    else:
        @app.post("/api/controllers/{controller_id}/manual")
        async def native_manual(request: Request, controller_id: str, body: ManualBody):
            manager = lookup(request, controller_id)
            try:
                return await manager.native_manual(controller_id, body.action, body.value, body.operation_id, body.reason)
            except ValueError:
                raise HTTPException(422, "Unsupported native control or value") from None

        @app.post("/api/controllers/{controller_id}/review-outcome")
        async def review_outcome(request: Request, controller_id: str, body: ReviewBody):
            return await lookup(request, controller_id).native_recover(controller_id, body.reason)

    @app.post("/api/controllers/{controller_id}/simulation")
    async def simulation(request: Request, controller_id: str, body: SimulationBody):
        return await lookup(request, controller_id).simulate(controller_id, body.status, body.delay_ms / 1000)

    @app.post("/api/controllers/{controller_id}/external-change")
    async def external(request: Request, controller_id: str, body: DesiredPatch):
        return await lookup(request, controller_id).external_change(controller_id, body.values())

    @app.post("/api/controllers/{controller_id}/policy")
    async def policy(request: Request, controller_id: str, body: PolicySpec):
        return await lookup(request, controller_id).administer(controller_id, "policy", body)

    @app.post("/api/controllers/{controller_id}/desired")
    async def desired(request: Request, controller_id: str, body: DesiredPatch):
        return await lookup(request, controller_id).administer(controller_id, "desired", body)

    @app.post("/api/controllers/{controller_id}/restrictions")
    async def restrictions(request: Request, controller_id: str, body: AdminChange):
        return await lookup(request, controller_id).administer(controller_id, "restrictions", body)

    @app.post("/api/controllers/{controller_id}/reset-budget")
    async def reset(request: Request, controller_id: str, body: ResetBudget):
        return await lookup(request, controller_id).administer(controller_id, "reset", body)

    @app.get("/api/controllers/{controller_id}/events")
    async def events(request: Request, controller_id: str,
                     limit: int = Query(default=100, ge=1, le=1000),
                     before_id: int | None = Query(default=None, ge=1)):
        return lookup(request, controller_id).store.events(controller_id, limit=limit, before_id=before_id)

    @app.api_route("/api/{legacy:path}", methods=["GET", "POST"])
    async def legacy_api(legacy: str):
        raise HTTPException(410, "Legacy API retired. Use /api/controllers/{logical-id}/...")

    return app
