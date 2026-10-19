"""Loopback DEMO API. The local token is CSRF protection, not user identity."""
import fcntl
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


class SimulationBody(StrictModel):
    status: Literal["online", "disconnected", "error"]
    delay_ms: int = Field(default=0, ge=0, le=2000)


class LocalBoundary:
    """ASGI body cap before JSON parsing; exact same-origin mutation checks."""
    def __init__(self, app, token: str):
        self.app = app
        self.token = token
        self.window_start = time.monotonic()
        self.requests = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode("latin-1")
        # Only explicit loopback hostnames; no proxy forwarded-header trust.
        import re
        if not re.fullmatch(r"(127\.0\.0\.1|localhost)(:[0-9]{1,5})?", host):
            return await JSONResponse({"detail": "Invalid host"}, 400)(scope, receive, send)
        now = time.monotonic()
        if now - self.window_start >= 60:
            self.window_start, self.requests = now, 0
        self.requests += 1
        if self.requests > 600:
            return await JSONResponse({"detail": "Local request limit exceeded"}, 429,
                                      headers={"Retry-After": "60"})(scope, receive, send)
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get(b"origin")
            if (origin is not None and origin != ("http://" + host).encode()
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


def build_demo_app(config: AppConfig, db_path: str = ":memory:") -> FastAPI:
    token = secrets.token_urlsafe(32)

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
            store = RoomStore(db_path, mode="demo")
            manager = RoomManager(config, store)
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
    app.add_middleware(LocalBoundary, token=token)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Do not echo submitted values, config UUIDs or exception strings.
        return JSONResponse({"detail": "Invalid request parameters"}, 422)

    @app.exception_handler(sqlite3.Error)
    async def storage_failed(request, exc):
        return JSONResponse({"detail": "Local storage unavailable; review before retrying"}, 503)

    @app.exception_handler(RoomBusy)
    async def busy(request, exc):
        return JSONResponse({"detail": "Controller busy; wait for current operation"}, 409)

    @app.exception_handler(RoomUnavailable)
    async def unavailable(request, exc):
        return JSONResponse({"detail": "Simulated controller unavailable; recover manually"}, 503)

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
        return FileResponse(WEB / "index.html")

    @app.get("/assets/{filename}")
    async def asset(filename: Literal["app.js", "style.css"]):
        return FileResponse(WEB / filename)

    @app.get("/api/session")
    async def session():
        return {"mode": "demo", "hardware_enabled": False, "token": token}

    @app.get("/api/controllers")
    async def controllers(request: Request):
        manager = request.app.state.manager
        return {"mode": "demo", "controllers": [manager.snapshot(rid) for rid in manager.rooms]}

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
