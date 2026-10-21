# Daikin BRC1H — Candidatos curados de memoria (NO IMPORTADOS)

**Corte:** 2026-10-21 Europe/Madrid. **Proyecto:** `javigines/Daikin-BRC1H-Mac-Web-Control`. **Dossier técnico completo publicable:** [project-context-2026-10-21.md](project-context-2026-10-21.md). **Contrato:** [Memory OS #76](https://github.com/javigines/memory-os/issues/76) y [playbook PR #77](https://github.com/javigines/memory-os/pull/77).

> **WORK es propuesta de clasificación, NO autorización de lectura/escritura.** La fuente GitHub es PÚBLICA: contiene solo arquitectura/decisiones/tipos de evidencia generales, no inventario real, UUID/MAC, correspondencias físicas, red/infra/localización de instalaciones, logs ni secretos. **`HOLD_DOCUMENT_DESTINATION`** para la extensión operativa en un repositorio o documento WORK privado autorizado. `MEMORY_WRITES=0`; `AUTO_CAPTURE=OFF`; `OWNER_APPROVAL=NOT_REQUESTED`; `PREPARED_NOT_WRITTEN`.

La acción `NEW` significa **candidato a nueva memoria si no existe ya**, no declaración de que sea realmente nuevo en el servidor. `UPDATE` significa **actualizar la clave vigente si existe**, sin sobrescribir sin cotejar fuente/revisión. `SKIP` se conserva para justificar deduplicación editorial; `HOLD` no puede importarse. Todos los `NEW/UPDATE` están **bloqueados para ejecución** hasta autorizar `WORK`, verificar duplicados exactos y semánticos y aprobar el lote/texto exacto. Los límites de 2048 bytes se refieren a `text`, no a metadata.

## Resumen de selección (12 filas orientativas, 10 memorias técnicas potenciales)

| N. | memory_key | Tipo | Acción propuesta | Sensibilidad | Texto UTF-8 |
|---:|---|---|---|---|---:|
| 1 | `daikin-brc1h/project-objective` | objective | NEW | PUBLIC_META | 219 |
| 2 | `daikin-brc1h/native-architecture` | architecture | NEW | PUBLIC_META | 263 |
| 3 | `daikin-brc1h/ble-framing-validation` | invariant | NEW | PUBLIC_META | 239 |
| 4 | `daikin-brc1h/manual-set-idempotence` | invariant | NEW | PUBLIC_META | 304 |
| 5 | `daikin-brc1h/power-ack-readback` | decision | UPDATE | PUBLIC_META | 304 |
| 6 | `daikin-brc1h/session-recovery` | invariant | NEW | PUBLIC_META | 338 |
| 7 | `daikin-brc1h/policy-boundary` | invariant | NEW | PUBLIC_META | 271 |
| 8 | `daikin-brc1h/local-security-boundary` | decision | NEW | PUBLIC_META | 284 |
| 9 | `daikin-brc1h/source-validation-current` | milestone | UPDATE | PUBLIC_META | 290 |
| 10 | `daikin-brc1h/upstream-licensing` | risk | NEW | PUBLIC_META | 263 |
| 11 | `daikin-brc1h/physical-acceptance` | current_state | HOLD | WORK_CONFIDENTIAL_HOLD | 257 |
| 12 | `daikin-brc1h/obsolete-local-ack-assumption` | history | SKIP | PUBLIC_META | 190 |

## Propuestas autocontenidas, fuentes y provenance

### 1. `daikin-brc1h/project-objective` — NEW

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/project-objective`
- `fact_type`: `objective`
- `text` (219 bytes UTF-8): Daikin BRC1H Mac Web Control tiene como objetivo manejar hasta seis controladores lógicos desde una interfaz web local en macOS, con observaciones y controles manuales separados por sala; la DEMO no usa Bluetooth real.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-19`
- `provenance`: README actual y PR #11 (código fuente); síntesis del coordinador
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/blob/1e0da649ae21da28280bfd16d0a349117c950bf5/README.md) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/11)
- `action`: `NEW` (solo propuesta documental; no ejecutable)
- `sensitivity`: `PUBLIC_META`
- `supersedes`: NINGUNO; verificar duplicados antes de crear
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`


### 2. `daikin-brc1h/native-architecture` — NEW

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/native-architecture`
- `fact_type`: `architecture`
- `text` (263 bytes UTF-8): El runtime nativo usa Python/FastAPI, Bleak 3.0.2 y CoreBluetooth con instancias BLE por controlador, RoomManager, locks por sala y capacidad global acotada; SQLite conserva observaciones, intención, operaciones y sesiones con identidades privadas no publicadas.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-19`
- `provenance`: PR #10 y arquitectura de código; verificado contra master
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/10) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/blob/1e0da649ae21da28280bfd16d0a349117c950bf5/docs/office-native-architecture.md)
- `action`: `NEW` (solo propuesta documental; no ejecutable)
- `sensitivity`: `PUBLIC_META`
- `supersedes`: NINGUNO; verificar duplicados antes de crear
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`


### 3. `daikin-brc1h/ble-framing-validation` — NEW

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/ble-framing-validation`
- `fact_type`: `invariant`
- `text` (239 bytes UTF-8): El enlace Daikin BRC1H usa fragmentación BLE con 19 bytes de carga útil por fragmento y decodificación estricta de índices, tamaños, correlación de comando y campos TLV; respuestas incompletas o desconocidas no se tratan como éxito.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-19`
- `provenance`: PR #1 y auditoría técnica (pruebas offline)
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/1) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/blob/1e0da649ae21da28280bfd16d0a349117c950bf5/docs/ble-reliability-audit.md)
- `action`: `NEW` (solo propuesta documental; no ejecutable)
- `sensitivity`: `PUBLIC_META`
- `supersedes`: NINGUNO; verificar duplicados antes de crear
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`


### 4. `daikin-brc1h/manual-set-idempotence` — NEW

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/manual-set-idempotence`
- `fact_type`: `invariant`
- `text` (304 bytes UTF-8): Cada cambio físico manual requiere observación GET previa, registro durable, operación identificada y como máximo un SET lógico al dispositivo elegido, seguido de GET de comprobación. Si el resultado es incierto no hay reintento ni restauración automática; Consultar estado realiza solo lecturas.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-19`
- `provenance`: Contratos PR #11/#12 y tests de regresión; no afirmación de garantía física universal
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/11) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/12)
- `action`: `NEW` (solo propuesta documental; no ejecutable)
- `sensitivity`: `PUBLIC_META`
- `supersedes`: NINGUNO; verificar duplicados antes de crear
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`


### 5. `daikin-brc1h/power-ack-readback` — UPDATE

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/power-ack-readback`
- `fact_type`: `decision`
- `text` (304 bytes UTF-8): Desde PR #12, una respuesta SET_POWER o SET_MODE con esquema conocido, aunque incluya un valor TLV distinto al solicitado, solo habilita comprobación GET; no confirma el cambio. Power admite hasta tres GET_POWER correlacionados para asentamiento y conserva STOP para respuestas o efectos no confirmados.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-19`
- `provenance`: RCA y código final de PR #12; reemplaza interpretación antigua del ACK
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/12) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/blob/1e0da649ae21da28280bfd16d0a349117c950bf5/docs/local-power-incident.md)
- `action`: `UPDATE` (solo propuesta documental; no ejecutable)
- `sensitivity`: `PUBLIC_META`
- `supersedes`: `daikin-brc1h/power-ack-readback (si existe versión anterior; comprobar por dedupe)`
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`


### 6. `daikin-brc1h/session-recovery` — NEW

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/session-recovery`
- `fact_type`: `invariant`
- `text` (338 bytes UTF-8): Las reconexiones nativas son acotadas y aisladas por controlador. Liberar o recuperar ownership exige evidencia de identidad y ausencia nativa CoreBluetooth; una desconexión pública, un timeout o una cuarentena por cierre no prueban por sí solos liberación física global. Se conserva el ledger y no se eluden bloqueos borrando datos.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-19`
- `provenance`: Diseño de lifecycle y pruebas de PR #10–#12
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/10) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/11) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/12)
- `action`: `NEW` (solo propuesta documental; no ejecutable)
- `sensitivity`: `PUBLIC_META`
- `supersedes`: NINGUNO; verificar duplicados antes de crear
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`


### 7. `daikin-brc1h/policy-boundary` — NEW

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/policy-boundary`
- `fact_type`: `invariant`
- `text` (271 bytes UTF-8): Las políticas FREE y MONITOR separan estado deseado y observado; ENFORCE permite reconciliación automática acotada exclusivamente en el simulador. En dispositivos BLE reales siguen deshabilitados ENFORCE, autoencendido, horarios, cambios de instalador y locks nativos.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-19`
- `provenance`: Modelo de políticas offline y límites públicos
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/2) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/10) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/blob/1e0da649ae21da28280bfd16d0a349117c950bf5/docs/control-policies.md)
- `action`: `NEW` (solo propuesta documental; no ejecutable)
- `sensitivity`: `PUBLIC_META`
- `supersedes`: NINGUNO; verificar duplicados antes de crear
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`


### 8. `daikin-brc1h/local-security-boundary` — NEW

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/local-security-boundary`
- `fact_type`: `decision`
- `text` (284 bytes UTF-8): Daikin Local arranca en primer plano con ./run-local.sh y sirve HTTP exclusivamente en 127.0.0.1:8090; no requiere TLS o contraseña local, pero valida peer/Host/Origin y CSRF. El modo local no habilita LAN ni autoinicio; activar esas funciones requeriría otro diseño/autorización.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-19`
- `provenance`: Diseño aprobado PR #11, límites actuales en README
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/11) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/blob/1e0da649ae21da28280bfd16d0a349117c950bf5/docs/local.md)
- `action`: `NEW` (solo propuesta documental; no ejecutable)
- `sensitivity`: `PUBLIC_META`
- `supersedes`: NINGUNO; verificar duplicados antes de crear
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`


### 9. `daikin-brc1h/source-validation-current` — UPDATE

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/source-validation-current`
- `fact_type`: `milestone`
- `text` (290 bytes UTF-8): La versión fuente confirmada de Daikin Local al 21/10/2026 es master 1e0da649ae21da28280bfd16d0a349117c950bf5 (squash PR #12). CI PASS con 603 pruebas Python 3.12, 603 Python 3.14 y cinco suites Chromium escritorio/móvil sin errores JS; estas pruebas son offline y no certifican hardware.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-19`
- `provenance`: Merge GitHub y workflow de PR #12 verificados al corte
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/12) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/actions/runs/39315164895) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/commit/1e0da649ae21da28280bfd16d0a349117c950bf5)
- `action`: `UPDATE` (solo propuesta documental; no ejecutable)
- `sensitivity`: `PUBLIC_META`
- `supersedes`: `daikin-brc1h/source-validation-current (revisión anterior si existe)`
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`


### 10. `daikin-brc1h/upstream-licensing` — NEW

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/upstream-licensing`
- `fact_type`: `risk`
- `text` (263 bytes UTF-8): El fork conserva atribución a upstream y fuentes de ingeniería inversa, pero la auditoría no encontró licencia explícita del upstream; antes de redistribuir derivados o contribuir código debe aclararse el permiso aplicable. No se debe inventar una licencia.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-19`
- `provenance`: Auditoría de atribución y README del fork
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/blob/1e0da649ae21da28280bfd16d0a349117c950bf5/docs/upstream-contribution-readiness.md) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/blob/1e0da649ae21da28280bfd16d0a349117c950bf5/README.md)
- `action`: `NEW` (solo propuesta documental; no ejecutable)
- `sensitivity`: `PUBLIC_META`
- `supersedes`: NINGUNO; verificar duplicados antes de crear
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`


### 11. `daikin-brc1h/physical-acceptance` — HOLD

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/physical-acceptance`
- `fact_type`: `current_state`
- `text` (257 bytes UTF-8): La aceptación física real debe resumirse solo desde evidencia autorizada en una fuente WORK privada y distinguiendo pruebas de hardware, informe del Owner y tests offline. No atribuir PROD-PASS a CI ni publicar trazas o identificadores de la instalación.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-21`
- `provenance`: GitHub delimita evidencia pública; evidencia operativa privada pendiente de destino
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/9) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/12)
- `action`: `HOLD` (solo propuesta documental; no ejecutable)
- `sensitivity`: `WORK_CONFIDENTIAL_HOLD`
- `supersedes`: `HOLD_DOCUMENT_DESTINATION`
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`


### 12. `daikin-brc1h/obsolete-local-ack-assumption` — SKIP

- `project_key`: `javigines/Daikin-BRC1H-Mac-Web-Control`
- `scope`: `WORK` **propuesto / política y permisos pendientes**
- `memory_key`: `daikin-brc1h/obsolete-local-ack-assumption`
- `fact_type`: `history`
- `text` (190 bytes UTF-8): El tratamiento histórico que consideraba incierta cualquier respuesta SET con objetos TLV ya fue reemplazado por PR #12; no crear un recuerdo independiente sobre ese comportamiento antiguo.
- `as_of`: `2026-10-21`; `valid_from`: `2026-10-19`
- `provenance`: Comparación de PRs #11/#12; historia conservada en dossier
- `sources`: [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/11) · [fuente](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/12)
- `action`: `SKIP` (solo propuesta documental; no ejecutable)
- `sensitivity`: `PUBLIC_META`
- `supersedes`: `daikin-brc1h/power-ack-readback`
- `approval`: `NOT_REQUESTED`; `write_status`: `NOT_WRITTEN`; `server_dedupe`: `NOT_CHECKED`

## Dedupe, privacidad, cobertura y gates

- **Deduplicación editorial:** el objetivo, arquitectura, validación BLE, control manual, ACK/readback, recovery, políticas, seguridad local, hito/versionado y riesgo de licencia cubren conceptos distintos. La interpretación antigua del ACK es **SKIP** por subsunción en `power-ack-readback`. No crear memorias redundantes para cada PR/test/commit.
- **Deduplicación real del servidor: UNKNOWN:** **no se consultó Memory OS** (ni PERSONAL ni WORK) en este encargo. Se requiere grant WORK autorizado y cotejo de `memory_key`, fuente, contenido, revisiones y equivalentes semánticos. Nunca tomar `NEW` como autorización de CREATE.
- **Estado actual mutable:** revisar `source-validation-current` contra `master`/deploy al ejecutar la futura importación; no almacenar “versión actual” sin `as_of` y posibilidad de UPDATE.
- **Fuente privada:** cualquier resumen de pruebas operativas, evidencia física, mapa lógico-físico, incidentes identificables, estado de arranque, conexiones y acceso permanece **HOLD_DOCUMENT_DESTINATION** hasta seleccionar una fuente WORK privada y cumplir política Owner/empresa. La fila `physical-acceptance` no puede convertirse en memory confirmada desde este manifest público.
- **Fuentes y validez:** enlaces apuntan a commits o PRs y workflows públicos verificables; pruebas Chromium y tests con SDK cerrado **no son pruebas físicas**. Las referencias de upstream no avalan todos los firmware.
- **Material excluido:** credenciales, identificadores reales, logs/tramas de equipos concretos, datos de terceros, configuración particular, conversaciones completas, capturas de pantallas reales y documentación de red o localización.
- **Importación futura:** el Coordinador General valida scope/política, dedup y preview exacto; el Owner aprueba un lote finito. Usar herramientas y grants WORK auténticos, STOP ante `PENDING/AMBIGUOUS`, registrar ACK/revisión/readback antes de `WRITTEN=PASS`. Ninguna escritura o captura queda autorizada por este documento.
- **Estado de este PR:** exclusivamente Markdown, `tests:N/A` por instrucción; no tests de documentos, no cambios de runtime/DB/despliegue ni Memory OS.

**Gates:** `PUBLIC_TECH_DOSSIER=PREPARED`; `CURATED_MANIFEST=PREPARED_NOT_WRITTEN`; `WORK_SCOPE=POLICY_HOLD`; `PRIVATE_OPERATIONAL_HANDOFF=HOLD_DOCUMENT_DESTINATION`; `MEMORY_WRITES=0`; `PROJECT_MIGRATION=NOT_COMPLETE`.
