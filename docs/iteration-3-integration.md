# Iteration 3 integration and final offline acceptance

## Candidate identity and scope

The candidate branch is `auto/iteration-3-integration`, based on verified PR #2 `92f715a469806ea61da6c87e260d7e6ed4488f25`. It incorporates these exact sibling heads through local merge commits, preserving their history and attribution:

| PR | Verified head | Content |
| --- | --- | --- |
| #3 | `71047cd52c2e047431ddca8f123191220bf52552` | Simplified Spanish interface and current Chromium harness |
| #4 | `06f9dbd27930a2b68b4b17b324fb50b46f614823` | Offline adapter, strict mapping, extracted Simulator |
| #5 | `d463240a042da106b01dd6f726c5d666188aeef3` | English documentation and upstream readiness |

Additional integration commits correct the two lifecycle findings below and align documentation with the combined interface. The exact candidate SHA is the DRAFT integration PR head, also supplied in the delivery report; verify it before acceptance. No merge into `feature/multiroom-offline`, master or any existing PR is performed. A local merge on this isolated branch does not accept or close a GitHub PR.

All runtime paths remain simulator-only. No BLE initialization, scanning, connection, pairing, writes, native restrictions, installer settings, SSH, Mac B changes, services, production changes or deployment took place. No upstream PR/contact or license addition. Product UI remains Spanish; technical documentation and new code comments are English.

## Investigation 1: connection setup versus operation deadlines

**Confirmed:** the sibling adapter wrapped all requests in a five-second operation deadline. Hardened `Controller.request` calls `_ensure_connected()` before its exchange timer. A direct bridge could therefore cancel legitimate initial discovery/connect/subscribe, with historical phase ceilings of 12 + 20 + 20 seconds. RoomManager has its own ordinary five-second deadline, so simply moving setup outside the adapter timer would not be sufficient.

**Correction:** DaikinBackend requires an explicit `prepare()` before snapshot/apply. Preparation has its own 60-second planning allowance, includes capacity wait and must complete before entering either ordinary adapter or manager operations. The normal operation default stays five seconds; cleanup has a separate 25-second allowance. The prepared-session port exposes `prepare`, `ready`, `exchange_ready` and `disconnect`. `exchange_ready` must reject a lost/unprepared session without lazy setup or reconnection. Raw legacy `Controller.request` does not satisfy that contract, and there is no physical bridge. Repeated preparation of a ready session is idempotent; failed/cancelled preparation latches STOP without retry and retains ownership until cleanup.

The setup default exceeds the historical aggregate setup ceiling with margin; it is not measured CoreBluetooth readiness or a hard wall-clock guarantee. Cooperative cancellation, failure cleanup and native Connecting state require physical evidence. A future coordinator must prepare explicitly before short operations, persist uncertain ownership and never work around this boundary by changing a flag. The constructor still accepts only the exact data-only ScriptedController.

Offline regressions cover setup that lasts longer than an ordinary operation, setup error/timeout/cancellation/capacity wait, no implicit preparation, ready-state loss without reconnect, and close during setup. No hardware timing claim follows from scaled simulated delays.

## Investigation 2: failed disconnect and session ownership

**Confirmed and independently reproduced:** sibling close released SessionBudget capacity in `finally`, even when disconnect failed. A later close saw no owner and marked cleanup successful without another disconnect. This lost both capacity protection and truthful cleanup status.

**Correction:** explicit states are NEW, PREPARING, READY, STOPPED, CLOSING, CLOSED, CLEANUP_UNCERTAIN and RELEASED_OFFLINE. SessionBudget keeps strong references to the port and owning adapter, verifies ownership on release, counts quarantined sessions against capacity and prevents identity reuse. Close disables operations and clears observations before waiting for its lock. Only successful data-only cleanup allows ordinary release. Any exception/timeout/cancellation, including cancellation before acquiring the lock, quarantines the reservation. Repeated or concurrent close cannot retry automatically, free capacity or become clean success.

An explicit `resolve_offline_cleanup(reason, evidence)` models a supervised decision for the exact simulator double only. It records immutable reason/evidence, rejects absent/replayed resolution, frees only simulated capacity, and leaves a terminal RELEASED_OFFLINE outcome with clean-close false. Subsequent close still reports unproven cleanup. It has no API/CLI/configuration route and implements no physical recovery. Mocked disconnect success proves only that mock code completed.

Reservations, quarantine and resolution records are in memory in this disconnected adapter prototype. They do not survive process restart and are not a production lease system. Future physical integration requires durable identity-bound quarantine restored before admission, native state verification, accountable supervised recovery and explicit release evidence. Restarting a process or constructing a new budget is never proof of physical release. There is no automatic reconciliation/retry policy for uncertain physical cleanup.

Regression coverage includes failed/slow/cancelled disconnect, close cancellation while waiting for an operation, repeated/concurrent close, denied duplicates, owner-token mismatch, strong-reference retention, blocked capacity and explicit offline resolution without a clean-close claim.

## Integrated evidence

The counts below come from the combined tree, never a sum of sibling counts. Original foundation and policy/migration tests remain present; adapter tests now call explicit preparation.

| Check | Result |
| --- | --- |
| Complete Python 3.12.15 suite | 292 PASS / 0 FAIL / 1 existing warning; 4.65s |
| Complete Python 3.14.8 suite | 292 PASS / 0 FAIL / 1 existing warning; 5.95s |
| Current simplified-interface Chromium, 1440px / 390px | PASS; zero JavaScript errors; screenshots visually reviewed |
| Both pure CLI selftests on Python 3.12 | ALL PASSED |
| Independent combined code/lifecycle/documentation review | PASS; independent Python 3.12 and 3.14 runs each 292 PASS / 0 FAIL / 1 existing warning; no material findings |
| English documentation, links/anchors and fences | PASS; 15 Markdown documents, 47 local links/anchors, balanced fences; Spanish retained only as product labels |
| Whitespace and source-history validation | PASS; all four exact verified commits remain ancestors |

Independent review also checked all 15 English Markdown documents and 47 local links; Chromium is the implementation run, not an additional independent browser run. The one existing test warning concerns Starlette TestClient's httpx deprecation. It is not suppressed. The full suite covers isolated six-room controls/faults/cancellation, independent protected fields, desired versus observed persistence, FREE/MONITOR/ENFORCE, STOP and finite correction budgets, maintenance/suspension/recovery, histories and lossless SQLite migration/source preservation. New lifecycle tests include a manager integration test proving that automatic correction rejects even a prepared offline Daikin adapter; another simulator continues working after rejection.

Chromium uses this tree's `integration/browser_demo.py`, which opens the simplified UI's room detail, separate configuration and laboratory. It covers all four ordinary controls, three policies, protected fields/advanced settings, confirmed administration, histories/events, busy/failure isolation, recovery, restart/token refresh, stale state, XSS, early navigation and keyboard interactions. It runs a real local DEMO CLI server with six simulators and temporary SQLite, not mocked application endpoints. Reviewed screenshots are under `docs/images/`; they contain simulation data only.

## Hardware exclusion evidence and remaining gates

- `multiroom_app.py --real` rejects before DB/backend construction. Missing mode, non-loopback host and legacy physical CLIs reject; subprocess tests forbid real Bleak import.
- RoomManager constructs only Simulator. No CLI, API or configuration can instantiate DaikinBackend; its constructor rejects anything except exact ScriptedController before inspecting it. Its physical factory always raises.
- A guarded subprocess imports and exercises prepare/snapshot/close plus the complete DEMO while prohibiting Bleak and legacy controller modules. Existing lifecycle tests use explicit doubles, not real drivers.
- Automatic correction requires `type(backend) is Simulator`. Even internal substitution of a prepared adapter is rejected and policy STOP is latched. Physical ENFORCE remains impossible through supported application paths; auto-on remains disabled by default.
- No device inventory was read or added. No native lock command is implemented. Existing local token is CSRF protection, not production owner authentication.

**Verdict: PASS offline / HOLD physical.** The evidence table records the actual integrated tree. Independent review results are recorded in the DRAFT PR; no mocked result certifies physical Bluetooth, which remains NOT_TESTED. See the [Mac B offline acceptance](mac-b-offline-iteration-3.md) and the separate [blocked read-only canary proposal](first-madoka-read-only-canary.md). No offline PASS authorizes physical execution.
