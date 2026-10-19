# Iteration 2 validation · Mac A

Date 2026-10-19. **PASS offline / HOLD real**. Open/draft PR 1 base verified before development and delivery: `bcb1bf8493881d1ee50b7558d17299346b0fb4ae`, branch `fix/ble-reliability-foundation`. New branch `feature/multiroom-offline`, PR stacked on that base. This report describes that PR diff; consult its HEAD for immutable identity. No merge/deployment or changes on Mac B.

## Executed evidence

| Check | Result |
| --- | --- |
| Original baseline before functionality |104 PASS / 0 FAIL; originals unmodified|
| Final Python 3.12.15 suite |227 PASS / 0 FAIL / 1 warning, 4.27s|
| Final Python 3.14.8 suite |227 PASS / 0 FAIL / 1 warning, 6.34s|
| Both Python 3.12 selftests |ALL PASSED; pure code without BLE|
| Real Chromium, integration/browser_demo.py |PASS, 0 JavaScript errors|
| Python compilation and node --check app.js |PASS|
| Dependencies via uv pip check |36 compatible packages|
| Clean venv installing only requirements-dev |33 packages resolved/installed; 227 PASS / 0 FAIL / 1 warning, 4.15s|

One warning per run: StarletteDeprecationWarning for httpx in TestClient (suggests httpx 2). The client was not changed for an unrelated migration. `pip` was absent from the managed development venv; dependency verification used `uv pip check`, not a claimed successful pip check.

Tested versions: fastapi 0.146.0, uvicorn 0.54.0, pydantic 2.14.1, bleak 3.0.2 (legacy tests only), pytest 9.1.1, httpx 0.28.1, starlette 1.9.0, Playwright 1.64.0. Requirements ranges are not a reproducible lock or CVE audit; future resolutions must repeat validation.

## Scenarios represented by tests

Config 0/ 1/ 6 and duplicate/invalid values; six instances with isolated locks/state/latencies/history; semaphore/deadline including wait; busy 409/cancellation; room 3 failure without affecting others; shutdown/startup without orphan tasks; SQLite same timestamp/restart/cursor/import with/without filter and unchanged source hash; DB identity/environment; DEMO imports without Bleak/legacy; real/CLIs fail before hardware; strict Host/Origin/token/body/rate/inputs.

Policies: FREE/MONITOR/ENFORCE, four independent fields, desired never derived from observed, confirmations/stability/flapping/repeated timestamp, cooldown/backward clock/restart, persistent budget without automatic reset, prevention of infinite correction, auto-on OFF default plus 3 readings/ 30s/one-use guard, maintenance/suspension/expiry, STOP on errors/cancellation, records/pagination; SQLite failure in administration, STOP, recovery and repeated resets.

Chromium integration: real DEMO CLI server on loopback/ephemeral port with temporary DB; browser→HTTP→Simulator→SQLite. Six cards, four operations per room, three policies through forms, external changes, selective correction, events/history, isolated failure/recovery; server restart with browser open/new token; configurable 5s stale; hostile names/errors rendered as text; desktop 1440 px/mobile 390 px without overflow and administration open. The API is not replaced by a mock: functional backends are the application's simulators. Two HTTP interceptions test frontend failure/rendering and are removed afterward.

First E2E found mobile overflow with a long name: corrected, complete rerun PASS. Untracked screenshots in `artifacts/demo-desktop.png` and `artifacts/demo-mobile.png` contain inert hostile-string test cases. Separate normal preview on 8000, closed after review; restart using the README command.

## Independent review

Initial read-only audit before functionality and complete independent-agent diff review. Eight P2 findings in new code/installation documentation, all treated: freshness, deadline, token, Pydantic, policy rollback, poller after failed STOP, latch after second failure, clean-venv dependencies. Injected reproductions are included in the suite. **Final independent result: PASS offline / HOLD real Bluetooth**; complete diff/documentation reviewed, independent suite 227 PASS/ 0 FAIL/ 1 warning, diff --check PASS, original 104 tests intact. No material findings remain open in the new DEMO path. No offline review lifts the physical HOLD.

## Boundary

Mac A: source, simulators, tests, Chromium and temporary/DEMO SQLite. Mac B: **NOT MODIFIED**. HARDWARE_BLE: **NOT_TESTED**. PRODUCTION_CHANGED: **NO**. No SSH, scan, pairing, connection, BLE write, Bluetooth permissions, native restrictions, installer settings, services, LaunchAgents, schedules or bulk commands. Installing Bleak dependencies to preserve tests does not initialize hardware.

Before physical use: [policy gates/authorizations](control-policies.md), [architecture](multiroom-architecture.md), [offline Mac B handoff](mac-b-offline-iteration-2.md). Classify state by evidence; successful demo does not certify hardware or production.
