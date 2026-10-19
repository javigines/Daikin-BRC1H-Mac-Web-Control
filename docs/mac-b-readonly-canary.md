# Mac B: one-controller read-only canary

**PREPARATION PASS / PHYSICAL EXECUTION HOLD.** No physical test has been authorized or performed. This guide prepares a separate, attended diagnostic; it does not authorize its physical command. The web application remains DEMO-only. Do not use historical spike/server commands to bypass its guards.

## Offline preparation

Use a new directory, without modifying existing Mac B directories. The owner must approve the final delivery SHA published in the draft PR, not just the historical base SHA. A document cannot embed its own containing commit hash: replace the placeholder below with the exact 40-character reviewed delivery SHA, then keep that value for the physical command. Do not use a moving branch name as the reviewed identity.

These commands are **offline with respect to Bluetooth**; clone/install use the network. They do not grant physical authorization:

```bash
git clone https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control.git Daikin-Canary-4A
cd Daikin-Canary-4A
REVIEWED_SHA='<exact reviewed delivery SHA from the draft PR>'
git checkout --detach "$REVIEWED_SHA"
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt 'bleak==3.0.2'
.venv/bin/python madoka_readonly_canary.py
.venv/bin/python madoka_readonly_canary.py --selftest
.venv/bin/python -m pytest -q
git status --porcelain --untracked-files=all
git rev-parse HEAD
```

Expected: default prints `OFFLINE`, selftest passes, the suite passes, status is empty and HEAD equals the reviewed SHA. The default and selftest never import Bleak. Stop on any mismatch. Use the ignored `.venv` directory or an external environment; an unignored environment or output file makes the physical checkout gate fail. No sudo, services, LaunchAgents, SSH, system permission changes or installer settings are needed or authorized.

## Attended preflight, before any physical command

1. Obtain explicit owner GO for the exact SHA, one controller, one attended window and any expected peripheral pairing. Preparation/offline PASS is not GO. Be physically present at the wall controller and Mac B, in an ordinary GUI user terminal. Do not run as root, through automation or with redirected terminal input/output.
2. Privately verify the room, model/firmware, exact controller and its CoreBluetooth UUID from the owner's existing inventory. Never select by signal strength, advertising name or array position. Do not scan to discover an identity during preflight. Record baseline wall display and responsiveness; check that ordinary local controls work with the owner, without changing installer/native restrictions.
3. Review existing processes and applications locally. The following is read-only and is only a clue, not proof of exclusivity:

   ```bash
   pgrep -fl 'madoka|daikin|brc1h|Bleak|python'
   ```

   Check other user sessions and known BLE clients, including Madoka Assistant. Do not kill processes, stop services, unpair devices or change Bluetooth settings. If another client might own this controller, STOP and request owner review.
4. Keep inventory outside the checkout in an absolute, owner-only regular file (mode `0600`), without a symlink. The strict schema is shown below with a **fictional** UUID. Supply the privately verified real value locally; never commit, paste or include it in shared logs. Select exactly one entry using `--controller-id`. No inventory or selection is inferred automatically.

   ```json
   {"controllers":{"chosen":{"uuid":"11111111-2222-4333-8444-555555555555"}}}
   ```

5. Check that the agreed macOS account has no previous `~/.madoka-readonly-canary/attempt.jsonl`. The actual directory uses the account database home, regardless of `HOME`. Any existing record, including an empty/partial one, means STOP. Never remove, move or edit it, change accounts, create another home, or reset a budget to get another attempt. The mechanism is per account, not a distributed lock; owner preflight must prevent use from another account/host.
6. Use the reviewed environment with no `PYTHONPATH` override. The CLI requires Bleak exactly `3.0.2`, a clean checkout including untracked files, exact reviewed HEAD, macOS, non-root and terminal input/output. Existing macOS Bluetooth permission must already be appropriate. A **new macOS privacy/TCC permission prompt means STOP**; do not accept it or change settings under this authorization. An expected peripheral pairing prompt also needs the owner's explicit prior approval; stop on any unexpected prompt or identity.

## Physical command — BLOCKED until separate owner GO

This command **can discover, connect, subscribe and transmit BLE GET queries**. Do not run it during offline acceptance. It does not change HVAC settings, but pairing/connection can change system/controller state. There is no enforced authorization database: the owner and attended operator must enforce the GO boundary.

After GO, from the reviewed checkout/environment, replace only the private path and selected inventory key; use the exact delivery SHA set and checked above:

```bash
.venv/bin/python madoka_readonly_canary.py --physical-read-only \
  --inventory /absolute/private/canary-inventory.json \
  --controller-id chosen --reviewed-sha "$REVIEWED_SHA"
```

The hidden prompt requires `READ ONLY ` followed by the exact selected inventory UUID. Do not put this confirmation/UUID on the command line. A mismatch rejects the attempt before driver import. If terminal echo control fails, confirmation aborts instead of falling back to visible UUID input. After confirmation, the program writes and fsyncs a permanent identity-hashed attempt record **before importing Bleak or discovering**. This consumes the attempt even if discovery fails. A concurrent invocation cannot obtain a second record.

The CLI selects one exact UUID, validates the discovered identity again, constructs one client, and makes one connect/subscribe attempt. Discovery has a 15-second allowance; connect plus subscribe has a separate 120-second pairing allowance, also passed into Bleak's internal client timeout. It never uses the web adapter's short request deadline. Four sequential GETs are allowed: power `0x0020`, mode `0x0030`, cooling/heating setpoints `0x0040`, and indoor sensor `0x0110`. Each query has a 6-second allowance. No fan query, SET, retries, reconnect, polling, schedules, ENFORCE or native function lock is provided. A GATT write carrying a GET frame is required by this protocol; it is not an HVAC SET.

Missing/empty fields are reported as `missing`/`unsupported`, without fabricated defaults. Remaining approved GETs may still be collected. Malformed/duplicate frames, invalid widths/values, link loss or query errors stop further GETs. All exit paths after client construction attempt stop-notify and disconnect, including partial setup and cancellation. Cleanup has a 10-second allowance per public call and shielding against task cancellation. These timeouts are **cooperative**, not hard native wall-clock guarantees; an uncooperative CoreBluetooth cancellation may remain unresolved. Public API return never proves native disconnection, released capacity or physical safety.

## Results, STOP and handoff

After exit, record the exit status immediately (`echo $?` in the same terminal). Sanitized JSON is printed and saved as `~/.madoka-readonly-canary/result.json` under the account's actual home, with owner-only permissions. The permanent `attempt.jsonl` contains stage/timestamp, reviewed SHA and a SHA256 identity binding, never the literal UUID. It remains consumed even on exit 0. Preserve the entire private directory; share only the sanitized result after review. Inventory, screen captures containing identity, OS Bluetooth logs and UUID confirmation remain private. Backend Python logging/stdout/stderr and fd output are suppressed during the session; OS diagnostics are outside this suppression.

| Exit | Meaning and action |
| --- | --- |
| 0 | All requested fields available and public cleanup returned; native state remains UNVERIFIED. STOP and review. |
| 2 | Preflight, confirmation or durable-record block. Inspect privately; do not automatically rerun. |
| 3 | At least one field missing/unsupported; no default substituted. STOP and review. |
| 4 | Setup/query/backend/malformed/link failure. STOP; preserve evidence. |
| 5 | Cleanup or result persistence uncertain. STOP; missing/partial result never authorizes retry. |
| 130 | Cancellation. STOP; native state and cleanup require review. |

Every result carries `CONSUMED_REVIEW_REQUIRED` and hardware state `UNVERIFIED`. Errors identify phase/category and the active read command, without raw backend exception text. Failed stage durations may only be represented by total elapsed time and the durable stage records. Crash/forced termination can leave only the consumed marker and no result. An early outer interruption can also leave the generic STOP output; preserve evidence regardless of the exit status.

**After every outcome**, physically inspect the wall display and local responsiveness, without reconnecting. Record operator, timing, display comparison for the available observations and any abnormal behavior. Do not assume BLE/native release from a responsive screen. Any wrong identity, unexpected prompt, malformed reply, timeout, cancellation, unresponsive wall control, missing result or uncertain cleanup means STOP and escalation to the owner.

Do not repeatedly press Ctrl+C, force native APIs, unpair, power-cycle, change permissions, delete the marker or retry with another account/device. If the process hangs beyond its planned allowances, record the situation and obtain a supervised recovery decision; automatic recovery is absent. A second attempt, including after apparent success, requires new evidence review, explicit owner authorization and a separately reviewed procedure; this CLI intentionally has **no reset/release command**. No manual SETs, multiroom BLE, physical ENFORCE or native restrictions are authorized by a successful read-only canary.

See [validation and remaining risks](iteration-4a-validation.md), [historical proposal](first-madoka-read-only-canary.md) and [control policy gates](control-policies.md).
