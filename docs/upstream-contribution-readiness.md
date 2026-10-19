# Upstream contribution readiness

Integration note: the sibling PR history and original validation below are preserved. The current combined tree and acceptance evidence are documented in [Iteration 3 integration](iteration-3-integration.md). No target branch or existing PR was merged by this integration work.

## Status and authority

This is a proposal for selective future contributions, **not a decision to contribute**. No upstream PR has been opened and its author has not been contacted. Opening a PR or contacting the author requires the owner's explicit authorization. Development remains on [this fork](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control); upstream attribution remains [txsl/cool-vibes](https://github.com/txsl/cool-vibes).

Comparison baseline: the original fork snapshot `3b3985ee5a6b93ed994a56331d13435986ca2f8a`, not a claim about upstream's current implementation. Verified fork milestones: PR #1 `bcb1bf8493881d1ee50b7558d17299346b0fb4ae` (BLE reliability) and PR #2 `92f715a469806ea61da6c87e260d7e6ed4488f25` (offline multiroom). Read-only GitHub metadata on 2026-10-19 reports upstream default branch `main` and `licenseInfo:null`; no author communication occurred. Recheck upstream source and changes immediately before any future proposal.

## Improvements relative to the original snapshot

- Pure protocol handling now reassembles 19-byte data fragments with strict indices, lengths and final-fragment validation; malformed/truncated TLVs fail instead of appearing partially successful. Zero-size objects remain representable as unsupported.
- Controller lifecycle has one setup attempt, request serialization, complete write/reply deadlines, failure latches, pending-request invalidation, stale-callback guards and cancellation-resistant best-effort cleanup. Shutdown attempts all resources despite failures. No automatic reconnection was added.
- Offline multiroom separates stable room identity, state, faults, locks, statistics and histories; strict configuration and API validation; bounded/staggered coordination; loopback/CSRF protections; simulator-only execution and migration to a new SQLite copy.
- Persistent desired state is separate from observation. FREE/MONITOR/ENFORCE policies support independent protected fields, confirmations, stabilization, persistent budgets/cooldown/STOP, guarded auto-on, maintenance/suspension and audit events.
- Iteration 3 proposals separate routine controls from administration/diagnostics, and prepare a strict offline adapter boundary. These are independent sibling branches based on PR #2, not behavior included by this documentation-only branch. Their exact implementation/evidence belongs in their own PR reports.

## Candidate contribution boundaries

| Candidate | Reusable content | Dependencies and extraction strategy |
| --- | --- | --- |
| A. Reassembly/TLV correctness | Pure `madoka_protocol.py` corrections and focused protocol regression tests | Independent of FastAPI, SQLite, UI, inventory and multiroom. Compare base→PR 1 for this file/tests; select only framing/parser hunks, excluding unrelated feature encoding. |
| B. Lifecycle/cleanup correctness | `ble_lifecycle.py`, Controller/spike failure/cancellation/callback/shutdown fixes and mock regressions | Depends on A where tests rely on strict replies. Extract lifecycle hunks rather than cherry-picking the entire PR 1 commit. Keep existing upstream startup/API conventions subject to review. |
| C. HTTP/history hardening | Safe text rendering, input/history bounds, append-only sample identity and explicit migration concepts | Can be proposed independently where compatible. Single-room upstream need not adopt room configuration or this dashboard. Do not ship migration without source-preservation tests and rollback instructions. |
| D. Backend contract/semantic mapping | Hardware-free interface, required/unsupported validation, exact readback comparison, admission/ownership/cancellation guards | Proposed only after reviewing the adapter PR and rechecking upstream architecture. Offline doubles do not validate wire semantics; preserve physical activation guards and hardware caveats. Does not require office labels or six-room UI. |
| E. Optional multiroom UI/policies | Configurable coordination, desired/observed separation, policy engine, simplified UI | Larger product choice; not a prerequisite for A–D. Must include auth/operational gates and migrations. Offer separately only if upstream wants this scope. |

Possible future PR order: A → B; C independently; D after its own adapter review and relevant A/B integration; E only after explicit upstream interest. Titles could be “Reject malformed Madoka fragments and TLVs”, “Latch ambiguous BLE exchanges and complete cleanup”, “Bound history reads without replacing samples”, and “Introduce a tested backend boundary with strict state decoding”. These are proposals only; none has been opened.

## Fork-specific content

Office room names, a default six-room DEMO, fictional UUID examples, Mac A/Mac B handoffs and owner-specific approval workflow belong to this fork. No real inventory, infrastructure addresses, credentials or locations are embedded in reusable modules. Stable identifiers are data; reusable protocol/adapter modules must not depend on room names or a particular installation. The configured six-device cap is a product/test limit, not a BLE capability claim.

Blocking legacy hardware CLIs, disabling LaunchAgent auto-start/restart and rejecting `--real` are deliberate safety choices for our offline development. They change original behavior and must not be silently included in a narrow upstream protocol fix. Similarly, a local CSRF token is not production authentication; do not present it as an upstream authorization solution. Spanish interface text is a product choice independent of English technical documentation.

## Compatibility and behavioral decisions

PR 1 retains integer target rounding, equal cooling/heating targets and the complete zeroed range/limit field block from the original behavior. These are not generic firmware guarantees. A protocol patch must not alter those semantics incidentally; a future change requires separate captures, rationale and tests. Serialization of individual Controller requests differs from a whole snapshot; any snapshot-level lock belongs in the adapter/coordinator and must have its own cancellation tests.

The pure parser has no Bluetooth dependency. Existing hardened Controller requires Bleak/CoreBluetooth for actual use, but no physical execution is authorized here. New multiroom runtime requires Python 3.12+ and Pydantic 2; do not raise upstream's Python/dependency minimums merely to export a pure parser fix. Keep optional features and their requirements separate. Tests cover installed versions, not all versions permitted by open dependency ranges.

SQLite schema 2 and per-room state are not backward compatible with the original single-room application. Never make upstream users open migrated DBs in old code. Preserve original files, require explicit identity assignment, avoid timestamp replacement and document rollback. Keep policy semantics, response errors and strict field validation explicit when extracting a patch; do not hide behavioral changes as a refactor.

## Evidence and remaining physical tests

Historical evidence: original 34 tests; PR 1 expanded to 104 PASS on Python 3.12/ 3.14 with both selftests; PR 2 expanded to 227 PASS, preserving 104, plus actual Chromium→HTTP→simulator→SQLite integration. See [iteration 1 audit](ble-reliability-audit.md), [iteration 2 validation](iteration-2-validation.md) and the individual iteration 3 PR reports for current totals. Do not add counts across branches or infer a combined integration test.

Offline evidence covers framing/TLV edge cases, serialization, cancellation, setup/cleanup faults, isolated rooms, migration, policy budgets/restarts and browser behavior. It does not prove pairing, native Connecting cancellation, peripheral responsiveness, actual firmware fields/ranges, ACK effects, native Function lock or reliable simultaneous physical connections.

Physical gates remain: separately authorized one-controller test with a physically present operator; private UUID/model/firmware identity; first read-only setup/query/cleanup; inspect cancellation and local responsiveness; STOP on any unexpected prompt/fault; no automatic retry, unpair or power cycle. A later manual-write test needs its own authorization, safe range and restoration plan. ENFORCE and especially auto-on require further auth/RBAC, thermal/operational safeguards and explicit approval. Native locks require an official separately authorized Madoka Assistant procedure, never unknown BLE commands. See [policy gates](control-policies.md) and [Mac B handoff](mac-b-validation.md).

## Licensing and attribution

No explicit upstream license was found in the audited snapshot; current metadata also reports none. Forking does not automatically grant every redistribution right. This fork adds or attributes no license. Preserve the original project, Benjamin Lafois reverse-engineering, pymadoka and Bleak credits and references. Clarify rights before redistribution or formal contribution, with separately authorized contact if needed. Do not promise license compatibility or contributor rights that have not been established.

## Preparation checklist before any authorized upstream proposal

Recheck upstream HEAD and equivalent fixes; confirm rights; agree scope; extract a small understandable patch with only required dependencies and no private inventory; reproduce focused tests in a clean environment; disclose behavioral changes, Python/dependency requirements, untested hardware assumptions and STOP policy; include restoration/migration details when relevant. Keep fork functionality robust without forcing artificial upstream compatibility. Obtain explicit permission before opening the PR or contacting the author.
