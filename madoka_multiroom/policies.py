"""Persistent desired state and bounded DEMO reconciliation, never native locks.

Only authorized service calls set desired values. Observations never become
desired. No wire commands, BLE adapters, scheduler or notifications to others.
"""
import copy
import time
from typing import Literal

from pydantic import Field, field_validator, model_validator

from madoka_protocol import round_setpoint
from .config import StrictModel

FieldName = Literal["setpoint", "mode_id", "fan_id", "power_on"]
ACTIONS = {"setpoint": "setpoint", "mode_id": "mode", "fan_id": "fan", "power_on": "power"}
FIELDS = {v: k for k, v in ACTIONS.items()}


class PolicySpec(StrictModel):
    mode: Literal["FREE", "MONITOR", "ENFORCE"] = "FREE"
    protected: tuple[FieldName, ...] = ()
    allow_auto_on: bool = False
    confirmations: int = Field(default=2, ge=2, le=5)
    stabilization_seconds: float = Field(default=5.0, ge=1, le=300, allow_inf_nan=False)
    cooldown_seconds: float = Field(default=15.0, ge=1, le=600, allow_inf_nan=False)
    max_attempts: int = Field(default=3, ge=1, le=5)

    @field_validator("protected", mode="before")
    @classmethod
    def to_tuple(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("protected")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate protected field")
        return value

    @model_validator(mode="after")
    def explicit_power(self):
        if self.allow_auto_on and "power_on" not in self.protected:
            raise ValueError("auto-on requires protected power")
        return self


class DesiredPatch(StrictModel):
    setpoint: float | None = Field(default=None, ge=16, le=32, allow_inf_nan=False)
    mode_id: int | None = Field(default=None, ge=0, le=5)
    fan_id: int | None = Field(default=None, ge=1, le=5)
    power_on: bool | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("desired patch must explicitly set non-null values")
        return self

    def values(self):
        data = self.model_dump(exclude_unset=True)
        if "setpoint" in data:
            data["setpoint"] = round_setpoint(data["setpoint"])
        return data


class AdminChange(StrictModel):
    maintenance: bool = False
    suspend_seconds: int = Field(default=0, ge=0, le=3600)
    reason: str = Field(min_length=3, max_length=160)


class ResetBudget(StrictModel):
    reason: str = Field(min_length=3, max_length=160)


class PolicyEngine:
    def __init__(self, store, room_id: str, clock=time.time):
        self.store, self.room_id, self.clock = store, room_id, clock
        self.record = store.control(room_id) or {
            "policy": PolicySpec().model_dump(mode="json"), "desired": {},
            "maintenance": False, "suspended_until": 0.0,
            "attempts": 0, "stopped": False, "last_attempt_at": None,
            "auto_on_used": False, "deviations": {}}
        self._stop_latched = self.record["stopped"]
        self._committed = copy.deepcopy(self.record)
        self.candidates = {}  # fresh consecutive observations required after restart
        self.last_monotonic = None
        self.awaiting = None

    @property
    def spec(self):
        return PolicySpec.model_validate(self.record["policy"])

    def save(self, kind, details):
        try:
            self.store.save_control(self.room_id, self.record, kind, details, self.clock())
        except BaseException:
            # A rejected administration must never become effective later via a
            # successful observation. SQLite record and event commit together.
            self.record = copy.deepcopy(self._committed)
            self.record["stopped"] |= self._stop_latched
            self.candidates.clear()
            self.awaiting = None
            raise
        self._committed = copy.deepcopy(self.record)

    def view(self):
        data = copy.deepcopy(self.record)
        data["suspended"] = self.clock() < data["suspended_until"]
        data["notification"] = bool(data["deviations"]) and self.spec.mode in {"MONITOR", "ENFORCE"}
        data["native_lock"] = "NOT_APPLIED_NOT_VERIFIED"
        data["correction_backend"] = "SIMULATOR_ONLY"
        return data

    def desired(self, patch: dict):
        # This method is only called by authorized mutation paths, never by observe.
        validated = DesiredPatch.model_validate(patch).values()
        self.record["desired"].update(validated)
        self.candidates.clear()
        self.awaiting = None
        self.save("desired_changed", {"source": "authorized_local_api", "values": validated})

    def configure(self, spec: PolicySpec):
        self.record["policy"] = spec.model_dump(mode="json")
        self.candidates.clear()
        self.save("policy_changed", {"source": "authorized_local_api", "policy": self.record["policy"]})

    def administrative(self, body: AdminChange):
        self.record["maintenance"] = body.maintenance
        self.record["suspended_until"] = self.clock() + body.suspend_seconds if body.suspend_seconds else 0.0
        self.candidates.clear()
        self.save("restrictions_changed", body.model_dump())

    def reset(self, reason: str):
        self.record.update(attempts=0, stopped=False, auto_on_used=False, last_attempt_at=None)
        self.candidates.clear()
        self.awaiting = None
        self.save("budget_reset", {"reason": reason, "source": "authorized_local_api"})
        self._stop_latched = False

    def stop(self, reason: str):
        self._stop_latched = True
        self.record["stopped"] = True
        self.candidates.clear()
        try:
            self.save("error", {"reason": reason, "origin": "unknown", "automatic_corrections_stopped": True})
        finally:
            # A failed audit write must never re-enable automatic corrections.
            self.record["stopped"] = True

    def observe(self, state: dict, monotonic: float, *, allow_correction: bool = True):
        """Return at most one correction intent; manager holds room lock/deadline.

        Candidates count distinct completed snapshots and use monotonic elapsed
        time. Budgets/cooldown persist across restarts; confirmations do not.
        """
        now, spec = self.clock(), self.spec
        if self.last_monotonic is not None and monotonic <= self.last_monotonic:
            return None
        self.last_monotonic = monotonic
        deviations = {key: {"desired": value, "observed": state[key], "origin": "unknown"}
                      for key, value in self.record["desired"].items() if key in state and state[key] != value}
        if deviations != self.record["deviations"]:
            self.record["deviations"] = deviations
            self.save("deviation" if deviations else "deviation_cleared", {"fields": deviations, "origin": "unknown"})
        if self.awaiting:
            key, value = self.awaiting
            if state.get(key) == value:
                self.save("correction_observed", {"field": key, "value": value, "simulated": True})
                self.awaiting = None
        if self.record["suspended_until"] and now >= self.record["suspended_until"]:
            self.record["suspended_until"] = 0.0
            self.save("suspension_expired", {})
        if not allow_correction:
            return None
        if (spec.mode != "ENFORCE" or self.record["maintenance"] or now < self.record["suspended_until"]
                or self.record["stopped"]):
            self.candidates.clear()
            return None
        for key in list(self.candidates):
            if key not in deviations or key not in spec.protected:
                self.candidates.pop(key)
        for key in spec.protected:
            if key not in deviations:
                continue
            observed = state[key]
            candidate = self.candidates.get(key)
            if not candidate or candidate["observed"] != observed:
                candidate = self.candidates[key] = {"observed": observed, "since": monotonic, "count": 0}
            candidate["count"] += 1
            power_on = key == "power_on" and self.record["desired"][key] is True
            if power_on and (not spec.allow_auto_on or self.record["auto_on_used"]):
                continue
            confirmations = max(spec.confirmations, 3) if power_on else spec.confirmations
            stabilization = max(spec.stabilization_seconds, 30.0) if power_on else spec.stabilization_seconds
            cooldown = max(spec.cooldown_seconds, 60.0) if power_on else spec.cooldown_seconds
            if candidate["count"] < confirmations or monotonic - candidate["since"] < stabilization:
                continue
            last = self.record["last_attempt_at"]
            if last is not None and (now < last or now - last < cooldown):
                continue
            if self.record["attempts"] >= spec.max_attempts:
                self._stop_latched = True
                self.record["stopped"] = True
                self.save("stop_budget_exhausted", {"attempts": self.record["attempts"]})
                return None
            self.record["attempts"] += 1
            self.record["last_attempt_at"] = now
            if power_on:
                self.record["auto_on_used"] = True
            value = self.record["desired"][key]
            # Durable budget reservation BEFORE an external effect, even in DEMO.
            self.save("correction_attempt", {"field": key, "value": value, "attempt": self.record["attempts"], "simulated": True})
            self.candidates.clear()
            self.awaiting = (key, value)
            return key, value
        return None
