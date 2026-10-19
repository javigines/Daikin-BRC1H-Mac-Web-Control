"""One selected read-only native room; all other configured rooms unavailable."""
from madoka_readonly_canary import GET_COMMANDS, observation
from ..native_access import require_authorization
from .base import BackendUnavailable
from .controller_bridge import HardenedControllerBridge
from .physical_fleet import PreparedDaikinBackend
from .physical_sessions import PhysicalSessionBudget
from .daikin import SessionState


class UnavailableRoom:
    claimed = False
    state = SessionState.NEW
    info = {"model": None, "backend": "native-room-unavailable", "hardware_enabled": False}

    async def prepare(self):
        raise BackendUnavailable("Room is outside this authorization")

    async def close(self):
        pass


class NativeReadonlyBackend(PreparedDaikinBackend):
    """Use only the four canary GET groups; no unproven limit/fan assumptions."""
    def __init__(self, room_id, port, budget):
        if not port.native or not budget.native:
            raise BackendUnavailable("Native read-only binding required")
        super().__init__(room_id, port, budget, timeout=30, setup_timeout=170)
        self.info.update(backend="native-readonly", hardware_enabled=True,
                         supported_fields=["power", "mode", "setpoint", "sensor"])

    async def _request(self, cmd, args=None):
        # Aggregate read has 30s; each of four wire exchanges has only 6s.
        if self.closed or self.stopped or self.state is not SessionState.READY:
            raise BackendUnavailable("Native session stopped")
        return await self.port.exchange_ready(cmd, args, timeout=6)

    async def _read(self, fields):
        observed = {}
        for command in GET_COMMANDS:
            objects = await self._request(command)
            group = observation(command, objects)
            if any(item["status"] != "available" for item in group.values()):
                raise BackendUnavailable("Required native observation unavailable; STOP")
            observed.update({key: item["value"] for key, item in group.items()})
        observed["setpoint"] = observed["heating_setpoint" if observed["mode_id"] == 4 else "cooling_setpoint"]
        observed["room_temp"] = observed.pop("indoor_temperature")
        observed["field_status"] = {key: "available" for key in observed}
        observed["field_status"].update(fan_id="not_queried", min_setpoint="not_queried", max_setpoint="not_queried")
        return observed

    def validate_action(self, action, value):
        raise BackendUnavailable("Native writes are disabled")

    async def apply(self, action, value):
        raise BackendUnavailable("Native writes are disabled")


class NativeReadonlyFleet:
    """Explicit factory accepts a preflight capability and matching native ledger."""
    def __init__(self, config, budget, grant):
        grant = require_authorization(grant)
        if (type(budget) is not PhysicalSessionBudget or not budget.native
                or budget.native_authorization is not grant or grant.used):
            raise BackendUnavailable("Unused native authorization and matching ledger required")
        selected = next((d for d in config.devices if d.id == grant.room_id), None)
        if selected is None or not selected.enabled or selected.address != grant.identity:
            raise BackendUnavailable("Selected private room mapping mismatch")
        if set(selected.queries.fields) != {"power", "mode", "setpoint", "sensor"}:
            raise BackendUnavailable("Native configuration must select exactly the four reviewed GET groups")
        grant.used = True
        self.config, self.budget, self.grant = config, budget, grant
        self.selected_id = grant.room_id
        self.initial_admissions = ()
        self.backends = {d.id: UnavailableRoom() for d in config.devices}
        self.backends[self.selected_id] = NativeReadonlyBackend(self.selected_id,
            HardenedControllerBridge.from_native_readonly(grant), budget)
        self.attempted = False

    def validate_config(self, config):
        if config != self.config:
            raise ValueError("Exact private native configuration required")

    def admitted(self, room_id):
        backend = self.backends[room_id]
        return (room_id == self.selected_id and backend.state is SessionState.READY
                and not backend.stopped and not backend.closed)

    def consume_admission(self, room_id):
        if room_id != self.selected_id or self.attempted:
            raise BackendUnavailable("One explicit selected admission only; no retry")
        self.attempted = True
