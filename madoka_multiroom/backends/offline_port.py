"""Deterministic Controller.request/disconnect double, incapable of BLE I/O.

Plans contain data, delays and exceptions only, never callbacks or driver
factories. Repeated reads use the last planned reply; writes never alter reads
implicitly, so tests must explicitly supply evidence of an applied effect.
"""
import asyncio
import copy
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Reply:
    objects: dict | None = None
    error: Exception | None = None
    delay: float = 0.0


class ScriptedController:
    def __init__(self, plan: dict[int, list[Reply]]):
        self.plan = {cmd: deque(replies) for cmd, replies in plan.items()}
        self.calls = []
        self.closed = False
        self.disconnect_calls = 0

    async def request(self, cmd_id: int, args=None, timeout=6.0):
        if self.closed:
            raise ConnectionError("Offline controller closed")
        self.calls.append((cmd_id, copy.deepcopy(args)))
        replies = self.plan.get(cmd_id)
        if not replies:
            raise ValueError("Unplanned offline exchange")
        reply = replies.popleft() if len(replies) > 1 else replies[0]
        await asyncio.sleep(reply.delay)
        if reply.error:
            raise reply.error
        return copy.deepcopy(reply.objects)

    async def disconnect(self):
        self.disconnect_calls += 1
        self.closed = True
