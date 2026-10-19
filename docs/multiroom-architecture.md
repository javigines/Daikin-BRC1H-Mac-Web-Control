# Arquitectura multiroom offline

## Frontera de ejecución

`multiroom_app.py` requiere `--demo`. `--real` y ausencia de modo fallan antes de DB/backend. El grafo nuevo es CLI → API → RoomManager → Simulator; solo importa helpers puros de `madoka_protocol.py`. No importa Bleak, `daikin_server` ni `brc1h_spike`. Una prueba con import hook prohíbe esos imports. No existe fábrica de backend real. La corrección automática además exige `type(backend) is Simulator`; sustituir un backend exige un cambio de código revisado, no un flag. Python 3.12+ por `asyncio.timeout`.

## Configuración e identidad

`config.example.json` contiene seis UUID ficticios, IDs estables y nombres editables. JSON estricto, extras rechazados, máximo 64 KiB. IDs `[a-z][a-z0-9-]{0,31}`, UUID CoreBluetooth normalizado, nombres imprimibles y sin espacios extremos. Cero, uno o seis dispositivos; IDs/UUID repetidos rechazados. `enabled`, campos consultados, intervalo 2–300 s, deadline 0.1–30 s por operación. `max_active_devices` exige autorización explícita en configuración para más de uno (default 1); `max_concurrent_operations` default 1, DEMO 2; escalonado default 0.25 s. No se rota silenciosamente una lista que exceda el máximo activo.

Los UUID no se devuelven por HTTP. SQLite vincula cada ID a un hash del UUID e impide reasignarlo a otro dispositivo. Cambiar nombre en configuración actualiza nombre al reiniciar. Usar `config.local.json`/`inventory.local.json` excluidos de Git para futuros datos privados. No se han recogido direcciones reales.

## Coordinación

Cada sala tiene backend, lock, snapshot, error, estadísticas, motor de política y muestras propios. El lock cubre orden + lectura y reconciliación; otra operación sobre esa sala recibe 409, sin cola ilimitada. Un semáforo acota operaciones entre salas; el deadline incluye la espera del semáforo. Máximo seis tareas de salas esperando. Pollers escalonados y fallos independientes. Timeout/cancelación/error enclavan la sala hasta recuperación DEMO explícita. El STOP de política sigue activo después de recuperar comunicación y requiere rearme separado con motivo.

Snapshots copiados; frescura basada en reloj monótono, lectura real de aplicación y umbral configurable. No se marca fresca una muestra al servir GET. Información de modelo/firmware está etiquetada SIMULADA. El cierre cancela y recoge pollers/operaciones y cierra todos los backends y SQLite. Un lock `flock` por archivo DB impide dos procesos cooperantes con la misma DB; no prueba exclusión de otros programas ni de dispositivos físicos.

## API

Prefijo `P=/api/controllers/{id}`. IDs lógicos obligatorios, inexistente 404, API antigua 410. Lecturas no generan operaciones de backend por cliente.

| Método/ruta | Payload o efecto |
| --- | --- |
| GET /api/session | Modo DEMO, hardware false, token local efímero |
| GET /api/controllers | Seis snapshots independientes |
| GET P | Estado, frescura, errores, stats y protección |
| POST P/power | `{"on":true}` |
| POST P/setpoint | `{"temp":23}`; 16–32 °C, entero redondeado con helper compartido |
| POST P/mode | `{"mode":3}`; entero 0–5 |
| POST P/fan | `{"speed":3}`; entero 1–5 |
| GET P/history | hours >0 y ≤744, limit 1–1000, before_id positivo |
| POST P/simulation | status online/disconnected/error, delay_ms 0–2000 |
| POST P/external-change | Campos de DesiredPatch; cambia simulador sin alterar deseado |
| POST P/policy | PolicySpec completo (reemplazo con defaults explícitos) |
| POST P/desired | Patch autorizado no vacío, sin escritura inmediata |
| POST P/restrictions | maintenance, suspend_seconds 0–3600, reason 3–160 |
| POST P/reset-budget | reason obligatorio; rearme explícito |
| GET P/events | limit y before_id con cursor exclusivo |

Ver detalles de política en [control-policies.md](control-policies.md). Toda mutación exige JSON y `X-Demo-Token` de la sesión actual; Origin si existe debe coincidir exactamente con http+Host. Hosts solo localhost/127.0.0.1. Sin CORS permisivo, sin confianza en headers proxy. Body ≤16 KiB antes de parsear, rate global 600/min, uvicorn concurrencia 32. Tipos estrictos, no bool como int, no strings como números, sin NaN/Inf/extras. 422 validación, 409 ocupado/no fresco, 503 fallo; sin valores privados en errores. Una mutación perdida no se reenvía automáticamente.

Token = CSRF local, no identidad/RBAC. Loopback no protege frente a otro proceso/usuario local. No publicar ni usar en producción. CSP self y render textContent; GET cache no-store. La UI obtiene token nuevo al sondear tras un reinicio sin repetir escrituras anteriores. Umbral stale de servidor se aplica también en navegador si HTTP deja de actualizar.

## SQLite

Schema `user_version=2`, tablas nuevas y transacciones cortas síncronas en el event loop (una sola instancia/worker). `app_meta` separa demo/real: nunca abrir una DB real como DEMO. `rooms` identidad/nombre/binding; `room_samples` sample_id autoincremental + controller_id + captured_at_ms + JSON, índice por sala/tiempo; INSERT sin reemplazo, admite seis muestras en el mismo milisegundo. `room_info` conserva info legacy importada. `room_observed` última observación persistente; `room_control` estado deseado/política/presupuesto; `control_events` eventos append-only. No se rehidrata una observación antigua como lectura fresca al reiniciar.

Registro de control y evento se guardan en una sola transacción. Si el guardado falla se revierte memoria al último registro confirmado; un STOP se conserva además en memoria aunque disco falle. La operación de sala falla cerrada. No hay garantía de persistir un evento si disco está roto: restaurar almacenamiento y revisar antes de rearme. Historial y control son transacciones separadas; fallo entre ambas puede conservar una intención sin muestra nueva; presupuesto reservado no se devuelve automáticamente.

Paginación por IDs exclusivos, nunca por timestamps ambiguos. No hay eliminación ni retención automática; crecimiento indefinido es un riesgo operativo pendiente. Antes de producción: cuota, retención autorizada, backup SQLite, permisos del directorio y prueba de restauración. Consultas/salidas acotadas no equivalen a cuota de disco.

## Migración segura desde single-room

Exclusivamente archivos, sin conexión a mandos:

```bash
python multiroom_app.py --migrate-history /ruta/copia-original.db --output /ruta/nueva-multiroom.db --legacy-controller-id sala-original
```

No usar el archivo operativo como destino. Origen abierto read-only por URI SQLite y backup a destino creado con exclusión (`xb`); destino existente se rechaza. Transacción valida esquema y copia cada fila con identidad explícita. Tabla original `samples` y `device_info` quedan preservadas; los datos previos con/sin filter_dirty se conservan, incluyendo campos originales en JSON. Importación idempotente con metadato legacy_id, no reasignable. Colisiones que la DB antigua ya perdió por REPLACE no se pueden recuperar. Una copia fallida se conserva para inspección y no se sobreescribe.

La copia se etiqueta real y no puede arrancar en DEMO. Los campos legacy conservan sus nombres (`power`, `mode`, `fan`); la futura lectura normalizada/visualización real necesita una adaptación explícita. No se pretende que la demo interprete datos físicos importados. Tests verifican integridad, hash del origen, reinicios, nombres previos y preservación de tablas. Para rollback conservar siempre origen intacto, cerrar proceso y volver a la versión/DB anterior; nunca abrir schema2 desde la aplicación antigua.

## Producción pendiente

Auth de propietario y permisos por sala/acción; transporte seguro si se habilita red; exclusión por UUID físico y detección de otros controladores; límites/rangos reales por firmware; esquema/campos required/unsupported; evidencia de desconexión CoreBluetooth; tiempos no cooperativos y cleanup nativo; observabilidad/retención; despliegue/reversión revisados. Sin auto-reconnect, horarios ni acciones masivas en esta iteración. La capacidad BLE de seis conexiones simultáneas sigue NO VERIFICADA.
