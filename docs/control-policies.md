# Per-controller policies and desired state

## Semantics

Default `FREE`, no protected fields or automatic power-on. Each room keeps a persistent record separate from the latest observation. Desired state changes only through authorized local API actions: ordinary controls persist intent before writing; `/desired` sets intent without an immediate command. Failed commands retain authorized intent and activate STOP; readings are not copied and success is not invented. Future schedules must use the same `administer/operate` service with owner authorization and a specific audit source; there is no active scheduler.

| Policy | Deviation from explicit desired state | Automatic writes |
| --- | --- | --- |
| FREE | Recorded; accepts observation without a restriction notice | None |
| MONITOR | Recorded and shown in dashboard/events | None |
| ENFORCE | Recorded and shown; only protected fields are considered | Bounded, Simulator only |

Accepting in FREE does not replace desired. Changing policy does not create desired or reset STOP/budget. `protected` independently selects setpoint/mode_id/fan_id/power_on; a field without desired or a reading is not corrected. Deviations are recorded for every intended field, including unprotected fields, with `unknown` origin. They are not attributed to a physical controller, person or another app.

## Bounded reconciliation

- Consecutive confirmations of the same observed value (2–5, default 2); a repeated timestamp does not confirm. Value changes or a missing field reset its candidacy.
- Monotonic stabilization 1–300s, default 5. Persistent cooldown 1–600s, default 15. A backward wall-clock jump cannot bypass cooldown.
- One field per operation, inside room lock/deadline. Durable attempt reservation **before** effect. Readback records correction_observed only if the value is observed; it cannot trigger another correction in that operation.
- Total maximum 1–5 attempts per owner reset, default 3. Success does not renew the budget. Exhaustion followed by another confirmed deviation activates persistent STOP; no infinite fight with another controller. Attempts, last attempt, STOP and auto_on_used survive restart; confirmations are discarded.
- Error, timeout or cancellation activates STOP and room failure. Recovering a simulator does not rearm corrections. Budget/STOP reset requires an administrative action and reason. SQLite failure rolls back administration and retains in-memory STOP; poller remains alive, inactive until explicit recovery.
- Desired/policy/restriction changes invalidate candidates. Errors do not silently relax control or trigger aggressive reconnect.

Automatic power-on additionally requires protected `power_on`, authorized desired true and explicit `allow_auto_on=true`. Effective minimums: three readings, 30s stabilization, 60s between attempts. Only one automatic power-on per budget/reset, even across policy changes or process restart. Never enable by default. Automatic power-off also requires protection/desired and normal bounds. These are simulation safeguards, not proof of actual thermal-system safety.

Maintenance inhibits corrections until explicit deactivation; temporary suspension lasts up to 3600s and expires using a persistent wall-clock deadline. They can be combined and require a reason; expiry is audited. Readings/deviations continue; neither blocks explicit owner commands. Suspension grants no new budget. Communication can be recovered and reviewed while STOP remains; reset is separate.

## Events and administration

Per-room events: desired_changed, policy_changed, deviation/deviation_cleared, correction_attempt, correction_observed, error, stop_budget_exhausted, restrictions_changed, suspension_expired, budget_reset and laboratory changes. Append-only in the application, not a tamper-proof ledger. Paginated API/events. MONITOR alerts locally; it sends no email or external messages. DEMO token proves a local request, not owner identity: production requires auth/RBAC and real-actor traceability.

## Native lock documented by Daikin

Official sources consulted 2026-10-19. Documentation describes **Function lock** using Madoka Assistant for BRC1H519 and BRC1H52 families:

- [BRC1H519W/K/S7, 4P513689-1D, 2019.05](https://www.daikin.eu/content/dam/document-library/Installer-reference-guide/ctrl/individual-control-systems/BRC1H519W7,K7,S7_4PEN513689-1D_2019_05_Installer%20and%20user%20reference%20guide_English.pdf), §14.5.28, printed pages 41–42.
- [BRC1H52W/K/S7, 4P813200-1A, 2026.01](https://www.daikin.eu/content/dam/document-library/Installer-reference-guide/ctrl/individual-control-systems/brc1h52k7/BRC1H52K7.BRC1H52S7.BRC1H52W7_Installer.User%20reference%20guide_4PEN813200-1A_English.pdf), Function lock, printed pages 112–114.

Both guides list locks for menu, setpoint, fan speed, operation mode and ON/OFF, plus other functions and individual modes. Blocked controls show a lock or disappear/are crossed out in menus; locking the active mode does not immediately stop it but prevents reselecting it after changing. These capabilities are documented for the guides' variants; they do not certify the six office controllers' version/model/configuration or compatibility with every installation.

Native locking applies restrictions in the controller through official tools. Our reconciliation detects a difference and can restore intent later in simulation: the external change exists during that interval. It does not prevent button presses or implement Function lock. No lock BLE commands have been inferred or implemented; installer settings have not changed and Madoka Assistant has not been connected to hardware. API/dashboard status: **NOT_APPLIED_NOT_VERIFIED**. No button promises physical locking.

## Gates before physical ENFORCE

1. Separate owner authorization per controller/UUID/model/firmware, field/value, test window, physically present operator, STOP/restoration criteria. Private inventory and room↔controller identity checked physically.
2. Resolve CoreBluetooth HOLD and semantic validation (size 0/unsupported/required fields, response/ACK versus real observation). First one supervised read-only session on one controller; verify setup, cancellation, disconnection, sleep/restart and cleanup without retries.
3. Separately authorize one known manual command within accepted limits and verify readback/physical screen, preserving/restoring previous values. Confirm mode/fan meaning, ranges, rounding and independent setpoints by variant.
4. Before reconciliation: owner auth/RBAC, manual emergency STOP, coordination with other apps, thermal limits, durable budgets under disk/power failure, events, maintenance/suspension and anti-fight tests. Keep auto-on forbidden; evaluating it requires further specific authorization and risk assessment.
5. Real activation requires a new reviewed backend/code/guard change and explicit permission. Pilot MONITOR on one controller before any ENFORCE; expand gradually only with native evidence per model and Mac B. Never automatically connect six controllers as an offline test.
6. For native locking, separate authorization and supervised official Madoka Assistant procedure; verify read-back setting, behavior of every function and restoration. No unknown commands or implied installer permission.

None of these steps has been executed. Mac B NOT MODIFIED; HARDWARE_BLE NOT_TESTED; PRODUCTION_CHANGED NO.
