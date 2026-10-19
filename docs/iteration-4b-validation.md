# Iteration 4B offline validation

**Offline architecture PASS; physical execution and production HOLD.** Exact delivery SHA is published in the draft PR. Base is verified PR #6 `f5267721a6936e53a0d1461e58ba0c5c9b547ab5`; PR #7 `ed6135013ce5aec4a39f35f002e788c166a94294` remains independent and unchanged.

See [architecture, session ownership, strategies and hardware gates](physical-multiroom-integration.md). This report records offline source/test evidence only; it does not certify Bluetooth, CoreBluetooth release, Mac B runtime or hardware safety.

## Reproducible checks

From a fresh development environment with `requirements-dev.txt` and optional `requirements-browser.txt` plus Chromium installed:

```bash
python -m pytest -q
python integration/browser_demo.py
python integration/browser_physical_contract.py
```

Both browser commands use temporary fictional six-room storage and loopback HTTP. The wire-contract harness blocks Bleak imports in both parent and server process. The ordinary launcher remains DEMO-only; `--real` is rejected. No hardware command is part of validation.

## Test results

| Tree / check | Actual result |
| --- | --- |
| PR #8 tree, Python 3.12.15 | **346 PASS**, 0 failures; 6.34 seconds |
| PR #8 tree, Python 3.14.8 | **346 PASS**, 0 failures; 8.54 seconds |
| Temporary PR #8 + exact PR #7 source overlay, Python 3.12.15 | **411 PASS**, 0 failures; 6.88 seconds |
| Same combined tree, Python 3.14.8 | **411 PASS**, 0 failures; 9.41 seconds |
| Existing simplified-interface Chromium on combined tree | PASS desktop 1440px/mobile 390px; six rooms, all controls, policies, administration, histories/events, fault isolation, recovery/restart, stale/XSS/keyboard; 0 JavaScript errors |
| Wire-contract Chromium on combined tree | PASS desktop/mobile; six exact identities, all four commands and confirmed power/setpoint/mode/fan state, isolated histories, hardware flags false; 0 JavaScript errors; Bleak imports blocked |
| Independent full suite before final link-loss delta | 345 PASS on each version; Python 3.12 9.80 seconds / 3.14 12.65 seconds |
| Independent final focused suite including link-loss delta | **54 PASS** on each version; Python 3.12 1.98 seconds / 3.14 2.58 seconds |
| Independent code/safety/documentation review | PASS offline; no outstanding material findings; hardware HOLD |
| Documentation validation | 17 Markdown files, 51 relative file links, no missing targets or unbalanced code fences; English technical prose/comments |

All Python full-suite runs reported one existing nonfatal Starlette/httpx deprecation warning. The final main full runs include the asynchronous link-loss regression; the independent reviewer reran all 54 new cases after that final delta. Earlier full independent totals are explicitly labeled rather than presented as final 346-test reruns.

PR #6 contributes 292 unchanged existing tests. This branch adds 54 wire/ownership/manager/API cases. PR #7 contributes 65 separate canary cases only in the temporary compatibility tree, not in this PR's source baseline. The table reports actual pytest totals from each tree.

The compatibility tree was a separate temporary source copy of all PR #8 runtime, test and harness files. Five files were overlaid byte-for-byte from exact PR #7 commit `ed6135013ce5aec4a39f35f002e788c166a94294`: `madoka_readonly_canary.py`, `tests/test_readonly_canary.py`, `docs/mac-b-readonly-canary.md`, `docs/iteration-4a-validation.md` and `docs/first-madoka-read-only-canary.md`. README kept PR #8's description with additional links to PR #7's guides. It is an offline source-compatibility test, not a history merge or permission to run the combined physical canary. No PR #7 file/branch was modified. Future integration must preserve individual history and resolve README/proposal framing explicitly.

## Review and limits

Independent review covers the actual inherited `Controller.request` call graph, ready-only override, strict notifications, readback, persistent reservation/quarantine, recovery evidence matching, application paths and hardware guards. Corrected findings include retaining failed cleanup as failure on later calls, independently injectable setup phases and both-field fan readback. The final independent verdict is PASS for offline scope, HOLD for hardware. A final additional fix prevents repeated prepare from reporting readiness after asynchronous link loss; that path quarantines, shows stale/disabled state and never reconnects.

Native runtime injection, independently verified physical recovery, separate production ledger/schema, owner authorization/authentication and physical capability/cleanup evidence remain required. These are explicitly blocked rather than replaced by successful mocks. No BLE, SSH, Mac B edits, merge, deployment, native restriction or physical SET occurred.
