# Coordinated setpoint transitions — offline implementation, physical pilot pending

Base: `master` at `2cd1e97f157166b78eca355b167eb3b36b57be56` (PR #17).
This change adds no installed commissioning profile. All committed fixtures use
invented temperatures, identifiers and payloads. No real connection, database,
configuration, installation capture or physical trace was accessed for this PR.

## Evidence and root cause

The application previously changed one target and required every other byte,
including the inactive target, to remain unchanged. This is correct for simple
changes, but cannot request a transition that also moves the inactive target to
preserve a minimum gap. A structurally recognized ACK cannot distinguish a
successful change from a request ignored by the controller.

The private commissioning report supplied for this iteration distinguishes:

- Simple upward/downward changes with structural ACK and confirming GET.
- A request that closes the target gap: recognized ACK, unchanged GET.
- A wall-controller transition that lowers both targets together, subsequently
  observed by GET. This supports a narrowly bounded proposal, **not** proof that
  the same coordinated BLE command has already succeeded.

No installation mapping, actual target pair or historical payload is reproduced
here. The unchanged-GET observation does not prove a firmware lower-temperature
limit or establish all causes of ignored writes.

## Sources and what they establish

1. [Daikin BRC1H52W/S/K installer/user reference guide, 4P596266-1,
   2020.12, §15.5.17, pp.109–110](https://www.daikin.eu/content/dam/document-library/Installer-reference-guide/ctrl/individual-control-systems/BRC1H52W,BRC1H52S,BRC1H52K_Installer%20and%20user%20reference%20guide_4PEN596266-1_English.pdf).
   With dual setpoints, Cooling ≥ Heating + minimum differential. Lowering
   Cooling through that gap automatically lowers Heating; raising Heating
   through it raises Cooling. Single/dual setpoint logic, indoor-unit versus
   remote-controller logic, and centralized control affect available behavior.
   A manual for this controller family explains the behavior; it does not
   certify the installed indoor units or their BLE semantics.
2. [Reverse protocol, a6394413](https://github.com/blafois/Daikin-Madoka-BRC1H-BLE-Reverse/blob/a6394413d82bae249c7f8a04133c49498f327560/README.md),
   GET_SETPOINT `0x0040` / SET_SETPOINT `0x4040`: two target fields and a
   minimum-differential/configuration block. Naming a byte does not establish
   a universal unit, flag interpretation or applicability in every mode.
3. [pymadoka SetPointStatus, eac7c3ad](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/features/setpoint.py)
   defines `MINIMUM_DIFFERENTIAL_IDX=(0x32,1)` and the known SET block. Its
   parsing divides and rounds one-byte fields too; its serialization supplies
   defaults. Neither behavior is copied here or taken as proof that a raw byte
   universally expresses degrees Celsius. See also the existing
   [GET/SET preservation contract](setpoint-compatibility.md) and
   [independent ACK classification](setpoint-ack-verification.md).

The minimum gap between stored setpoints is distinct from Auto changeover
intervals/hysteresis (guide pp.21/56) and setback recovery differentials (§15.5.18).
No automatic mode, capability, differential or firmware-limit inference is added.

## Narrow commissioning contract

`LocalConfig.coordinated_setpoints` is an optional map, empty by default, in the
existing private configuration. Each entry must match an existing logical room
and SHA256 of its configured physical identity. It is separate from `Controls`:
the ordinary controls UI/API/sidecar cannot commission this behavior. Existing
installations continue with the single-target contract unless a profile is
explicitly supplied in private configuration after evidence review.

Only `cool-lower-one-v1` exists. It permits exactly one commissioned before-pair:

- Cool mode only, Cooling exactly one whole degree above Heating.
- Exact opaque configuration fingerprint `0x30/0x31/0x32 = 0/0/1`.
- Request Cooling minus one, propose Heating minus one in the same SET.
- Both baseline and proposed targets fit each bank's fresh device limits and
  the existing owner window. Missing, zero, inverted or unrecognized ranges
  cannot be substituted. Half-degree requests remain unsupported.

The fingerprint is an equality precondition, **not** a decoder of single/dual
logic or a universal differential. No extrapolation to other rooms, baseline
pairs, Heat, Auto, other differential bytes, increases of the inactive target or
further coordinated decrements occurs. Another observed pair requires explicit
private commissioning; matching a byte alone never enables the feature.

Schematic configuration (placeholders are intentionally not usable as-is; all
numeric example temperatures below are invented):

```json
"coordinated_setpoints": {
  "<existing-private-logical-room>": {
    "profile": "cool-lower-one-v1",
    "identity_digest": "<SHA256 of exact configured identity, kept private>",
    "cooling_before": 26,
    "heating_before": 25
  }
}
```

Do not copy the synthetic example into an installation or infer a profile from
current GET alone. This PR does not modify any private configuration.

Unprofiled requests retain the existing simple SET behavior and strict inactive
preservation. They may still return `no_effect`/uncertain. An unsupported profile
does not gain a coordinated proposal; a known paired request with invalid
inactive limits rejects even if the client omits consent.

## Consent, transmission and confirmation

1. A fresh controller snapshot may expose a proposal containing both before/
   after pairs and an opaque digest. No physical identity or raw response is
   exposed in the proposal. Rendering/polling sends no SET.
2. The local UI shows both targets and explicitly identifies the inactive Heat
   change. Cancel or Escape sends no manual request. Only **Confirmar ambos
   cambios** supplies affirmative consent and the proposal token.
3. Under the existing room/transport locks the backend takes a new baseline.
   It independently recomputes the plan and token, binding the configured room,
   physical-identity digest, profile, owner controls, power/mode/fan banks, both
   setpoints, limits, symbols, flags and **all** existing GET extensions. Natural
   sensor movement is excluded. Any changed binding or missing consent rejects
   before ledger intent, desired state or SET. The client cannot supply an
   alternative inactive target or authorize arbitrary fields.
4. The existing durable `before_state` JSON records both authorized target IDs,
   exact expected bytes, original wire baseline and confirmed proposal before
   transmission. No ledger schema changes. Desired active state remains the
   existing scalar policy field; the durable operation records the full pair.
5. Exactly one logical SET contains the 17 known arguments, replacing only
   `0x20/0x21`; every other known byte is copied. Unknown GET extensions are
   preserved for validation and never transmitted as SET arguments.
6. Existing ACK structural recognition only permits verification. Immediate
   GET_SETPOINT must match **both** explicitly authorized target byte strings.
   All other known bytes and pre-existing extensions must match, while new
   well-formed GET extensions may appear. Power, mode and fan banks retain
   their strict preservation checks. A simple operation still authorizes only
   the active target.
7. Both targets unchanged → `no_effect`; partial/wrong change → active/inactive
   target mismatch. Any divergent preserved field, GET error, invalid ACK or
   interruption keeps durable uncertainty and STOP. An unknown well-framed ACK
   may obtain the existing single diagnostic GET but **cannot** confirm, even
   if the desired pair appears. No retries, repeated SET, fallback single-target
   command, rollback or automatic restoration.
8. Polling and restart retain the unresolved-write block. Existing explicit
   GET-only review can record `reviewed_uncertain`; it never replays intent or
   changes the historical operation into confirmed.

## Offline validation

`tests/test_coordinated_setpoint.py` uses an invented pair and six independent
closed SDKs. It covers private identity binding/default-off configuration,
simple versus coordinated changes, exact scope, cancellation/missing consent,
wrong/stale/cross-room tokens, both device/owner windows, known-byte/extension
preservation, one SET, compact ACK, no effect, partial effect, unknown ACK,
failed GET, durable restart blocking and GET-only review. The existing baseline
suites continue to cover malformed framing/correlation and transport fencing.

`integration/browser_local.py` exercises the actual Local API with desktop and
mobile Chromium: both displayed target pairs, default Cancel focus, cancellation
without SET, stale-plan rejection, explicit confirmation, one SET and isolation.
The other four Chromium harnesses remain part of complete CI.

Reproduce with the existing development/browser requirements, Python 3.12 and
3.14, `MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci`: run `python -m pytest -q` on both,
then all five `integration/browser_*.py` harnesses listed in the unchanged CI
workflow. Only closed SDKs and temporary synthetic databases are allowed.
Generated screenshots remain local and are excluded from this PR.

Local validation: **853 passed** on Python **3.12.15** and **853 passed** on
Python **3.14.9**; all five Chromium harnesses passed, including desktop/mobile
Local consent and cancellation checks, with zero JavaScript errors. The existing
Starlette/httpx deprecation warning remains. Hosted CI is reported separately on
the PR so local results cannot be mistaken for a hosted or physical run.

Offline PASS proves software contracts. It does not establish coordinated BLE
acceptance, firmware support in another mode or controller, or physical safety
outside the commissioned transition.

## One future supervised physical pilot (not executed by this PR)

1. With separate operational GO after code review, select only the privately
   evidenced controller. Privately commission its exact observed pair/identity;
   leave every other profile absent. Do not change modes, fan configuration or
   controller configuration to make the test pass. Require healthy fresh GET,
   no pending/uncertain operation, the exact commissioned baseline/fingerprint,
   and valid limits for both before/after targets. If absent, defer the pilot;
   do not send preparatory SETs to manufacture the starting state.
2. Open the decrement proposal, verify both before/after targets, cancel once
   and verify zero SET. Reopen and explicitly approve the two targets once.
   Observe one logical SET, correlated ACK structural profile/widths and the
   immediate strict GET. Keep actual values and identifiers private.
3. PASS only if GET shows both exact approved targets, all preserved bytes and
   prior extensions remain intact, mode/power/fans are unchanged, the wall
   controller agrees, the ledger says confirmed and other rooms are unchanged.
   This is the first coordinated BLE acceptance; prior wall behavior is not
   equivalent proof. Record ACK lengths privately rather than assuming a
   compact profile from identifier presence.
4. Any no effect, partial/divergent observation, unknown ACK, timeout or link
   loss: STOP; leave uncertainty durable. No second SET, retry or restoration.
   If separately requested, use existing GET-only review. Review does not turn
   a failed pilot into success. Do not broaden scope or repeat the experiment
   across six rooms to establish this one transition.

Fan behavior, additional modes, Bluetooth recovery, ledger implementation and
PRs #13/#16 remain outside this change. Merge and deployment are not authorized.
