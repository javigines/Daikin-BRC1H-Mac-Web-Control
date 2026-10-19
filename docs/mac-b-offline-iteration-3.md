# Mac B: Iteration 3 offline acceptance

This procedure is for a human operator later. It was not executed on Mac B. Do not use SSH, an operational checkout/DB/venv, real inventory, Bluetooth permissions or services. It runs the integrated simplified UI and simulated adapter tests only. Installation may need internet; functional tests do not use hardware or external services.

1. Obtain the exact integration PR head SHA from the reviewed delivery. Choose a fresh directory and use Python 3.12 (Python 3.14 can be checked in another isolated venv). Replace the placeholder below; the check deliberately fails if it is not the exact 40-character commit. The subshell stops on any failure.

```bash
(
set -eu
git clone --single-branch --branch auto/iteration-3-integration https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control.git Daikin-Iteration3-Offline
cd Daikin-Iteration3-Offline
MADOKA_EXPECTED_SHA='REPLACE_WITH_REVIEWED_INTEGRATION_SHA'
python3.12 -c 'import re, subprocess, sys; expected=sys.argv[1]; actual=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(); assert re.fullmatch("[0-9a-f]{40}", expected) and actual == expected, "STOP: reviewed SHA mismatch"' "$MADOKA_EXPECTED_SHA"
python3.12 -m venv .venv-offline
.venv-offline/bin/python -m pip install -r requirements-browser.txt
.venv-offline/bin/python -m playwright install chromium
.venv-offline/bin/python -m pytest -q
.venv-offline/bin/python daikin_server.py --selftest
.venv-offline/bin/python brc1h_spike.py --selftest
.venv-offline/bin/python integration/browser_demo.py
)
```

2. Compare the actual total and warning with the [integration report](iteration-3-integration.md). The harness owns a temporary DB and ephemeral port and stops its server. Do not add sibling totals. Inspect the regenerated desktop/mobile screenshots in `docs/images/`. Any failure, unexpected BLE prompt or changed SHA means STOP and preserve logs; do not bypass a guard or start hardware.

3. Optional visual acceptance from the new checkout:

```bash
cd Daikin-Iteration3-Offline
.venv-offline/bin/python multiroom_app.py --demo --db demo_history.db --port 8000
```

Open only [127.0.0.1:8000](http://127.0.0.1:8000) on that Mac. If port 8000 is occupied, choose another free unprivileged port; do not kill unknown processes. Verify six cards, immediate target/power, room details/history, separate Configuración and Laboratorio DEMO, protected fields, simulated policies, maintenance/suspension/STOP/recovery and independent faults. Use fictional simulator data only. Stop with one Ctrl+C and wait for application shutdown.

**STOP after offline acceptance.** Do not run scans, physical commands, pairing, native restrictions, installer settings, permissions changes, LaunchAgents or deployment. `--real` and physical ENFORCE stay disabled. A future canary requires the separately authorized [read-only procedure](first-madoka-read-only-canary.md), which is currently blocked.
