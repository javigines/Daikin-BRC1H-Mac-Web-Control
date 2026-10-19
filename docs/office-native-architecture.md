# Complete office native application

Status: implemented and tested offline; **physical execution HOLD**. This source
does not certify a macOS Bluetooth session, a SET effect, native release, a
LaunchAgent's TCC permissions or six simultaneous physical links. Owner-reported
pairing, four GET groups, the previous read-only web test and wall responsiveness
are useful prior evidence, not release evidence. The old web owner remains
quarantined until the recovery procedure establishes additional evidence.

## Runtime and boundaries

`multiroom_app.py` still defaults to simulators and rejects `--real`. The previous
single-room `madoka_readonly_web.py` retains its attended four-GET-only contract.
The explicit new `madoka_office.py` entrypoint also defaults to OFFLINE. Its
`--serve` mode requires a clean exact reviewed checkout, pinned Bleak 3.0.2,
macOS non-root execution, private configuration and an unexpired source/config
bound authorization issued in an attended terminal. CI/offline/PYTHONPATH gates
reject native execution before driver import. Expiration blocks further native
requests, while cleanup remains possible. Authorization never frees a session.

Six stable room IDs bind six private CoreBluetooth UUIDs. All configured rooms
appear in the Spanish dashboard; unobserved fields remain unavailable. There is
no simulator fallback in the native fleet. Each room has its own Controller,
request lock, backend, observations, history and failure state. Global setup and
operation capacity is configurable from one to six; start commissioning at one.
Connections persist between polls. Connection/subscription gets a shared 120s
window after 15s discovery, bounded by the existing setup phase. Five GETs fit a
30s aggregate deadline, with 6s per wire exchange. SET plus pre-read and readback
uses the same aggregate deadline and is never a connection-setup path.

Read-only recovery may reconnect only after verified application ownership
release. Failed attempts back off 10/30/120s and latch after three attempts in an
episode. Successful observations over at least 60s rearm the episode, with an
additional durable rolling limit of three admissions per room per ten minutes.
This admits normal later outages without creating a connection storm; repeated
unstable failures require an explicit operator review. Pending/uncertain SETs
block automatic recovery. An unresolved room retains capacity but does not
invalidate another room's existing session or history. Explicit review can admit
one new read-only session only if ownership permits; it does not replay a SET.

## Native lifecycle investigation and implemented evidence

The inspected Bleak 3.0.2 CoreBluetooth `client.disconnect()` returns early unless
`CBPeripheral.state()` equals **connected**. Its public `is_connected` is false
for disconnected, connecting and disconnecting. A returned public call or a
responsive wall controller cannot distinguish these cases. The pinned delegate
resolves its disconnect future and invokes the registered callback from
`did_disconnect_peripheral`; a disconnect error fails the future.

The new live-session verifier retains the exact client and session identity. It
requires the correlated callback, a completed cleanup, a powered-on central,
the exact peripheral in native state **disconnected (0)**, and absence of that
UUID from `retrieveConnectedPeripheralsWithServices` for the reviewed service.
It samples both conditions over two seconds. Sampling duration alone is never
evidence. A native callback after an established connection can also establish
an unsolicited disconnect, followed by the same absence checks. A failure,
missing callback, unknown backend layout, state 1/2/3, another visible connection,
failed unsubscribe or cancellation retains quarantine. Partial connection setup
does not gain an unproven release. Discovery which never attempted connect and
never created a client can release its reservation with that narrower fact.

Only a successful verifier sets the evidence consumed by the durable ledger.
The ledger atomically appends `released_native` and retains all session rows,
revisions and audit events. Ordinary read-only-web cleanup remains quarantined.
SQLite uses the **same fixed legacy native ledger**, private file ownership,
process lock and full synchronous writes. No separate fresh native ledger path,
time-based lease expiration or boolean override bypasses old ownership.

After a crash, callbacks from the previous process cannot be reconstructed.
`--recover-after-reboot` therefore requires a kernel boot time newer than every
previous native event, an attended operator and independent reviewer reference,
the exact inventory digests, and a bounded native state/system-service absence
probe. That probe initializes a central but does not scan, connect, cancel another
client or SET. All identities must pass before any row transitions; revisions
and audit history are preserved. Restart-bookkeeping events are excluded from
the boot comparison, not prior native/recovery events. A same-boot process exit
alone cannot release a crashed session. Native pending operations survive this
ownership recovery and require separate explicit GET review.

This is evidence of **local application ownership**, not a mathematical proof
of the peripheral's global radio state. Apple's API explicitly allows another
application to retain a physical connection after our cancellation. An absent
system-service inventory is a conservative additional check, not omniscient
proof across hosts. Commissioning must exclude other clients, establish the
original ledger's local provenance, verify clock continuity and validate the
native observations against hardware/native tracing. Do not copy a ledger from
another Mac and apply this reboot procedure to it. The old ledger did not record
a machine identity; that historical provenance cannot be recovered offline.
Unknown provenance, backward clock changes or inconclusive evidence means STOP.

Primary references:

- [Apple cancellation semantics](https://developer.apple.com/documentation/corebluetooth/cbcentralmanager/cancelperipheralconnection(_:)).
- [Apple disconnection callback](https://developer.apple.com/documentation/corebluetooth/cbcentralmanagerdelegate/centralmanager(_:diddisconnectperipheral:error:)).
- [Bleak 3.0.2 CoreBluetooth client source](https://github.com/hbldh/bleak/blob/v3.0.2/bleak/backends/corebluetooth/client.py).
- [Bleak 3.0.2 central delegate source](https://github.com/hbldh/bleak/blob/v3.0.2/bleak/backends/corebluetooth/CentralManagerDelegate.py).

## Manual commands and firmware contract

`POST /api/controllers/{id}/manual` accepts a UUID `operation_id`, action
`power|setpoint|mode|fan`, strict value and an operator reason. All browser controls
ask for confirmation. The legacy ordinary SET endpoints remain forbidden in
office mode. Each request performs a fresh full GET baseline, validates the
device/owner capability intersection, durably journals the baseline and full
raw object block, and updates authorized desired state before any SET fragment.
One exact ordered frame sequence is then armed at the radio boundary. Each
fragment is consumed **before** awaiting transport. No write retries, replay
queue, offline command queue, bulk action or reconnect-and-reissue path exists.
Repeated operation IDs never transmit again. The previous observed values are
retained for a separately confirmed restoration; restoration is never automatic.

| Action | Native command | Contract |
| --- | --- | --- |
| Power | `0x4020` | Boolean object `0x20`; explicit action, including power-on |
| Mode | `0x4030` | Object `0x20`; only commissioned Auto/Cool/Heat IDs 2/3/4 |
| Setpoint | `0x4040` | Whole degrees; active heat/cool target changes; inactive target and every observed flag/limit byte preserved |
| Fan | `0x4050` | Commissioned speeds 1–5; active heat/cool value changes and opposite fan value preserved |

The four previously proven GET groups do **not** prove supported modes, fan GET,
SET framing/effects or valid temperature windows. Owner capability configuration
is an explicit commissioning allowlist, not inferred device support. Fan is not
queried when its capabilities are empty. Setpoint additionally requires the
complete recognized two-target/range/mode/limit GET block, valid native limits
and the configured 16–32°C safety envelope. Missing/zero limits or unknown schema
disable setpoint controls; no fallback window is guessed. Auto-mode temperature
and fan semantics are unproven and are rejected, while Auto mode selection can
be commissioned. Exact raw precision is retained for readback comparisons.

The legacy setpoint builder sends zeroed range/limit defaults. It remains intact
for existing/offline compatibility and **is not used for native office SETs**.
The new encoder uses the full returned block unchanged except the active target.
This preserves restrictions instead of resetting installer parameters. Firmware
may require a different schema or coupled targets: reject/mismatch and STOP;
never guess an alternate command after a failed attempt. The protocol has no
atomic compare-and-set: another client changing a field between GET and SET can
race our baseline. Excluding other clients during commissioning is essential.

Only an empty or no-argument-marker ACK is recognized, and ACK alone never proves
success. A complete GET readback must confirm the intended field, both targets,
both queried fan values, power/mode invariants and the full setpoint block.
Temperature sensor drift is allowed. Timeout, cancellation, ambiguous ACK,
disconnect, failed GET or mismatch latches STOP and a durable uncertain outcome.
Even if ownership later releases successfully, an uncertain SET remains blocked
until explicit read-only operator review. Review records the actual state; it
does not reclassify the previous write as confirmed or claim its origin.

FREE/MONITOR remain usable per room, with persistent desired/observed separation,
deviations, maintenance and suspension. Physical ENFORCE, schedules, auto-on,
native function restrictions and installer changes remain disabled. Explicit
manual power-on is distinct from automatic power-on. Native desired state may
only be established by an authorized manual operation.

## HTTP and deployment

Office mode serves HTTPS directly through Uvicorn with explicit private key and
certificate, a specific private/loopback bind, and exact allowed authorities.
Every resource uses a strong owner-issued credential. Basic credentials are
never offered over an unencrypted LAN listener. Mutation token, exact HTTPS
origin/Host, body cap, strict schemas and no-store/CSP headers remain enforced.
Forwarded headers and reverse-proxy trust are disabled. No Internet or wildcard
bind is accepted. Local-network routing/firewall and trusted certificate delivery
must be verified at installation. Any public ingress, port forwarding or VPN
exposure requires a separate access review; the application does not configure it.

The supplied LaunchAgent is a template for a logged-in non-root Aqua user. It is
not installed by source/tests. TCC consent for a terminal is not assumed to cover
launchd/Python automatically. Foreground and LaunchAgent executions must each be
validated on the actual Mac with the exact interpreter/checkout. No LaunchDaemon,
root execution, pairing reset, permission reset or unattended login assumption
is supplied. Logged-out operation is not certified. See the [single commissioning
guide](office-commissioning.md) and [offline validation](office-validation.md).

Attribution is unchanged. Upstream's missing explicit license remains unresolved;
this work grants no redistribution rights and opens no upstream contribution.
