# Validación de iteración 2 · Mac A

Fecha 2026-10-19. **PASS offline / HOLD real**. Base PR1 abierta/draft verificada antes del desarrollo y antes de entrega: `bcb1bf8493881d1ee50b7558d17299346b0fb4ae`, rama `fix/ble-reliability-foundation`. Rama nueva `feature/multiroom-offline`, PR apilada a esa base. Este informe corresponde al diff de la PR; consultar su HEAD para identidad inmutable. No merge/deploy ni cambios en Mac B.

## Evidencia ejecutada

| Comprobación | Resultado |
| --- | --- |
| Base original antes de funcionalidades | 104 PASS / 0 FAIL; originales no editados |
| Suite final Python3.12.15 | 227 PASS / 0 FAIL / 1 warning, 4.27s |
| Suite final Python3.14.8 | 227 PASS / 0 FAIL / 1 warning, 6.34s |
| Ambos selftests Python3.12 | ALL PASSED; código puro, sin BLE |
| Chromium real, integration/browser_demo.py | PASS, 0 errores JavaScript |
| Compilación Python y node --check app.js | PASS |
| Dependencias con uv pip check | 36 paquetes compatibles |
| Venv limpio instalando solo requirements-dev | 33 paquetes resueltos/instalados; 227 PASS / 0 FAIL / 1 warning, 4.15s |

Warning único por ejecución: StarletteDeprecationWarning sobre httpx en TestClient (sugiere httpx2). No se cambió el cliente por una migración ajena al alcance. `pip` no estaba instalado en el venv gestionado de desarrollo; verificación de dependencias ejecutada con `uv pip check`, no se declara pip check exitoso.

Versiones probadas: fastapi0.146.0, uvicorn0.54.0, pydantic2.14.1, bleak3.0.2 (solo tests legacy), pytest9.1.1, httpx0.28.1, starlette1.9.0, Playwright1.64.0. Rangos de requirements no son lock reproducible ni auditoría CVE; futuras resoluciones deben repetir esta validación.

## Escenarios que representan las pruebas

Config0/1/6 y duplicados/inválidos; seis instancias con locks/estados/latencias/históricos aislados; semáforo/deadline incluyendo wait; busy409 y cancelación; fallo sala3 sin impacto en otras; cierre y startup sin huérfanos; SQLite mismo timestamp/reinicio/cursor/importación original con/sin filtro y hash de origen inalterado; identidad/entorno de DB; imports DEMO sin Bleak ni legacy; real/CLIs fallan antes de hardware; Host/Origin/token/body/rate/inputs estrictos.

Políticas: FREE/MONITOR/ENFORCE, cuatro campos independientes, desired nunca derivado de observed, confirmaciones/estabilidad/flapping/repeated timestamp, cooldown/reloj atrás/reinicio, presupuesto persistente sin reset automático, prevención de correcciones infinitas, auto-on OFF default y guarda3lecturas/30s/una vez, mantenimiento/suspensión/vencimiento, STOP errores/cancelación, registros y paginación; SQLite fallido en administración, STOP, recuperación y reset repetidos.

Integración Chromium: servidor CLI DEMO real en loopback/puerto efímero con DB temporal; browser→HTTP→Simulator→SQLite. Seis tarjetas, las cuatro operaciones por sala, tres políticas via formulario, cambios externos, corrección selectiva, eventos/historial, fallo/recuperación aislados; reinicio servidor con navegador abierto y token nuevo; stale configurable5s; nombres y errores hostiles como texto; desktop1440px/móvil390px sin overflow con administración abierta. No usa un backend simulado para sustituir la API: todos los backends funcionales son los simuladores que incluye la aplicación. Dos interceptaciones HTTP prueban fallo/render de frontend y se retiran.

El primer E2E detectó overflow móvil con nombre largo: corregido y repetición completa PASS. Capturas en `artifacts/demo-desktop.png` y `artifacts/demo-mobile.png` no versionadas, contienen los casos hostiles inertes de prueba. Previsualización normal independiente en8000, cerrada después de revisión; puede iniciarse con el comando README.

## Revisión independiente

Auditoría inicial read-only antes de funcionalidades y revisión completa del diff por agente independiente. Detectó ocho P2 del código nuevo/documentación de instalación, todos tratados: frescura, deadline, token, Pydantic, rollback de política, poller al fallar STOP, latch ante segundo fallo y dependencias de venv limpio. Reproducciones inyectadas incorporadas a suite. **Resultado final independiente: PASS offline / HOLD Bluetooth real**; diff completo y documentación revisados, suite independiente227 PASS/0 FAIL/1 warning, diff --check PASS y104 originales intactos. Sin hallazgos materiales abiertos en la nueva ruta DEMO. Ninguna revisión offline levanta HOLD físico.

## Frontera

Mac A: fuente, simuladores, tests, Chromium y SQLite temporales/DEMO. Mac B: **NO MODIFICADO**. HARDWARE_BLE: **NOT_TESTED**. PRODUCTION_CHANGED: **NO**. Sin SSH, scan, pairing, conexión, write BLE, permisos BT, restricciones nativas, instalador, servicios, LaunchAgents, horarios o acciones masivas. El hecho de instalar dependencias Bleak para preservar tests no inicializa hardware.

Antes de uso físico: [gates y autorizaciones de políticas](control-policies.md), [arquitectura](multiroom-architecture.md) y [handoff offline Mac B](mac-b-offline-iteration-2.md). Seguir estado por evidencia; demo satisfactoria no certifica hardware ni producción.
