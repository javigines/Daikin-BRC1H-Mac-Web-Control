"""Complete in-memory simulator. No imports of adapters, controllers or Bleak."""
import asyncio
from .base import BackendUnavailable


class Simulator:
    def __init__(self, index: int):
        self.status = "online"
        self.delay_seconds = 0.0
        self.closed = False
        self.state = {"power_on": index % 3 != 0, "mode_id": 3, "setpoint": 22.0,
                      "room_temp": 22.0 + index * 0.5, "fan_id": 3,
                      "cooling_setpoint": 22.0, "heating_setpoint": 22.0,
                      "min_setpoint": 16.0, "max_setpoint": 32.0}
        self.info = {"model": "BRC1H · SIMULADO", "controller_version": "demo-1",
                     "comm_version": "demo-1"}

    async def snapshot(self, fields):
        await asyncio.sleep(self.delay_seconds)
        if self.closed or self.status != "online":
            raise BackendUnavailable("Simulated controller unavailable")
        keys = {"power": ["power_on"], "mode": ["mode_id"],
                "setpoint": ["setpoint", "cooling_setpoint", "heating_setpoint", "min_setpoint", "max_setpoint"],
                "sensor": ["room_temp"], "fan": ["fan_id"]}
        return {key: self.state[key] for group in fields for key in keys[group]}

    async def apply(self, action: str, value):
        await asyncio.sleep(self.delay_seconds)
        if self.closed or self.status != "online":
            raise BackendUnavailable("Simulated controller unavailable")
        if action == "setpoint":
            self.state.update(setpoint=value, cooling_setpoint=value, heating_setpoint=value)
        else:
            self.state[{"power": "power_on", "mode": "mode_id", "fan": "fan_id"}[action]] = value

    async def close(self):
        self.closed = True
