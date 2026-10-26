# Fan capability investigation: offline observation, no capability activation

Baseline: `master` at `ae2bab76d16500bc63d4b7eeccbd091bbc08b65c`
(PR #15). This investigation is independent of temperature confirmation.
No physical observation or successful real fan operation is certified here.

## Finding and scope

The current native reader admits GET_FAN only when **both** cooling object
`0x20` and heating object `0x21` are exactly one byte with a value from 1 to 5.
Consequently a legible active field can be hidden by an absent, automatic or
unrecognized inactive field. This is a demonstrated software condition, not an
explanation proven for any particular installation. A configured list of
1–5 is an owner allowlist; it does not discover five physical fan levels.

The new `madoka_multiroom.fan_observation` module classifies an explicitly
supplied, already parsed response offline. It has no integration with device
readers, control views, SET construction, BLE, recovery or storage. It grants no
write authority and changes no runtime gates. It separates useful historical
evidence from the capability claims that still need device-specific proof.

## Current code and previous decision

In `madoka_multiroom/backends/office_native.py`:

- `_read()` validates the pair before selecting the active field. A native
  local observation retains other telemetry and leaves `fan_id=None` on an
  unrecognized pair; the office authorization path raises `BackendUnavailable`.
- `field_status` labels any `None` value `not_queried`, including a GET_FAN that
  was attempted but rejected. That status cannot distinguish an omitted query
  from an unknown response. The offline analyzer makes that distinction.
- Fan controls and action validation admit only Cool (3) and Heat (4). Local
  fan actions also require a non-`None` observation and configured choices.
- A fan SET sends both `0x20` and `0x21`: the requested active byte and the
  untouched inactive byte. Relaxing a read gate does not establish that an
  absent or unknown inactive byte is safe to send. No byte may be invented.
- Fan ACK validation currently accepts the generic empty response forms only;
  it has no independently established data-bearing SET_FAN profile. Later GET
  verification compares active and inactive fan state along with other
  preserved state. An upstream ACK success flag would not prove an effect.

`office_access.Controls` restricts configured fan choices to 1–5. The separate
`backends/daikin_mapping.py` reader also checks both fields. Neither is changed.
Commit `76c65ef` introduced the local degraded observation: unreadable fan data
must not discard valid room telemetry or enable manual fan writes. Existing
`test_local_unsupported_fan_does_not_hide_room_temperature_or_allow_write`
coverage protects that zero-SET outcome. Existing
tests also allow a legible inactive value outside the owner's write choices;
observation and permission are deliberately distinct.

## Pinned public evidence

| Source | Demonstrated behavior | What it does not establish |
| --- | --- | --- |
| [Reverse engineering README, `a639441`](https://github.com/blafois/Daikin-Madoka-BRC1H-BLE-Reverse/blob/a6394413d82bae249c7f8a04133c49498f327560/README.md), GET_FAN `0x0050`, SET_FAN `0x4050` | Cooling/heating one-byte objects `0x20`/`0x21`; 1 low, 2–4 medium, 5 maximum; SET includes both objects. General responses may contain extra objects. | A universal absent-field sentinel, five distinct speeds, an active-only SET, or safe arbitrary SET extensions. |
| [pymadoka `fanspeed.py`, `eac7c3a`](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/features/fanspeed.py) | AUTO=0, LOW=1, MID=3, HIGH=5; 2–4 collapse to MID. Reads and writes both objects. Other enum values fail. | Physical Auto support everywhere, missing inactive fields, distinct 2/3/4 levels or a reason to normalize `0xff`. |
| [OpenHAB reader, `a3cce8e`](https://github.com/openhab/openhab-addons/blob/a3cce8efd73d04c1a31a88e06593cc248cefc9fa/bundles/org.openhab.binding.bluetooth.daikinmadoka/src/main/java/org/openhab/binding/bluetooth/daikinmadoka/internal/model/commands/GetFanspeedCommand.java) and [enum](https://github.com/openhab/openhab-addons/blob/a3cce8efd73d04c1a31a88e06593cc248cefc9fa/bundles/org.openhab.binding.bluetooth.daikinmadoka/src/main/java/org/openhab/binding/bluetooth/daikinmadoka/internal/model/MadokaProperties.java) | Reads both fields; 2–4 become MEDIUM. The enum falls back to AUTO for other values. | That unknown-as-Auto fallback is safe; this project must retain unknown values as unknown. |
| [OpenHAB writer, `a3cce8e`](https://github.com/openhab/openhab-addons/blob/a3cce8efd73d04c1a31a88e06593cc248cefc9fa/bundles/org.openhab.binding.bluetooth.daikinmadoka/src/main/java/org/openhab/binding/bluetooth/daikinmadoka/internal/model/commands/SetFanspeedCommand.java) | Sends both one-byte fields and marks the response successful. | A response alone proves application or unchanged inactive state. This success inference is not adopted. |
| [OpenHAB binding README, same revision](https://github.com/openhab/openhab-addons/blob/a3cce8efd73d04c1a31a88e06593cc248cefc9fa/bundles/org.openhab.binding.bluetooth.daikinmadoka/README.md) | Documents 1–5 as virtual values grouped low/medium/maximum; some models support Auto. | Every configured integer is a distinct physical speed on every unit. |

The [official BRC1H52 reference guide, 4P596266-1, 2020.12](https://www.daikin.eu/content/dam/document-library/Installer-reference-guide/ctrl/individual-control-systems/BRC1H52W,BRC1H52S,BRC1H52K_Installer%20and%20user%20reference%20guide_4PEN596266-1_English.pdf)
(printed pages 28 and 63, sections 5.5.2/13.5.2) describes unit-dependent
two/three/five-speed choices and optional Auto. Protection may force Auto or
stop the fan, and a change can take time. Printed page 84 describes independent
heating and cooling/dry/fan settings. Dry controls temperature and fan
automatically (pages 18/54). These product-family statements require matching
the actual indoor-unit/controller documentation; they are not installation
capability discovery.

The [newer BRC1H52 K7/S7/W7 reference guide, 4P813200-1A, 2026.01](https://my.daikin.eu/content/dam/document-library/Installer-reference-guide/ctrl/individual-control-systems/brc1h52k7/BRC1H52K7.BRC1H52S7.BRC1H52W7_Installer.User%20reference%20guide_4PEN813200-1A_English.pdf)
(printed page 111, active airflow circulation) adds a relevant model-specific
case where the feature controls fan speed and manual changes are unavailable.
Its applicability to an older/different controller is unproven.

## Interpretation rules

| Observation | Offline classification | Safe conclusion |
| --- | --- | --- |
| GET was not requested | `not_queried` | No current fan evidence, even if stale objects were supplied. |
| Query attempted, no exported response | `response_missing` / `not_observed` | No response available; this does not prove a device feature is absent. |
| Requested response lacks one known object | That field `not_observed` | Other field may be legible. No substitute byte or SET permission follows. |
| Known field is one byte 1–5 | `manual_observed` | A public protocol code was observed; 2/4 do not establish distinct speed levels. |
| Known field is one byte 0 | `automatic_observed` | Public sources interpret this as Auto. No Auto write permission or universal support follows. |
| Wrong type/width or another value | `unknown` with structural reason | Preserve uncertainty; never convert `0xff` or any unknown code into Auto/unavailable. |
| Additional well-formed GET objects | Count only | Known fields can still be analyzed. Extra objects are not SET arguments. |
| Cool/Heat | Select cooling/heating for analysis | Current software policy admits the mode; physical fan support remains unestablished. |
| Fan/Dry/Auto/Ventilation | `not_admitted_by_current_fan_policy` | A software restriction, not a finding of physical incompatibility. |

A GET value alone cannot distinguish “this speed is physically unavailable”
from “currently overridden/automatic”, “mode not suitable” or “unknown encoding”.
Those require independent device documentation, controller menu/prohibition
state and, where necessary, a bounded observed operation. The classifier always
reports `physical_capability=not_established` and `write_authority=not_established`.

## Reuse existing evidence before new connections

Only an operator-selected **existing local export** may be inspected. Do not
scan for devices, connect to a controller, open a real ledger/database, or
discover private paths. Useful existing evidence includes a saved parsed
GET_FAN response, its already known mode and whether the GET was attempted;
logs that report only `fan_id=None` cannot recover missing TLV lengths/values.
Existing screenshots or controller/indoor-unit documentation can establish
menu choices privately but should not be added to this public repository.
An old `not_queried` status is ambiguous because of the runtime behavior above.
Do not reinterpret it as proof that GET_FAN was skipped.

Create a minimal JSON export by explicit selection of the already available
GET_FAN fields. This entirely synthetic example contains no identities:

```json
[
  {"mode": 3, "queried": true, "objects": {"0x20": "03", "0x21": "ff"}},
  {"mode": 4, "queried": true, "objects": {"0x20": "00", "0x21": "05"}},
  {"mode": 3, "queried": false, "objects": null}
]
```

Save it to an explicit path, then run from the repository with Python 3.12+:

```sh
python -m madoka_multiroom.fan_observation /explicit/path/synthetic-fan.json
```

Each result corresponds to its input array position. There are no controller
identifiers, inventory fields, raw payloads or automatic filesystem discovery.
Recognized protocol codes and structural reasons are retained; unknown raw
codes/extensions and invalid-input excerpts are not printed. Malformed/minimal
schema violations return exit status 2 with a generic safe error. This is a
read-only analysis utility; it never writes the input file or opens storage.
Do not publish actual exports or results tied to room names/device identities.

## Decision and minimum future acceptance

**Offline PASS / runtime capability activation HOLD.** An inactive-field gate
can suppress a legible active observation. The exact response variant causing
an installation's unreadable state is not established by source analysis.
Changing the live gate would also require safe SET argument preservation,
recognized ACK handling, strict readback and capability/permission policy.
Those conditions are not supplied by protocol enum membership or owner config.

1. Reuse historical exports first, grouping privately by response structure and
   documented indoor-unit/controller capability. Record only aggregate
   structural conclusions in public. Do not run the same experiment per room
   if existing evidence already answers it.
2. For unresolved structures only, an independently authorized future GET-only
   observation should record known object presence/width, active mode, existing
   fan menu choices and prohibition/automatic-control state. Check cooling and
   heating banks when an already planned/authorized mode observation supplies
   them; this document authorizes no mode transition.
3. Establish two/three/five-speed and optional Auto support from the matching
   unit documentation and local controller menu. Distinguish distinct physical
   choices from virtual 2–4 aliases. Central locks and protective overrides
   must not be interpreted as parser failures or overwritten.
4. Only if a later scoped implementation and physical acceptance are authorized:
   use one meaningful fan change per unresolved capability/response class,
   preserve the inactive byte, admit only a grounded ACK schema, and confirm
   active effect plus unchanged inactive/preserved state via GET. Account for
   documented settling delay in the acceptance plan. An unknown ACK, failed
   GET or divergence remains STOP/uncertain with no automatic SET retry.
5. Reuse that observation across matching units only where matching capability
   and response evidence justify it. Confirm all six controller assignments
   independently without publishing identities. Existing uncertain operations
   still require their authorized GET-only review; this utility cannot clear a
   ledger or unblock writes.

The offline fixtures cover partial pairs, Auto, all manual protocol codes,
unknown/missing/malformed fields, harmless extensions, mode-policy separation,
six independent reports and an explicit-file CLI that imports neither BLE nor
SQLite. They prove classification and isolation, not real fan support, physical
effects, an ACK profile or production acceptance. Temperature behavior and
mode enablement remain separate work.

## Offline validation

Both complete Python suites pass with the offline BLE import guard:

```sh
MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python -m pytest -q
```

- Python 3.12: **801 passed**, including 46 new fan analysis cases.
- Python 3.14: **801 passed**, including the same 46 new cases.
- Both retain the existing Starlette/httpx TestClient deprecation warning.
- All five Chromium desktop/mobile harnesses pass: demo, physical contract,
  native read-only, office and local; zero JavaScript errors. Only closed,
  synthetic fixtures were used with physical driver imports forbidden.
- GitHub CI: pending publication of the draft PR.

These are offline software results only. No physical fan, capability or ACK
acceptance is claimed.
