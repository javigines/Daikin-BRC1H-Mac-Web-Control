# Daikin BRC1H Mac Web Control

Control six Daikin Madoka BRC1H controllers from the browser on the same Mac.

```bash
./run-local.sh
```

Open **http://127.0.0.1:8090**. The local mode uses the existing private inventory,
real observations, direct manual controls and automatic bounded reconnects. It
requires no password, TLS certificate, expiring authorization, reviewer or manual
SHA. See the [short local installation/update and six-device check](docs/local.md).
Implementation and automated validation remain offline; no Mac B installation,
Bluetooth operation, merge or deployment is performed by this PR.

Use `./run-local.sh --demo` for six simulators without Bluetooth. `multiroom_app.py`
also retains its existing simulator-only interface; its `--real` stays disabled.
The older authorized office/read-only entrypoints are retained for compatibility,
not required by local startup. Historical office procedures below describe those
older modes and do not add authorization requirements to Daikin Local.

## Start the DEMO

Python **3.12+** (tested on 3.12.15 and 3.14.8). Use a separate development directory:

```bash
python3.12 -m venv .venv-demo
.venv-demo/bin/python -m pip install -r requirements-dev.txt
.venv-demo/bin/python multiroom_app.py --demo --config config.example.json --db demo_history.db --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop with one Ctrl+C and wait for application shutdown. The server accepts only `127.0.0.1`, an unprivileged port and one worker. Without a configuration file it uses six fictional UUIDs. Never put the office inventory in the tracked example.

Each card offers target temperature, power, availability and a discreet protection notice. “Más opciones” opens mode, fan, last reading and history. “Configuración” holds administration; “Laboratorio DEMO” holds simulated errors, disconnections, delays, recovery, external changes and technical events. Controls are disabled when data is stale or the room has an error; other rooms can continue working.

In **Configuración**, “Libre”, “Supervisar cambios” and “Proteger configuración” select `FREE`, `MONITOR` and `ENFORCE`, respectively, with protected fields and an explicitly authorized desired state. The latest reading never automatically becomes desired state. `ENFORCE` corrects simulators only, with confirmations, stabilization, cooldown, persistent attempt budgets and STOP. Automatic power-on is disabled by default. Maintenance, temporary suspension, manual reset and events are managed per room. No native Daikin locks are activated.

## Offline tests

```bash
.venv-demo/bin/python -m pytest -q
.venv-demo/bin/python daikin_server.py --selftest
.venv-demo/bin/python brc1h_spike.py --selftest
```

Optional real-Chromium integration against temporary HTTP/SQLite resources, without hardware:

```bash
.venv-demo/bin/python -m pip install -r requirements-browser.txt
.venv-demo/bin/python -m playwright install chromium
.venv-demo/bin/python integration/browser_demo.py
.venv-demo/bin/python integration/browser_physical_contract.py
.venv-demo/bin/python integration/browser_native_readonly.py
.venv-demo/bin/python integration/browser_office.py
.venv-demo/bin/python integration/browser_local.py
```

Current combined results and limits: [local validation](docs/local-validation.md). Historical results: [office validation](docs/office-validation.md), [Iteration 5 integration acceptance](docs/iteration-5-native-integration.md), [Iteration 3 integration acceptance](docs/iteration-3-integration.md) and [Iteration 2 validation](docs/iteration-2-validation.md). The original 104 tests remain unchanged. The simplified-interface Chromium harness saves reviewed screenshots under `docs/images/`.

## Documentation

- [Daikin Local: one-command startup, existing-data update and short physical check](docs/local.md).
- [Local implementation review and offline validation](docs/local-validation.md).

- [Complete office native architecture, command contracts and ownership evidence](docs/office-native-architecture.md).
- [One Mac B installation, TLS, service, commissioning, update and rollback procedure — physical HOLD](docs/office-commissioning.md).
- [Office configuration template](office.example.json) (fictional UUIDs; capabilities default disabled).
- [Offline validation and independent review](docs/office-validation.md).

- [Native transport, consolidated history, tests and release/rollback](docs/iteration-5-native-integration.md).
- [One-controller authenticated read-only web procedure — blocked pending fresh owner GO](docs/native-readonly-web.md).
- [Physical multiroom architecture, durable ownership and hardware gates](docs/physical-multiroom-integration.md).
- [Iteration 4B validation and independent PR #7 compatibility](docs/iteration-4b-validation.md).

- [Integrated candidate and lifecycle investigations](docs/iteration-3-integration.md).
- [Simplified interface](docs/iteration-3-ux.md) and [offline adapter](docs/iteration-3-ble-adapter.md).
- [Mac B offline acceptance](docs/mac-b-offline-iteration-3.md).
- [One-shot read-only canary runbook — physical execution blocked until separate authorization](docs/mac-b-readonly-canary.md).
- [Iteration 4A offline validation and hardware-dependent limits](docs/iteration-4a-validation.md).
- [Historical read-only canary proposal](docs/first-madoka-read-only-canary.md).

- [Full repository audit](docs/full-code-audit.md): confirmed defects, risks and treatment.
- [Architecture, API, configuration and SQLite migration](docs/multiroom-architecture.md).
- [Policies, safeguards and native locks documented by Daikin](docs/control-policies.md).
- [Exclusively offline Mac B procedure](docs/mac-b-offline-iteration-2.md).
- [Previous BLE audit](docs/ble-reliability-audit.md) and [previous physical handoff](docs/mac-b-validation.md): historical evidence, not authorization to run hardware from this branch.
- [Upstream contribution readiness](docs/upstream-contribution-readiness.md): candidate patches, dependencies, compatibility and unresolved licensing.
- [English documentation normalization](docs/english-documentation-normalization.md): translation scope and checks.

## Compatibility and security

Iteration 4B's explicitly injected **offline** six-controller contract fleet uses closed wire models and the hardened Controller exchange path. It supports manual command/readback testing and durable session quarantine without importing a BLE driver. Iteration 5 adds an isolated native factory, attended macOS preflight, operator authentication and explicit browser admission. Its read-only grant permits only four reviewed GET groups. The new office grant separately enables capability-scoped manual SETs, a commissioned fan GET, native release evidence and supervised recovery after a verified reboot. The historical `multiroom_app.py` launcher selects simulators exclusively. The new `run-local.sh` explicitly selects native local operation, while its `--demo` remains hardware-free. The standalone canary retains its own permanent attempt record, separate from the native web ledger and the offline ledgers.

The standalone `madoka_readonly_canary.py` defaults to OFFLINE; `--selftest` never imports Bleak. Its explicit physical mode is isolated from the web application, requires an exact reviewed clean SHA and private UUID confirmation, and permanently consumes one attempt before discovery. There is no reset/retry path. See the runbook before any separately authorized physical test.

The legacy physical entrypoints `daikin_server.py` and `brc1h_spike.py` allow only `--selftest`; debugging/range helpers are blocked. `multiroom_app.py --real` fails before opening the DB or importing a BLE backend. The old read-only LaunchAgent template remains unchanged. A separate uninstalled office template supports user-session startup and throttled abnormal restart; its actual Bluetooth/TCC permissions remain a commissioning gate. No schedules or bulk commands are active.

The old `/api/*` API returns 410: migrate to `/api/controllers/{id}/...`, never array positions. New history is separated by identifier, uses a different DB and must not be opened with old code. An explicit import into a new copy of the original history is available; see architecture.

The DEMO ephemeral local token protects mutations against CSRF; **it does not authenticate users**. The dedicated native web mode additionally requires a random ephemeral operator password for every page, asset and API request. That read-only mode binds only to loopback and still blocks writes/recovery. The new office runtime requires attended source/config authorization and a specific private HTTPS listener for LAN use; all physical execution remains HOLD until owner authorization. Physical ENFORCE, schedules, auto-on and native restrictions remain disabled. MONITOR notifications are events and dashboard notices; no external messages are sent.

## Credits

- Upstream: [txsl/cool-vibes](https://github.com/txsl/cool-vibes).
- BLE protocol reverse-engineering: [Benjamin Lafois — Daikin-Madoka-BRC1H-BLE-Reverse](https://github.com/blafois/Daikin-Madoka-BRC1H-BLE-Reverse).
- Reference implementation: [pymadoka](https://github.com/mduran80/pymadoka).
- Native BLE library (reviewed version 3.0.2): [bleak](https://github.com/hbldh/bleak).

The audited upstream declares no explicit license. Attribution is preserved; this fork adds no license and infers no redistribution rights. Clarify rights with the author before redistributing derivatives or making a formal contribution. Historical claims about units in other environments do not certify these six office controllers.
