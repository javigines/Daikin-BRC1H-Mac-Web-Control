# P0 Local mode incident: read-only triage and recovery requirements

Incident tracking: https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/issues/25

## Known result

An attempted Cool (3) to Heat (4) produced a recognized `mode_status_v1` ACK and a complete post-SET readback. The validation failed with `mode_state_mismatch` for the raw GET_MODE group. No captured diagnostic distinguishes the main mode byte from other GET_MODE TLVs; the physical outcome remains UNKNOWN. No raw post-SET GET_MODE was recorded, so the new diagnostic classifier cannot retrospectively resolve this historic incident.

The Local validator intentionally compares the entire raw mode group and requires all other raw groups preserved. It currently accepts no uncommissioned mode-metadata changes. This PR preserves that acceptance boundary, while classifying *future* mismatches as `mode_code_different`, `mode_metadata_changed`, or `mode_readback_invalid`, together with changed TLV **IDs**, not values.

## Safe operator next step

1. Do not send another SET to the affected room from the web, change its control permissions, reset the database, or restart merely to clear the STOP.
2. From the running Mac Local dashboard, use **Consultar estado** once; this issues a read-only GET, NOT a rearm. Note the resulting connection status, current mode, current cooling/heating stored targets, last-operation status, and freshness.
3. Observe the wall controller's **current displayed mode and targets** without pressing buttons. Record only human-readable states and times in a private incident note.
4. If the wall must be used for immediate comfort, its use is an **independent physical action**, not validation of the historical SET. Record the change and time privately.
5. Preserve native-sessions.sqlite, other databases, private config, and the existing operation/event ledger. Do not publish physical UUIDs, raw TLVs or private snapshots.

## Recovery implementation still required (NOT delivered by this diagnostic PR)

A separately reviewed and tested audited reconciliation procedure must bind the original operation identity, room identity and immutable historical outcome; use fresh stable GETs and wall observation, reject stale or contradictory evidence, add a new review event and a separate durable resolution marker without changing an old uncertain result into `confirmed`; and only then permit a *new*, independently authorized operation for that room. Restart, polls, simple GET, settings or other-room actions must never rearm. Also handle persistent policy STOP independently from operation STOP.

Any timing-based extra GET after a mode SET must remain strictly bounded, GET-only and justified by physical timing evidence. No protocol fields should be permitted to change merely because the readback is late. Unit and browser tests must cover no-effect, lag, extensions, uncertain restart, review, and six-room isolation.

No Mac, BLE, real ledger, firmware, installer settings, merge or deploy changes are made by this branch. Current physical room remains blocked pending evidence and audited recovery.
