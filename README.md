# Daikin BRC1H Mac Web Control · multiroom offline

**Iteración 2: PASS offline / HOLD Bluetooth real.** Esta rama solo ejecuta simuladores: seis salas independientes, dashboard, históricos y políticas de protección. No escanea, conecta, empareja ni escribe sobre Bluetooth. No está habilitada para producción.

## Iniciar la DEMO

Python **3.12+** (probado en 3.12.15 y 3.14.8). En una carpeta de desarrollo independiente:

```bash
python3.12 -m venv .venv-demo
.venv-demo/bin/python -m pip install -r requirements-dev.txt
.venv-demo/bin/python multiroom_app.py --demo --config config.example.json --db demo_history.db --port 8000
```

Abrir [http://127.0.0.1:8000](http://127.0.0.1:8000). Finalizar con un solo Ctrl+C y esperar al cierre de la aplicación. El servidor solo admite `127.0.0.1`, puerto no privilegiado y un worker. Sin configuración usa seis UUID ficticios. No colocar el inventario de oficina en el ejemplo versionado.

Cada tarjeta permite temperatura, modo, ventilador, encendido y consulta de su histórico. El laboratorio simula errores, desconexiones, retardos, recuperación y cambios externos. Los controles se deshabilitan ante datos obsoletos o error; otra sala puede seguir funcionando.

En **Administrar protección** se elige `FREE`, `MONITOR` o `ENFORCE`, los campos protegidos y un estado deseado autorizado. La última lectura nunca se convierte automáticamente en estado deseado. `ENFORCE` corrige exclusivamente simuladores con confirmaciones, estabilización, cooldown, presupuesto persistente y STOP. Encendido automático deshabilitado por defecto. Mantenimiento, suspensión temporal, rearme manual y eventos se administran por sala. No se activan bloqueos nativos Daikin.

## Pruebas offline

```bash
.venv-demo/bin/python -m pytest -q
.venv-demo/bin/python daikin_server.py --selftest
.venv-demo/bin/python brc1h_spike.py --selftest
```

Integración opcional con Chromium real, API y SQLite temporales (sin hardware):

```bash
.venv-demo/bin/python -m pip install -r requirements-browser.txt
.venv-demo/bin/python -m playwright install chromium
.venv-demo/bin/python integration/browser_demo.py
```

Resultados y límites: [validación](docs/iteration-2-validation.md). Se conservan sin cambios los 104 tests originales. Capturas locales en `artifacts/`, excluidas de Git.

## Documentación

- [Auditoría integral del repositorio](docs/full-code-audit.md): defectos confirmados, riesgos y tratamientos.
- [Arquitectura, API, configuración y migración SQLite](docs/multiroom-architecture.md).
- [Políticas, salvaguardas y bloqueo nativo documentado por Daikin](docs/control-policies.md).
- [Procedimiento exclusivamente offline para Mac B](docs/mac-b-offline-iteration-2.md).
- [Auditoría BLE anterior](docs/ble-reliability-audit.md) y [handoff físico anterior](docs/mac-b-validation.md): evidencia histórica; no autorizan ejecutar hardware desde esta rama.

## Compatibilidad y seguridad

Los entrypoints reales `daikin_server.py` y `brc1h_spike.py` solo permiten `--selftest`; los helpers de depuración/rangos están bloqueados. `multiroom_app.py --real` falla antes de abrir la DB o importar un backend BLE. La plantilla LaunchAgent no arranca ni reinicia automáticamente y no se ha instalado. No hay horarios ni órdenes masivas activas.

La API antigua `/api/*` devuelve 410: migrar a `/api/controllers/{id}/...`, nunca a posiciones de arrays. El nuevo histórico se separa por identificador, usa una DB distinta y no se debe abrir desde código antiguo. Se ofrece importación explícita a una copia nueva del histórico original; véase arquitectura.

El token efímero local protege mutaciones frente a CSRF; **no autentica usuarios**. Un proceso o usuario local puede obtenerlo. No se debe publicar el puerto ni habilitar BLE hasta implementar autenticación/autorización de propietario, controles operacionales y las pruebas físicas documentadas. Las notificaciones de MONITOR son eventos y avisos en dashboard; no se envían mensajes externos.

## Credits

- Upstream: [txsl/cool-vibes](https://github.com/txsl/cool-vibes).
- BLE protocol reverse-engineering: [Benjamin Lafois — Daikin-Madoka-BRC1H-BLE-Reverse](https://github.com/blafois/Daikin-Madoka-BRC1H-BLE-Reverse).
- Reference implementation: [pymadoka](https://github.com/mduran80/pymadoka).
- BLE library retained for legacy tests: [bleak](https://github.com/hbldh/bleak).

El upstream auditado no declara licencia explícita. Se conservan las atribuciones; este fork no añade licencia ni infiere permisos de redistribución. Aclarar derechos con el autor antes de redistribuir derivados. Las afirmaciones históricas sobre unidades de otros entornos no certifican los seis mandos de esta oficina.
