"""Narrow supervised recovery for durable ownership left by a previous process.

A kernel boot boundary newer than EVERY event is required. This is intentionally
not an elapsed-time, PID, public-disconnect or operator-checkbox override.
"""
import asyncio
import getpass
import hashlib
import os
import re
import subprocess
import sys
import time
from types import SimpleNamespace

from madoka_readonly_canary import PreflightError
from .backends.native_release import native_snapshot, stable_absence


def boot_time():
    raw = subprocess.check_output(["/usr/sbin/sysctl", "-n", "kern.boottime"], text=True, timeout=3)
    match = re.fullmatch(r"\{ sec = ([0-9]+), usec = [0-9]+ \}.*\n?", raw)
    if not match:
        raise PreflightError("Kernel boot evidence unavailable")
    return int(match[1])


async def probe_absence(identity):
    """Initialize a central only; NEVER scan/connect/cancel another client."""
    from bleak.backends.corebluetooth.CentralManagerDelegate import CentralManagerDelegate
    from Foundation import NSUUID
    delegate = CentralManagerDelegate()
    await asyncio.wait_for(delegate.wait_until_ready(), 30)
    peripherals = delegate.central_manager.retrievePeripheralsWithIdentifiers_([NSUUID.alloc().initWithUUIDString_(identity)])
    if len(peripherals) != 1:
        raise PreflightError("Exact native peripheral unavailable for release evidence")
    client = SimpleNamespace(_backend=SimpleNamespace(_peripheral=peripherals[0], _central_manager_delegate=delegate))
    await stable_absence(lambda: native_snapshot(client, identity))


async def recover_after_reboot(budget, grants, *, operator, reviewer, reference):
    """Preserve all rows and append identity/revision-bound release evidence."""
    from .office_access import environment
    environment()
    if (not budget.office or not sys.stdin.isatty() or not sys.stdout.isatty()
            or any(not isinstance(v, str) or not 3 <= len(v.strip()) <= 160 for v in (operator, reviewer, reference))
            or operator.strip() == reviewer.strip()):
        raise PreflightError("Attended independent recovery authorization required")
    if getpass.getpass("Type VERIFY OWNERSHIP AFTER REBOOT (hidden): ") != "VERIFY OWNERSHIP AFTER REBOOT":
        raise PreflightError("Recovery confirmation mismatch")
    boot = boot_time()
    # Opening the old ledger appends restart_quarantine NOW. Exclude only that
    # bookkeeping event; every prior native/recovery event remains binding.
    latest = budget.db.execute("SELECT MAX(at) FROM session_events WHERE kind!='restart_quarantine'").fetchone()[0]
    if latest is None or not latest < boot <= time.time():
        raise PreflightError("A verified host reboot after the recorded native session is required")
    identities = {hashlib.sha256(g.identity.encode()).hexdigest(): g.identity for g in grants.values()}
    rows = [r for r in budget.records() if r["state"] == "quarantined"]
    if not rows or any(r["digest"] not in identities for r in rows):
        raise PreflightError("Every quarantined identity must match the authorized local inventory")
    for row in rows:
        await probe_absence(identities[row["digest"]])
    # All evidence must succeed before changing any owner. Lock is held by the
    # fixed native ledger; no second process may race admission or recovery.
    with budget.db:
        for row in rows:
            changed = budget.db.execute("UPDATE sessions SET state='released_native',revision=revision+1 WHERE session_id=? AND revision=? AND state='quarantined'",
                                        (row["session_id"], row["revision"]))
            if changed.rowcount != 1:
                raise PreflightError("Recovery revision drift; STOP")
            budget._event(row["session_id"], "native_release_after_host_reboot", {
                "boot_time": boot, "operator": operator.strip(), "reviewer": reviewer.strip(),
                "reference": reference.strip(), "native_absence_observed": True,
                "scope": "local_application_ownership", "global_radio_release": "not_proven"})
