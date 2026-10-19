"""Authorization, startup, durable restarts and native evidence recovery offline."""
import asyncio
import hashlib
import importlib.abc
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from integration.office_mock import closed_office, SHA
from madoka_multiroom import office_access as access, native_recovery as recovery
from madoka_multiroom.native_access import PreflightError, require_authorization
from madoka_multiroom.backends.base import BackendUnavailable
from madoka_multiroom.backends.daikin import BackendBusy
from madoka_multiroom.backends.physical_sessions import PhysicalSessionBudget, ConnectionPlan
from madoka_multiroom.backends.office_native import OfficeFleet


def reopen(office, path):
    office.budget.close_storage()
    office.budget = PhysicalSessionBudget(path, ConnectionPlan("retained", 6, 2),
        native_authorization=next(iter(office.grants.values())), native_grants=office.grants)
    office.fleet = OfficeFleet(office.config, office.budget, office.grants)


def test_crash_retains_owners_and_pending_writes_without_replaying(tmp_path):
    with closed_office(tmp_path) as office:
        async def crash():
            backend = office.fleet.backends["room-1"]
            await backend.prepare()
            office.budget.begin_operation(backend.port, backend, str(uuid4()), "power", False, {"state": {"power_on": True}})
        asyncio.run(crash())
        path = tmp_path / ".madoka-readonly-web/native-sessions.sqlite"
        reopen(office, path)
        assert office.budget.quarantined and office.budget.pending_operation("room-1")
        async def blocked():
            with pytest.raises(BackendUnavailable): await office.fleet.ensure_room("room-1")
            with pytest.raises(BackendBusy): await office.fleet.ensure_room("room-1", explicit=True)
            # Other configured rooms retain their independent available capacity.
            backend = await office.fleet.ensure_room("room-2")
            await backend.close()
        asyncio.run(blocked())
        assert office.budget.pending_operation("room-1")


@pytest.mark.parametrize("fault", [None, "same_boot", "native_connected", "wrong_identity", "same_reviewer", "confirmation"])
def test_reboot_recovery_requires_all_evidence_and_preserves_history(tmp_path, monkeypatch, fault):
    with closed_office(tmp_path) as office:
        async def crash():
            await office.fleet.backends["room-1"].prepare()
        asyncio.run(crash())
        path = tmp_path / ".madoka-readonly-web/native-sessions.sqlite"
        reopen(office, path)
        row = office.budget.records()[0]
        now = time.time()
        monkeypatch.setattr(recovery, "boot_time", lambda: now - 1 if fault == "same_boot" else now)
        # Model old native events before a new kernel boot, but leave the new
        # restart bookkeeping intact. This touches only a temporary closed ledger.
        office.budget.db.execute("UPDATE session_events SET at=? WHERE kind!='restart_quarantine'", (now-10,))
        office.budget.db.commit()
        monkeypatch.setattr(recovery.getpass, "getpass", lambda prompt: "wrong" if fault == "confirmation" else "VERIFY OWNERSHIP AFTER REBOOT")
        evidence = []
        async def probe(identity):
            evidence.append(identity)
            if fault == "native_connected": raise BackendUnavailable("Native link present")
        monkeypatch.setattr(recovery, "probe_absence", probe)
        if fault == "same_boot": monkeypatch.setattr(recovery, "boot_time", lambda: now-20)
        if fault == "wrong_identity":
            office.budget.db.execute("UPDATE sessions SET digest='unknown'")
            office.budget.db.commit()
        async def case():
            if fault:
                with pytest.raises((PreflightError, BackendUnavailable)):
                    await recovery.recover_after_reboot(office.budget, office.grants, operator="Owner", reviewer="Owner" if fault == "same_reviewer" else "Reviewer", reference="Recorded commissioning evidence")
                assert office.budget.quarantined
            else:
                await recovery.recover_after_reboot(office.budget, office.grants, operator="Owner", reviewer="Reviewer", reference="Recorded commissioning evidence")
                assert not office.budget.owners
                released = office.budget.records()[0]
                assert released["session_id"] == row["session_id"] and released["revision"] > row["revision"]
                assert office.budget.db.execute("SELECT COUNT(*) FROM session_events WHERE kind='native_release_after_host_reboot'").fetchone()[0] == 1
                assert evidence == [office.grants["room-1"].identity]
        asyncio.run(case())


def test_failed_discovery_can_retry_but_never_retries_connection_setup_uncertainty(tmp_path):
    with closed_office(tmp_path) as office:
        async def case():
            office.stage, office.error = "discovery", RuntimeError("Closed discovery miss")
            with pytest.raises(BackendUnavailable): await office.fleet.ensure_room("room-1")
            assert not office.clients
            office.error = None
            office.fleet.next_attempt["room-1"] = 0
            backend = await office.fleet.ensure_room("room-1")
            assert backend.port.ready and office.budget.records()[0]["state"] == "released_native"
            await backend.close()
            office.stage, office.error = "connect", RuntimeError("Closed setup uncertainty")
            with pytest.raises(BackendUnavailable): await office.fleet.ensure_room("room-2")
            office.fleet.next_attempt["room-2"] = 0
            with pytest.raises(BackendUnavailable): await office.fleet.ensure_room("room-2")
            assert office.budget.quarantined
        asyncio.run(case())


@pytest.mark.parametrize("fault", ["expired", "config", "password", "schema", "permissions", "symlink", "offline"])
def test_authorization_cannot_outlive_scope_or_bypass_gates(tmp_path, fault):
    with closed_office(tmp_path) as office:
        path = tmp_path / ".madoka-readonly-web/office-authorization.json"
        data = json.loads(path.read_text())
        if fault == "expired": data["expires"] = time.time()-1
        if fault == "config": data["config_digest"] = "wrong"
        if fault == "password": data["password"] = "short"
        if fault == "schema": data["schema"] = 2
        path.write_text(json.dumps(data))
        if fault == "permissions": path.chmod(0o644)
        if fault == "symlink":
            target = path.with_name("other.json")
            path.rename(target)
            path.symlink_to(target)
        if fault == "offline":
            os.environ["MADOKA_OFFLINE_ONLY"] = "1"
            with pytest.raises(PreflightError): require_authorization(office.grants["room-1"])
        else:
            with pytest.raises((PreflightError, OSError)): access.service_grants(office.config)
        assert not office.calls


@pytest.mark.parametrize("args", [[], ["--serve"], ["--authorize-service"], ["--recover-after-reboot"]])
def test_office_cli_default_and_ci_gates_never_import_driver(args):
    code = """
import importlib.abc, sys
class NoDriver(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname in ('bleak', 'CoreBluetooth', 'Foundation') or fullname.startswith('bleak.'):
   raise AssertionError('FORBIDDEN_DRIVER')
sys.meta_path.insert(0, NoDriver())
from madoka_office import main
raise SystemExit(main(ARGS))
""".replace("ARGS", repr(args))
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env={**os.environ, "MADOKA_OFFLINE_ONLY": "1"})
    assert result.returncode == (2 if args else 0)
    assert "FORBIDDEN_DRIVER" not in result.stdout + result.stderr


def test_no_native_enforce_or_auto_on_from_office_admin(tmp_path):
    from madoka_multiroom.manager import RoomManager, RoomUnavailable
    from madoka_multiroom.storage import RoomStore
    from madoka_multiroom.policies import PolicySpec
    with closed_office(tmp_path) as office:
        async def case():
            store = RoomStore(":memory:", mode="real")
            manager = RoomManager(office.config.rooms, store, offline_fleet=office.fleet)
            with pytest.raises(RoomUnavailable):
                await manager.administer("room-1", "policy", PolicySpec(mode="ENFORCE"))
            assert not office.calls
            await manager.close()
            store.close()
        asyncio.run(case())
