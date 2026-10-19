# Offline multiroom architecture

## Execution boundary

`multiroom_app.py` requires `--demo`. `--real` and missing mode fail before DB/backend creation. The new graph is CLI → API → RoomManager → Simulator; it imports only pure helpers from `madoka_protocol.py`. It does not import Bleak, `daikin_server` or `brc1h_spike`. An import-hook test forbids those imports. There is no real backend factory. Automatic correction additionally requires `type(backend) is Simulator`; substituting a backend requires a reviewed code change, not a flag. Python 3.12+ is required because of `asyncio.timeout`.

## Configuration and identity

`config.example.json` contains six fictional UUIDs, stable IDs and editable names. Strict JSON, forbidden extras, maximum 64 KiB. IDs match `[a-z][a-z0-9-]{0,31}`, CoreBluetooth UUIDs are normalized and names printable without surrounding whitespace. Zero, one or six devices; duplicate IDs/UUIDs rejected. `enabled`, query fields, interval 2–300s, per-operation deadline 0.1–30s. `max_active_devices` requires explicit configuration approval for more than one (default 1); `max_concurrent_operations` defaults to 1, DEMO 2; default staggering 0.25s. A list exceeding active capacity is not silently rotated.

UUIDs are not returned over HTTP. SQLite binds each ID to a UUID hash, preventing reassignment to another device. Editing a configured name updates it on restart. Use Git-ignored `config.local.json`/`inventory.local.json` for future private data. No real addresses have been collected.

## Coordination

Each room owns a backend, lock, snapshot, error, statistics, policy engine and samples. The lock covers command+read and reconciliation; another operation on that room gets 409 without an unlimited queue. A semaphore bounds cross-room operations; the deadline includes semaphore wait. At most six room tasks wait. Staggered pollers and isolated faults. Timeout/cancellation/error latch a room until explicit DEMO recovery. Policy STOP remains after communication recovery and needs a separate reset with a reason.

Copied snapshots; freshness uses monotonic time, actual application readings and a configurable threshold. Serving GET does not refresh a sample. Model/firmware information is labeled SIMULATED. Shutdown cancels/gathers pollers/operations and closes every backend and SQLite. A per-file `flock` prevents cooperating processes sharing a DB; it does not prove exclusion of other programs or physical devices.

## API

Prefix `P=/api/controllers/{id}`. Logical IDs required; unknown ID404, old API410. Reads do not initiate backend operations per HTTP client.

| Method/path | Payload or effect |
| --- | --- |
| GET /api/session | DEMO mode, hardware false, ephemeral local token |
| GET /api/controllers | Six independent snapshots |
| GET P | State, freshness, errors, statistics and protection |
| POST P/power | `{"on":true}` |
| POST P/setpoint | `{"temp":23}`; 16–32°C, integer rounded with shared helper |
| POST P/mode | `{"mode":3}`; integer 0–5 |
| POST P/fan | `{"speed":3}`; integer 1–5 |
| GET P/history | hours >0 and ≤744, limit 1–1000, positive before_id |
| POST P/simulation | status online/disconnected/error, delay_ms 0–2000 |
| POST P/external-change | DesiredPatch fields; modifies simulator without changing desired |
| POST P/policy | Complete PolicySpec (replacement with explicit defaults) |
| POST P/desired | Nonempty authorized patch, no immediate write |
| POST P/restrictions | maintenance, suspend_seconds0–3600, reason 3–160 characters |
| POST P/reset-budget | Required reason; explicit reset |
| GET P/events | limit and before_id with an exclusive cursor |

Policy details: [control-policies.md](control-policies.md). Every mutation requires JSON and the current session's `X-Demo-Token`; if Origin exists it must exactly match http+Host. Hosts are localhost/ 127.0.0.1 only. No permissive CORS or trust in proxy headers. Body≤16KiB before parsing, global rate 600/min, uvicorn concurrency 32. Strict types: no bool as int, strings as numbers, NaN/Inf or extras.422 validation, 409 busy/not fresh, 503 failure; no private values in errors. Lost mutations are not automatically resent.

Token = local CSRF protection, not identity/RBAC. Loopback does not protect against another local process/user. Do not publish or use in production. Self-only CSP, textContent rendering, GET cache no-store. UI obtains a new token on polling after restart without replaying old writes. The server stale threshold also applies in the browser if HTTP stops updating.

## SQLite

Schema `user_version=2`, new tables and short synchronous transactions on the event loop (one instance/worker). `app_meta` separates demo/real: never open a real DB as DEMO. `rooms` stores identity/name/binding; `room_samples` uses autoincrement sample_id + controller_id + captured_at_ms + JSON and room/time index; INSERT without replacement permits six samples in the same millisecond. `room_info` preserves imported legacy information. `room_observed` stores the latest persistent observation; `room_control` desired/policy/budget; `control_events` append-only events. Old observations are not rehydrated as fresh after restart.

Control record and event are saved in one transaction. If save fails, memory rolls back to the last committed record; STOP is also retained in memory even if disk fails. Room operation fails closed. An event cannot be guaranteed persistent on broken storage: restore storage and review before reset. History and control use separate transactions; failure between them may preserve an intent without a new sample; reserved budget is not automatically refunded.

Pagination uses exclusive IDs, never ambiguous timestamps. No deletion or automatic retention; indefinite growth remains an operational risk. Before production: quota, authorized retention, SQLite backup, directory permissions and restore testing. Bounded queries/responses are not a disk quota.

## Safe migration from single-room

File operations only, without connecting to controllers:

```bash
python multiroom_app.py --migrate-history /path/original-copy.db --output /path/new-multiroom.db --legacy-controller-id original-room
```

Do not use the operational file as destination. Source opens read-only with a SQLite URI and backs up to an exclusively-created target (`xb`); existing destination is rejected. A transaction validates schema and copies every row under explicit identity. Original `samples` and `device_info` tables remain preserved; previous data with/without filter_dirty retains all original JSON fields. Idempotent import using non-reassignable legacy_id metadata. Collisions already lost by old REPLACE cannot be recovered. Failed copies remain for inspection and are not overwritten.

The copy is tagged real and cannot start in DEMO. Legacy fields retain their names (`power`,`mode`,`fan`); future normalized reading/real visualization needs explicit adaptation. The demo is not intended to interpret imported physical data. Tests verify integrity, source hash, restarts, previous field names and table preservation. For rollback keep the source intact, close the process and return to the previous version/DB; never open schema 2 from the old application.

## Production requirements

Owner authentication/permissions per room/action; secure transport if networking is enabled; physical UUID exclusion and other-controller detection; actual firmware ranges/limits; required/unsupported schema validation; CoreBluetooth disconnection evidence; non-cooperative timing/native cleanup; observability/retention; reviewed deployment/rollback. No automatic reconnect, schedules or bulk actions in this iteration. Six simultaneous BLE connections remain NOT VERIFIED.
