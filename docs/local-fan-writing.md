# Local fan writes with commissioned Auto/manual banks (issue #18)

Base: `master` `2cd1e97f157166b78eca355b167eb3b36b57be56` (PR #17).
This implements the Local runtime and UI, independent of research draft #16,
temperature work and Modes #19. It does not certify physical effect. No real
Bluetooth, Mac mini, private configuration/SQLite, SET, merge or deployment is
used to develop or validate this change. All published fixtures are invented.

## RCA and source basis

The reader correctly reports independent one-byte 0..5 banks, but the old write
predicate requires **both** banks in 1..5. It rejects a legitimate inactive
Auto=0 before the already-existing two-bank SET builder can preserve it.
Reading Auto establishes a protocol observation, not a physical choice or write
permission. A numerical range is also insufficient evidence of five speeds.

Pinned upstream sources, rechecked for this implementation:

- [pymadoka FanSpeedStatus/FanSpeed](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/features/fanspeed.py): SET 16464 (`0x4050`), cooling `0x20`, heating `0x21`, both serialized as one byte. Auto=0, Low=1, Mid=3, High=5; 2..4 are parsed as Mid.
- [pymadoka Feature.update](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/feature.py): constructs a fresh FanSpeedStatus and parses the SET reply. This grounds a bounded **pair_v1** ACK schema: exactly two one-byte banks in 0..5. Its warning explicitly says a parseable reply may not reflect device state. This application's strict parser rejects zero-width/defaulted values, unknown IDs, reserved values and wider integers rather than copying upstream's normalization.
- [OpenHAB SetFanspeedCommand](https://github.com/openhab/openhab-addons/blob/a3cce8efd73d04c1a31a88e06593cc248cefc9fa/bundles/org.openhab.binding.bluetooth.daikinmadoka/src/main/java/org/openhab/binding/bluetooth/daikinmadoka/internal/model/commands/SetFanspeedCommand.java): sends both one-byte banks. Its response handler marks success without inspecting the body; that is **not** evidence for another ACK schema or physical effect here.
- [PR #17 / read-only diagnosis](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/17) established the observed Auto/manual structural classes. Installation mappings, payloads and identifiers remain private and are not recopied.

`pair_v1` is source-grounded, not installation-certified. A pilot can discover a
different response class; only that class's activation then remains pending.
An unknown ACK cannot be enabled with arbitrary raw data or a UI override.

## Permissions and invariants

`Controls.fans` remains the legacy owner allowlist. The optional per-room
`fan_policy` contains `cooling_steps`, `heating_steps` and the fixed
`ack_profile: "pair_v1"`. Each mode's steps must be unique manual codes in 1..5
and a subset of `fans`. Empty steps mean no permission in that mode. No default
adds a choice. Example **synthetic configuration**, not an installation claim:

```json
{
  "fans": [1, 3, 5],
  "fan_policy": {
    "cooling_steps": [1, 3, 5],
    "heating_steps": [1, 5],
    "ack_profile": "pair_v1"
  }
}
```

The Local settings UI commissions the same fields, saves them with the existing
identity-bound private settings and sends no SET while saving. Selected mode
steps are explicitly added to that room's `fans` allowlist. Owner evidence must
independently establish the wall-selectable steps **for that unit and mode**;
seeing an active Auto=0 is insufficient. Labels say Low/High and Medium protocol
codes 2/3/4; they do not claim three different middle speeds. Only permitted
choices and the read-only current observation are exposed in the selector.

| Case | Local permission |
| --- | --- |
| No `fan_policy` | Preserve legacy manual/manual gate and existing choices; no Auto bank newly admitted. |
| Explicit policy, current Cool/Heat steps | Require both raw banks as exact one-byte 0..5; allow active or inactive Auto. |
| Auto/Auto without commissioned steps | Disabled; no inferred capability. |
| Missing, unknown, wrong-width bank or non-Cool/Heat mode | Disabled, zero SET. |
| Auto write selection | Always rejected. No independent Auto-write commissioning is implemented. |
| Pending/uncertain operation, stale/unavailable room | All ordinary write restrictions remain; no replay. |

Manual operations use the existing room lock, session ownership and durable
ledger. At the fresh baseline they revalidate current mode, complete banks,
physical step permissions, setpoint block and absence of unresolved operations.
Cool/Heat changes since the request are rejected, even when both modes have
permissions. The browser binds fan requests to its displayed `expected_mode`;
the backend also compares against its pre-operation observation for callers
without that optional field. A final mode GET guards changes during the
baseline, and permissions are checked again after that await.

The journal (baseline raw bytes, requested value/mode and commissioned profile)
and desired-state persistence both precede arming **one logical SET_FAN**.
Only `0x20`/`0x21` are SET arguments. The inactive bank is copied byte for byte,
including 0; missing bytes are never synthesized, 0xff never becomes Auto, and
GET extensions never become SET arguments. An already-observed permitted value
is an explicit journaled no-op with zero SET.

After a correlated ACK, `pair_v1` only permits verification. The legacy path
also retains its previous empty-ACK schema for previously admitted manual pairs;
new commissioned Auto-bank writes never gain that fallback. ACK classification
uses the policy captured before transmission, not a later configuration change.
No ACK value is used as an observation or required to echo the requested value.

GET_FAN is first after ACK, once, followed by fresh power/mode/setpoint/sensor
GETs. Confirmation requires the exact requested active byte, the unchanged
inactive byte, all pre-existing fan GET fields, both targets, full setpoint
configuration/flags/ranges, power and mode including their pre-existing GET
extensions. Additional newly returned GET fields may be retained; room sensor
temperature may move naturally. A sensor or other required GET failure is still
uncertainty, not permission to skip verification.

No effect, changed preserved data, partial readback, unknown ACK, framing or
correlation error, disconnect, timeout, cancellation or persistence failure
after journaling leaves `uncertain` + STOP. A healthy correlated unknown ACK gets
one diagnostic readback pass; an exact observed result **still cannot confirm**.
Transport/correlation failures cannot enter that healthy-stream branch. No SET
retry, replay, automatic restoration or automatic write is added. Polling,
reconnect, UI GET-only review and restart never clear durable fan uncertainty;
the UI review continues to show the blocked outcome. There is no new rearm API.

## Offline validation

`tests/test_local_fan_writing.py` uses only the closed SDK, temporary state and
synthetic TLVs. It covers manual/manual, manual/Auto, Auto/manual, Auto/Auto in
Cool/Heat; per-mode allowlists; absent/partial/malformed banks; active Auto
without permission; fresh mode/permission/pending-state changes; extra GET-only
fields; exact SET args; journal/intent before SET; unknown ACK even with exact
GET; no effect and side effects; transport/correlation failures; persistent STOP
after review/restart; no-op; and six-room permission/state/failure isolation.
The previous fan-reading regression suite remains, including legacy rooms.

The actual Local HTTP/SQLite/closed-SDK Chromium harness exercises card/detail
observations, permission hints, commissioning, manual/Auto and Auto/manual writes
from desktop/mobile, preservation, stale-mode rejection, no effect, persistent
STOP after review/reload, and unaffected rooms. All five existing Chromium
harnesses and the complete Python 3.12/3.14 suites must pass on the final PR HEAD.
These are software proofs only. CI links and final counts belong in the PR.

## Minimal supervised physical acceptance (pending; do not run in this task)

This procedure replaces an exhaustive fan speed sweep or one blind write per
room. It requires a **new explicit owner authorization** identifying the room,
mode, response class, one supported manual step and the reviewed commit.

1. Reuse existing private observations to group matching response classes and
   previously verified owner steps. Without changing mode/power/temperature,
   choose **one** representative already in Cool or Heat with a supported manual
   step different from the active value, preferably an inactive Auto=0 class.
   Obtain fresh GETs and a passive wall observation of mode, active fan, both
   targets and power. Do not proceed with unresolved operations or guessed
   permissions. Retain exact raw data privately. No new evidence is fabricated
   merely to produce another class or active Auto case.
2. In this room's **Configurar controles**, enable the per-mode fan policy and
   only the independently owner-confirmed step(s) for its current mode. Verify
   card/detail show the real observation, only permitted manual choices are
   selectable and Auto remains display-only. Saving must produce zero SET.
3. After authorization, select the one agreed different manual step **once**.
   Inspect the private journal and exchange evidence: committed baseline and
   intent before one SET_FAN, exactly the two known banks, inactive byte preserved,
   a correlated bounded ACK, then strict fresh GET verification of active,
   inactive and preserved configuration. Compare the new wall setting and
   physical fan response without changing other controls. ACK or GET alone
   cannot demonstrate physical speed. No automatic or test restoration follows.
4. At any unknown ACK, no effect, divergent/missing GET, wall disagreement or
   transport error: **STOP**, preserve uncertainty and evidence, no second SET,
   retry, restore, review-based rearm or SQLite editing. If the ACK is another
   schema, analyze its structure privately and add a separately reviewed bounded
   profile offline before any future owner-authorized write.
5. Record the exact SHA, room's private identity binding, response class, mode,
   commissioned speed and outcome. Reuse protocol evidence across matching
   classes, while retaining each room's independent physical choice permission.
   One pilot does not prove every speed, mode, unit, linked scope or distinct
   physical level. Only unobserved activations stay pending; the implemented
   offline path is complete. Additional physical writes need separate authority.

No merge/deployment or mutation of a real installation is authorized by these
instructions. The physical outcome remains **NOT VERIFIED** until that pilot.
