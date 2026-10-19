# Iteration 1 — BLE reliability audit

Date: 2026-10-19 (Europe/Madrid). **PASS offline / HOLD real BLE.**

## Identity and boundary

- Official fork: <https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control>.
- Audited base: `origin/master`, `3b3985ee5a6b93ed994a56331d13435986ca2f8a`.
- `origin` fetch/push: `https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control.git`.
- New checkout, initially clean; branch `fix/ble-reliability-foundation`.
- Attributed upstream: <https://github.com/txsl/cool-vibes>.
- Mac A: development and offline tests. System Python unchanged; test interpreters and packages inside Git-ignored `.venv/`.
- Mac B: **NOT MODIFIED**, without SSH or direct access. The inventory and six controllers were outside this execution's scope. Their previous results were supplied by the owner, not tests performed here.
- HARDWARE_BLE: **NOT_TESTED**. PRODUCTION_CHANGED: **NO**. No scans, pairing, real commands, running server, LaunchAgents, macOS configuration, merge or deployment. Lifespan tests launch only simulated pollers or stubs.

## Demonstrated defects and corrections

| Defect in the base | Evidence and result |
| --- | --- |
| Premature reassembly | Divided by 20 although each fragment reserves one index byte. Running the original module offline: declared length 20 emits 19 bytes in fragment 0; 39/ 40 emit 38 in fragment 1. Now divides by 19 and requires exact size before delivery. |
| Invalid indices and fragments accepted | Appended fragments without checking index, order, size or start. Now rejects orphan/duplicate continuations, gaps, short/long fragments and invalid lengths; discards incomplete messages. Index 0 starts/restarts a message. |
| Malformed replies appeared partially successful | `parse_objects` returned objects preceding a truncated value and ignored a final byte without a header. Now rejects invalid lengths and truncated TLVs with `ValueError`; preserves zero-length objects for unsupported fields. |
| Failed subscription left a reusable client | `ensure_connected` returned when transport was connected even if `start_notify` had failed. Ready now means connected and subscribed; any setup failure triggers cleanup and stops the session. |
| Disconnection did not wake requests or invalidate cache | The callback only changed a boolean. Now fails the pending Future, invalidates cache/reassembler and blocks new operations. Ignores callbacks from old clients. |
| Timeout covered only the reply | A write could remain outside the deadline. Timeout now covers all writes and the reply; preserves the original error and attempts cleanup after failure/cancellation. |
| A late reply could satisfy the next request | The dictionary uses only `cmd_id`. An ambiguous exchange ends that session; late replies are ignored and no retry occurs. |
| Cleanup concealed failures | The spike suppressed stop/disconnect errors and announced clean shutdown. Now attempts both calls, bounds each, propagates cleanup failures if no primary error exists and logs secondary errors while preserving the primary. Never certifies physical safety. |
| Stale state and shutdown races | Independent review reproduced reconnection when `is_connected` changed before the callback and setup advancing after close was requested. Fixed with a latch for an existing unready client and guards after scan/connect. |
| Shutdown could skip resources | Poller exceptions could prevent disconnect/store.close. `lifespan` cancels and awaits the poller; `finally` blocks attempt disconnection and SQLite closure even if another step fails. Replaces deprecated `on_event`. |
| README contradicted temperature behavior | Claimed to preserve the inactive setpoint. The actual behavior is documented: same integer for cooling/heating and the complete field block. That policy is unchanged. |

## Lifecycle decisions

One client and lock per controller are retained. Requests serialize individual exchanges, not an entire five-command status read. Cancellation while waiting for the lock does not touch the link or harm the active request. Cancellation after setup or an exchange starts requires STOP and releases the lock; future operations fail explicitly instead of hanging. Recovery requires owner inspection and deliberate creation of a new session/manual restart, never a restart loop.

The server makes one setup attempt: discovery with a 10s internal timeout and 12s outer limit, connection 20s, subscription 20s. Default exchange timeout 6s. The spike retains up to 120s for one complete setup to allow time for the prompt, but removes connection/subscription retries based on error-message comparisons. It aborts on the first query failure (5s). Discovery failure is also latched for review.

`ble_lifecycle.py` is a small shared helper without new dependencies. It calls stop-notify when the backend reports connected and then disconnect, including after partial connection. Each call has a 10s timeout; the cleanup task is shielded from repeated caller cancellation. A stop-notify error does not prevent attempting disconnect. Cleanup failures remain visible; the server does not discard the client after failed cleanup.

**Deadlines are cooperative asyncio limits.** If an implementation ignores cancellation, `wait_for` can take longer. This is neither a physical bound nor a guarantee that native transport was released.

## Tests and environment

Baseline executed before changes on Mac A, Python 3.14.8: **34 PASS, 0 FAIL, 25 warnings**, both selftests PASS, `pip check` PASS. The original baseline was then repeated in a temporary directory with Python 3.12.15: identical results.

Final suite on Mac A:

| Isolated environment | Total | PASS | FAIL | Warnings | Selftests |
| --- | ---: | ---: | ---: | ---: | --- |
| CPython 3.14.8, macOS arm64 |104|104|0|1|both PASS|
| CPython 3.12.15, macOS arm64 |104|104|0|1|both PASS|

The 34 original tests remain unmodified. New tests cover 4–255-byte boundaries, invalid duplicates/sequences/sizes, incomplete messages, truncated TLVs, correct versus wrong-command replies, client reuse, serialization, write/reply timeout, setup failures, disconnect with a pending request, old callbacks, close during setup, cancellation in every phase, cancelled waiters, failed/slow/repeatedly-cancelled cleanup, a single-attempt spike, equal setpoints and shutdown with poller/disconnect failure. `tests/conftest.py` prevents loading real Bleak during the suite; each simulation explicitly replaces that stub.

Verified main versions: Bleak 3.0.2, FastAPI 0.146.0, Uvicorn 0.54.0, pytest 9.1.1, httpx 0.28.1, Starlette 1.9.0, Pydantic 2.14.1, PyObjC 12.2.2. [Full Python 3.12 snapshot](tested-macos-py312.txt). `pip check`/`uv pip check` PASS. The only remaining warning is Starlette TestClient's httpx deprecation; the original suite client is retained without an unrelated BLE migration or warning suppression.

Requirements retain their original minimums (`bleak>=0.22`, etc.), without adding runtime packages or changing providers in iteration 1. The snapshot describes the tested environment, not a lock for all platforms. It does not certify every version admitted by those ranges or Python 3.10/ 3.11/ 3.13.

Verification commands (each command must succeed):

```bash
.venv/bin/python -m pytest -q
.venv/bin/python daikin_server.py --selftest
.venv/bin/python brc1h_spike.py --selftest
.venv/bin/python -m pip check
.venv/py312-env/bin/python -m pytest -q
.venv/py312-env/bin/python daikin_server.py --selftest
.venv/py312-env/bin/python brc1h_spike.py --selftest
.venv/bin/uv pip check --python .venv/py312-env/bin/python
git diff --check
```

## Prioritized remaining risks

1. **HIGH — native state during connect cancellation and controller safety.** Static inspection of Bleak 3.0.2 on Mac A: `CentralManagerDelegate.connect` cancels native connection on `asyncio.TimeoutError`; it has no equivalent explicit treatment for `CancelledError`. `BleakClientCoreBluetooth.disconnect` returns if the peripheral is not yet Connected. A native Connecting attempt may remain even after public calls return. This is a backend risk not physically reproduced, not an observed fact on Mac B. No private APIs are called and passing mocks does not establish native release.
2. **HIGH — pairing and disconnection specific to each BRC1H.** Neither successful `stop_notify`/`disconnect` nor a timeout proves the controller still responds. Requires an owner physically present and an authorized one-controller test. No automatic reconnection strategy is enabled. An external autorestart supervisor could bypass the latch: do not use one during validation.
3. **MEDIUM — no transaction ID/checksum in the contract used.** Command ID is compared, not transaction identity. The latch avoids reusing a reply after timeout/cancellation but cannot distinguish a late duplicate after an already-successful request from the next same-command reply. An unsolicited notification with the same ID is also indistinguishable. Do not invent delays as guarantees; physical captures and a specification are needed if this occurs.
4. **MEDIUM — protocol and temperature assumptions.** Retains19-byte non-final fragments and an exact final fragment, without padding; captures for the six models/firmwares remain necessary. Fragment 0 replaces an incomplete message because no message identifier exists. The reassembler has no timer of its own; request/disconnect reset it and messages are bounded by their one-byte length. Integer rounding and equal cooling/heating writes, including the zeroed limits block, are retained. Do not generalize upstream historical claims to the office controllers.
5. **LOW — open dependency matrix.** Installed versions pass offline; future resolution may differ. Preserve versions/SHA for each validation and review before using another environment.

## Review and next step

Independent read-only review by another agent reproduced the two described races; both were corrected with regressions. No BLE, SSH or edits by the reviewer. Independent revalidation of the complete diff, including rejection of truncated TLVs and documentation: PASS on both interpreters and both selftests. Further fragments are also prevented after link loss during a multipart write.

No license is added: the base tree contains no LICENSE/COPYING/NOTICE and GitHub returns `licenseInfo:null` for upstream. Attribution is retained. Absence of an explicit license does not itself grant general redistribution permission; clarify rights with upstream before distributing derivatives.

Next step: human review of the draft PR and manual execution of **only the offline phase** of the [Mac B handoff](mac-b-validation.md). GO for a real BLE test is later, separate and requires available physical intervention. Historical physical commands in that handoff remain blocked on iteration 2+ branches; they are not authorization to reactivate them.
