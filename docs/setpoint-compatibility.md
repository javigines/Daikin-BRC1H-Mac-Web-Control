# Setpoint protocol compatibility

Base: `master`, `1e0da649ae21da28280bfd16d0a349117c950bf5` (PR #12).
All examples and fixtures in this change are invented. No installation data,
device identifiers, captures, or private configuration were collected.

## Root cause

The native backend could display valid temperature observations while disabling
the setpoint controls. `control_view()` calls `setpoint_block()` as its capability
check. That function required the GET object IDs to equal the SET argument IDs
exactly, and incorrectly required the four limit symbol bytes to be boolean.
An otherwise valid GET with extra TLVs or a symbol greater than one therefore
disabled both buttons before any write.

Two subsequent boundaries also needed separate treatment:

- Readback compared the entire GET dictionary with the SET arguments. Read-only
  extensions would make a correctly applied write uncertain.
- `validate_set_ack()` recognized only empty responses for SET_SETPOINT. A
  complete status-shaped response would stop at `ack_validate`, even if the
  target had changed. Its contents must never be treated as effect confirmation.

## Protocol evidence and limits of the evidence

Pinned sources consulted:

- [Lafois protocol reference, a6394413](https://github.com/blafois/Daikin-Madoka-BRC1H-BLE-Reverse/blob/a6394413d82bae249c7f8a04133c49498f327560/README.md):
  the introduction to identified functions explicitly allows additional returned
  objects. GET_SETPOINT (`0x0040`) lists temperatures, configuration, limits and
  one-byte symbols. SET_SETPOINT (`0x4040`) lists the two temperature arguments;
  it does not establish that arbitrary extra GET objects are writable.
- [pymadoka SetPointStatus, eac7c3ad](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/features/setpoint.py):
  field constants define all 17 IDs and widths. `get_values()` serializes all
  17 as SET arguments; `set_values()` consumes the same complete status schema.
  This supports the existing full SET block, not sending unknown TLVs. Its
  zero defaults and lossy conversions are deliberately not copied.
- [pymadoka Feature.update, eac7c3ad](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/feature.py):
  SET responses are parsed using `new_status().parse()`. The method explicitly
  warns that parseable response data may not reflect the actual device state.
  This supports recognizing a complete status-shaped SET_SETPOINT reply as a
  candidate ACK. It does not support success inference from ACK contents, a
  partial two-target echo, a one-byte result code, or arbitrary ACK extensions.

These sources establish schema compatibility, not physical firmware behavior.
No Bluetooth exchange or physical effect was tested in this iteration.

## Behavior

`setpoint_fields()` validates required IDs and exact byte widths, checks the
existing supported range/mode flags, and separates additional GET TLVs. Symbol,
differential and other configuration bytes are retained as opaque bytes; their
numeric values are not normalized or replaced with defaults.

`setpoint_block()` emits only the 17 documented SET arguments. It replaces
exactly the active target (`0x20` for Cool, `0x21` for Heat), copying every other
known byte from the fresh baseline. Unknown GET extensions remain read-only.
Only Cool/Heat and finite whole-degree requests are allowed. Active device
limits must be present, ordered, within the existing conservative 10–40 degree
envelope, and intersect the configured owner window. No missing/zero/inverted
range is replaced with an assumed capability.

ACK recognition is independent of GET validation. The existing empty forms
remain accepted. A data-bearing SET_SETPOINT ACK must contain exactly the full
known status schema, valid widths and supported range/mode flags. Its target
and limit values are not used as observations or as evidence of success; for
example, a full ACK with zero limits can only permit GET verification. Partial,
extra, malformed or otherwise unrecognized ACKs leave the operation uncertain.
No error-code meaning is invented.

`validate_setpoint_readback()` requires the exact requested target bytes and
every untouched known byte, including the inactive target, flags, symbols and
limits. All extensions present in the baseline must also remain byte-identical
and present. New well-formed GET extensions may appear. The existing checks on
mode, power and other observed fields remain in force. Even a raw one-bit
target mismatch fails if the displayed rounded temperature happens to match.

Unknown schemas, GET errors and divergent readbacks retain STOP, diagnostics
and durable uncertainty. There is no retry or repeated SET. Polling and restart
do not clear an unresolved operation. Only the existing explicitly requested
GET-only review can mark it `reviewed_uncertain`; it never replays the intent.
Power controls, Bluetooth recovery, ledger and idempotency implementations are
unchanged.

## Offline validation and minimal acceptance

Local results for this change: 682 tests passed on Python 3.12.15 and 682 on
Python 3.14.9. All five existing Chromium harnesses passed (demo, physical wire
contract, native read-only, office and local), including desktop/mobile checks
with zero JavaScript errors. The only pytest warning was the existing
Starlette/httpx deprecation. These results use synthetic SDKs and temporary
stores only; they are not physical or deployment evidence.

Run from an isolated checkout with test environments for both Python versions:

```sh
python3.12 -m venv /tmp/madoka-test312
/tmp/madoka-test312/bin/python -m pip install -r requirements-dev.txt
env MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci /tmp/madoka-test312/bin/python -m pytest -q
python3.14 -m venv /tmp/madoka-test314
/tmp/madoka-test314/bin/python -m pip install -r requirements-browser.txt
env MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci /tmp/madoka-test314/bin/python -m pytest -q
/tmp/madoka-test314/bin/python -m playwright install chromium
env MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci /tmp/madoka-test314/bin/python integration/browser_local.py
```

The Chromium harness refuses an occupied loopback port or a server without its
temporary fixture marker. It injects closed SDKs and blocks physical drivers.
It checks desktop/mobile buttons, one SET for one action, byte isolation across
six rooms, status-shaped ACK followed by GET, uncertainty and explicit review.
Any screenshots generated by existing harnesses stay local to their test run;
the CI workflow no longer uploads screenshot artifacts. Do not commit images,
fixture databases or generated diagnostics.

Minimum acceptance:

1. The complete pytest suite passes on Python 3.12 and 3.14, and all existing
   offline Chromium harnesses pass in CI. The PR records the exact head/run.
2. All six synthetic rooms with extra GET objects and nonboolean symbols expose
   enabled `+` and `−` controls. Each action sends one SET, changes only its
   active target and preserves every other room and byte.
3. Unsupported baseline/limits send zero SETs. A recognized ACK without effect,
   unrecognized ACK, GET failure or divergent readback leaves `uncertain` and
   blocks all further writes for that room through polling and restart, until
   explicit GET-only review. Review itself sends zero SETs.

This procedure is entirely synthetic. Physical acceptance, Mac mini access,
real SQLite, deployment and merge remain outside this iteration's authority.
