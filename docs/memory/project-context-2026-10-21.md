# Contexto histórico técnico — Daikin BRC1H Mac Web Control

> **Corte:** 2026-10-21 (Europe/Madrid). **Clasificación de ESTE archivo:** documentación técnica general publicable. **Proyecto:** `javigines/Daikin-BRC1H-Mac-Web-Control`. **Scope para Memory OS:** `WORK` **PROPUESTO, PENDIENTE de autorización/política corporativa**. **Estado de migración:** `PREPARED_NOT_WRITTEN`; `MEMORY_WRITES=0`.
>
> **Frontera editorial:** este repositorio es **PÚBLICO**. No copiar aquí inventario real, identificadores MAC/CoreBluetooth, correspondencias dispositivo-sala, nombres o ubicación de instalaciones, redes, infraestructura, credenciales, logs de operación, datos identificables de terceros ni transcripciones privadas. La documentación operativa que requiera esos datos queda en **`HOLD_DOCUMENT_DESTINATION`** hasta identificar y autorizar una fuente WORK privada. Este archivo **no sustituye** ese dossier privado.

## 1. Identidad, objetivo y exclusiones

El proyecto es un controlador web local para **hasta seis controladores lógicos Daikin Madoka BRC1H**, con lecturas y acciones manuales por dispositivo. Busca reducir la dependencia de clientes móviles individuales mediante un proceso macOS con Bluetooth, una interfaz en español, estados separados e historial por controlador. La fuente técnica de verdad es el código/versionado de GitHub; la evidencia operacional individual pertenece a una fuente privada autorizada.

Objetivo cotidiano de la línea **Daikin Local**: arrancar `./run-local.sh`, abrir `http://127.0.0.1:8090` **en el mismo Mac**, consultar temperatura/estado, actuar sobre power, setpoint, modo y ventilador **si el modelo/capacidades lo permiten**, y ver errores comprensibles. `./run-local.sh --demo` es un camino separado sin Bluetooth real.

**No objetivos / funciones no autorizadas como equivalentes:** control por LAN/Internet, servicio de autoarranque del modo Local, programaciones, envío masivo de órdenes, escritura de ajustes de instalador, bloqueo nativo del mando, reconciliación física automática, `ENFORCE` físico ni autoencendido físico. Los modos históricos de servicio autenticado y canary siguen en el repositorio, pero no son requisitos del arranque Local. Véase [README](../../README.md), [guía Local](../local.md) y [políticas](../control-policies.md).

## 2. Arquitectura efectiva y límites de confianza

- **Servidor y UI:** Python 3.12+, FastAPI/ASGI y frontend HTML/CSS/JavaScript; tarjetas de sala independientes, diálogos de detalles e históricos, estado de conexión, antigüedad de lectura y resultado de la última operación. Chromium se ejercita con suites de escritorio y móvil en CI.
- **Backend nativo:** Bleak **3.0.2** sobre CoreBluetooth en macOS; `NativeRadio` + `HardenedControllerBridge` + `OfficeBackend`/`LocalFleet`. Las asociaciones `room_id`↔identidad se validan desde **configuración privada fuera del repositorio**, sin publicar las identidades en la API.
- **Protocolo BLE:** comandos GET para power `0x0020`, modo `0x0030`, setpoint `0x0040` y sensor `0x0110`, con fan GET `0x0050` si procede; SET manual power `0x4020`, mode `0x4030`, setpoint `0x4040`, fan `0x4050`. Reensamblado de fragmentos de **19 bytes útiles** y validación estricta de frames/TLV. Es protocolo inferido por implementación y proyectos de referencia, **no certificación oficial de firmware**. [Fundación BLE, PR #1](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/1); [arquitectura nativa](../office-native-architecture.md).
- **Coordinación:** `RoomManager` mantiene sala, lock, backend, caché de observaciones, estadísticas y política propios. Un semáforo limita operaciones compartidas; preparación/conexión y operaciones de equipos listos tienen ventanas separadas. Los pollers son escalonados y un fallo aislado no debe lanzar comandos en otras salas.
- **Persistencia:** SQLite separa muestras/estado observado/desired-eventos de política del **ledger durable de sesiones y operaciones físicas**. Identidades privadas se vinculan a los IDs mediante digest, no por posiciones en listas. Una operación de escritura conserva baseline, idempotencia por `operation_id`, outcome y diagnóstico por etapas; se conservan registros históricos y migraciones aditivas.
- **Frontera HTTP:** solo `127.0.0.1:8090` en Local, sin credenciales web/TLS por diseño local; exactitud de peer, Host/Origin, CSRF, JSON/body limits, CSP y rechazo de endpoints legacy/simulados de escritura. **Loopback no protege frente a otros procesos con acceso al mismo usuario de macOS.** No se debe exponer ese listener por proxy. [Local](../local.md), [arquitectura](../multiroom-architecture.md).
- **Simulador:** seis modelos cerrados para pruebas y demo; los tests importan mocks y bloquean Bleak/CoreBluetooth reales. Una suite verde no demuestra una acción física.

## 3. Semántica de control y fiabilidad

1. **Lectura:** los campos esenciales se validan contra esquemas conocidos y lecturas correlacionadas. Valor ausente, flags desconocidos, rangos no verificables o respuesta malformada deshabilitan el control afectado. Fan es opcional; su fallo no debe ocultar todas las demás lecturas.
2. **SET manual:** un clic crea identidad durable; antes de enviar se hace GET de referencia y se registra el intento. Cada operación admite **como máximo un SET lógico** al controlador seleccionado; un mismo ID no se reproduce. Temperaturas se validan frente a ventana informada por dispositivo y límite local; se conservan bytes de campos inactivos/flags/limitaciones.
3. **ACK no equivale a efecto:** desde [PR #12](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/12), ACK vacío conocido o respuesta TLV power/mode de forma válida permite **pasar a GET de confirmación**, pero no certifica éxito. Un power SET puede necesitar hasta tres lecturas GET_POWER exitosas y correlacionadas para estabilizarse; después se verifica el resto del estado esperado. Tramas, valores o objetos desconocidos siguen fallando cerrados.
4. **Incertidumbre:** timeout, cancelación, error de transporte, fallo de decodificación o readback discordante **no producen reenvío ni restauración automática**. El resultado queda `uncertain`; `Consultar estado` ejecuta GET sin convertir retrospectivamente la orden anterior en confirmada. Un efecto no confirmado, pero con enlace nativo saludable, ya no se confunde automáticamente con desconexión.
5. **Plazos:** PR #12 desacopla la espera de cola local (hasta 30 s) de la ejecución manual (hasta 90 s), con allowance del navegador de 150 s. Lectura GET, SET, readback y limpieza tienen límites distintos. Las cifras son límites de implementación, no SLA físico.
6. **Recuperación:** reintentos acotados con backoff por sala; recuperación de sesiones antiguas solo cuando identidad, exclusión del proceso y ausencia nativa estable se corroboran. Una cuarentena por cierre no demuestra una caída radio simultánea; una desconexión pública sin evidencia CoreBluetooth tampoco acredita liberación global. Nunca borrar SQLite para esquivar incertidumbre.
7. **Visualización:** distinguir enlace BLE conocido, frescura de observación y resultado manual. La UI puede indicar `Conectado · lectura pendiente` y deshabilitar controles hasta datos frescos, sin afirmar desconexión de radio por un HTTP 409/error.

Fuentes primarias: [RCA y cambios PR #12](../local-power-incident.md), [guía Local](../local.md), [PR #11](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/11).

## 4. Políticas FREE / MONITOR / ENFORCE

El motor guarda **desired separado de observed**, y un GET nunca reemplaza automáticamente la intención. `FREE` registra sin corrección; `MONITOR` puede mostrar desviaciones/eventos locales; `ENFORCE` admite reconciliación acotada **solo para `Simulator`**, con confirmaciones, estabilización, cooldown, presupuesto durable y STOP. Mantenimiento, suspensión y rearme explícitos permanecen en el contrato de simulación. Para Bluetooth real, `ENFORCE`, bloqueos nativos, corrección periódica y autoencendido **están desactivados**; no prometerlos por aparecer en la interfaz o en documentación heredada. [Políticas](../control-policies.md), [PR #2](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/2), [PR #10](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/10).

## 5. Cronología verificable de código (hitos, no todos ellos merges)

| Fecha (2026) | Hito / iteración | Evidencia y límite |
| --- | --- | --- |
| 18–19 oct. | Fundación BLE PR #1: framing 19B, ciclo de vida y STOP; 104 tests offline | [#1](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/1). PR histórica cerrada, **no mergeada individualmente**. |
| 19 oct. | Multiroom + políticas PR #2: seis modelos simulados, SQLite, desired/observed, FREE/MONITOR/ENFORCE; 227 tests offline | [#2](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/2). Integración posterior; no certificar hardware. |
| 19 oct. | UI sencilla #3, adaptador estricto cerrado #4, normalización documental #5, integración/lifecycle #6; 292 tests en candidato integrado | [#3](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/3), [#4](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/4), [#5](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/5), [#6](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/6). PRs intermedias cerradas sin merge directo. |
| 19 oct. | Canary GET de una identidad, reserva/propiedad y preparación multiroom #7/#8. Existe reporte de **canary físico de solo lectura anterior**, no prueba de escrituras ni de seis dispositivos | [#7](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/7), [#8](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/8), [#9](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/9). Fuente de observación física: declaración en PR #9; detalles privados no reproducidos aquí. |
| 19 oct. | Integración nativa PR #9, squash a `02fa53c7773e4a551158165cab5f246a0d19b03a`; 464 tests y 3 suites Chromium offline | [#9](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/9). La suite no operó hardware. |
| 19 oct. | Seis sesiones nativas y operaciones condicionadas PR #10, squash `e6822f126b3e61c36ec0665a1b68fc6af54422b9`; 531 tests/version y 4 suites Chromium, CI master PASS | [#10](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/10), [CI](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/actions/runs/39283620586). Arranque supervisado antiguo. |
| 19 oct. | Simplificación **Daikin Local** PR #11, squash `76c65ef4ddd51ba39f362d67caa59f4c4359ae03`; 579 tests/version y 5 suites Chromium | [#11](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/11), [CI](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/actions/runs/39298797529). Local sin autenticación temporal. |
| 19 oct. | Corrección de ACK, GET de confirmación, plazos, UI y diagnósticos PR #12; squash **`1e0da649ae21da28280bfd16d0a349117c950bf5`**, tree `b85b8f74539601ebf6330f7aa3d90805f1086b0d` | [#12](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/12), [CI](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/actions/runs/39315164895): **603 tests/version y cinco suites Chromium PASS**. Fuente más reciente verificada al corte. |

**Reconciliación:** la nota histórica de un procedimiento que decía “no instalado/mergeado” describe su PR en el momento de redactarse, no el estado actual de `master`. Los SHAs de ramas fuente y de commits squash no son intercambiables. El HEAD de `master` al corte es el squash de PR #12; no se localizó una GitHub Release publicada. Verificar de nuevo GitHub antes de cualquier decisión de despliegue.

## 6. Incidente técnico y corrección final

Una experiencia física comunicada por el Owner motivó [PR #12](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/12). **Los detalles de la instalación y eventos reales no se reproducen en GitHub público por política de datos.** El código anterior tenía defectos **reproducidos offline**: rechazo prematuro de ACK TLV válidos para fase GET; plazos de cola/operación incompatibles con intercambios secuenciales; semántica de incertidumbre confundida con pérdida de enlace; y UI que trataba errores HTTP como desconexión. PR #12 añadió esquema de ACK estricto pero compatible para power/mode, lecturas de asentamiento power, allowance independiente, diagnósticos por etapa y etiquetado de cuarentenas por cierre. Conservó **una sola escritura por intento**, ni éxito inventado ni repetición.

**Límite de atribución:** no hay capturas de ACK/tramas BLE originales suficientes para demostrar cuál de esos defectos causó cada evento histórico. Los casos offline prueban la clase de fallo, no la causa exacta del episodio físico ni compatibilidad universal de firmware. [RCA técnico público](../local-power-incident.md).

## 7. Estado de corte y matriz de evidencia

| Subárea | Estado al 2026-10-21 | Evidencia / precisión |
| --- | --- | --- |
| Código fuente `master` / PR #12 | **PASS verificado GitHub** | SHA `1e0da649…`; merge confirmado y tree equivalente al candidato. |
| Python 3.12/3.14, BLE cerrado | **SYNTHETIC-PASS** | 603 tests por versión en [run 39315164895](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/actions/runs/39315164895). |
| Chromium desktop/mobile | **SYNTHETIC-PASS** | Cinco suites, cero errores JS en el run citado; no usan controladores físicos. |
| Canary real de solo lectura anterior | **OWNER-REPORTED / alcance limitado** | PR #9 menciona evidencia de una sesión atendida; no prueba seis escrituras. |
| Control real, firmware y seis links persistentes | **PRIVATE_EVIDENCE_HOLD** | El repositorio público no puede acreditar el estado de campo; no trasladar evidencia operativa aquí. |
| Web local `127.0.0.1:8090` foreground | **IMPLEMENTED** | `./run-local.sh`, documentación de uso y tests de HTTP. |
| Arranque automático de modo Local | **NOT_IMPLEMENTED/NOT_ACCEPTED** | App de primer plano; plantillas legacy de LaunchAgent no equivalen a servicio instalado. |
| Acceso desde LAN, otros clientes o Internet | **NOT_IMPLEMENTED en Local** | Bind loopback por decisión de seguridad. |
| ENFORCE físico, schedules, auto-on, locks nativos | **DISABLED** | Contratos de código; funciones de simulación no trasladables a hardware. |
| Licencia upstream / redistribución | **UNKNOWN / RISK** | Atribución conservada; referencia upstream sin licencia explícita en la auditoría. |

## 8. Riesgos, restricciones y reversibilidad

- **Seguridad física y lógica:** no probar `SET` a ciegas, no inferir correspondencias sala-dispositivo, no reintentar comandos inciertos; conservar locks, idempotencia, GET readback y STOP por sala.
- **Protocolo externo:** la semántica de ACK, flags, fan/mode y liberación CoreBluetooth puede variar entre versiones; el código no valida por sí solo todas las consecuencias del equipo físico.
- **Conexiones y cierre:** `quarantined` denota propiedad sin evidencia suficiente, no necesariamente seis desconexiones simultáneas. No resetear ledger para hacer “verde” un estado.
- **Localhost como frontera:** sin autenticación de usuarios locales, no exponer listener ni copiar configuración a un host ajeno. Acceso remoto y autoarranque son **mejoras futuras**, no funcionalidades ya desplegadas por este código.
- **Datos:** historial y configuraciones reales son privados; este repositorio solo preserva patrones de diseño y referencias. No incorporar identificadores ni logs privados a PR/manifest público.
- **Licencias:** resolver derechos de upstream antes de redistribuir/autorizar contribución. El proyecto no añade una licencia por inferencia.
- **Reversibilidad documental:** esta PR es documentación DRAFT, sin código, migraciones de BD, despliegue ni cambios de control; se puede cerrar sin efecto runtime. Los datos operacionales existentes no se tocan.

## 9. Backlog ordenado; permisos actuales

1. **P0 documental:** identificar destino WORK privado y obtener política/GO de clasificación antes de consolidar aceptación operacional o importar candidatos en Memory OS. `HOLD_DOCUMENT_DESTINATION`.
2. **P0 curación:** coordinador general/Owner valida textos/scope; deduplicación exacta y semántica contra memorias WORK solo con grants autorizados. No ejecutar escrituras en esta fase.
3. **P1 técnica opcional:** si se continúa evolucionando el hardware, recopilar diagnóstico reproducible **en la fuente privada autorizada** y distinguir evidencia de pruebas simuladas; ninguna ejecución real está autorizada por este documento.
4. **P2 producto, no autorizado aquí:** evaluar arranque automático en sesión macOS y acceso LAN con modelo de autenticación/red distinto; evitar interpretar estas posibilidades como pendientes ya aprobados para implementación.
5. **P2 legal:** aclarar derechos/licencia upstream antes de distribución adicional.

## 10. Reglas de trabajo para Codex / ChatGPT

- Leer `README.md`, ADR/docs vigentes, PR #12 y HEAD antes de usar recuerdos viejos como estado actual.
- Respetar `project_scope=WORK` **solo una vez autorizado**; el scope escrito aquí no concede permisos MCP. No consultar PERSONAL para “encontrar” datos WORK.
- Una decisión duradera o release justifica **proponer** actualización del manifest; no crear memoria por commit ni copiar chats/logs completos. Ante duplicado, preferir versión/UPDATE y referencia.
- Memory OS es **opt-in**: sin autorización y sin grants, `MEMORY_OS=HOLD`. Toda escritura requiere GO exacto, preview, ACK/recibo y lectura posterior; `PENDING/AMBIGUOUS` congela reintentos.
- No ejecutar Bluetooth, pruebas físicas, cambios de puertos, launch agents, servicios, red, BD ni secretos para una tarea exclusivamente documental.
- Fuentes web/repo no sustituyen pruebas reales; no afirmar `PROD-PASS` a partir de mocks.

## 11. Fuentes verificables y límites de cobertura

- Gobierno Memory OS: [fase 6 #76](https://github.com/javigines/memory-os/issues/76), [playbook PR #77](https://github.com/javigines/memory-os/pull/77).
- Cronología histórica: [PRs #1–#12](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pulls?q=is%3Apr), especialmente [#9](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/9), [#10](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/10), [#11](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/11), [#12](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/pull/12).
- Contratos: [README](../../README.md), [protocolo y arquitectura nativa](../office-native-architecture.md), [BLE reliability audit](../ble-reliability-audit.md), [arquitectura multiroom](../multiroom-architecture.md), [políticas](../control-policies.md), [Local](../local.md), [RCA](../local-power-incident.md), [upstream](../upstream-contribution-readiness.md).
- Evidencia CI exacta: [#9](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/actions/runs/39269149525), [#10](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/actions/runs/39283620586), [#11](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/actions/runs/39298797529), [#12](https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control/actions/runs/39315164895).
- **UNKNOWN:** inventario/identidades y mapping reales, versión firmware de cada equipo, sesiones/logs completos, resultado físico detallado por función, prueba prolongada de estabilidad, evidencia de TCC/native release, estado operativo actual del host, cumplimiento de política WORK y fuente privada destino. **Fuera de cobertura a propósito** por clasificación, no rellenar con supuestos.

## 12. Manifest de memorias

Ver [`curated-candidates-2026-10-21.md`](curated-candidates-2026-10-21.md). Los candidatos son un **índice semántico pequeño**, no resumen que sustituya este dossier ni solicitudes ejecutables de `memory_confirm`. Estado: `NO_WRITTEN`, `NO_APPROVAL`, `DEDUPE_NOT_VERIFIED`, `HOLD_DOCUMENT_DESTINATION` para evidencia privada.

**Document-only:** `tests:N/A`. No pruebas documentales, runtime, prod, datos reales ni escrituras Memory OS en este encargo.
