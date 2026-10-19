# Iteration 4A: read-only canary preparation and offline validation

## Identity and decision

**PASS: offline preparation. HOLD: physical execution until separate owner authorization and attended preflight.** No Bluetooth operation, Mac B access, SSH, deployment, merge, service/permission change or native restriction was performed. Hardware behavior and native release remain unverified.

Base: draft [PR #6](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/6), exact commit `f5267721a6936e53a0d1461e58ba0c5c9b547ab5`, independently rechecked OPEN/DRAFT before delivery. Work is on isolated branch `auto/iteration-4a-readonly-canary`, directly from that commit. The delivery PR supplies the final exact SHA; this document cannot embed its own containing commit identity.

The change adds one standalone CLI and offline tests. Existing runtime/test files and the six-room application remain unchanged. README and the historical canary proposal link to the [new attended runbook](mac-b-readonly-canary.md).

## Audited entrypoint and boundaries

`madoka_readonly_canary.py:main` imports only standard-library code, pure `madoka_protocol`, the guarded historical spike's setup/selftest helpers, and shielded `ble_lifecycle.cleanup_client`. Default execution and `--selftest` return before any physical gate/driver import. Fresh-process tests prohibit Bleak, web API and manager imports on both paths.

The physical branch checks macOS/non-root/terminal, pinned Bleak metadata, no `PYTHONPATH`, clean exact reviewed checkout, strict owner-only external inventory and exact selection, then hidden UUID confirmation. A durable exclusive attempt record precedes the sole lazy Bleak import in `run_physical`. The call graph continues through exact UUID discovery, one client, the reused `connect_and_subscribe`, fixed `ReadSession.query`, semantic decoding and shielded cleanup. It has no web/coordinator/policy/scheduler dependency or recovery path. Internal helpers assume the supported CLI entrypoint and a trusted local account/environment; they are not a security sandbox against an operator editing code or importing internals.

The known query allowlist is `0x0020`, `0x0030`, `0x0040`, `0x0110`. The sole transmit method rejects every other command and calls `build_query(command)` without SET arguments. Existing reassembly/TLV parsing is reused; the diagnostic additionally rejects malformed fragments, repeated first fragments, reserved header values, wrong command IDs and duplicate object IDs. Power/mode enums, exact setpoint widths/scaling and signed indoor sensor width/range are checked. Missing/empty values remain distinct from invalid readings. Invalid data stops further GETs; missing/unsupported fields produce a partial result without invented observations.

The 120-second setup ceiling is separate from each 6-second GET allowance, and is passed to the Bleak client itself as well as the outer reused setup helper. Review of Bleak 3.0.2's installed CoreBluetooth source found that omitting this constructor timeout would retain its shorter internal default despite the outer allowance. The diagnostic does not modify or activate the production adapter. Discovery is separately limited to 15 seconds.

The cleanup adapter conservatively attempts stop-notify and disconnect even after partial setup; backend-raised cancellation of one call cannot skip the other. The shared helper shields cleanup and applies a 10-second allowance per public call. Async/native cancellation is cooperative. Successful mocked cleanup or a public disconnect return is never treated as evidence of native release. No capacity is automatically released: **every attempt is permanently consumed and review-required**, including apparent success.

The record is per agreed macOS account, exclusive, owner-only, identity-hashed, fsynced before discovery (file, state directory and parent directory). Account-home lookup avoids a `HOME` override bypass. Partial/crash markers block subsequent invocation; no reset exists. This is deliberately not a multi-host/distributed lease. It assumes durable trustworthy local storage and operator compliance; deleting the record, another account/host or source modification is outside its enforcement boundary and explicitly forbidden by the runbook.

Backend logging, Python stdout/stderr and file descriptors 1/2 are suppressed while the driver is loaded/used/cleaned up. Only structured observations, widths, timings, phase/error category, command IDs and cleanup evidence are emitted. No literal UUID, selected private ID, inventory path or raw backend exception enters shared results. OS Bluetooth logs remain private OS data. Record/persistence failures retain quarantine and cannot enable retry.

## Offline evidence on Mac A

| Check | Actual result |
| --- | --- |
| Full Python 3.12.15 suite | 357 PASS, 0 FAIL, 1 existing Starlette/httpx warning; 5.51 seconds |
| Full Python 3.14.8 suite | 357 PASS, 0 FAIL, 1 existing Starlette/httpx warning; 7.68 seconds |
| Independent full suites | 357 PASS on each Python version; no failures, same known warning (3.12: 5.53 seconds; 3.14: 8.00 seconds) |
| Default and protocol selftest | PASS; fresh-process import guard forbids real BLE/web/manager |
| New mocked-boundary coverage | 65 canary cases; only a closed fake Bleak module, no SDK/device calls |
| Simplified-interface Chromium, 1440px desktop / 390px mobile | PASS; six rooms, controls, policies, administration, histories, failures, recovery and restart; zero JavaScript errors; screenshots visually reviewed |
| Technical documentation | 17 Markdown files, 57 relative file links, no missing targets or unbalanced code fences; new technical prose/comments in English |
| Existing baseline | All 292 prior cases retained unchanged; integrated total is measured, not sibling totals added together |

The actual CLI is exercised with mocked platform/TTY/version/checkout gates and a closed `bleak` module. Cases cover exact one-device/four-GET frames, explicit selection, mismatched identity, all authorization/preflight blocks, setup/query timeouts and cancellations, strict malformed/unsupported/invalid replies, disconnection, cleanup errors/timeouts/cancellation, durable lock on success/error/crash, actual child-process termination after marker persistence, concurrent marker acquisition, fsync failure, inventory permissions/schema/symlink rejection, output/fd privacy, and result/record persistence failures. Temporary state directories prevent touching the real account canary ledger. Offline `--physical-read-only` branch tests are simulations and are not physical acceptance.

Independent review inspected the real entrypoint/import graph, transmit allowlist, durable record ordering, cleanup and installed Bleak CoreBluetooth source. Findings corrected before acceptance: invalid semantic responses must STOP; new ledger directory needs parent fsync; `HOME` must not redirect the ledger; Python redirects must supplement fd suppression; the 120-second pairing allowance must reach the client's internal timeout; terminal echo failure must abort before getpass can fall back to echoed UUID input. No outstanding material code findings were reported. Final independent review also passed the runbook, README and historical proposal updates; no material code or documentation findings remain. Focused independent canary suite: 65 PASS.

## Hardware-dependent limits and remaining authorization

No real discovery, connection, pairing, query, timeout, cancellation or native cleanup has been validated. Exact UUID-to-room mapping, controller model/firmware, actual TLV availability/units, pairing duration/prompts, macOS TCC state, native cancellation/disconnect behavior, privacy behavior in OS logs and wall responsiveness remain physical checks. Successful public API calls do not prove controller/native safety. A stuck native operation can exceed the cooperative allowances; quarantine and attended escalation remain mandatory.

The owner must separately approve the exact delivery SHA, selected private inventory entry/controller, attended window and expected peripheral pairing. New TCC grants, permission changes, unpairing, force recovery, SETs, native function lock and a second attempt are not covered. The [operator runbook](mac-b-readonly-canary.md) provides the physical command, STOP criteria, sanitized evidence and wall checks; **do not execute it until GO**.

`multiroom_app.py --real` remains blocked before backend/DB initialization. Legacy `daikin_server.py` and `brc1h_spike.py` physical entrypoints remain disabled except offline selftests. Physical ENFORCE, six-controller BLE, manual HVAC SETs, schedules and automatic recovery remain unavailable. This standalone diagnostic is the only new deliberately gated physical path and is not connected to the web application. Licensing/attribution are unchanged; see [upstream readiness](upstream-contribution-readiness.md).
