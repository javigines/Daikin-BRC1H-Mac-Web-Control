# Iteration 5: consolidated native read-only candidate

This is a source and offline acceptance report. No Bluetooth operation, Mac B/SSH access, canary-record change, permissions/pairing/service mutation, deployment, upstream contact or GitHub merge was performed. The ordinary application remains DEMO-only and rejects `--real`. The dedicated native web entrypoint now implements actual transport, but its physical execution is **HOLD pending fresh authorization**.

## Consolidation and provenance

The isolated `auto/iteration-5-native-consolidation` branch starts at exact PR #8 `4893be8a5bac6c7d9b5f82a435fe335fcb02f38b` and merges exact PR #7 `ed6135013ce5aec4a39f35f002e788c166a94294` with history preserved. README was the only merge conflict; both feature sets and documents were retained. The combined baseline measured **411 tests passed** on Python 3.12 before native implementation. Earlier sibling counts were not added together.

Required ancestors, checked in the consolidated history:

| PR | Exact source commit |
|---|---|
| 1 | `bcb1bf8493881d1ee50b7558d17299346b0fb4ae` |
| 2 | `92f715a469806ea61da6c87e260d7e6ed4488f25` |
| 3 | `71047cd52c2e047431ddca8f123191220bf52552` |
| 4 | `06f9dbd27930a2b68b4b17b324fb50b46f614823` |
| 5 | `d463240a042da106b01dd6f726c5d666188aeef3` |
| 6 | `f5267721a6936e53a0d1461e58ba0c5c9b547ab5` |
| 7 | `ed6135013ce5aec4a39f35f002e788c166a94294` |
| 8 | `4893be8a5bac6c7d9b5f82a435fe335fcb02f38b` |

Existing PRs remain draft and unmerged. Original attribution is retained. [Upstream contribution readiness](upstream-contribution-readiness.md) still applies: no explicit upstream license has been established, no license is added, and formal redistribution/contribution requires rights clarification. Squash merging PR #9 would produce one master commit while the integration branch and source PR histories retain attribution/evidence. Do not delete those branches prematurely.

## Native transport and web behavior

`native_access.py` mints an explicit capability only after attended macOS, non-root terminal, reviewed clean SHA, private inventory, pinned Bleak version, fresh authorization reference and hidden UUID confirmation. The dedicated `madoka_readonly_web.py` defaults to OFFLINE. Ordinary DEMO imports never traverse the native factory or import Bleak. CI sets an offline gate and installs an import guard that rejects the real driver; closed tests inject a fake SDK into `sys.modules`.

`NativeRadio` uses the exact selected CoreBluetooth UUID and `BleakClient(device, timeout=120)` once. Discovery, connection and notification setup are explicit. The hardened bridge retains the actual `Controller.request` serialization/framing path, overrides lazy connection with ready-only checks, and rejects malformed/duplicate/unexpected replies and asynchronous link loss. No casts, constructor monkeypatches or subclass tricks enable physical access. The native backend is an explicit read-only implementation with validated bridge/ledger admission.

The bridge and radio independently allow only exact argument-free GET power, mode, targets and indoor sensor commands. The radio checks characteristic, exact frame bytes and `response=False`. Physical SET, fan queries, ENFORCE, auto-on, schedules, desired-state mutations and policy/restriction resets are rejected. Read-only observations reuse the canary's strict semantic decoder, without assuming unverified fan or range TLVs. Missing/unsupported required fields stop the native read; no default observation is fabricated.

The existing Spanish dashboard displays one selected native room, connection status, observations and history. Five other configured rooms remain unavailable. Every page/asset/API requires a random 256-bit ephemeral operator password via HTTP Basic, in addition to existing CSRF/origin/Host checks for admission. Binding is fixed to `127.0.0.1`, one worker, standard asyncio and no proxy-header trust. There is no LAN mode. Startup does not connect. A confirmed browser action consumes one admission and enables bounded polling; the UI disables all HVAC controls and configuration/laboratory navigation. Errors never replay a mutation or reconnect.

## Lifecycle investigations and retained ownership

**Deadlines:** ordinary five-second requests cannot cancel initial setup. Discovery has its own 15 seconds; the shared 120-second connection/subscription window begins after discovery, since macOS pairing can happen during subscription. Bridge overall setup is 150 seconds and prepared backend setup is 170 seconds, separate from ordinary operation deadlines. The native browser admission timeout is 180 seconds; normal API requests retain ten seconds. Native reads have four six-second exchanges and a 30-second aggregate budget. Offline regression tests delay each preparation stage beyond a deliberately short operation timer.

**Uncertain disconnect:** the original in-memory `SessionBudget` model, offline durable `physical-contract-v1` ledger, permanent PR #7 canary attempt and new `native-readonly-v1` ledger remain separate. The native ledger commits an identity-bound reservation before driver import, uses private files, FULL synchronous SQLite, directory/parent fsync and a process lock, and fixes capacity/concurrency to one. Restart quarantines unresolved rows. Public cleanup never releases capacity, repeated close never converts uncertainty into success, and offline evidence cannot resolve native rows. No native recovery verifier is implemented. Link/setup/read/cancellation failures STOP and retain ownership; best-effort cleanup still attempts disconnect after stop-notify errors/cancellation.

The native account path cannot be changed through CLI or HOME. Native/offline schema and bridge mismatches reject before hardware. Original canary files/evidence are untouched. The entrypoint suppresses Python/logging/C-FD backend output for its server lifetime; boundary exceptions and HTTP errors use fixed sanitized messages. Private SQLite retains identity digests and the owner authorization reference, not raw inventory UUIDs.

## Evidence boundaries

The owner supplied prior Mac B canary evidence: pairing succeeded; four GET groups were valid; discovery took **3.382s**; setup including pairing took **26.347s**; public cleanup returned; exit **0**; and the owner later confirmed responsive wall controls. This evidence was not rerun or independently physically verified in Iteration 5. **Native release remains unverified.** The earlier permanent attempt is consumed. A new web attempt requires fresh GO and a reviewed procedure, not deletion/reuse of that marker.

The six-device contract architecture remains tested with scripted radios at capacities up to six, independent histories/errors, serialized commands, cancellation and persistent quarantine. Native runtime capacity remains one regardless of configuration capacity. Mocked cleanup proves implementation behavior only. Physical native release, one-controller web operation, six-controller capacity, SET/readback compatibility, unattended operation and production ENFORCE remain unverified.

## Reproducible offline validation

```bash
MADOKA_OFFLINE_ONLY=1 PYTHONPATH="$PWD/ci" python -m pytest -q
python integration/browser_demo.py
python integration/browser_physical_contract.py
python integration/browser_native_readonly.py
python madoka_readonly_web.py --selftest
```

Run the suite separately on Python 3.12 and 3.14. Chromium harnesses use real loopback HTTP and temporary SQLite with six fictional identities. The third harness drives authentication, explicit native factory admission, disabled controls, one available room/five unavailable, observations/history and no reconnect on reload against a closed fake SDK. It blocks real Bleak module resolution in both processes. GitHub Actions exercises the full Python matrix on PRs with the same real-driver import prohibition. Final measured results and independent review are recorded below before publication.

| Check | Measured result |
|---|---|
| Combined baseline before native changes | Python 3.12: 411 passed |
| Final integrated Python 3.12.15, CI import guard enabled | **464 passed**, 8.28s |
| Final integrated Python 3.14.8, CI import guard enabled | **464 passed**, 11.98s |
| Independent full guarded suites | 3.12: **464 passed**, 8.21s; 3.14: **464 passed**, 11.51s |
| Existing simplified DEMO Chromium | PASS desktop 1440px/mobile 390px; all six rooms, controls, policies, administration, histories, independent faults, recovery, restart, stale/XSS/keyboard; zero JavaScript errors |
| Existing six-room wire-contract Chromium | PASS desktop/mobile; four manual simulated commands, strict readback, isolated histories; zero JavaScript errors; real-driver imports forbidden |
| New native-factory mock Chromium | PASS desktop/mobile; operator authentication, selected room name, explicit single admission, one available room/five unavailable, disabled controls/admin, history, reload without readmission, graceful SIGINT with retained quarantine; zero JavaScript errors; real driver forbidden |
| Documentation | 21 Markdown documents, 70 relative links/anchors; no missing targets/anchors or unbalanced fences; English technical prose, Spanish product text |
| Source ancestry | All eight exact source commits are ancestors; PR #7 canary and its tests remain byte-for-byte unchanged |
| Independent code/safety review | PASS for offline source acceptance; no outstanding material findings; physical execution HOLD |
| Hosted CI | PR #9's exact-head Python matrix is authoritative; inspect its checks separately from the local results above |

Each Python suite reports one existing Starlette TestClient/httpx deprecation warning. The 464 count is the actual collected integrated suite, not a sum of sibling PR counts. Closed native tests cover attended preflight gates, the actual CLI/factory/API, fixed account state, sanitized output including file descriptors, schema/capacity separation, four GETs, semantic rejection, cancellation during setup/read/shutdown, asynchronous disconnect, uncertain cleanup and restart. Independent review repeated the full guarded suites and checked the native factory, ownership, authentication and operator procedure. It identified parent-directory durability and runner/timeout/operator-clarity issues during implementation; those corrections and regressions are included.

The initial native browser harness treated Uvicorn's signal re-raise as a failed process check. The harness now sends SIGINT, waits for the application's drained lifespan and verifies a single disconnect plus durable quarantine. The native entrypoint likewise handles Uvicorn's post-drain KeyboardInterrupt without claiming native release; both normal and re-raised signal returns are tested. Test scaffolding outcomes remain offline evidence only.

## Release and rollback

PR #9 targets `master` as a DRAFT and carries the complete consolidated tree. Recommend eventual owner-controlled squash merge only after full offline tests/CI, independent review and owner source acceptance; this is not physical production acceptance. Do not merge older PRs separately. No deployment, service enablement, native restriction or installer change is bundled. The existing LaunchAgent remains disabled.

Source rollback after an eventual merge is a reviewed revert of its squash commit. No rollback should delete/migrate the private native ledger, old canary marker or physical evidence. Before any future physical activation follow the [blocked operator procedure](native-readonly-web.md); on uncertainty stop, preserve state and obtain supervised review. Native recovery and a second native admission are deliberately unavailable in this candidate.
