# One Mac B installation and physical commissioning session

**HOLD. Future human procedure only.** No command below was executed on Mac B or
against Bluetooth during implementation. No merge or deployment is authorized by
this document. The office application and every manual command are implemented
in one source candidate; hardware acceptance is the remaining activity, not a
promise of another feature iteration.

## Authorization and preparation

Obtain one explicit owner authorization for the exact final reviewed source SHA
(a squash merge will produce a different SHA), six private UUID/room mappings,
allowed commands/modes/speeds, safe temperature envelope, reversible power/mode
changes, exclusion of other clients, a possible controlled Mac reboot, read-only
ownership probes, service installation/restart, TLS setup and office LAN testing.
Name the operator, independent reviewer, time window and evidence destination.
If reboot/service changes are outside that authorization, stop at that gate.
Implementation authorization alone is not physical authorization. Do not reuse
the consumed canary authorization or touch its permanent marker.

Before the session: review the PR and CI; verify the clean exact checkout and
Python 3.12 or 3.14 with Bleak **3.0.2**. Keep the known previous checkout and a
private, consistent backup of stopped SQLite databases, journals/locks and
authorization/configuration. Never restore an older native ledger over newer
ownership or pending operations. Do not include real UUIDs, credentials, raw SDK
logs, private certificates or local ledgers in GitHub artifacts.

Install dependencies in a stable private checkout/interpreter location, using
the logged-in non-root macOS office account. Do not use a root LaunchDaemon or
reset Bluetooth/TCC/pairing. From that checkout, **offline only**:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci .venv/bin/python -m pytest -q
.venv/bin/python madoka_office.py
```

The last command must report OFFLINE and must not initialize Bluetooth. Check
Python 3.14 separately if it is the installation interpreter. Development tests
must never run against the production authorization or native state files.

## Private configuration and protected office access

Copy [office.example.json](../office.example.json) to an **absolute path outside
the checkout**, owner-only directory (0700), file0600. Replace all six fictional
UUIDs privately with the independently checked wall/inventory mapping. Configure
room labels, intervals and stale threshold. Start with concurrency1, six retained
slots. All control capabilities default off in the template. Enable only the
model/firmware-supported controls covered by the commissioning authorization:

```json
{"power": true, "setpoint": true, "modes": [2, 3, 4],
 "fans": [1, 3, 5], "minimum": 20, "maximum": 25}
```

This is an example allowlist, **not evidence that any particular firmware
supports it**. Match fan speeds/modes to documentation and actual available wall
settings. Empty fans skips unproven fan GET/SET. Setpoint also needs a complete
recognized GET block and meaningful actual limits; a zero/unknown window is
STOP for that control. Do not insert assumed device limits or zero installer
defaults to make a test pass. Keep other rooms monitored while one capability is
being accepted. Configuration changes require attended reauthorization of the
new digest, within the already approved session scope.

Use an internal CA or another explicitly trusted office certificate covering the
exact configured hostname/private IP, with a private key readable only by the
service account. Set absolute certificate/key paths (both owner-only regular
files), a specific private bind address, port8443 and exact authorities, e.g.
`madoka.office.example:8443`. No wildcard bind, public IP, port forwarding,
unencrypted HTTP listener, proxy trust or Internet exposure is offered. Configure
the office firewall to permit only the intended office subnet and distribute
the CA trust securely to client devices. Do not bypass certificate warnings in
production; the E2E harness uses a test-only self-signed certificate.

Before any native probe, issue the scoped service authorization in an attended
terminal, with no CI/offline/PYTHONPATH variables set:

```bash
.venv/bin/python madoka_office.py --authorize-service \
  --config /ABSOLUTE/PRIVATE/office.json \
  --reviewed-sha EXACT_REVIEWED_SHA \
  --authorization-reference OWNER_SESSION_REFERENCE --days 7
```

The hidden phrase binds the reviewed SHA. Store the generated credential in the
approved private password manager and share it only with authorized office users.
All resources require username `operator`. HTTPS is mandatory even on loopback
in office mode. The expiring authorization is fixed in the actual account home,
alongside the **existing** native ledger, independent of environment HOME.

## Gate 1: previous web ownership and native release

The previous read-only session remains quarantined. Wall responsiveness and a
public disconnect return do not clear it. Inspect the private recorded identity,
revision and event timestamps without changing them. Confirm that the ledger is
the original local Mac B ledger, not copied from another host; establish clock
continuity and exclude Madoka Assistant/other apps/other host clients. Preserve
the ledger and consumed read-only canary marker unchanged in the evidence backup.

If the old session lacks correlated native evidence, stop the old application
and, **only if included in owner authorization**, perform a controlled Mac reboot.
Confirm the same local account/inventory and no auto-starting competing clients.
A same-boot exit is insufficient. In the new attended GUI session:

```bash
.venv/bin/python madoka_office.py --recover-after-reboot \
  --config /ABSOLUTE/PRIVATE/office.json \
  --operator OPERATOR_ID --independent-reviewer REVIEWER_ID \
  --authorization-reference OWNER_SESSION_EVIDENCE_REFERENCE
```

This initializes a central and retrieves native identity/state/system-service
inventory; it does not scan, connect, cancel another application or SET. It
requires a kernel boot newer than all prior native events and stable exact-UUID
native absence. The independent reviewer must correlate the evidence with native
tracing/diagnostics and the physical controller. Reject copied-ledger provenance,
clock rollback, state drift, unknown private Bleak layout or an inconclusive
system-service query. Preserve evidence and STOP on any failed check; never edit
rows, delete files, change the state directory or retry via another launcher.

The recovery appends release evidence and preserves historical rows. It certifies
local application ownership only. No public CoreBluetooth API proves that another
host has released the physical radio link. Exclude other clients and independently
observe that the subsequent bounded connection is safe. If this native evidence
does not agree with actual hardware tracing, stop commissioning; do not weaken
the verifier in the office to proceed.

## Gate 2: foreground observations and one reversible SET

Start the HTTPS app in the same attended GUI account/interpreter:

```bash
.venv/bin/python madoka_office.py --serve --config /ABSOLUTE/PRIVATE/office.json
```

Attend any macOS permission/pairing dialog; do not repeatedly pair or reset it.
Check six room identities independently against the wall units. A room without
valid observations must show unavailable, never fictional values. Check latest
readings, raw/recognized setpoint capability schema privately and current mode,
power, fan and safe limits. Begin with the previously observed controller only;
do not infer its native release from the wall remaining responsive.

For the first accepted cooling/heating room, record both target values and the
complete existing range/flag/limit block, power, mode and fan. Choose a one-degree
change inside **both** owner and reported device limits, with no conflicting
client. Confirm it in the UI with the authorization reference/reason. Observe
pending, exactly one SET, strict GET readback and confirmed status; compare the
wall state independently. Save the operation ID and before/after evidence.
Then separately confirm restoration to the recorded initial active target;
verify GET and the wall again. Restoration is a new explicit SET, never automatic.

**STOP** on timeout, ambiguous ACK, lost response, disconnect, wrong field,
unexpected flag/limit changes, stale data or failed/mismatched readback. Do not
press the action again, recreate a command ID or restart to resend it. Inspect
the durable uncertain outcome. “Revisar estado” performs only GET and records
operator acceptance of the actual observation; it does not confirm the old SET
or bypass unresolved ownership. An uncertain write is not an automatic reason
to attempt restoration; decide a safe physical/manual recovery with the owner.

## Gate 3: all supported controls and all rooms

Within the same bounded authorization, test one room at a time, preserving and
restoring initial values using separately confirmed actions:

1. Power OFF, GET/wall confirmation, explicit restoration ON only if authorized
   and safe for the installation. Distinguish manual ON from forbidden auto-on.
2. Cooling/heating/Auto mode selections which the model actually supports,
   GET/wall confirmation and restoration. Auto-mode setpoint/fan commands remain
   rejected because their semantics are unproven.
3. Each commissioned fan speed in a supported heat/cool mode, check both fan
   bytes and restore the initial active fan. Unsupported fan control is recorded
   as unsupported and left disabled; no alternate undocumented command is tried.
4. Repeat the supported control checks for all six exact room identities. Check
   that histories, desired state, errors and outcomes stay isolated by room ID.

Validate manual setpoint on/off changes exactly once; the protocol lacks atomic
compare-and-set, so avoid wall/other-client changes between baseline and readback.
FREE and MONITOR may record deviations with unknown origin. Maintenance and
suspension must persist. Physical ENFORCE, automatic power-on, schedules, native
locks and installer modifications must remain unavailable.

## Gate 4: concurrency, loss, clean shutdown and restart

Keep six retained connections while exchanging at concurrency1. Validate demand
from multiple office clients and independent room actions. If covered by owner
scope, increase concurrency gradually up to six only while setup, readback,
wall responsiveness and the Mac's native diagnostics remain healthy. Record
the accepted capacity rather than declaring six concurrent links from mocks.

With no SET pending, simulate one controlled loss by an approved reversible
method. Confirm immediate staleness, correlated disconnect/native evidence,
bounded backoff/reconnect, no command replay and unaffected other rooms. Do not
power-cycle the HVAC system or reset pairing as a convenient loss injection.
Confirm the three-attempt breaker/rolling limit and operator-only uncertainty
review; do not create a storm to measure a limit.

Stop the foreground app once and wait for completed cleanup. Verify every
`released_native` row against correlated callbacks, native state0 and absent
service inventory; neither public-return logs nor exit0 alone certify hardware.
Restart the same exact source/config and verify histories and operation outcomes
persist. An unresolved room must retain capacity and remain blocked. If a crash
test is separately authorized, preserve pending records; recovery after reboot
must still require the full evidence gate, and no SET can be replayed.

## Gate 5: user-session service and office browsers

Only after successful foreground acceptance, adapt
[the LaunchAgent template](../deploy/com.madoka.office.plist.example), with exact
absolute paths and no shell/environment/PYTHONPATH injection. Store the plist as
`~/Library/LaunchAgents/com.madoka.office.plist`, owned by the non-root office user.
The template requires the user's **Aqua login session**. It starts at login and
throttles abnormal restarts to five minutes. A clean stop is not restarted.
Use `launchctl bootstrap gui/USER_UID /ABSOLUTE/PLIST` only under the explicit
service-installation authorization. Do not install a LaunchDaemon.

Verify TCC/Bluetooth permission with the **actual LaunchAgent executable**, not
the previously permitted Terminal. Bleak/Apple documentation does not prove
that those permission identities are interchangeable. If consent cannot be
granted in the authenticated GUI context, STOP service acceptance. Do not change
TCC databases or sign/package a different executable without review. This
template does not promise operation before login or after logout. Normal office
unattended operation requires that accepted user session to remain active.

Test a trusted desktop and phone on the allowed office network: HTTPS trust,
authentication before assets/API, six real rooms, all commissioned controls,
pending/confirmed/uncertain outcomes, last readings and independent failures.
Verify HTTP does not accept credentials, forbidden origins/hosts cannot mutate,
and no public network route exists. Stop/restart the service with the GUI-user
launchctl domain, inspect native proof and confirm persistence once more.

Primary execution-context references:
[Apple agents/services](https://developer.apple.com/documentation/servicemanagement/),
[Apple launchd agents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html),
[Bleak macOS permissions](https://bleak.readthedocs.io/en/latest/backends/macos.html).

## Acceptance, updates and rollback

The independent reviewer records exact source SHA, configuration digest, native
ledger revisions, operation IDs, actual platform/firmware/Bleak versions,
observed limits, unsupported controls, accepted concurrency, certificate/service
context and every STOP. Keep private evidence local; publish only sanitized
outcomes. Release remains HOLD for any failed or unverified material gate.

For an update: stop the GUI-user agent, verify ownership release, preserve the
full durable state, review a new exact clean SHA and config, test offline, and
issue attended authorization for that source/config before restart. Do not pull
over a running mutable checkout. For rollback: stop, inspect native outcomes,
select the last reviewed compatible checkout/interpreter and reauthorize it.
Do not open new schema/history with incompatible old code; use a reviewed
forward-compatible release or the previous read-only runtime with its original
history, preserving the shared native ledger. A data backup is for evidence and
recovery planning, not permission to overwrite newer ownership. No licensing or
redistribution assumption changes when installing or rolling back this fork.
