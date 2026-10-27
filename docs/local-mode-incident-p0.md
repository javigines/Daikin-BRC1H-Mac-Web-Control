# P0 Local Cool-to-Heat incident: read-only triage and wall-verified reconciliation

Tracking: [#25](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/issues/25); corrective [PR #27](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/27).

## Physical evidence and limitation

A Cool (3) to Heat (4) command got a recognized `mode_status_v1` ACK. A single immediate GET_MODE and subsequent full readback triggered `mode_state_mismatch` for the entire raw mode block; the historical operation is uncertain and the web disables this room's controls. The diagnostic did not retain the exact post-SET raw mode block, therefore it cannot retrospectively discriminate a still-old mode code from an opaque TLV change.

**Later Owner UI evidence, 27 Oct 2026 01:29:28:** this room is connected; software reports **Heat (4), ON, ambient 21°C, stored Cool 23°C, Heat 21°C, active target 21°C**. This demonstrates only a later application observation. It does **not** establish that the original SET had completed at the immediate GET, that the physical wall shows Heat, or that firmware metadata was unchanged. The real wall observation is still missing.

## Existing production version (not recovered)

The Mac still runs `master=312c46341e4704ff35cbdeba9e1298cf2e9ec396` as last verified. The corrective PR is a **DRAFT** until CI, review and explicit deployment GO. Do not press other modes/power/temperature on this room in the existing UI, restart to clear uncertainty, reset DB, delete ledger/quarantine, change commissioning, or assume an ordinary **Consultar estado** re-arms any mode write. A wall control, if needed for comfort, is an **independent physical action** to be recorded privately, not evidence that the original BLE write succeeded.

## Correction in PR #27 (offline only)

1. **Better diagnosis:** classify invalid mode GET, wrong primary code, or a correct primary mode with changed mode metadata; record field IDs and human-readable mode ID but never raw private bytes.
2. **Bounded settling:** when immediate readback shows *precisely the original mode block* with all other state preserved, perform at most two subsequent GET-only observations with bounded delay. Accept only the complete strict raw-preservation contract. A third mode, altered TLV or extra effect still yields a durable STOP; no SET retry.
3. **Supervised operator reconciliation:** a separate Local-only CSRF-protected route shows a form requiring the actual **wall** mode, active target if Cool/Heat, power status and an explicit inspection note. The backend requires exactly one pending/uncertain MODE operation and the original operation ID, the same durable Bluetooth identity and **two stable complete GETs** across mode/power/setpoints/fan. Only if these match the wall attestation does the ledger set the review outcome to `reviewed_uncertain` and append `operator_mode_reconciled`; every original `failed` event, before-state and uncertainty remains. This does **not** label the historical SET successful.
4. No automatic on/off, restore, retry, cross-room correction, room/grant bypass or ENFORCE. A later new SET is always a **separate** operation and must be authorized on its own merits.

## Post-merge procedure on the physical Mac (NOT YET AUTHORIZED)

**Preflight:** CI for both supported Python versions and all Chromium harnesses green at final HEAD; review the audit/no-bypass contract; make a consistent backup under private permissions while the app is stopped (see #24), preserving office.json, local-controls.json, native-sessions.sqlite, office-observations.db and sidecars. Confirm exact deployed commit and do a GET-only health check of all six rooms before review. Never import demo/synthetic fixtures into real data.

1. With no new web SET, observe the wall display of the affected room **physically** and record its current mode, active temperature setting (if Cool/Heat) and power.
2. Click **Consultar estado** and confirm connected, current fresh GET, same room, and that last operation remains `uncertain` (or `pending` after crash). If UI and wall disagree: STOP and retain block.
3. Click **Revisar bloqueo y recuperar controles** for **this room only**. Do not copy fields from the web without checking the wall. Enter the wall's mode, current active target, power and a short physical inspection note. Explicitly attest that the wall was checked.
4. The app issues **two complete GETs only**, checks raw-block stability and compares the wall observation. If any mismatch, timeout, identity mismatch, second unresolved operation or connection error: STOP. No SET or rollback is sent.
5. If successful, verify `last_operation.status == reviewed_uncertain`, original operation ID unchanged, audit event `operator_mode_reconciled` present, existing historical error retained, room controls re-enabled and the other five rooms unaffected. This is permission to consider a **future independent write**, not a retrospective success claim nor an instruction to immediately click a control.
6. A separately authorized single physical mode pilot can follow after owner review, with wall comparison and STOP on any new divergence.

The original mismatch's exact raw cause remains **UNKNOWN** until new evidence identifies it. No Mac/BLE/real SQLite changes were made by this PR.
