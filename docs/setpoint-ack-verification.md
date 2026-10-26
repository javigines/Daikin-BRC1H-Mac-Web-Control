# SET_SETPOINT ACK classification and GET verification

Base: `master`, squash PR #14,
`72e111ee06d779623cabfd5322870f7d9e243fa8`.
This iteration contains invented protocol fixtures and closed SDKs only. No
private evidence, installation values, inventory, device identifiers or actual
traces are reproduced here. No hardware access or deployment was performed.

## RCA

PR #14 called `setpoint_fields()` from `validate_set_ack()`. The former validates
GET_SETPOINT observations: it requires two-byte temperatures and effective
limits and interprets the range/mode flags. Those are necessary GET conditions,
but they are not a SET response schema. Applying them to an opaque ACK can stop
at `ack_validate` before any immediate GET and leave an applied change uncertain.

Response correlation and a familiar set of IDs prove neither acceptance nor
physical effect. An ACK with no effect and an ACK followed by the intended effect
can have the same structure. GET must distinguish those outcomes. A failure to
decrease a setpoint is **not yet a proven firmware limit**: firmware restrictions,
configuration and delayed/no effect remain hypotheses for physical acceptance.

## Public evidence

- [Lafois protocol reference, a6394413](https://github.com/blafois/Daikin-Madoka-BRC1H-BLE-Reverse/blob/a6394413d82bae249c7f8a04133c49498f327560/README.md):
  describes variable-length TLV responses, additional returned objects, and the
  GET_SETPOINT temperature/limit widths. The SET_SETPOINT section describes
  arguments, not a complete ACK value or error-code specification. GET widths
  cannot be assumed to be ACK widths from that table.
- [pymadoka FeatureStatus.parse / Feature.update, eac7c3ad](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/feature.py):
  `update()` parses the returned SET message, but explicitly warns that its data
  need not reflect device state. `parse()` obtains widths from each response TLV,
  rather than enforcing the GET width constants.
- [pymadoka SetPointStatus.set_values, eac7c3ad](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/features/setpoint.py):
  consumes the 17 known IDs using `int.from_bytes`, which accepts one-byte as well
  as two-byte integers. No per-field response width or success-code check is
  applied there. This supports numeric response forms with different widths;
  it does not establish the meaning of those values or validate physical effect.
- [OpenHAB SetSetpointCommand.handleResponse, a3cce8ef](https://github.com/openhab/openhab-addons/blob/a3cce8efd73d04c1a31a88e06593cc248cefc9fa/bundles/org.openhab.binding.bluetooth.daikinmadoka/src/main/java/org/openhab/binding/bluetooth/daikinmadoka/internal/model/commands/SetSetpointCommand.java):
  treats the received response as command completion without interpreting its
  body. This independently separates SET response handling from GET decoding.
  Its `SUCCEEDED` inference is not adopted as physical confirmation here.

These sources support bounded structural candidates for subsequent GET, not a
universal ACK specification. No private response has been loaded or compared
against the implemented profiles in this iteration.

## Separate schemas and state transitions

ACK profiles are explicit and independent of the GET validator. The bridge must
first prove complete framing, unique objects, the expected command/header and a
live correlated pending request. Only then can ACK classification run.

| ACK profile | Structural rule | Consequence |
| --- | --- | --- |
| `empty` | Existing empty body or empty no-argument marker | Candidate; strict GET required |
| `full_fields` | Exactly the 17 known IDs; six numeric response fields have width 2, the other eleven width 1 | Candidate; strict GET required |
| `compact_fields` | Exactly the same 17 IDs; every field has width 1 | Candidate; strict GET required |
| Unknown, well framed and correlated | Missing/additional IDs, zero lengths, mixed numeric widths, unsupported widths | One diagnostic GET snapshot; always `uncertain` |
| Malformed or uncorrelated | Truncation, duplicate IDs, wrong command/header, framing failure | Existing transport STOP; no further GET exchange on that stream |

The six variable response fields are `0x20`, `0x21`, `0xA2`, `0xA3`, `0xB2`,
`0xB3`. IDs describe public protocol fields, never device identities. Only the
two uniform profiles above are recognized, not arbitrary combinations of widths.
The payload must contain byte values. Values are deliberately opaque: no ACK
byte is decoded as temperature, effective limit, a GET flag, acceptance or an
error code. Even a recognized response containing contradictory-looking bytes
only permits a GET. The old ACK flag-domain test was replaced with an unsupported
width test; the GET flag-domain validation remains strict and is still tested.

The first command after a returned ACK is GET_SETPOINT. There is no setpoint
read retry or settling loop. A bounded snapshot then reads power, mode, sensor
and, if already configured, fan; it reuses that first setpoint reply. Confirmation
requires the requested active target **byte for byte**, the inactive target,
all preserved limit/configuration/symbol bytes and baseline GET extensions.
Power, mode and the existing observed fan invariants must also match. New GET
extensions remain read-only. Missing/invalid GET fields and invalid limits retain
the existing safety gates. An unchanged active target is diagnosed `no_effect`;
an unexpected target is `active_target_mismatch`, not a claimed firmware limit.

For a well-framed unknown ACK, the same single snapshot is diagnostic only.
`requested_state_observed` records that strict GET checks matched; it **cannot**
confirm the operation. A divergent or failed observation records `not_verified`
with a fixed reason. Both paths retain the original unknown-ACK cause and finish
`uncertain`. Cancellation and transport failure preserve STOP. The observation
does not arm another write, acknowledge intent, reconnect, or retry the SET.

The existing journal is persisted before SET, and there remains exactly one
logical SET per operation. Pending/uncertain entries block all further writes
for that room through polling and restart. Only the existing explicit GET-only
review can mark an unresolved entry `reviewed_uncertain`; it sends no SET and
does not retroactively claim the original command succeeded. No ledger schema,
idempotency, recovery, capability allowlist or power-control behavior is changed.

## Safe diagnostics

Operation events record fixed phase names, profile names, result/rejection codes,
elapsed time and exception classes. Readback mismatch events identify logical
fields without expected/observed values. Transport diagnostics retain command,
stage, response size and failure metadata, replacing raw `reply_hex` capture.
ACK object maps and full response payloads are no longer copied into diagnostics
or quarantine events. The existing private baseline/observed ledger records are
unchanged; no ledger exports are included in this PR.

Reasons include `ack_object_ids`, `ack_value_type`, `ack_field_widths`,
`readback_schema`, `no_effect`, `active_target_mismatch`,
`preserved_field_mismatch`, `state_mismatch`, `get_unavailable` and
`reply_framing_or_correlation`. Unknown-ACK observation has its own events and
ends with the primary ACK rejection, even if the snapshot matched.

## Offline acceptance

Local results: **755 tests passed** on Python **3.12.15** and **755 passed** on
**3.14.9**. All five existing Chromium harnesses passed desktop/mobile checks
with zero JavaScript errors. The only pytest warning was the existing
Starlette/httpx deprecation. These results certify synthetic behavior only.

Use the existing offline guard and temporary test stores, never an installation
checkout or real database:

```sh
env MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python3.12 -m pytest -q
env MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python3.14 -m pytest -q
env MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python3.14 integration/browser_local.py
```

The existing CI runs full pytest on both versions and all five Chromium harnesses.
The local browser harness now uses compact synthetic ACKs for setpoint actions
in desktop/mobile flows, including increases, decreases and no effect. Synthetic
tests cover all six rooms in Cool/Heat, both directions, recognized ACK with and
without effect, strict field preservation/limits, unknown observation without
confirmation, malformed/correlated framing, GET failure, safe diagnostics,
uncertainty after restart and GET-only review without a SET. Existing PR #14
tests continue to cover valid/invalid device and owner windows and GET extensions.

Only synthetic behavior is certified. The PR records actual test counts and
the exact head/CI run. CI screenshot uploads remain disabled; generated local
images, temporary databases and runtime evidence are not included in Git.

## Conditions for first physical acceptance (not executed or authorized here)

1. Use a separately authorized physical acceptance session and one controller
   with fresh recognized GET observations in Cool or Heat. Establish privately
   which ACK profile is actually returned; do not publish values or raw traces.
   A different profile remains unknown rather than being normalized to fit.
2. If there is prior uncertainty, use the existing explicit GET-only review
   first. Verify that it reads actual state and emits no SET. Do not clear or
   rewrite the ledger by hand.
3. Request one allowed whole-degree adjustment with a new operation identity.
   Require exactly one SET followed immediately by GET_SETPOINT and the bounded
   snapshot. A recognized ACK only permits that verification. Compare the active
   and inactive targets, mode, limits and every preserved field.
4. Only after a confirmed observation, separately request the opposite direction
   as a new explicit operation. Any no effect, divergence, unknown ACK or read
   failure ends the sequence at STOP/uncertain; no automatic retry or restoration.
   Keep other controllers' state and write counts unchanged.
5. Record sanitized phase/result codes and the acceptance decision privately.
   An unknown ACK with matching GET still requires review and cannot satisfy
   confirmed-command acceptance. No conclusion about lower firmware limits is
   justified until this physical distinction is observed and reviewed.

This is a checklist for a later authorized session, not permission to access
hardware, install, deploy, merge or inspect real SQLite in this iteration.

## Independent pending capabilities

- Fan capability remains a separate investigation: four controllers were
  reported with fan configuration enabled but unrecognized GET_FAN. This PR
  neither changes fan decoding nor enables additional fan values.
- Fan, Dry and Ventilation modes are restricted by the existing software
  allowlist and remain pending physical capability validation. No modes are
  added or broadened here.
