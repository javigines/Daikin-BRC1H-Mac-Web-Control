# Daikin Local: implementation review and offline evidence

This records the original PR #11 candidate. The subsequent
[power incident RCA](local-power-incident.md) supersedes its manual deadlines,
ACK acceptance and availability/error handling, and records the new regressions.

Base: master `e6822f126b3e61c36ec0665a1b68fc6af54422b9`. This candidate adds
one local runtime, not another physical pilot. The [short operator guide](local.md)
is the only installation/update and six-device check procedure for the new mode.
Historical read-only/office runbooks remain compatibility documentation.

## Actual complete-tree validation

| Check | Result |
| --- | --- |
| Python 3.12.15, complete offline suite | **579 passed** |
| Python 3.14.8, complete offline suite | **579 passed** |
| Local mode tests | **48 passed**, included in the full total |
| New HTTP loopback Chromium | PASS, desktop/mobile, zero JavaScript errors |
| Existing four Chromium suites | PASS, desktop/mobile, zero JavaScript errors |
| Relative documentation links | 87 resolved, no missing targets |
| Launcher shell syntax and whitespace checks | PASS |

The only Python warning is the existing Starlette/httpx TestClient deprecation.
Counts are from the complete tree, not a sum of sibling PR reports. Hosted CI
links and its exact tested SHA are recorded in the PR; local PASS is not hosted
or hardware proof. `ci/sitecustomize.py` forbids real Bleak/CoreBluetooth/Foundation/
objc imports. Tests explicitly inject a closed SDK; even fixtures that remove
runtime environment flags cannot load the physical driver.

Reproduction:

```bash
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python -m pytest -q
MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python integration/browser_demo.py
MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python integration/browser_physical_contract.py
MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python integration/browser_native_readonly.py
MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python integration/browser_office.py
MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python integration/browser_local.py
```

The local harness reserves port 8090 before starting and verifies a private closed
fixture marker before any control. It never operates an existing listener or
accepts physical configuration. Screenshots `artifacts/local-desktop.png` and
`artifacts/local-mobile.png` were inspected visually. New E2E covers six real-mode
closed controllers, immediate buttons without reasons/passwords/dialogs,
capability settings, power/mode/fan/target readback, restoration, pending-room
isolation, ACK without effect, GET-only outcome inspection, persistence and restart.

## Technical review and corrections

Source review covered the complete new startup, native admission/release,
write/journal/readback, settings, HTTP boundary and UI paths. It is a technical
implementation review, not a claim of independent physical verification.

- Local sessions are identity contexts, not fabricated office/read-only grants.
  Old attended authorization checks retain their contracts. No seal, password,
  fake reviewer, expiry or reviewed SHA is minted to satisfy an old check.
- The launcher reports a nonzero exit if server startup fails. Runtime transport
  metadata identifies local mode correctly.
- HTTP socket ownership precedes ledger opening/native tasks. The original
  ledger lock excludes other app instances, including compatible legacy runtimes.
- Setup waits outside its actual connection deadline and uses a separate bounded
  semaphore from ready GET/SET exchanges. Pairing cannot consume healthy rooms'
  wire slots. Legacy timeout ordering is preserved.
- Local manual queue plus execution is bounded at 35 seconds, shorter than the
  browser's 40-second response window. An unstarted queued command times out
  without SET or journal, rather than starting after the user has lost its response.
- The fresh pre-read can reject changed limits/mode before SET without falsely
  stopping a healthy link. Already-observed values are journaled no-ops.
- A complete SET sequence is consumed once. Baseline and full raw fields are
  durable before any SET frame; strict readback checks the active target and all
  preserved flags, ranges, inactive targets/fans, mode and power.
- Timeout, cancellation, disconnect, bad ACK, missing effect and failed readback
  are tested with no automatic SET replay. Reconnection can collect observations
  while unresolved writes still disable manual controls. A user-requested GET
  acknowledges current state without claiming the prior write succeeded.
- Local fan observations decode each bank independently as one byte 0–5, even
  without configured fan write choices; Auto=0 is display-only. An inactive
  manual speed outside the enabled write subset remains a valid observation.
  Missing/unknown banks retain separate field status and do not hide the other
  bank or room temperature. Raw extensions are preserved. The previous pair of
  one-byte 1–5 values is still required for fan writes, including fresh preflight;
  this correction grants no new SET permission. Modes and
  speeds beyond current observations require the simple local wall-supported
  choices; enum membership is not firmware evidence.
- The existing private inventory is read unchanged; obsolete TLS fields do not
  affect local startup. Settings are atomic owner-only sidecar writes, scoped by
  identity digest, and survive restart. Unique temporary names avoid a stale
  temporary file blocking later configuration after a crash.
- Exact loopback peer, HTTP Host/port and origin are enforced. CSRF, body/content
  limits, restrictive response headers and a native POST allowlist block remote
  web origins, DNS rebinding and legacy/simulator write routes. Proxy headers are
  not trusted. No LAN listener, TLS credentials or login is offered in local mode.
- User errors distinguish missing/occupied connection, unsupported value,
  unavailable observations, Bluetooth permission/adapter state and unconfirmed
  physical results. Overview count updates immediately after operations.

## Automatic native recovery design

Normal disconnections reconnect automatically with backoff and a rolling limit
of three connection admissions per room per ten minutes. Pairing/setup and ready
exchanges have separate bounded capacities, so a slow new connection cannot take
all healthy rooms' exchange slots. Per-room commands remain serialized. Manual
requests have a 35-second total deadline including queue time; ready operations
have a 30-second wire window, setup starts its own deadline after obtaining its
slot. All six links persist between polls.

The original process lock prevents duplicate application instances sharing the
ledger. The HTTP port is acquired before native startup. Cleanup is accepted only
with completed calls, exact native identity/state `disconnected` and stable
absence from CoreBluetooth's connected-service inventory; a public disconnect
return alone is insufficient. A lost callback alone does not require a reboot.

Old quarantined rows are recovered **per device**, under the exclusive original
ledger lock, only after identity matching and stable native absence. A legacy
row also requires kernel boot time newer than its native events (restart-only
bookkeeping is excluded). The owner reports that the previous Mac B session has
already crossed this verified boot boundary; the program still checks it.
Local-runtime rows can recover after process death in the same boot with the
recorded boot identity and native absence. No surviving current-process session
can be released by this path. Connected/connecting/disconnecting, unknown native
state, mismatched identity or contradictory timestamps remain a useful room error;
other rooms continue. No reviewer, signature, ledger reset or mandatory reboot
is used for an ordinary local disconnect.

These checks establish local application ownership, not global radio absence
across other hosts. Keep competing Madoka apps closed. Bleak is pinned to 3.0.2;
unknown private CoreBluetooth layout fails closed for the affected room. If macOS
cannot establish native absence, preserve the records and use the room error and
optional details/history to diagnose it. Do not clear SQLite to bypass it.


Tests include original v1 schema migration without lost events, post-reboot legacy
quarantine, same-boot local process death, actual native connected/connecting/
disconnecting states, identity and clock mismatch, duplicate locks, normal close/
reopen, lost callbacks, independent failures, rolling retry limits and uncertain
operation persistence. No runtime deletes old native records, changes the consumed
canary marker or substitutes a new ledger.

## Hardware evidence still needed

No Bluetooth scan, connection, physical SET, Mac B/SSH, production data access,
installation, restart, deployment or merge was performed during implementation.
Native state/inventory agreement, actual firmware controls, first SET effects,
six retained links and shutdown/reopen behavior still require the short real-device
check. A public API cannot prove absence of other-host connections. The installed
Bleak private layout is pinned; unknown state remains an affected-room error.

Local native startup is intentionally selected by `./run-local.sh`; its `--demo`
is separate and hardware-free. CI and offline guards forbid native startup.
Physical ENFORCE, schedules, auto-on, installer writes and native locks remain
unavailable. Daily local use requires no administrative recovery procedure; real
uncertainty is still surfaced instead of inventing proof. Licensing and attribution
are preserved; no new license or redistribution rights are asserted.
