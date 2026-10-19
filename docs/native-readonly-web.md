# Supervised native read-only web procedure

**HOLD: preparation only. No new physical attempt is authorized by this document or PR.** The earlier canary's consumed record and evidence must be preserved. Public cleanup returned and the owner reported responsive wall controls; native release is still unverified. Before any new attempt, the owner and an independent reviewer must examine that evidence, exclude competing clients, agree a safe recovery/STOP plan, and explicitly authorize the exact controller, new window and reviewed SHA. Do not reuse an earlier GO.

## Safe Mac B offline acceptance

These commands are proposed for a human operator. No SSH or Mac B changes were performed for this iteration. Use a separate checkout at the accepted exact PR #9 SHA and a separate virtual environment. Do not touch existing deployments, permissions, pairing, inventory, databases, services or canary state. Dependency installation requires network; functional validation uses fictional data only.

```bash
python3.12 -m venv .venv-offline
.venv-offline/bin/python -m pip install -r requirements-dev.txt
MADOKA_OFFLINE_ONLY=1 PYTHONPATH="$PWD/ci" .venv-offline/bin/python -m pytest -q
.venv-offline/bin/python madoka_readonly_web.py --selftest
.venv-offline/bin/python multiroom_app.py --demo --port 8090 --db demo_acceptance.db
```

Visit `http://127.0.0.1:8090`, check six Spanish room cards, details/history and separate configuration/laboratory. Exercise simulated policies only. Stop once with Ctrl-C and await shutdown. **STOP after offline acceptance.** Do not run the native command below as part of offline acceptance. Do not edit/delete a marker or ledger, grant OS permissions, pair, scan or change installer/native restrictions.

## Private preparation before a separately authorized attempt

Use Bleak **3.0.2**, an attended non-root macOS terminal, a clean exact reviewed commit, and no `PYTHONPATH`, CI or offline override. Use a separate fresh native checkout, rather than the offline acceptance checkout containing test databases or `.venv-offline`. Create its ignored `.venv` with `python3.12 -m venv .venv`, install `requirements.txt` there, and verify `git status --porcelain --untracked-files=all` is empty before the reviewed procedure. The application checks these before minting authorization. The native factory imports Bleak only after a durable session claim and the explicit browser action. Merely starting the server does not discover/connect.

Keep two owner-only regular JSON files outside the checkout, without symlinks. The inventory format is the [canary inventory format](mac-b-readonly-canary.md): `{"controllers":{"room-id":{"uuid":"EXACT-COREBLUETOOTH-UUID"}}}`. Explicitly select the logical ID; never infer it from an array index or scan result. The native room ID follows the app's lowercase/hyphen ID format.

The configuration uses the [multiroom schema](multiroom-architecture.md). It may contain six identities, but only the selected room can become available. For the selected room set `queries.fields` to exactly `["power","mode","setpoint","sensor"]`; use `timeout_seconds: 30.0`, `interval_seconds: 30.0` and `stale_seconds: 90.0` for this attended check. Configuration admission limits must permit its enabled entries, while the native ledger independently fixes actual capacity and operation concurrency to **one**. Fan and setpoint limits are not queried or fabricated. All controls stay disabled.

The native runtime stores ownership in the macOS account's fixed `~/.madoka-readonly-web/native-sessions.sqlite`, with a process lock and owner-only directory. Actual observation history is separate in `observations.db` with `real` storage metadata. Neither CLI options nor HOME overrides can relocate the ownership ledger. Keep it on trustworthy local durable storage. The permanent `~/.madoka-readonly-canary/attempt.jsonl` belongs to the earlier one-shot procedure and is never read, reset, deleted or rewritten by this web entrypoint.

## Blocked native command

**Do not execute until fresh owner authorization and independent procedure acceptance.** Replace every placeholder deliberately; never paste an inventory UUID into shared logs or Git. The authorization reference identifies the new approved window and evidence review; it is an audit reference, not machine-verifiable proof of physical safety.

```bash
.venv/bin/python madoka_readonly_web.py \
  --serve-read-only \
  --config /absolute/private/native-config.json \
  --inventory /absolute/private/inventory.json \
  --controller-id SELECTED-LOGICAL-ID \
  --reviewed-sha EXACT-REVIEWED-40-HEX-SHA \
  --authorization-reference NEW-OWNER-GO-REFERENCE \
  --port 8091
```

The hidden prompt requires `READ ONLY WEB ` followed by the exact selected UUID. The terminal prints a fresh operator password once. Open only `http://127.0.0.1:8091/`, authenticate as `operator`, keep the password private and remain physically attended. HTTP Basic authentication covers assets and APIs as well as the page; the CSRF token/origin check remains separate. Never forward the port, publish it on LAN, enable proxy headers, share browser credentials or treat loopback HTTP as a future LAN security design. Application/SDK output is suppressed during native execution; OS Bluetooth logs remain private OS data.

The Spanish page identifies supervised read-only mode. Check the selected room name and the owner's current GO before clicking **Conectar una vez · solo lectura** and accepting its confirmation. The browser allows 180 seconds; the backend has a separate 170-second setup budget, a 15-second discovery bound and a shared 120-second connect/subscription window including pairing. Each subsequent GET has six seconds, with a 30-second aggregate read budget. The remaining five rooms stay unavailable. One preparation only; no automatic reconnect, retry, connection cycling or rotation.

## Observe, stop and preserve evidence

Confirm power, mode, both targets and room temperature are credible, selected-room history is isolated, last observation advances, and all physical controls/policy administration remain disabled. Four GETs are the only permitted command groups. Read polling starts only after explicit admission. Check the wall controller during the agreed short window. Any mismatch, unexpected notification, stale/error state, pairing uncertainty, link loss, competing client, cancellation or unresponsive wall control means **STOP**.

Any new macOS privacy/Bluetooth permission prompt or unexpected pairing request also means **STOP**. Do not grant/change TCC permissions, unpair, reset Bluetooth or alter existing pairing state during this procedure. A future owner authorization must explicitly cover any separately reviewed permission/pairing work; the read-only GO alone does not authorize it.

Stop with one Ctrl-C and wait for shutdown. Public stop-notify and disconnect are best effort; even successful public returns leave the native session **quarantined** and consume capacity across restart. Shutdown after any admission is intentionally reported as requiring review (CLI exit 3); preflight/runner failures return 2. Preserve the private native ledger and observation database plus the unchanged earlier canary evidence. Do not repeatedly restart, reset policies, resolve through simulated evidence, remove files or use another account/path to bypass ownership. No native recovery/release command is implemented. A subsequent attempt requires separately reviewed native recovery evidence and code/procedure authorization.

The owner must physically check wall responsiveness and gather agreed native-state evidence after shutdown. A successful mocked test, public disconnect or responsive wall alone does not prove native release. One successful read-only controller does not establish six-controller capacity, manual SET safety, native restrictions, installer compatibility, LAN authentication/TLS, unattended operation or production ENFORCE. These remain separate future gates.
