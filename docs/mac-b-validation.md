# Handoff Mac B — validación posterior, no ejecutada por Codex

Mac A es desarrollo. Mac B es el Mac mini de oficina con seis BRC1H y Python
3.12. Los escaneos, dos selftests y 34 tests previos de Mac B son evidencia
aportada por el propietario, no ejecuciones de esta iteración.

## 1. Preflight local, solo lectura

Ejecutar personalmente en Mac B, sin SSH. No instalar servicios, modificar
permisos de macOS ni detener procesos a ciegas. Revisar los resultados antes de
seguir; si aparece un controlador activo, STOP y acordar cómo cerrarlo.

```bash
cd "$HOME/madoka-test/cool-vibes"
git status --short --branch
git remote -v
command -v python3.12
python3.12 --version
pgrep -fl 'daikin_server|brc1h_spike|debug_setpoint|probe_range' || true
launchctl list | grep -Ei 'daikin|madoka|cool.?vibes' || true
lsof -nP -iTCP -sTCP:LISTEN | grep -E 'Python|python|:80 |:8000 ' || true
ls -l "$HOME/madoka-test/madoka_inventory.json"
```

Revisar también LaunchAgents con nombres distintos y cualquier proceso de
control conocido. La ausencia de coincidencias no certifica ausencia de todos
los clientes BLE; comprobar la sesión gráfica y cerrar la app Madoka del móvil
solo cuando se prepare la prueba autorizada. No imprimir ni subir el inventario.
Si no existe Python 3.12, STOP: resolverlo con el propietario sin cambiar el
Python o venv existentes.

## 2. Obtener el cambio sin alterar el checkout ni el venv existentes

Verificar que `origin` es el fork oficial antes de usar estos comandos. Un
worktree separado evita checkout/reset/stash sobre el entorno anterior. Si el
directorio de destino ya existe, STOP y revisarlo, no borrarlo.

```bash
git fetch origin master fix/ble-reliability-foundation
MADOKA_REVIEW_SHA="$(git rev-parse origin/fix/ble-reliability-foundation)"
git log -1 --format=fuller "$MADOKA_REVIEW_SHA"
git diff --stat origin/master..."$MADOKA_REVIEW_SHA"
# Comparar MADOKA_REVIEW_SHA con el SHA exacto de la PR y el informe final.
# STOP si no coincide; no aceptar una rama que haya cambiado sin revisión.
git worktree add --detach "$HOME/madoka-test/cool-vibes-ble-iteration1" "$MADOKA_REVIEW_SHA"
cd "$HOME/madoka-test/cool-vibes-ble-iteration1"
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python --version
.venv/bin/python -m pip freeze
git rev-parse HEAD
```

Guardar las versiones y el SHA junto con los resultados. Los requirements
originales tienen límites inferiores, no un lock: una instalación futura puede
resolver versiones distintas. No actualizar el venv anterior, copiar `.env`,
inventario, bases de datos ni LaunchAgents a este worktree.

## 3. Pruebas offline, permitidas sin contacto BLE

```bash
.venv/bin/python -m pytest -q
.venv/bin/python daikin_server.py --selftest
.venv/bin/python brc1h_spike.py --selftest
pgrep -fl 'daikin_server|brc1h_spike|debug_setpoint|probe_range' || true
launchctl list | grep -Ei 'daikin|madoka|cool.?vibes' || true
git status --short
```

La suite sustituye `bleak` por un backend prohibido por defecto; los tests que
lo necesitan inyectan clientes simulados. Ambos selftests terminan antes de
crear el servidor, escanear o conectar. Comparar preflight y postflight y
devolver resultados, warnings, versiones y SHA. No ejecutar los scripts sin
`--selftest` en esta fase. **STOP aquí.**

## 4. Primera prueba BLE, bloqueada hasta nueva autorización

Requisitos previos: autorización explícita posterior para **un solo** UUID del
inventario local y una persona físicamente junto al mando con capacidad de
intervención. Identificar el mando por inventario y ubicación, no solo RSSI.
Registrar hora, modelo, firmware disponible, estado visible y bond previo.
Todos los demás clientes deben estar desconectados; sin servidor web, poller,
LaunchAgent, scripts de escritura ni bucles de reinicio activos.

Tras esa autorización, desde una sesión gráfica visible en Mac B y desde el
worktree revisado, el propietario sustituirá el valor por el UUID aprobado:

```bash
MADOKA_APPROVED_UUID='REEMPLAZAR_POR_UUID_APROBADO'
# Solo después del GO posterior y con el UUID comprobado:
.venv/bin/python -u brc1h_spike.py --address "$MADOKA_APPROVED_UUID" --timeout 8
```

El spike hace un escaneo previo y un único intento de conexión/suscripción,
con hasta 120 s para el conjunto del establecimiento (incluido el prompt de
emparejamiento). Solo consulta potencia, modo, setpoint y sensor. Cada intercambio
tiene un límite de 5 s; la limpieza intenta stop-notify y disconnect con 10 s
por llamada. Estos límites dependen de que el backend coopere con la cancelación.
No reintenta automáticamente. En Bleak 3.0.2, cancelar durante el estado nativo Connecting puede dejar un
intento pendiente aunque disconnect público retorne: STOP, sin reintentos y con
verificación presencial. No confundir una lectura con ausencia total de
riesgo: conectar, emparejar y desconectar afectan al estado BLE.

**STOP** ante UUID ambiguo, prompt inesperado, error/PIN de pairing, timeout,
fallo de conexión/suscripción/lectura/limpieza, caída del enlace, cambio inesperado
del mando o pérdida de respuesta física. No aceptar prompts de otro dispositivo.
No repetir la prueba, forzar reconexiones, desemparejar, cambiar permisos ni
reiniciar alimentación automáticamente. Si necesita abortar, un solo Ctrl+C y
esperar el resultado de limpieza; no repetir Ctrl+C, cerrar Terminal o usar
`kill -9`. Si se bloquea, mantener STOP y decidir presencialmente la intervención.

Tras la salida, guardar stdout/stderr y código de salida, verificar físicamente
que el mando sigue respondiendo a sus controles locales y observarlo sin abrir
otra conexión. `BLE cleanup calls completed; hardware state unverified` solo
informa de llamadas terminadas. No certifica desconexión segura para el BRC1H.
No probar los otros cinco mandos hasta revisar esta evidencia.

## 5. Temperaturas: otra autorización posterior

La primera prueba no escribe. Para una futura prueba de `set_setpoint`, acordar
un rango inocuo y una forma manual de restauración, registrar valores anteriores
y posteriores de cooling/heating, modo, límites y pantalla. Verificar si el
modelo admite setpoints independientes, si exige valores iguales, cómo redondea
medios grados y si el bloque de límites a cero mantiene los límites efectivos.
El código conserva el envío del mismo entero para ambos setpoints. No ejecutar
`probe_range.sh`, `debug_setpoint.sh` ni el servidor como parte de este handoff.
