# Local power incident: RCA and offline validation

Incident source: `76c65ef4ddd51ba39f362d67caa59f4c4359ae03` (PR #11).
No Mac mini database, Bluetooth driver or physical device was accessed. No
deployment, merge, ledger reset or canary-marker change was performed.

## Evidence and historical attribution

The owner reports six connections and at least two, possibly three, power-button
presses. Some units switched on while the dashboard lost availability.

| Owner-local time | Room | Action | Requested value | Recorded result |
| --- | --- | --- | --- | --- |
| 20:37:33 | room-6 | power | true | uncertain |
| 20:37:40 | room-5 | power | true | uncertain |
| 20:38:15 | room-4 | power | true | uncertain |

The supplied excerpt includes `room-6 restart_quarantine` at 20:38:25; per-room
`local_native_absence_recovery` and reservations at 20:41:27–20:41:32; and six
`ready` events at 20:41:30–20:41:35. A `ready` event means setup/subscription
completed, not that every subsequent GET passed or the connection stayed up.
Six `quarantined` events share 21:23:36 and may represent program shutdown,
not simultaneous spontaneous disconnections. Aggregate rows show one quarantine
per room, five released rows for room-1 and four for each other room. These are
application ownership records, not proven radio-failure counts.

The old operation rows contain no failure stage, exception, ACK or readback
trace. **The exact historical trigger is not proven.** Which units changed,
which ACK arrived, whether a GET was stale and whether a timeout/cleanup failed
cannot be recovered from this excerpt. The defects below are independently
reproduced offline and explain this failure class; no physical trace is invented.

## SET_POWER and ACK investigation

Power uses command `0x4020`, object `0x20`, width one, values `0x00`/`0x01`.
Including chunk index, ON is `00 07 00 40 20 20 01 01` and OFF is
`00 07 00 40 20 20 01 00`. These match
[Cool Vibes' code and selftest](https://github.com/txsl/cool-vibes/blob/3b3985ee5a6b93ed994a56331d13435986ca2f8a/daikin_server.py)
and [pymadoka's PowerState](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/features/power.py).
No transmitted-power-frame defect is demonstrated; framing remains unchanged.

[pymadoka Feature.update](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/feature.py)
parses status-shaped SET responses. Its documented mode capture sends
`00 07 00 40 30 20 01 01` and receives `00 07 00 40 30 20 01 00`: the reply
value does not prove the requested state. The power feature uses the same
one-byte status object. This supports that known power schema for subsequent
GET verification, but is **not a power ACK capture from the incident units or
a firmware guarantee**. These reverse-engineered sources are not a Daikin spec.

Our manual backend required `{}` or `{0: b""}` for every SET. A correlated valid
`{0x20: b"\x00"}` or `{0x20: b"\x01"}` power response was rejected **before
any confirmation GET**, even if the effect had occurred. The old closed SDK
always returned an empty ACK and masked this incompatibility.

The correction accepts the two known boolean power shapes, known mode enum
shapes and legacy empty responses. ACK values never establish success; GET is
mandatory. Unknown objects/widths/values, duplicate objects, wrong commands,
malformed frames and missing responses remain failures. Fan/setpoint ACK schemas
are not broadened, and `0xff` is not silently interpreted as success.

## Other verified defects and corrections

- **Immediate readback:** one old, well-framed GET caused instant uncertainty.
  Power now allows at most three successful GET_POWER samples with 0/.3/.6-second
  delays. After the requested value appears, the remaining GET groups verify
  preserved mode, targets and fans. A timed-out, malformed or failed GET is never
  retried in the session. No SET repeats. Three old values still mean uncertain;
  the real devices' settling latency remains unmeasured.
- **Inconsistent deadlines:** with fan enabled, five baseline GETs + one SET +
  five readback GETs each allow six seconds, but the entire backend operation
  allowed only 30 seconds including capacity wait. The manager allowed 35 seconds
  including queue and the browser 40. Valid slow exchanges could be cancelled
  after transmission. Local queue admission now allows 30 seconds before any
  execution, then a full independent 90-second execution budget; the browser
  allows 150. A queued request cannot consume the execution allowance. Power's maximum planned path is 13
  exchanges: five baseline, SET, three power GETs and four remaining readbacks,
  under 79 seconds plus persistence. Normal reads retain 30 seconds. Cleanup
  has separate bounded allowances; cancellation is cooperative, not a hard
  native wall-clock guarantee.
- **Effect uncertainty treated as transport loss:** every exception stopped
  the backend, cleared trusted data and quarantined ownership. A well-framed
  ACK/readback mismatch alone did not show link loss but forced reconnect.
  Local semantic failures now keep an otherwise ready link and GET polling.
  The unresolved durable operation independently disables every manual SET
  until the existing GET-only **Consultar estado** action. That action never
  changes the old outcome to confirmed. Transport/framing failures still stop
  the session and retain ownership until existing native cleanup proof succeeds.
- **Dashboard conflation:** `connected` depended on data age and any operation
  error; the browser also set it false on every HTTP error, including a busy
  rejection. Local bridge/public connection, observation freshness and manual
  outcome are now separate. Old data disables controls and shows **Conectado ·
  lectura pendiente**. Failed HTTP does not invent a radio disconnect; the next
  authoritative poll clears obsolete browser errors. Other rooms remain usable.
- **Lost diagnostics:** cleanup could replace the original exchange exception;
  operations recorded only `uncertain`, quarantine lacked a cause. Additive table
  `native_operation_events` preserves phase, command, timing, ACK objects, raw
  correlated reply, mismatch fields, exception class, started-frame count,
  transport state and separate cleanup error class. No raw SDK exception text
  or UUID is stored. Original operation rows/history remain intact. Stages
  distinguish baseline, persistence, SET, ACK validation, settling, remaining
  readback and comparison. Deadline cancellation can appear as CancelledError
  inside the coroutine; stage is accurate without inventing its source. Optional
  room details expose diagnostics. Quarantine/release events now distinguish
  `application_shutdown`, `explicit_close` and operation failure.

The initial regression run against incident behavior produced **5 failed,
2 passed**: two status-shaped ACK cases, delayed successful GET, uncertain-effect
link availability and stale-read connection reporting failed; empty ACK controls
passed. This is source reproduction, not proof of historical wire bytes.

## Sessions, concurrency and review

Each room retains its own backend, Controller lock/future, native client and
operation ID. Global ready-operation capacity still bounds traffic; setup has
separate capacity. Three power-ON actions across six closed rooms are tested
with one shared slot and accelerated slow exchanges: each executes once and
healthy rooms do not reconnect. Queue expiry before execution still emits no
SET and creates no pending operation.

No native release criterion is weakened. Quarantine is ownership uncertainty,
not proof of a dropped radio link. Cleanup failures preserve capacity and the
original ledger. Historical recovery cannot free a current-process uncertain
owner. Reconnect can collect GET observations but cannot replay/unlock an
unresolved SET. Timeout, cancellation, disconnect, failed readback/cleanup and
restart are tested separately. Shutdown-only six-room quarantine is reproduced
without claiming six spontaneous link drops.

Technical source review covers strict framing/correlation, single-use native
frames, raw-byte preservation, additive migration, timer nesting, locks,
cancellation, recovery, polling and browser state. This is implementation review,
not a new independent or physical review. No new authorizations, operators,
reviewers, dependencies or recovery administration are introduced. Attribution
and unresolved license rights remain unchanged. No upstream contact or PR occurs.

## Validation

| Complete candidate check | Result |
| --- | --- |
| Python 3.12.15 offline suite | 603 passed |
| Python 3.14.8 offline suite | 603 passed |
| New incident cases | 24, included in the full total |
| Chromium: all five harnesses, desktop/mobile | PASS, zero JavaScript errors |
| Relative documentation links | 90 resolved, none missing |
| Whitespace check | PASS |

Each Python suite retains one existing Starlette/httpx deprecation warning.
Tested SHA and hosted CI are recorded separately in the PR. Reproduce
on Python 3.12 and 3.14 with `MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python -m pytest -q`.
Run the five `integration/browser_*.py` harnesses under the same driver guard.
All new tests use closed SDKs and temporary SQLite. Local Chromium also checks
status-shaped power ACKs, power-ON, HTTP busy without a false disconnect,
connected uncertain rooms, diagnostics, room isolation and mobile layout.

## Minimal future physical validation

After installation/physical approval; implementation performs none of these steps:

1. Start with `./run-local.sh`, open `http://127.0.0.1:8090`, verify six room
   identities/readings and connection labels. Never edit or delete records.
2. Select one safely switchable room, record its power, click once and wait.
   Compare the result with the wall and check the other five rooms still update.
   If uncertain, retain the operation ID and **Más opciones → Diagnóstico del
   último cambio**. Never click again merely because a result is slow/uncertain.
3. Only after a confirmed expected change, restore with one deliberate click.
   Then check the other rooms individually within safe office conditions. Never
   automatically restore uncertainty. **Consultar estado** performs GET only.
4. Close/reopen normally and check history and connections. Distinguish shutdown
   cleanup events from spontaneous drops. On unexpected effects, repeat
   uncertainty or unproven cleanup, stop that room's test and retain diagnostics.
   SQL repair, forced release, pairing/TCC changes and marker reset are excluded.

Readback cannot prove all hardware side effects or global radio release. Actual
incident ACKs, firmware settling and six-link shutdown behavior remain evidence
gaps. A future mismatch remains uncertain even when the wall changed.
