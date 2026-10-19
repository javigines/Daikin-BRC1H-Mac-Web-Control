# Complete office candidate: offline validation

Base: `master` at `02fa53c7773e4a551158165cab5f246a0d19b03a`.
No merge, deployment, Mac B/SSH access, pairing change, physical scan/connection,
SET, native restriction, ledger reset or consumed-canary-marker change occurred.

## Actual integrated results

The complete tree contains **530 tests** (not a sum of sibling PR results).

| Validation | Result |
| --- | --- |
| Python 3.12.15 full suite, offline import guard | 530 PASS |
| Python 3.14.8 full suite, offline import guard | 530 PASS |
| New office transport/access/recovery tests | 66 PASS, included in 530 |
| Chromium simplified six-simulator UI | PASS, desktop/mobile, zero JavaScript errors |
| Chromium six-room hardened wire contract | PASS, desktop/mobile, zero JavaScript errors |
| Chromium existing authenticated native read-only mock | PASS, desktop/mobile, zero JavaScript errors |
| Chromium new authenticated HTTPS office native mocks | PASS, desktop/mobile, zero JavaScript errors |

The only observed Python warning is the existing Starlette/httpx TestClient
deprecation. It does not change runtime behavior. Hosted exact-HEAD CI evidence
is recorded in the draft PR after publication; local results alone do not prove
hosted CI or hardware.

Reproduce in a fresh environment with `requirements-dev.txt`, then run:

```bash
MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python -m pytest -q
```

Use `requirements-browser.txt` and `python -m playwright install chromium` for
all four `integration/browser_*.py` harnesses listed in the README. CI installs
Chromium system dependencies on Ubuntu and runs each harness. The HTTPS harness
uses an ephemeral test certificate and accepts it only in that closed context.
Production browsers must validate their trusted office certificate.

## Coverage and evidence limits

New tests exercise six independent actual native factories against injected
closed SDK modules, capacities1/2/6, manual power/mode/fan/setpoint encodings,
full raw range/limit preservation, inactive target/fan preservation, restoration,
strict ACK and exact GET readback. They cover ACK without effect, timeout,
cancellation, disconnection, failed readback, changed mode/inactive target,
unsupported schema, unknown/zero limits, owner boundary intersections, Auto
mode control restrictions, duplicate operation IDs and no repeat SET.

Lifecycle cases cover callback-before-cleanup, missing callback, connecting and
disconnecting states, another visible connection, unsubscribe/disconnect errors,
no-connect discovery failure, durable reservations, crash/pending-intent restart,
reboot evidence, identity/revision scope, same-boot rejection, independent reviewer,
unknown identity, expiry/config/password/file restrictions and explicit GET-only
uncertain review. Successful ownership release and unresolved write outcome are
separate facts. Healthy observation resets the retry episode while a durable
rolling admission limit prevents storms. A direct-client rate-limit test proves
five polling clients do not starve each other and an abusive client is throttled.

Browser office tests use the real HTTPS listener/API/UI, six closed native
controllers, SQLite, explicit confirmation/cancel, every manual control,
setpoint restoration, pending-room isolation, ACK-no-effect STOP/review, per-room
history, restart persistence, desktop/mobile and JavaScript error collection.
Existing three browser suites still pass. The simulator suite also retains all
FREE/MONITOR/ENFORCE, maintenance/suspension/STOP, XSS and keyboard checks.

`ci/sitecustomize.py` rejects real Bleak, CoreBluetooth, Foundation and objc imports
before test code runs. Closed mocks are inserted explicitly into `sys.modules`;
removing environment flags within a test cannot load the physical driver.
Default/forbidden CLI paths are independently exercised with another import trap.
The ordinary `--real` and legacy physical launchers remain disabled. The old native
read-only grant continues to reject SET and fan GET. All new native access requires
the dedicated expiring source/config authorization; no authorization was issued
against hardware. Physical ENFORCE/schedules/auto-on remain impossible through
these application paths. No license changed.

Documentation checks: 83 relative links resolve; private office example validates
against its strict model; LaunchAgent XML parses; `git diff --check` passes. New
technical documentation/comments are English; product UI remains Spanish.

## Independent review

A separate review agent inspected native ownership, cleanup, framing, journaling,
readback, recovery, HTTPS access and commissioning boundaries. Its operational
findings were corrected within this candidate: commissioned setpoint bounds now
match the desired model; discovery which never attempted connect can release its
reservation safely; retry episodes rearm after healthy observations with a durable
rolling limit; owner/device temperature intersections and Auto fan restrictions
are checked before transmission; office rate limiting is isolated by direct TLS
client. Final independent disposition: **PASS for source/offline readiness; physical HOLD**.
The reviewer independently reran all 66 office tests under the Python3.14 import
guard (66 passed); no remaining reachable material findings were reported.
Exact hosted CI is linked in the PR.
Mocked cleanup success was explicitly not accepted as physical release evidence.

## Remaining physical gates and recommendation

Source is ready for PR review and one bounded, independently supervised acceptance
session, subject to final CI/review. **Physical rollout remains HOLD** until the
owner authorizes the exact final SHA and the [commissioning gates](office-commissioning.md).
Native callback/state/service-inventory evidence, historical local ledger
provenance/clock continuity, first SET effects and actual supported firmware
schema/limits/fan/modes, six-link capacity, user-session TCC/LaunchAgent behavior
and office TLS/firewall access cannot be proven by offline mocks. A public API
cannot establish global radio release across other applications/hosts. Unknown
or contradictory hardware evidence is STOP, not permission to relax a gate.
