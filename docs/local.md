# Daikin Local

Run the web application in the browser on the same Mac that uses Bluetooth:

```bash
./run-local.sh
```

Open **http://127.0.0.1:8090**. Close with Ctrl+C and wait for shutdown. Start
it again with the same command after closing the application or restarting macOS.
There are no passwords, certificates, expiring authorizations, reviewer names,
manual SHA entry, per-command reasons or service installation in this mode.
This is a foreground application; keep its terminal open while using it.

## One installation/update procedure for the existing Mac B

These are future operator instructions. Implementation/testing did not access
Mac B or Bluetooth. Install this PR only after approval; it is not merged or
deployed by these instructions.

1. Stop the previous foreground application with Ctrl+C. Do not run an old
   background service alongside it. Keep the original private inventory and
   data directory in place; never delete/reset session records or the consumed
   canary marker. Retain the previous checkout as a rollback reference.
2. In the existing repository checkout, check for local changes with
   `git status --short`. Preserve any changes. Once this PR is merged and approved
   for installation, update with `git pull --ff-only origin master`.
3. Install the runtime once (reuse the existing `.venv` if it already exists):

   ```bash
   python3.12 -m venv .venv  # first installation only
   .venv/bin/python -m pip install -r requirements.txt
   ./run-local.sh
   ```

4. Allow the normal macOS Bluetooth permission/pairing dialog if prompted.
   Open http://127.0.0.1:8090 in the Mac's browser. No LAN, HTTPS, LaunchAgent,
   firewall modification, TCC reset or authorization-file generation is needed.

The default inventory is the existing private file:
`~/madoka-test/madoka-office-private/office.json`, using the actual account home.
Its six UUIDs, enabled devices and timing configuration are read without changing
its bytes. Legacy certificate, HTTPS host/port and authorization files are not
used by the local runtime. An alternate private inventory can be selected with
`./run-local.sh --config /absolute/private/office.json`.

The same `~/.madoka-readonly-web/native-sessions.sqlite` retains all native
history. Additive schema migration creates missing operation tables, without
replacing old records. Existing `office-observations.db` histories are reused.
Optional control choices are saved separately to owner-only `local-controls.json`
in that directory; saving controls sends **no** SET and does not rewrite the
inventory. Do not replace newer databases with an older backup during rollback.

## Controls and connection recovery

The six cards show real readings only. A recognized power observation enables
power; a complete recognized setpoint block with actual limits enables the active
cooling/heating target. Both target values remain available in room details.
Values are restricted to the device window intersected with the configured local
16–32°C envelope; inactive targets and every accompanying byte are preserved.

Use **Configurar controles** for supported modes and fan speeds visible on that
room's wall controller. Do not enable a mode or speed merely because it appears
in the protocol enum. Fan GET observations are independent of that write
configuration: cooling and heating banks are decoded separately, with 0 shown
as observed Auto. Only Cool/Heat select an active fan bank; other modes have no
inferred active fan. Raw values and GET extensions are retained unchanged.
Legacy fan permissions retain the two-manual-bank gate. The optional **Ventilador
con bancos Auto/manual** settings add explicit per-room Cool/Heat step permissions
for complete one-byte 0–5 pairs, preserving inactive Auto=0 exactly. Reading Auto
never grants permission, and Auto is never a fan write choice. See
[fan writing and minimal physical pilot](local-fan-writing.md) for the bounded
ACK/GET contract and pending physical acceptance, and the historical
[fan reading correction](local-fan-reading.md). Unknown fields/flags/limits disable the affected
control; unrecognized fan values do not hide otherwise valid room observations.
Auto mode selection can be configured; setpoint/fan writes in Auto mode remain unavailable
because their semantics have not been verified. No installer field is changed.

A normal click sends at most one logical SET, with a unique operation ID, fresh
GET baseline, durable previous values and GET readback. A value already observed
needs no SET. Transport errors, cancellation or mismatched readback never trigger
write retries. **No se pudo confirmar el cambio** means do not assume success.
**Consultar estado** reads current state and allows a later new action; it does
not retry or retroactively confirm the old command. Restoration, when safe,
is another deliberate click using the recorded initial value.

A change may wait behind another room's communication; keep the page open and
do not click again while it is pending. Local manual execution has a 90-second
budget after up to 30 seconds of queue admission (120 seconds combined, excluding
bounded cleanup). Connection and observation
freshness are separate: **Conectado · lectura pendiente** disables controls until
fresh data arrives. An uncertain effect can retain a healthy link and GET polling,
while manual changes remain blocked. Optional **Más opciones → Diagnóstico del
último cambio** records the failed phase without exposing native SDK identifiers.
See the [power incident RCA](local-power-incident.md) for the correction and limits.

Normal disconnections and old-session recovery are automatic when CoreBluetooth
proves the device is disconnected. Otherwise the affected card explains the
problem; other rooms continue. Do not edit SQLite or reset data. Technical
recovery rules and evidence limits are in [local validation](local-validation.md).

## Short six-device physical check

After owner approval of the source and physical operation:

1. Start once, verify six labels against the wall controllers, real ambient/target
   readings and independent connection states. Enable only wall-supported modes
   and speeds through **Configurar controles**.
2. For each room, record initial power, both targets, mode and fan. Make one safe
   one-degree change, check **Cambio confirmado** and the wall, then deliberately
   restore. Test and restore supported power/mode/fan changes within safe limits.
3. Check that using another room works while one connects. With no change pending,
   check a safe normal disconnection/reconnection. Close/reopen the application,
   then check after the next normal macOS restart. Histories and settings must
   remain, and no SET may repeat automatically.

Stop that room's tests on an unconfirmed write, unexpected wall effect, changed
inactive target/flags or unresolved native state. Keep other rooms independent;
do not retry an uncertain command or force restoration automatically. Native
release, firmware behavior and six simultaneous links still need real-device
acceptance; offline SDK tests cannot prove those properties. Physical ENFORCE,
schedules, automatic power-on and native locks remain disabled.

For a hardware-free preview use `./run-local.sh --demo`. It uses six simulators,
separate in-memory history and a visible DEMO label; it does not read the real
inventory or initialize Bluetooth. Local HTTP trusts applications/users on this
Mac, not other devices. Exact loopback peer/Host, same-origin checks, CSRF tokens,
JSON/body limits, restrictive CSP and disabled legacy write routes protect against
web-origin attacks. Local software running as your account is inside this trust
boundary.

## Mode commissioning

Fan Only and Dry can be commissioned per room after a passive wall inventory.
Ventilation additionally requires compatible equipment. See
[mode controls, strict SET/ACK/GET contract and minimal supervised pilot](local-modes.md).
