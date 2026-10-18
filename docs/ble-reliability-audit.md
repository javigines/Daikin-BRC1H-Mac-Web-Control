# Iteración 1 — auditoría de fiabilidad BLE

Fecha: 2026-10-19 (Europe/Madrid). **PASS offline / HOLD para BLE real.**

## Identidad y frontera

- Fork oficial: <https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control>.
- Base auditada: `origin/master`, `3b3985ee5a6b93ed994a56331d13435986ca2f8a`.
- `origin` fetch/push: `https://github.com/javigines/Daikin-BRC1H-Mac-Web-Control.git`.
- Checkout nuevo, inicialmente limpio; rama `fix/ble-reliability-foundation`.
- Upstream atribuido: <https://github.com/txsl/cool-vibes>.
- Mac A: desarrollo y pruebas offline. Python del sistema sin modificar;
  intérpretes y paquetes de prueba dentro de `.venv/` ignorado por Git.
- Mac B: **NO MODIFICADO**, sin SSH ni acceso directo. Inventario y seis mandos
  fuera del alcance de esta ejecución. Sus resultados previos son aportados por
  el propietario, no pruebas realizadas aquí.
- HARDWARE_BLE: **NOT_TESTED**. PRODUCTION_CHANGED: **NO**. Sin scans, pairing,
  órdenes reales, servidor activo, LaunchAgents, configuración de macOS, merge
  o deploy. Los tests de lifespan lanzan solo pollers simulados o stubs.

## Defectos demostrados y correcciones

| Defecto en la base | Evidencia y resultado |
| --- | --- |
| Reensamblado prematuro | Dividía por 20 aunque cada fragmento reserva un byte de índice. Ejecutando el módulo original offline: longitud declarada 20 emite 19 bytes en fragmento 0; 39/40 emiten 38 en fragmento 1. Ahora divide por 19 y exige tamaño exacto antes de entregar. |
| Índices y fragmentos inválidos aceptados | Se agregaban fragmentos sin comprobar índice, orden, tamaño ni inicio. Ahora rechaza continuaciones huérfanas, duplicadas, saltos, fragmentos cortos/largos y longitud inválida; descarta el mensaje incompleto. Un índice 0 inicia/reinicia el mensaje. |
| Respuestas malformadas aparentaban éxito parcial | `parse_objects` devolvía los objetos previos al valor truncado e ignoraba un byte final sin cabecera. Ahora rechaza longitud inválida y TLV truncados con `ValueError`; mantiene objetos de tamaño cero para respuestas de campos no soportados. |
| Suscripción fallida dejaba cliente reutilizable | `ensure_connected` retornaba si el transporte estaba conectado aunque `start_notify` hubiese fallado. Ahora listo significa conectado y suscrito; cualquier fallo de setup dispara limpieza y detiene la sesión. |
| Desconexión no despertaba peticiones ni invalidaba caché | El callback solo cambiaba un booleano. Ahora falla el Future pendiente, invalida caché/reensamblador y bloquea nuevas operaciones. Ignora callbacks de clientes antiguos. |
| Timeout solo cubría respuesta | Una escritura podía quedar fuera del plazo. Ahora el timeout abarca todas las escrituras y la respuesta; se conserva el error original y se intenta limpiar tras fallo/cancelación. |
| Una respuesta tardía podía satisfacer la petición siguiente | El diccionario usa solo `cmd_id`. Tras un intercambio ambiguo no hay otra petición en esa sesión; respuestas tardías se ignoran y no se reintenta. |
| Limpieza ocultaba fallos | El spike suprimía errores de stop/disconnect y anunciaba cierre limpio. Ahora intenta ambas llamadas, limita cada una, propaga fallos de limpieza si no existe error primario y registra errores secundarios conservando el primario. Nunca certifica seguridad física. |
| Estado obsoleto y carreras de cierre | La revisión independiente reprodujo reconexión cuando `is_connected` cambiaba antes del callback y avance de setup después de solicitar cierre. Corregido con latch para cliente existente no listo y guards después de scan/connect. |
| Apagado podía saltarse recursos | Excepciones del poller podían evitar disconnect/store.close. `lifespan` cancela y espera el poller; bloques `finally` intentan desconectar y cerrar SQLite incluso si otro paso falla. Sustituye `on_event` obsoleto. |
| README no coincidía con temperaturas | Afirmaba preservar el setpoint inactivo. Se documenta el comportamiento real: mismo entero para cooling/heating y bloque completo de campos. No se cambia esa política. |

## Decisiones de ciclo de vida

Se mantiene un solo cliente y un lock por controlador. Las peticiones se
serializan a nivel de intercambio, no a nivel de toda una lectura de estado de
cinco comandos. Una cancelación mientras se espera el lock no toca el enlace y
no perjudica a la petición activa. Una cancelación después de iniciar setup o
intercambio obliga a STOP y libera el lock; futuras operaciones fallan de forma
explícita, sin quedarse bloqueadas. Recuperación: inspección del propietario y
creación deliberada de una sesión nueva/reinicio manual, nunca bucle de restart.

El servidor hace un solo intento de setup: búsqueda con timeout interno de
10 s y límite externo de 12 s, conexión 20 s, suscripción 20 s. Intercambio
por defecto 6 s. El spike conserva hasta 120 s para un único setup, para dejar
tiempo al prompt, pero elimina los reintentos de conexión/suscripción por
comparación de mensajes de error. Aborta en el primer fallo de consulta (5 s).
Un fallo de descubrimiento también queda detenido para revisión.

`ble_lifecycle.py` es un helper pequeño compartido, sin dependencias nuevas.
Ejecuta stop-notify cuando el backend informa conectado y luego disconnect,
también tras connect parcial. Cada llamada tiene timeout de 10 s; la tarea de
limpieza está protegida frente a cancelaciones repetidas del llamador. Un error
de stop-notify no impide intentar disconnect. Fallos de limpieza quedan visibles;
no se borra el cliente tras una limpieza fallida en el servidor.

**Los plazos son límites cooperativos de asyncio.** Si una implementación
ignora la cancelación, `wait_for` puede tardar más. Esto no equivale a un límite
físico ni a garantía de liberación del transporte nativo.

## Tests y entorno

Línea base ejecutada antes de cambios en Mac A, Python 3.14.8: **34 PASS,
0 FAIL, 25 warnings**, ambos selftests PASS, `pip check` PASS. Después se repitió
la base original en un directorio temporal con Python 3.12.15: mismos resultados.

Suite final en Mac A:

| Entorno aislado | Total | PASS | FAIL | Warnings | Selftests |
| --- | ---: | ---: | ---: | ---: | --- |
| CPython 3.14.8, macOS arm64 | 104 | 104 | 0 | 1 | ambos PASS |
| CPython 3.12.15, macOS arm64 | 104 | 104 | 0 | 1 | ambos PASS |

Los 34 tests originales permanecen sin modificaciones. Los nuevos cubren
fronteras de 4–255 bytes, duplicados/secuencias/tamaños inválidos, mensajes
incompletos, TLV truncados, respuesta correcta frente a otro comando, reuse del
cliente, serialización, timeout de escritura/respuesta, fallos de setup,
desconexión con petición pendiente, callbacks antiguos, cierre durante setup,
cancelación en cada fase, waiter cancelado, cleanup fallido/lento/cancelado
repetidamente, spike de un solo intento, setpoints iguales y shutdown con fallo
del poller/disconnect. `tests/conftest.py` impide cargar Bleak real durante la
suite; cada simulación sustituye explícitamente ese stub.

Versiones principales verificadas: Bleak 3.0.2, FastAPI 0.146.0, Uvicorn 0.54.0,
pytest 9.1.1, httpx 0.28.1, Starlette 1.9.0, Pydantic 2.14.1, PyObjC 12.2.2.
[Snapshot completo de Python 3.12](tested-macos-py312.txt). `pip check`/`uv pip
check` PASS. El único warning restante es la deprecación de httpx en TestClient
de Starlette; se conserva el cliente del suite original, sin migración ajena a
BLE ni silenciamiento del warning.

Los requirements mantienen sus mínimos originales (`bleak>=0.22`, etc.), sin
añadir paquetes de runtime ni cambiar de proveedor. El snapshot describe el
entorno probado; no es un lock de todas las plataformas. No se certifica cada
versión admitida por esos rangos ni Python 3.10/3.11/3.13.

Comandos de verificación (cada comando debe terminar correctamente):

```bash
.venv/bin/python -m pytest -q
.venv/bin/python daikin_server.py --selftest
.venv/bin/python brc1h_spike.py --selftest
.venv/bin/python -m pip check
.venv/py312-env/bin/python -m pytest -q
.venv/py312-env/bin/python daikin_server.py --selftest
.venv/py312-env/bin/python brc1h_spike.py --selftest
.venv/bin/uv pip check --python .venv/py312-env/bin/python
git diff --check
```

## Riesgos pendientes, priorizados

1. **ALTO — estado nativo durante cancelación de connect y seguridad del mando.**
   Inspección estática de Bleak 3.0.2 en Mac A: `CentralManagerDelegate.connect`
   cancela la conexión nativa ante `asyncio.TimeoutError`; no tiene el mismo
   tratamiento explícito para `CancelledError`. `BleakClientCoreBluetooth.disconnect`
   retorna si el periférico aún no figura Connected. Puede quedar un intento
   nativo Connecting aunque las llamadas públicas terminen. Es un riesgo de
   backend no reproducido físicamente, no un hecho observado en Mac B. No se
   llama a APIs privadas ni se afirma liberación nativa por pasar mocks.
2. **ALTO — pairing y desconexión específicos de cada BRC1H.** Ni el éxito de
   `stop_notify`/`disconnect` ni el timeout prueban que el mando siga respondiendo.
   Requiere propietario presencial y una prueba autorizada con un solo mando.
   Ninguna estrategia de reconexión automática queda habilitada. Un supervisor
   externo con autorestart podría sortear el latch: no usarlo en validación.
3. **MEDIO — ausencia de transaction id/checksum en el contrato usado.** Se
   compara command id, no identidad de transacción. El latch evita reutilizar
   una respuesta tras timeout/cancelación, pero no distingue un duplicado tardío
   después de una petición ya exitosa de la respuesta de la siguiente petición
   del mismo comando. Una notificación espontánea con el mismo id tampoco puede
   distinguirse. No inventar delays como garantía; hacen falta capturas físicas
   y especificación si se observa ese comportamiento.
4. **MEDIO — supuestos del protocolo y temperaturas.** Se conserva framing de
   fragmentos no finales de 19 bytes y final exacto, sin padding; falta confirmar
   con capturas de los seis modelos/firmwares. Un fragmento 0 reemplaza un
   mensaje incompleto porque no hay identificador de mensaje. El reensamblador
   no tiene timer propio; request/disconnect lo reinician y un mensaje está
   limitado por su longitud de un byte. Se conserva rounding a entero y envío
   igual cooling/heating, incluido bloque de límites a cero. No generalizar
   afirmaciones históricas del upstream a los mandos de oficina.
5. **BAJO — matriz de dependencias abierta.** Las versiones instaladas pasan
   offline; una resolución futura puede cambiar. Conservar versiones/SHA de
   cada validación y revisar antes de usar otro entorno.

## Revisión y siguiente paso

Revisión independiente read-only mediante otro agente: reprodujo las dos
carreras descritas, se corrigieron y se añadieron regresiones; sin BLE, SSH ni
ediciones por el revisor. Revalidación independiente del diff completo,
incluido rechazo de TLV truncados y documentos: PASS en ambos intérpretes y
ambos selftests. También se impide seguir enviando fragmentos tras perder el
enlace durante una escritura multiparte.

No se añade licencia: el árbol base no incluye LICENSE/COPYING/NOTICE y GitHub
devuelve `licenseInfo: null` para upstream. Se conserva atribución. Ausencia de
licencia explícita no concede por sí sola permiso general de redistribución;
aclarar derechos con upstream antes de distribuir derivados.

Siguiente paso: revisión humana de la PR draft y ejecución manual **solo de la
fase offline** del [handoff Mac B](mac-b-validation.md). El GO para una prueba
BLE real es posterior, separado y requiere intervención física disponible.
