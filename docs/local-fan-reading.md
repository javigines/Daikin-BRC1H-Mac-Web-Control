# Local GET_FAN: independent observations, unchanged write permission

Base: `master` at `ae2bab76d16500bc63d4b7eeccbd091bbc08b65c` (PR #15).
This correction is independent of the offline research draft PR #16 and needs
no code from it. No temperature/ACK algorithm, extra mode, ledger, reconnection,
SET argument construction or automatic retry policy is changed.

## RCA and evidence boundary

The Local reader required both GET_FAN objects, cooling `0x20` and heating
`0x21`, to be exactly one byte in 1–5 before reporting either. Already-reviewed
historical evidence establishes one-byte Auto=0 banks alongside manual banks,
with additional returned objects. The prior predicate therefore hid a valid
active observation. These historical observations do not establish current
equipment state, physical speed choices or permission to write.

No historical payloads, room-to-device mapping, extension IDs/values from the
installation, identifiers, screenshots or operational traces are included here.
The six synthetic fixtures reproduce the structural classes (two manual/manual,
three manual/Auto and one Auto/manual) with invented manual values and four
invented opaque extensions. They are examples, not an installation inventory.

## Source basis

- [Reverse-engineered GET/SET_FAN](https://github.com/blafois/Daikin-Madoka-BRC1H-BLE-Reverse/blob/a6394413d82bae249c7f8a04133c49498f327560/README.md): one-byte cooling/heating objects; 1=low, 2–4=medium, 5=maximum.
  GET replies may include extra objects; their presence is not SET support.
- [pymadoka fanspeed.py](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/features/fanspeed.py): Auto=0, Low=1, Mid=3, High=5;
  2–4 collapse to Mid. Thus 0 is a grounded observed code, while five distinct
  physical speeds are not proved by membership in 1–5.
- [OpenHAB fan enum](https://github.com/openhab/openhab-addons/blob/a3cce8efd73d04c1a31a88e06593cc248cefc9fa/bundles/org.openhab.binding.bluetooth.daikinmadoka/src/main/java/org/openhab/binding/bluetooth/daikinmadoka/internal/model/MadokaProperties.java): groups 2–4 as medium, but
  falls back to Auto for unknown values. This fallback is deliberately rejected:
  `0xff`, reserved values, wrong widths and absent fields stay unknown/absent.

## Reading, presentation and authorization

| Layer | Behaviour |
| --- | --- |
| Local reading | Query GET_FAN independently of configured write choices. Decode each known bank separately: exact `bytes`, length 1, value 0–5. Retain the untouched response, including unknown fields and extensions, in the existing raw observation. |
| Active observation | Cool (3) selects the cooling bank; Heat (4) selects heating. Missing/unknown inactive data never hides a recognized active value. Other modes have no inferred active fan bank. |
| Field status | Known 0–5 is `available`; absent object is `not_observed`; invalid type/width/value is `unknown`; no established mode mapping is `not_applicable`. A rejected GET is not mislabeled as an unrequested field. |
| UI | Card and detail select the observed manual value or **Auto**. Unknown/missing values have explicit placeholders. Auto is a disabled display option. A readable but uncommissioned/blocked fan remains visible and disabled. |
| Write authorization | Preserve the old Local permission exactly: Cool/Heat, configured manual choices, latest fan observed, **both raw banks one byte 1–5**, healthy available session and no unresolved operation. Recheck after the fresh baseline before journaling or arming SET. No 0/partial/unknown pair gains permission. |

The existing manual access for previously admitted pairs remains unchanged;
this is not new commissioning or physical certification. No default replaces a
missing byte, and no GET extension is added to SET. The Office/non-Local reader
retains its existing strict interpretation. Optional transport failures still
follow the existing session failure path; this patch does not suppress them.

The old five-number selector/commissioned choices are not evidence of five
distinct physical speeds. Reading a code or Auto does not certify that a
manual change is supported, currently selectable, or physically effective.

## Offline validation contract

Synthetic tests cover the six independent structural examples in Cool and Heat,
Auto in either/both banks, no fan write configuration, unknown/reserved codes,
wrong types/widths, absent fields, additional objects, mode changes, and no
inferred active bank outside Cool/Heat. They assert raw preservation and room
isolation, and reject new manual/Auto writes before SET or durable intent.

A stale UI cannot bypass the gate: a newly Auto/unknown/missing baseline or
changed mode rejects before SET. A synthetic uncertain operation remains blocked
on polling; explicit review uses GET only and does not turn its outcome into a
confirmed write. Existing ledger semantics are unchanged.

The complete Python 3.12/3.14 suites and all five Chromium desktop/mobile
harnesses must pass, including the actual Local API with the six synthetic fan
structures and zero extra SETs during observation. CI must pass on the reviewed
draft HEAD. These certify software behaviour only, not physical acceptance.

Local validation: **795 passed** on Python 3.12 and **795 passed** on Python
3.14 (755 baseline + 40 new fan cases), with the existing Starlette/httpx
deprecation warning. All five Chromium harnesses pass on desktop/mobile with
zero JavaScript errors. GitHub CI status must be checked on the draft's exact
HEAD rather than inferred from these local results.

## Minimum future physical acceptance — pending, not performed here

1. Reuse the historical structure analysis; do not collect it again or assume
   its values remain current. After a separately authorized run of this patch,
   use **GET-only** observations on the existing Local path, without changing
   settings, power or mode. Do not clear any unresolved operation to proceed.
2. Compare the active mode and observed fan against the normal wall display
   for each currently available response class. If no Auto-active example is
   currently present, defer that physical assertion; do not switch mode or fan
   merely to manufacture one. Synthetic Cool/Heat selection is already covered.
3. Verify card/detail agree, a current 0 appears as Auto, invalid/missing fields
   do not become Auto, other telemetry remains available, and newly recognized
   pairs still have no fan write permission. Confirm zero SET_FAN during this
   read-only acceptance and retain evidence privately by logical assignment.
4. No exhaustive speed sweep or six-room mode-transition matrix is needed for
   a reading fix. Future fan writes remain a separate task requiring supported
   physical choices, exact SET arguments, a grounded ACK profile, GET effect and
   preservation checks. An ACK never proves physical success; uncertainty keeps
   STOP, no automatic retry and the existing explicit GET-only review path.

No physical Bluetooth, Mac mini access, real SQLite, deployment or merge is
part of this correction. Temperature acceptance, including whether a real
setpoint ACK matches a compact profile, remains separate and pending.
