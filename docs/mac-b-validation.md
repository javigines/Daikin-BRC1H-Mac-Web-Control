# Mac B handoff — later validation, not executed by Codex

Mac A is development. Mac B is the office Mac mini with six BRC1H units and Python 3.12. Its previous scans, two selftests and 34 tests are evidence supplied by the owner, not executions from this iteration.

Historical iteration 1 procedure: physical entrypoints are blocked in iteration 2 and later. Section 4 preserves the original proposal, but its command cannot run from these branches. A separately reviewed implementation and authorization are required; do not remove guards to follow this document.

## 1. Local read-only preflight

Run personally on Mac B, without SSH. Do not install services, change macOS permissions or stop processes blindly. Review results before continuing; if a controller client is active, STOP and agree how to close it.

```bash
cd "$HOME/madoka-test/cool-vibes"
git status --short --branch
git remote -v
command -v python3.12
python3.12 --version
pgrep -fl 'daikin_server|brc1h_spike|debug_setpoint|probe_range' || true
launchctl list | grep -Ei 'daikin|madoka|cool.?vibes' || true
lsof -nP -iTCP -sTCP:LISTEN | grep -E 'Python|python|:80 |:8000 ' || true
ls -l "$HOME/madoka-test/madoka_inventory.json"
```

Also review differently named LaunchAgents and any known controller process. No matches do not certify that all BLE clients are absent; check the graphical session and close the phone's Madoka app only when preparing the authorized test. Do not print or upload the inventory. If Python 3.12 is absent, STOP: resolve with the owner without changing existing Python or virtual environments.

## 2. Obtain changes without altering the existing checkout or virtual environment

Verify `origin` is the official fork before these commands. A separate worktree avoids checkout/reset/stash in the previous environment. If the target directory exists, STOP and inspect; do not delete it.

```bash
git fetch origin master fix/ble-reliability-foundation
MADOKA_REVIEW_SHA="$(git rev-parse origin/fix/ble-reliability-foundation)"
git log -1 --format=fuller "$MADOKA_REVIEW_SHA"
git diff --stat origin/master..."$MADOKA_REVIEW_SHA"
# Compare MADOKA_REVIEW_SHA with the exact PR SHA and final report.
# STOP on mismatch; do not accept a changed branch without review.
git worktree add --detach "$HOME/madoka-test/cool-vibes-ble-iteration1" "$MADOKA_REVIEW_SHA"
cd "$HOME/madoka-test/cool-vibes-ble-iteration1"
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python --version
.venv/bin/python -m pip freeze
git rev-parse HEAD
```

Save versions and SHA with results. Original requirements have lower bounds rather than a lock: later installation may resolve different versions. Do not update the previous virtual environment or copy `.env`, inventory, databases or LaunchAgents into this worktree.

## 3. Offline tests, allowed without BLE contact

```bash
.venv/bin/python -m pytest -q
.venv/bin/python daikin_server.py --selftest
.venv/bin/python brc1h_spike.py --selftest
pgrep -fl 'daikin_server|brc1h_spike|debug_setpoint|probe_range' || true
launchctl list | grep -Ei 'daikin|madoka|cool.?vibes' || true
git status --short
```

The suite replaces `bleak` with a forbidden backend by default; tests that need it inject simulated clients. Both selftests finish before server creation, scanning or connection. Compare preflight/postflight and return results, warnings, versions and SHA. Do not run the scripts without `--selftest` in this phase. **STOP here.**

## 4. First BLE test, blocked until new authorization

Prerequisites: later explicit authorization for **one** UUID in the local inventory and a person physically beside the controller who can intervene. Identify by inventory and location, not RSSI alone. Record time, model, available firmware, visible state and previous bond. All other clients must be disconnected; no web server, poller, LaunchAgent, write scripts or restart loops may be active.

After that authorization, from a visible graphical session on Mac B and a reviewed worktree, the owner would substitute the approved UUID. This historical command is blocked in iteration 2+ and requires a new reviewed change before execution:

```bash
MADOKA_APPROVED_UUID='REPLACE_WITH_APPROVED_UUID'
# Only after the later GO and UUID verification:
.venv/bin/python -u brc1h_spike.py --address "$MADOKA_APPROVED_UUID" --timeout 8
```

The spike performs preliminary discovery and one connection/subscription attempt, allowing up to 120s for complete setup including the pairing prompt. It only queries power, mode, setpoint and sensor. Each exchange has a 5s limit; cleanup attempts stop-notify and disconnect with 10s per call. Limits depend on cooperative backend cancellation. No automatic retry. In Bleak 3.0.2, cancellation during native Connecting may leave an attempt pending even when public disconnect returns: STOP, no retries and physical verification. A read-only query is not an absence of all risk: connection, pairing and disconnection affect BLE state.

**STOP** on ambiguous UUID, unexpected prompt, pairing error/PIN, timeout, connection/subscription/read/cleanup failure, link loss, unexpected controller change or loss of physical responsiveness. Do not accept another device's prompt. Do not repeat the test, force reconnection, unpair, change permissions or power-cycle automatically. To abort, press Ctrl+C once and wait for cleanup results; do not repeat Ctrl+C, close Terminal or use `kill -9`. If stuck, maintain STOP and decide intervention in person.

After exit, save stdout/stderr and exit code, physically verify local controller controls still respond and observe without opening another connection. `BLE cleanup calls completed; hardware state unverified` reports only completed calls. It does not certify safe disconnection for the BRC1H. Do not test the other five controllers before reviewing this evidence.

## 5. Temperatures: another later authorization

The first test does not write. A future `set_setpoint` test requires an agreed harmless range and manual restoration method, recording before/after cooling/heating values, mode, limits and screen state. Check whether the model accepts independent setpoints, requires equal values, how it rounds half degrees and whether the zeroed limits block preserves effective limits. Code retains the same integer for both setpoints. Do not run `probe_range.sh`, `debug_setpoint.sh` or the server as part of this handoff.
