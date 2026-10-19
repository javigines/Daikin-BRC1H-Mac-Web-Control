# Proposed first Madoka read-only canary — BLOCKED

**Status: proposal only. Physical authorization has NOT been granted. Do not execute this procedure, remove guards or run historical scan/spike commands.** The integrated candidate cannot activate physical hardware. No runnable physical command or enabling patch is provided here. Offline acceptance does not change this status.

## Required authorization and implementation gates

The owner must explicitly authorize one identified controller, an attended test window and a reviewed one-shot implementation/SHA. Record model/firmware and a privately verified room↔UUID association; keep real inventory outside Git. The operator must be physically present with a known safe way to inspect the local controller and stop testing. Identify other clients and prevent concurrent access without altering unrelated services or controllers. Agree evidence retention, STOP escalation and a restoration plan before starting.

Before authorization can be used, a separate reviewed implementation must provide a read-only physical bridge with explicit preparation outside ordinary request deadlines, ready-only exchanges with no lazy setup/reconnection, one owner, one active session and one operation. It must persist uncertain session ownership across restart, expose no writes or ENFORCE, and implement evidence-based supervised native cleanup/release. No physical bridge or recovery mechanism exists in this RC. A raw Controller.request bridge or a larger operation timeout alone does not satisfy these gates.

## Proposed attended sequence after all gates and separate GO

1. Record reviewed SHA, selected controller/model/firmware, operator, time window and baseline local-screen responsiveness. Do not start six controllers. Verify no incompatible client is using this one session.
2. Prepare exactly once with separately reviewed discovery/connect/subscription limits. Historical ceilings are 12/20/20 seconds; the adapter's 60-second setup planning allowance is not native proof. No automatic retry. Stop immediately for an unexpected prompt, incorrect identity, setup/link error or ambiguous timeout. Pairing, even for a read-only application query, can change BLE/system state and needs explicit authorization.
3. Once ready, perform only the approved minimal reads, sequentially. Capture sanitized raw field widths and required/unsupported objects, semantic values, request timing and local display comparison. No target, mode, fan, power, installer or native Function lock writes. Missing/inconsistent values are errors; never replace them with successful defaults.
4. Attempt the reviewed one-shot cleanup independently of ordinary operation timers. Record each cleanup result, cancellation and native state evidence. An error, timeout or uncertainty retains/quarantines capacity and forbids another connection. Completed public disconnect calls alone do not prove native release or physical safety. Never clear quarantine by restarting, creating a new budget or applying the offline resolution method to hardware.
5. After exit, inspect the local screen and controls physically; observe without reconnecting. Preserve stdout/stderr, exit status, timings and operator evidence. End the test and request independent review before any second attempt, second device or capacity increase.

## STOP and escalation

Unexpected prompts, unresponsive local controls, incorrect identity, malformed reads, timeout/cancellation, uncertain cleanup or unexplained physical behavior mean STOP. Do not automatically retry, reconnect, unpair, power-cycle, change permissions or force native/private API state. Escalate to the owner with sanitized evidence; any recovery action requires its own explicit authorization and confirmed procedure. No hidden supervisor may restart the test.

Manual writes need another approved safe-value/restoration test after read-only evidence review. Physical ENFORCE, automatic power-on, multiple controllers and native restrictions require further separate design, safeguards and authorization. Native Function lock belongs to the official Madoka Assistant procedure and is not implemented by reconciliation. See [policy gates](control-policies.md), [BLE risks](ble-reliability-audit.md) and [integration acceptance](iteration-3-integration.md).
