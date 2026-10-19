# Full audit — iteration 2

Date: 2026-10-19. Verified base: `bcb1bf8493881d1ee50b7558d17299346b0fb4ae`, HEAD of open/draft PR #1. Audit performed before new functionality, with an independent read-only review. Baseline: 104 PASS, 0 FAIL, 1 warning on Python 3.12.15. No BLE, SSH, macOS configuration or hardware. Base locations below refer to that immutable commit; iteration 2 corrections are recorded in the closing sections.

## Findings

P0: none identified. No newly demonstrated P1 in the BLE core invalidates PR #1. The previously documented native Connecting risk maintains the physical HOLD; no real multiroom path is built on that behavior.

| ID / priority / evidence | Base location | Impact and reproduction | Recommendation and treatment |
| --- | --- | --- | --- |
| A01 P1 confirmed, unauthenticated exposure | daikin_server.py:1014, 542-570; deploy/com.example.daikin-aircon.plist:37-41 | Default 0.0.0.0, POST without identity or authorization: another LAN client can modify a controller if the physical process is running. Does not require BLE failure. Static evidence; no real server activated. | Block physical entrypoints in this iteration; loopback demo, strict Host/Origin, no permissive CORS. Authentication/authorization required before production. |
| A02 P2 confirmed, XSS through errors | daikin_server.py:709 | A response error is concatenated into innerHTML; it can create active HTML if a controllable error reaches that path. Unsafe sink confirmed, no real attack demonstrated. | Render text; new frontend uses textContent without dynamic HTML or remote dependencies, with CSP. |
| A03 P2 confirmed, CSRF/rebinding and coercion | daikin_server.py:500-512, 542-570 | No Host/Origin validation or token; bool/int bodies are coerced and extras ignored. An external origin lacks CORS permission, but that is not protection against all requests or DNS rebinding. | Ephemeral local mutation token + JSON + exact Origin + Host allowlist; strict types, forbidden extras and bounds. Does not replace authentication. |
| A04 P2 confirmed, unbounded history | daikin_server.py:528-540, 429-433 | hours=nan/inf reaches int and fails; a huge range loads every row. No limit/pagination/retention. | Finite range, row limit and cursor; explicit retention without automatic deletion. |
| A05 P2 confirmed, timestamp collisions lose data | daikin_server.py:385-390, 401-411 | Two INSERT OR REPLACE statements with ts=100 leave only the latest row. Reproduced with offline SQLite. Six controllers share the same key. | Internal sample ID + controller_id + temporal index; INSERT without REPLACE. Atomic/idempotent migration, explicit legacy ownership, original tables preserved. |
| A06 P2 confirmed, no data identity/environment | daikin_server.py:391-418 | device_info is a singleton and samples lacks device_id; opening a physical DB from demo would mix data. | Mode metadata and stable bindings without UUID in HTTP; incompatible demo/real files, separate offline migration. |
| A07 P2 confirmed, UI freshness/concurrency | daikin_server.py:658-665, 693-694, 701-729 | Render time is shown as update time; offline retains old values and CSS dimming does not disable buttons. Global busy blocks every room; an in-flight GET can overwrite POST. | Read timestamp, stale by age/error, native disabled controls, per-card busy and consistent response ordering. |
| A08 P2 confirmed, no error/queue policy | daikin_server.py:170-255, 542-570 | Lock serializes but HTTP waiters are unbounded and BLE exceptions may produce 500 with internal context. | Bounded admission, 409 for busy room, 503 for faults, sanitized public errors and per-room statistics. |
| A09 P2 confirmed, unsafe scripts/supervisor operation | probe_range.sh:40-78; debug_setpoint.sh:71-72; deploy/com.example.daikin-aircon.plist:37-41 | Sweep continues after error; restoration is not guaranteed if interrupted. LaunchAgent restarts bypass the process latch. Scripts can operate hardware through the server. | Block scripts/physical CLIs for this iteration; template without RunAtLoad/KeepAlive. Do not install services. |
| A10 P2 confirmed, partial preflight/duplicates | daikin_server.py:990-1001; deploy/* | EADDRINUSE returns True and allows BLE startup before uvicorn bind failure; no device/process exclusion. | New app has no hardware, real mode denied; one process per DB via local lock, no multiple workers. Per-UUID exclusion remains a future physical requirement. |
| A11 P2 confirmed, unvalidated response semantics | madoka_protocol.py:143-158; daikin_server.py:258-299; brc1h_spike.py:77-99 | TLV size=0 is valid for unsupported data, but [0] access raises IndexError; missing power/mode fields use defaults that appear successful. Does not demonstrate physical danger. | Do not reuse legacy decoding as proof of real state; define required/unsupported fields after captures. Blocks real activation, not an isolated DEMO. |
| A12 P2 probable risk, CoreBluetooth | ble_lifecycle.py:15-24; daikin_server.py:184-204; Bleak 3.0.2 backend | Cancelling native connect may leave a Connecting attempt; wait_for is cooperative, public cleanup does not prove native release. Already documented by PR 1; no hardware reproduction here. | Physical HOLD; no private API adaptation or reconnections. Present owner and later gradual test. |
| A13 P2 hardware assumption, protocol/concurrency | madoka_protocol.py:121-129, 181-190; daikin_server.py:342-357 | Exact19 framing, zero limits, equal setpoints and six persistent connections are not certified for every model/firmware. Without transaction ID, a duplicate after success remains ambiguous. | Retain PR 1 wire behavior, coordinate with conservative limits, no real factory activation. Captures and one-controller test before expansion. |
| A14 P3 confirmed, dependencies/maintenance | requirements*.txt; daikin_server.py | Unlocked ranges, Python 3.10+ claim without a complete matrix, embedded HTML and mixed responsibilities. Known TestClient warning. | Retain originals, separate new modules by responsibility and record tested versions. No cosmetic refactor or unsupported absence-of-CVE claim. |
| A15 P3 operational risk, SQLite/logs | daikin_server.py:98-111, 379-399 | check_same_thread=False assumes serialization; operational DB/logs lack a formal quota/backup. Logs retained 180 days, history indefinitely. | New store serializes short synchronous access, timeouts/bounded queries, no automatic history deletion; SQLite backup and retention policy before production. |
| A16 P3 unresolved distribution rights | README.md Credits; tree without LICENSE | Without an explicit license there is no inferable general permission to distribute derivatives. | Preserve attribution, add no license, clarify rights before redistribution. |

## Review coverage

Reviewed all source files, scripts, launchd template, requirements, configuration, both selftests and all tests. Security: absent authentication, CSRF/CORS/Host, XSS, inputs/errors and bounds. SQLite: transactions, timestamp/identity, migration/restart/retention. UI: loading, stale data, errors, controls, mobile/keyboard. macOS: graphical session, permissions, sleep, restart, duplicates, logs/update/rollback. Quality: new-module separation, mocks are not BLE, limited dependency matrix. No secret scanning or real inventory inspection.

## Correction order and boundary

A01/A09 are treated first by blocking real execution and hardware-capable helpers; subsequent multiroom development uses simulators exclusively. Pure protocol imports do not initialize Bleak. The remote PR 1/branch is unchanged. New real mode is rejected before DB opening, client creation or BLE import. No DEMO result lifts A11–A13's physical HOLD.

## Iteration 2 review findings

Locations refer to this PR's new code; reproduced offline and corrected before delivery. R01–R08 came from independent review; R09 from Chromium E2E.

| ID/priority | Precise location | Evidence/impact | Correction and test |
| --- | --- | --- | --- |
| R01 P2 confirmed | madoka_multiroom/web/app.js, freshness timer | Fixed 35s threshold contradicted configurable stale_seconds, permitting stale data presentation. | Use server stale_after_seconds; E2E sets 5s and checks disabled controls. |
| R02 P2 confirmed | madoka_multiroom/manager.py, RoomManager.operate | Deadline began after acquiring the semaphore: an order could wait indefinitely before writing. | Timeout wraps wait+exchange; capacity_wait_included_in_deadline_no_late_write regression. |
| R03 P2 confirmed | madoka_multiroom/web/app.js, poll/api | Browser ephemeral token became stale after server restart, mutations failed 403. | Refresh session on polling without write replay; E2E restarts server with page open and sends a new order. |
| R04 P2 confirmed | requirements.txt | Pydantic 2 API without explicit dependency allowed incompatible installation. | Declare pydantic>=2.9,<3; clean environment and dependency resolution. |
| R05 P2 confirmed | madoka_multiroom/policies.py, save/configure/reset/administrative/desired | SQLite failure after memory mutation could activate ENFORCE/reset STOP without persistence/event. | Committed snapshot and rollback on failure; errors injected in four mutators. |
| R06 P2 confirmed | madoka_multiroom/manager.py, stop_policy/operate/poll | STOP save failure escaped catch, terminating poller; apparent recovery stopped updating. | In-memory STOP despite disk failure, protected logging and live poller; audit_failure_does_not_kill_poller regression. |
| R07 P2 confirmed | madoka_multiroom/policies.py, save/stop/reset; madoka_multiroom/manager.py, simulate | Second disk failure during recovery could roll STOP back to false and clear room failure. | Independent latch released only by committed reset; save recovery before clearing failure; three error/recovery/reset failures followed by repair. |
| R08 P2 confirmed | requirements-dev.txt; README/Mac B | Clean venv installed only pytest/httpx; launcher failed without runtime dependencies. | requirements-dev includes requirements.txt; fresh installation/full suite. |
| R09 P3 confirmed | madoka_multiroom/web/style.css, card-top/fieldset | Long name (inert XSS test) or admin grid exceeded 390 px width. | min-width 0, minmax 0 and overflow-wrap; desktop/mobile E2E with administration open. |

## Final treatment and limits

**25 findings: P0=0, P1=1, P2=20, P3=4.** No new P0/P1 invalidates PR 1's BLE core. P1 A01 is mitigated for this offline delivery by blocked real entrypoints, fully isolated DEMO and absence of LAN exposure. Future real-version authentication is not declared solved.

P2: **17 treated within offline scope** (A02–A10 and R01–R08). A11–A13 remain **physical HOLD**: protocol/unsupported semantics, native cancellation and compatibility/concurrency. Legacy code retaining these risks does not serve endpoints in the new launcher. P3: R09 resolved; A14–A16 partially mitigated or pending (lock/version matrix, quota/retention/logs, rights). This change does not repair/certify the entire legacy runtime: it blocks execution and documents gates.

The original 104 tests remain intact; additional tests, browser integration and final review are recorded in [iteration-2-validation.md](iteration-2-validation.md). Python tests/selftests/simulators do not demonstrate real BLE correctness or that hardware applies commands. Mac B NOT MODIFIED; HARDWARE_BLE NOT_TESTED; PRODUCTION_CHANGED NO.
