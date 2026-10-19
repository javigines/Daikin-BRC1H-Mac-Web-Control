# Daikin BRC1H Mac Web Control · offline multiroom

**Iteration 3 integrated candidate: offline validation only / HOLD real Bluetooth.** This branch runs simulators only: six independent rooms, a dashboard, history and protection policies. It does not scan, connect, pair or write over Bluetooth. It is not enabled for production.

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
```

Current combined results and limits: [Iteration 3 integration acceptance](docs/iteration-3-integration.md). Historical results: [Iteration 2 validation](docs/iteration-2-validation.md). The original 104 tests remain unchanged. The simplified-interface Chromium harness saves reviewed screenshots under `docs/images/`.

## Documentation

- [Integrated candidate and lifecycle investigations](docs/iteration-3-integration.md).
- [Simplified interface](docs/iteration-3-ux.md) and [offline adapter](docs/iteration-3-ble-adapter.md).
- [Mac B offline acceptance](docs/mac-b-offline-iteration-3.md).
- [Proposed read-only canary — blocked until separate authorization](docs/first-madoka-read-only-canary.md).

- [Full repository audit](docs/full-code-audit.md): confirmed defects, risks and treatment.
- [Architecture, API, configuration and SQLite migration](docs/multiroom-architecture.md).
- [Policies, safeguards and native locks documented by Daikin](docs/control-policies.md).
- [Exclusively offline Mac B procedure](docs/mac-b-offline-iteration-2.md).
- [Previous BLE audit](docs/ble-reliability-audit.md) and [previous physical handoff](docs/mac-b-validation.md): historical evidence, not authorization to run hardware from this branch.
- [Upstream contribution readiness](docs/upstream-contribution-readiness.md): candidate patches, dependencies, compatibility and unresolved licensing.
- [English documentation normalization](docs/english-documentation-normalization.md): translation scope and checks.

## Compatibility and security

The physical entrypoints `daikin_server.py` and `brc1h_spike.py` allow only `--selftest`; debugging/range helpers are blocked. `multiroom_app.py --real` fails before opening the DB or importing a BLE backend. The LaunchAgent template neither starts nor restarts automatically and has not been installed. No schedules or bulk commands are active.

The old `/api/*` API returns 410: migrate to `/api/controllers/{id}/...`, never array positions. New history is separated by identifier, uses a different DB and must not be opened with old code. An explicit import into a new copy of the original history is available; see architecture.

The ephemeral local token protects mutations against CSRF; **it does not authenticate users**. A local process or user can obtain it. Do not publish the port or enable BLE until owner authentication/authorization, operational controls and the documented physical tests are implemented. MONITOR notifications are events and dashboard notices; no external messages are sent.

## Credits

- Upstream: [txsl/cool-vibes](https://github.com/txsl/cool-vibes).
- BLE protocol reverse-engineering: [Benjamin Lafois — Daikin-Madoka-BRC1H-BLE-Reverse](https://github.com/blafois/Daikin-Madoka-BRC1H-BLE-Reverse).
- Reference implementation: [pymadoka](https://github.com/mduran80/pymadoka).
- BLE library retained for legacy tests: [bleak](https://github.com/hbldh/bleak).

The audited upstream declares no explicit license. Attribution is preserved; this fork adds no license and infers no redistribution rights. Clarify rights with the author before redistributing derivatives or making a formal contribution. Historical claims about units in other environments do not certify these six office controllers.
