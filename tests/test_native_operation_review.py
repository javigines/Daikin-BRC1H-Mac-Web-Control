"""GET-only review scope using real ledgers in temporary closed runtimes."""
import asyncio
import json
from uuid import uuid4

import pytest

from integration.office_mock import closed_office
from madoka_protocol import CMD_SET_FAN, CMD_SET_MODE, CMD_SET_POWER, CMD_SET_SETPOINT


@pytest.mark.parametrize("local", [False, True], ids=["office", "local"])
@pytest.mark.parametrize("action,value", [("fan", 5), ("mode", 4), ("setpoint", 23)])
@pytest.mark.parametrize("status", ["pending", "uncertain"])
def test_get_only_review_preserves_runtime_and_action_scope(tmp_path, local, action, value, status):
    with closed_office(tmp_path, local=local) as runtime:
        async def case():
            budget = runtime.budget
            assert budget.local is local
            backend = await runtime.fleet.ensure_room("room-1")
            other = await runtime.fleet.ensure_room("room-2")
            operation_id, other_id = str(uuid4()), str(uuid4())
            try:
                budget.begin_operation(backend.port, backend, operation_id, action, value, {})
                budget.begin_operation(other.port, other, other_id, action, value, {})
                if status == "uncertain":
                    budget.finish_operation(operation_id, "uncertain")
                    budget.finish_operation(other_id, "uncertain")
                original, untouched = budget.operation(operation_id), budget.operation(other_id)
                observed, reason = {"fan_id": 3, "mode_id": 3, "setpoint": 22}, "Synthetic GET-only review"
                budget.acknowledge_operations("room-1", observed, reason)
                reviewed = budget.operation(operation_id)
                blocked = local and action in ("fan", "mode")
                if blocked:
                    assert reviewed == original
                    assert budget.pending_operation("room-1")
                else:
                    assert reviewed["status"] == "reviewed_uncertain"
                    assert json.loads(reviewed["observed"]) == {"state": observed, "reason": reason}
                    assert not budget.pending_operation("room-1")
                    assert {k: v for k, v in reviewed.items() if k not in ("status", "observed")} == {
                        k: v for k, v in original.items() if k not in ("status", "observed")}
                budget.acknowledge_operations("room-1", observed, reason)
                assert budget.operation(operation_id) == reviewed
                assert budget.operation(other_id) == untouched
                assert budget.pending_operation("room-2")
                assert not any(len(call) == 3 and call[1] in (
                    CMD_SET_FAN, CMD_SET_MODE, CMD_SET_POWER, CMD_SET_SETPOINT) for call in runtime.calls)
            finally:
                await backend.close()
                await other.close()
        asyncio.run(case())
