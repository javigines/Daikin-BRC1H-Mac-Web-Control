# Mac B · exclusively offline iteration 2 handoff

Procedure for later human execution. **Mac B NOT MODIFIED** during this iteration. No SSH, pairing, scan, BLE, macOS services or LaunchAgents. Do not execute old scripts or historical connection instructions. Do not reuse the operational virtual environment, DB or checkout.

## Separate preparation

After human review of the DRAFT PR, in a Mac B terminal without touching the existing directory:

```bash
git clone --branch feature/multiroom-offline --single-branch https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control.git Daikin-Multiroom-Offline
cd Daikin-Multiroom-Offline
git rev-parse HEAD
python3.12 -m venv .venv-offline
.venv-offline/bin/python -m pip install -r requirements-dev.txt
.venv-offline/bin/python -m pytest -q
.venv-offline/bin/python daikin_server.py --selftest
.venv-offline/bin/python brc1h_spike.py --selftest
.venv-offline/bin/python multiroom_app.py --demo --config config.example.json --db demo_history.db --port 8000
```

Compare SHA with the PR HEAD before using results. Open only [127.0.0.1:8000](http://127.0.0.1:8000) on that Mac. If the port is occupied, choose another free port≥1024; do not kill existing processes. No real inventory or copy of the operational DB. Installing dependencies may require internet; functional tests require neither Bluetooth nor external services.

## Acceptance criteria

See six rooms and DEMO label; change the four functions independently; separate history; failure in room 3 without affecting others; manual recovery; FREE does not correct, MONITOR alerts without writing, ENFORCE only selected fields after confirmations/stabilization. Desired must stay separate from laboratory changes. Test maintenance/suspension/reset and restart retaining policy/desired/budget. Auto-on remains OFF unless deliberately testing the simulator. See [validation](iteration-2-validation.md) for optional Chromium automation.

Stop with Ctrl+C and await shutdown. Keep results by SHA/Python/OS and warnings. Returning to the original environment means closing only this demo and leaving the previous checkout intact; never open its DB with this version.

**STOP after offline tests.** Any physical action requires separate authorization and an operator present. Detailed gates: [control-policies.md](control-policies.md). Do not automatically connect six controllers or treat these results as CoreBluetooth proof. Do not enable native restrictions or alter installer parameters.
