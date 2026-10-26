# Local operation modes: commissioning, verification and minimal pilot

Issue [#19](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/issues/19).
Base: `master` at `2cd1e97f157166b78eca355b167eb3b36b57be56`.
Integrated offline with `master` at `ee7c715bf3cdbde25ff7825fc4238efff970d775`
after PR #21 merged. The original branch history is retained.
This change implements Local Fan/Dry mode selection and conditional Ventilation.
It does not operate hardware. Existing Office commissioning remains restricted to
Auto/Cool/Heat. Temperature SET construction and PRs #13/#16 are outside this work.

## Cause and implementation

The enum and GET decoder already understood 0..5, but the shared `Controls`
validator rejected 0/1/5. Simply relaxing it would admit modes to Office and to
unverified equipment. The old generic mode readback checked selected stored
scalar fields, but had no mode-specific fan-effect policy or preservation of
complete opaque blocks. Non-temperature modes displayed the stored cooling
target as though it were active. The new verifier preserves the complete raw
blocks and recomputes active fields; tests include Cool→Heat with different
stored targets without assuming those derived fields must remain equal.

`LocalControls` adds private, identity-bound per-room wall commissioning. Existing
settings load unchanged. New modes require a wall inventory, an unlocked and
selectable mode menu and verified independent physical equipment. GET/enum
membership cannot populate this evidence. Ventilation
also requires positively identified compatible ventilation equipment and menu.
Unknown or linked topology stays disabled for new commissioning: software room
locks do not isolate connected indoor/outdoor groups. Group transitions need a
separate group contract and are not implemented here. No installer, lock,
centralized-control or master/slave setting is changed.

New wall authorizations are **persistent and identity-bound**, with no daily
inspection requirement (`expires_at: null`). The former mandatory 24-hour TTL
was an application policy without a Daikin requirement; it is removed. Record
`checked_at` as the inspection date, not a renewal clock. The private settings
already bind this record to the controller identity and reject identity mismatch.
Revoke authorization when the equipment, firmware/effect variant, linkage,
installation, master/slave/centralized control or restrictions change; re-inventory
before reauthorizing. GET cannot prove wall availability or detect every lock or
installation change. The operator must record known changes; a fresh full GET
baseline is still required for every operation.

Existing records with an explicit expiry retain it; there is **no automatic
migration or renewal** into persistent authority. Expiry removes only additional
0/1/5 targets, retaining previously configured Auto/Cool/Heat 2/3/4. A return from
an inventoried additional mode to a configured legacy mode remains possible after
expiry with the same one-SET/ACK/GET and raw-preservation checks. The historical
source mode, independent scope and hardware must still be established. An unknown
additional source without commissioning still blocks a transition; GET alone
cannot establish physical isolation.

Known `revoked`, locked or linked records block **all mode targets**, including
legacy and returns. They remain loadable and persist across restarts. The UI can
record revocation without another wall inspection or any SET; removing a known
restriction requires a new explicit wall verification with a later inspection
date. Deleting the evidence or saving the former unblocked record cannot bypass
it. Saving unrelated controls preserves the record and timestamps. Pending or
uncertain writes remain blocked even after explicit new commissioning; no rearm
is added. Other independent rooms retain their own controls.

### Active controls and effects

| Target | Active target | Indoor fan bank | Transition fan effect |
|---|---|---|---|
| Fan 0 | None | Unverified; manual disabled | Preserve both stored banks |
| Dry 1 | None; automatic temperature | Automatic; manual disabled | Preserve by default; exact both-to-Auto profile only after supervised evidence |
| Auto 2 | No active manual target inferred | Unverified; manual disabled | Preserve both stored banks |
| Cool 3 | Stored cooling target | Existing cooling mapping | Preserve both stored banks |
| Heat 4 | Stored heating target | Existing heating mapping | Preserve both stored banks |
| Ventilation 5 | None | No fabricated indoor-fan control | Preserve both stored banks |

Every transition preserves power, both stored targets, the complete setpoint
metadata, and all opaque mode/power/setpoint/fan TLVs. The sole additional
allowance is **Dry target + commissioned `auto_both`**, which requires exactly
0 in both fan banks and preserves their extensions. This describes stored GET
banks, not an inferred physical active bank or measured airflow. Other fan
changes, target changes, extension additions/removals or metadata changes are
unknown effects and stop further writes. Room sensor temperature may vary.
Derived active target/fan and applicable limits may change when the mode changes;
they are recomputed rather than compared as stored settings.

The UI displays stored cooling/heating targets separately in detail. Fan/Dry/
Ventilation have no active target. Dry has no manual fan controls. Power-off via
Local is conservatively disabled in running Dry and Ventilation: drain completion
and smooth ventilation start cannot be established by the available GETs. A
supervised wall stop must follow the unit instructions. No delayed power SET or
automatic restoration is scheduled. Dry may not dehumidify at low room
temperatures; mode confirmation does not prove humidity reduction.

### SET_MODE contract

1. Require admitted fresh room observations, unused operation ID, no unresolved
   operation, and configured capability. UI sends its `expected_mode`; new-mode
   HTTP requests must include it. A changed fresh mode rejects before journal/SET.
2. Under the room lock, read mode, power, both targets and metadata, sensor and
   both fan banks. Require verified schemas, current mode and applicable equipment
   authorization. Recheck target authority after GET; apply the legacy/expiry
   distinction above without bypassing known restrictions or uncertainty.
   Save the baseline and desired state durably before allowing a write.
3. Send exactly one `0x4030`, one TLV `0x20`, one byte with the requested mode.
   The existing bridge verifies framing, command correlation and duplicate TLVs.
4. For new commissioned capabilities recognize only `mode_status_v1`: exactly
   `0x20`, one byte in 0..5. The ACK may describe a different mode; it is not effect
   confirmation. Empty ACK compatibility remains only for legacy configurations.
5. First read after ACK is GET_MODE. Complete one bounded readback of power,
   stored setpoints/metadata, sensor and both fan banks. Compare the raw blocks
   with the transition matrix, then confirm. No polling SET, resend or restore.
6. Unknown ACK permits diagnostic GET on a healthy correlated stream, but never
   confirmation. No effect, corruption, link loss, timeout or any unknown effect
   leaves durable `uncertain`. GET-only review, reconnect, reload and saving
   controls cannot clear a Local mode uncertainty. Another room may still work.
   Resolving an actual uncertain mode write needs a separate operator procedure;
   this PR supplies no automatic or HTTP rearm.

## Accepted capabilities and remaining physical evidence

No real room is newly commissioned by this PR. Accepted real room capabilities
are **unverified** until a private wall inventory exists. Existing settings are
preserved. Synthetic tests commission independent model rooms with modes 0/1,
and separately exercise 5 with compatible-hardware evidence; these are not an
inventory of the real six-room installation. Do not copy synthetic values to it.

For each candidate room privately record: selectable wall modes, indoor-unit
model/variant, controller firmware, mode locks and centralized/master/slave
restrictions, independent versus linked scope, observation time, and, only for
5, the ventilation model and appropriate menu. Keep identities, topology and raw
payloads out of public GitHub. Recheck restrictions immediately before a pilot.

## Minimal supervised pilot and rollback (NOT EXECUTED)

No live BLE/SQLite access or SET is authorized by this issue. This is a concrete
acceptance procedure for a later explicit physical GO, not a six-by-three matrix.

1. **Passive inventory first:** observe wall menus and labels without changing
   mode, pairing, firmware or installer settings. Establish independent scope
   from actual equipment documentation/installer evidence. Do not switch modes
   to discover support. If linkage is unknown, stop only that candidate.
2. Pick one independent representative per proven hardware/firmware/effect
   variant. Review the exact PR SHA, private room evidence and saved baseline.
   Authorize one transition to Fan or Dry explicitly. A separate representative
   is needed only when its variant differs; Ventilation is skipped without its
   own hardware evidence and does not block Fan/Dry acceptance.
3. After GO, capture fresh GET baseline and wall state. From the enabled mode
   selector send one transition. Verify one SET, recognized correlated mode ACK,
   GET_MODE match, preserved power/targets/opaque fields and exact fan profile.
   Observe adjacent/linked-looking units passively for unexpected effects.
   Fan acceptance includes circulation without heating/cooling; Dry acceptance
   includes automatic controls and wall agreement, without asserting humidity
   performance from GET. Store sanitized field names/outcome publicly and full
   evidence privately.
4. Unexpected ACK, preservation mismatch, external room effect or no effect:
   retain durable STOP; do not retry, speculate about a fan bank, expand the
   profile or send a reverse SET. Do not clear SQLite. Other independently
   commissioned variants remain available.
5. **Rollback is a separate operation:** record the original wall mode and power.
   Returning to it requires another explicit authorization and a fresh independent
   baseline. An uncertain write cannot use this app to restore. Use supervised
   wall procedures after owner review, observing Dry drainage (approximately one
   minute in the guide) and the unit's ventilation-start instructions. Reverting
   source code does not restore hardware state and must not erase the ledger.

## Offline validation

Local complete suite: Python **3.12.15** and **3.14.9**, **876 passed** per
version on the original mode-only base. After integration with #21, **944 tests
pass per version**, retaining the complete #21 and mode suites plus ten
coexistence regressions. The authority/expiry correction now passes **995 tests
per version**, including 50 mode-authority regressions and the revised inventory
validation cases: expired legacy actions/returns from all six source modes,
expired additional targets, persistent 60-day authorization, known restrictions,
revocation/reconfiguration/reload, changing authority during baseline GET and
no rearm of pending/uncertain writes. Chromium desktop/mobile: all five existing harnesses pass with zero
JavaScript errors (`browser_demo`, `browser_physical_contract`,
`browser_native_readonly`, `browser_office`, `browser_local`). The Local harness
also commissions Fan/Dry, conditionally commissions a separate synthetic
ventilation unit, verifies disabled card/detail controls, checks other-room
state and retains a mode STOP after unknown ACK + GET-only review + refresh.
It also exercises coordinated-pair consent/cancel/stale rejection alongside
mode controls and, in the same synthetic room, rejects consent after an external
mode change, then confirms the pair and preserves both targets across Fan/Dry/
Heat/Cool transitions. GET-only temperature review retains #21 semantics;
it cannot clear mode uncertainty.
The Local browser also checks persistent authorization after 60 days, expiry
with all additional HTTP targets rejected before SET, a verified Dry-to-Heat
return, unchanged expired evidence after unrelated settings, and explicit
revocation/renewal (including revocation with no fabricated wall inventory).
Hardware drivers are guarded out of these tests; SQLite paths are temporary.
One existing FastAPI/httpx test-client deprecation warning is unrelated.

Reproduce in each supported interpreter with dependencies from
`requirements-browser.txt`: `MADOKA_OFFLINE_ONLY=1 PYTHONPATH=ci python -m pytest -q`,
then each of the five `integration/browser_*.py` harnesses listed above after
`python -m playwright install chromium`. Run browser harnesses sequentially
because they own fixed loopback ports and reject existing listeners.

## Sequential integration boundaries

The #21 integration resolves shared-file conflicts by preserving both contracts:
`LocalManualBody`, `RoomManager.native_manual` and `OfficeBackend.manual` carry
`expected_mode` and `setpoint_confirmation` independently. Readback and diagnostic
unknown-ACK observation retain `authorized_targets` alongside `action`. The
temperature proposal remains bound to the private exact profile and fresh raw
baseline; commissioning modes grants no temperature-pair authority. Conversely,
pair consent cannot authorize a mode action. `LocalConfig.coordinated_setpoints`
remains outside `LocalControls` and ordinary controls persistence, defaulting off.
The corrected deterministic full-operation deadline test from #21 is retained.

PR #23 (fan writes) is intentionally not absorbed into #22. It remains DRAFT for
the second integration after a separately authorized #22 merge. Preserve its
`fan_policy` through inherited `Controls` fields and settings saves. Combine its
durable fan-uncertainty exclusion with the Local mode exclusion in
`PhysicalSessionBudget.acknowledge_operations`; neither GET-only review nor
temperature consent may rearm them. No #22/#23 merge or deployment is authorized
by this integration, and no physical/private profile is read or activated.

## Sources

- [Daikin BRC1H52 guide 4P596266-1B, 2022.02](https://www.daikin.es/content/dam/document-library/Installer-reference-guide/ctrl/individual-control-systems/BRC1H52K_S_W_Installer%20and%20user%20reference%20guide_4PEN596266-1B_English.pdf):
  §§5.2–5.3 (printed pp.18–24), Dry safety (p.20), ventilation §§5.6/13.6
  (pp.29–30/64–65), master/slave §11.1 and cooling/heating masterhood §14.1.5
  (pp.43–44/86–88). These establish product behavior and restrictions, not this
  installation's selectable capabilities or BLE ACK evidence.
- [Pinned pymadoka OperationMode](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/features/operationmode.py):
  enum 0..5, GET 48/0x0030, SET 16432/0x4030, one-byte object 0x20.
  [Feature update parsing](https://github.com/mduran80/pymadoka/blob/eac7c3ad83794e14307f62e51b43a3f409e910d1/pymadoka/feature.py)
  supports the bounded status ACK profile; it does not prove effect or hardware
  support. Unknown variants remain uncertain.
