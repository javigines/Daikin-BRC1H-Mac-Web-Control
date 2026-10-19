# Mac B · handoff exclusivamente offline de iteración 2

Documento para ejecución humana posterior. **Mac B NO MODIFICADO** durante esta iteración. No SSH, emparejamiento, scan, BLE, servicios macOS ni LaunchAgents. No ejecutar scripts antiguos ni instrucciones históricas de conexión. No reutilizar venv, DB ni checkout operativo.

## Preparación separada

Tras revisión humana de la PR DRAFT, en una terminal de Mac B, sin tocar el directorio existente:

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

Comparar SHA con HEAD de la PR antes de usar resultados. Abrir solo [127.0.0.1:8000](http://127.0.0.1:8000) en el propio Mac. Si puerto está ocupado, elegir otro puerto libre ≥1024; no matar procesos existentes. No usar inventario real ni copiar DB operativa. La instalación de dependencias puede necesitar internet; las pruebas funcionales no necesitan Bluetooth ni servicios externos.

## Criterios

Ver seis salas y rótulo DEMO; cambiar las cuatro funciones independientemente; historial separado; error en sala3 sin afectar a las demás; recuperar manualmente; FREE no corrige, MONITOR avisa sin escribir, ENFORCE solo campos elegidos después de confirmaciones/estabilización. Desired debe permanecer separado de cambios de laboratorio. Probar mantenimiento/suspensión/rearme y reinicio conservando política/desired/budget. auto-on debe seguir OFF salvo prueba deliberada del simulador. Ver [validación](iteration-2-validation.md) para automatización Chromium opcional.

Cerrar con Ctrl+C y esperar shutdown. Conservar resultados por SHA/Python/OS y advertencias. Volver al entorno original implica cerrar únicamente esta demo y dejar el checkout anterior intacto; no abrir su DB con esta versión.

**STOP después de las pruebas offline.** Para cualquier acción física se requiere autorización separada y presencia del operador. Gates detallados en [control-policies.md](control-policies.md). No conectar automáticamente seis mandos ni tratar estos resultados como prueba de CoreBluetooth. No habilitar restricciones nativas ni ajustar parámetros de instalador.
