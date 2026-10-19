# Políticas por controlador y estado deseado

## Semántica

Default `FREE`, sin campos protegidos ni encendido automático. Cada sala mantiene registro persistente distinto de la última observación. Desired solo cambia mediante API local autorizada: los controles ordinarios persisten intención antes de escribir; `/desired` establece intención sin orden inmediata. Una orden fallida conserva la intención autorizada y activa STOP; no se copia lectura ni se inventa éxito. Futuros horarios deberán entrar por el mismo servicio `administer/operate` con autorización de propietario y fuente de auditoría específica; no existe scheduler activo.

| Política | Desviación de un desired explícito | Escrituras automáticas |
| --- | --- | --- |
| FREE | Se registra, se acepta la observación y no se muestra aviso de restricción | Ninguna |
| MONITOR | Se registra y avisa en dashboard/eventos | Ninguna |
| ENFORCE | Se registra y avisa; se consideran solo campos protegidos | Acotadas, únicamente Simulator |

Aceptar en FREE no reemplaza desired. Cambiar política no crea desired ni rearma STOP/presupuesto. `protected` permite seleccionar de forma independiente setpoint/mode_id/fan_id/power_on; un campo sin desired o sin lectura no se corrige. Las desviaciones se registran para todos los campos con intención, incluso no protegidos, con origen `unknown`. No se atribuyen al mando físico, a una persona o a otra app.

## Reconciliación acotada

- Confirmaciones consecutivas del mismo valor observado (2–5, default2); repetir el mismo timestamp no confirma. Cambios de valor o ausencia del campo reinician su candidatura.
- Estabilización monótona 1–300 s, default5. Cooldown persistente 1–600 s, default15. Si reloj civil retrocede no se elude cooldown.
- Un campo por operación, dentro del lock por sala y deadline. Reserva durable de intento **antes** del efecto. Lectura posterior registra correction_observed solo si el valor se observa; no desencadena otra corrección en esa misma operación.
- Máximo total 1–5 intentos por rearme de propietario, default3. No se renueva presupuesto después de un éxito. Al agotarse y reaparecer desviación confirmada se activa STOP persistente; no hay ciclos infinitos con otro controlador. Persisten intentos, último intento, STOP y auto_on_used tras reinicio; confirmaciones se descartan.
- Error, timeout o cancelación activa STOP y error de sala. Recuperar simulador no rearma correcciones. Presupuesto/STOP solo se rearma con acción administrativa y motivo. Si falla SQLite, se revierte administración y se mantiene STOP en memoria; el poller sigue vivo, inactivo hasta recuperación explícita.
- Cambios de desired, política y restricciones invalidan candidaturas. No se relaja automáticamente el control tras error ni se reconecta agresivamente.

Para autoencendido se necesita además `power_on` protegido, desired true autorizado y `allow_auto_on=true` explícito. Mínimos efectivos: tres lecturas, 30 s de estabilización y 60 s entre intentos. Solo un autoencendido por presupuesto/rearme, aunque cambie la política o reinicie proceso. No activarlo por defecto. Apagado automático también exige protección/desired y límites normales. Son salvaguardas de simulación; no prueban seguridad del sistema térmico real.

Mantenimiento inhibe correcciones hasta desactivación explícita; suspensión temporal dura hasta 3600 s y vence usando deadline civil persistente. Ambas pueden combinarse y requieren motivo; vencimiento se audita. Las lecturas y desviaciones continúan; no bloquean órdenes explícitas del propietario. La suspensión no concede nuevo presupuesto. Se puede recuperar comunicación y revisar estado aun con STOP; el rearme es separado.

## Eventos y administración

Eventos por ID de sala: desired_changed, policy_changed, deviation/deviation_cleared, correction_attempt, correction_observed, error, stop_budget_exhausted, restrictions_changed, suspension_expired, budget_reset y cambios de laboratorio. Registro append-only en aplicación; no es un ledger antimanipulación. API/eventos paginados. MONITOR avisa localmente; no envía correo ni mensajes externos. Token DEMO acredita petición local, no identidad de propietario: antes de producción es obligatorio auth/RBAC y trazabilidad de actor real.

## Bloqueo nativo documentado por Daikin

Consulta oficial 2026-10-19. La documentación describe **Function lock** desde Madoka Assistant para familias BRC1H519 y BRC1H52:

- [BRC1H519W/K/S7, 4P513689-1D, 2019.05](https://www.daikin.eu/content/dam/document-library/Installer-reference-guide/ctrl/individual-control-systems/BRC1H519W7,K7,S7_4PEN513689-1D_2019_05_Installer%20and%20user%20reference%20guide_English.pdf), §14.5.28, pp impresas41–42.
- [BRC1H52W/K/S7, 4P813200-1A, 2026.01](https://www.daikin.eu/content/dam/document-library/Installer-reference-guide/ctrl/individual-control-systems/brc1h52k7/BRC1H52K7.BRC1H52S7.BRC1H52W7_Installer.User%20reference%20guide_4PEN813200-1A_English.pdf), Function lock, pp impresas112–114.

Ambas guías enumeran bloqueo de menú, setpoint, fan speed, operation mode y ON/OFF, además de otras funciones y modos individuales. Explican que controles bloqueados muestran bloqueo o desaparecen/tachan en menú; bloquear un modo activo no lo detiene inmediatamente, pero impide seleccionarlo de nuevo después de cambiar. Son capacidades documentadas para variantes de esas guías; no certifican versión/modelo/configuración de los seis mandos de oficina ni compatibilidad con todas sus instalaciones.

Ese bloqueo aplica restricciones en el controlador mediante herramientas oficiales. Nuestra reconciliación detecta una diferencia y puede restaurar intención más tarde en simulación: durante el intervalo el cambio externo existe. No equivale a impedir pulsaciones ni a Function lock. No se han deducido ni implementado comandos BLE de bloqueo, no se han cambiado ajustes de instalador y no se ha conectado Madoka Assistant a hardware. Estado mostrado por API/dashboard: **NOT_APPLIED_NOT_VERIFIED**. No hay botón que prometa activar un bloqueo físico.

## Gates antes de ENFORCE físico

1. Autorización separada del propietario, por mando/UUID/modelo/firmware, campos y valores, ventanas de prueba, responsable presencial, criterios STOP y restauración. Inventario privado y correspondencia sala↔mando comprobados físicamente.
2. Resolver HOLD de CoreBluetooth y validación semántica (size0/unsupported/campos obligatorios, respuesta/ACK frente a lectura real). Primero una sesión read-only de un único mando, supervisada; verificar setup, cancelación, desconexión, sleep/reinicio y cleanup sin reintentos.
3. Autorizar por separado una orden manual conocida dentro de límites admitidos y verificar con lectura y pantalla física, preservando/restaurando valores previos. Confirmar significado de modos, fan, rangos, redondeo y setpoints independientes por variante.
4. Antes de reconciliar: auth/RBAC de propietario, emergencia/STOP manual, coordinación con otras apps, límites térmicos, presupuesto duradero con fallos disco/energía y eventos, mantenimiento/suspensión y pruebas de no pelea. Mantener auto-on prohibido; su evaluación requiere autorización y análisis de riesgo adicionales específicos.
5. Habilitación real necesitaría nuevo código/backend/guard revisados y permiso explícito. Pilotar MONITOR de un mando antes de cualquier ENFORCE; ampliar capacidad gradualmente solo con evidencia nativa por modelo y Mac B. Nunca conectar seis automáticamente como test offline.
6. Si se desea bloqueo nativo, autorización separada y procedimiento oficial Madoka Assistant supervisado; verificar ajuste leído, comportamiento de cada función y restauración. No usar comandos desconocidos ni permisos de instalador implícitos.

Nada de lo anterior se ha ejecutado. Mac B NO MODIFICADO; HARDWARE_BLE NOT_TESTED; PRODUCTION_CHANGED NO.
