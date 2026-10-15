# Validación de equipos, contratos HL7 y despliegue

> Auditoría histórica previa a las correcciones. El comportamiento actual y el
> alcance final acordado están en [Configuración de equipos HL7](CONFIGURACION_EQUIPOS_HL7.md).

Fecha: 15 de octubre de 2026. Se revisó el árbol de trabajo local, incluidos los
cambios que ya estaban pendientes. El resultado no certifica el servidor externo.
No se modificaron la aplicación, sus bases actuales ni el despliegue.

## Dictamen

El simulador permite crear y editar equipos y perfiles, y recuperarlos de SQLite
al reiniciar. Se comprobó también un listener creado en ejecución mediante la API
y una orden real por TCP/MLLP con ACK positivo.

No está listo para exponerse directamente a Internet ni para asumir cumplimiento
estricto del contrato. Hay problemas reproducibles de validación, concurrencia,
configuración y trazabilidad. Para un laboratorio externo privado, primero se
deben corregir los bloqueantes y verificar conectividad y restauración de datos
en el servidor de destino.

## Respuestas a las preguntas

| Pregunta | Comportamiento actual |
| --- | --- |
| ¿Puedo agregar equipos? | Sí, desde UI o `POST /api/analyzers`. Deben tener identificador único y pruebas soportadas, salvo perfiles especiales. |
| ¿Puedo modificarlos? | Sí, con UI o `PUT /api/analyzers/{id}`. Enviar la configuración completa: los valores por defecto del modelo reemplazan campos omitidos. |
| ¿Requiere reconstruir la imagen? | No para equipos, contratos o destinos de resultados. Son configuración persistida. |
| ¿Requiere recrear el contenedor? | Sí cuando se necesita publicar un puerto entrante que Docker todavía no publica. No si el puerto ya está publicado. |
| ¿Puedo reservar puertos para evitar recreaciones futuras? | Sí. Se dejó un overlay opcional para `5100–5119`, descrito abajo. La aplicación todavía no restringe ni ofrece un selector de puertos del pool. |
| ¿Cambiar el destino de resultados necesita publicar un puerto? | No: es una conexión saliente. El destino debe escuchar y permitir el tráfico. |
| ¿Los contratos pertenecen a un equipo? | Sí. Se comprobó aislamiento del perfil activo entre dos equipos. |
| ¿Se guardan y pueden editarse? | Sí. Se comprobó recuperación del equipo, perfil editado, orden y mensajes desde una nueva instancia del motor. |
| ¿Se cumplen obligatoriamente? | No. `strictValidation=true` genera advertencias para diferencias estructurales y puede guardar la orden y devolver `AA`. Algunas validaciones técnicas y de negocio sí rechazan con `AE`. |
| ¿La salida respeta el esquema outbound? | Se genera con `outbound.events.*.segments`; el esquema `outbound.schema` no se valida antes del envío. |

## Puertos y recreaciones

En `docker-compose.yml` se publican estos puertos internos:

| Puerto interno | Uso previsto |
| --- | --- |
| 8090 | UI/API |
| 5001, 5002, 5003 | Equipos HL7 documentados |
| 5004 | Equipo ASTM documentado |
| 5010, 5011 | Equipos genéricos HL7/ASTM documentados |
| 5020 | PACS |
| 9500 | AnaPath |

Publicar el puerto no crea un equipo ni abre su listener. En una base vacía el
motor actual crea automáticamente solamente `pacs-simulator`; los demás equipos
del README deben existir en la base o darse de alta. Por tanto, no debe asumirse
que un despliegue nuevo tendrá el catálogo de la instalación actual.

Las variables como `ABBOTT_RUBY_ORDER_PORT` cambian el puerto del **host**:
`15001:5001` significa conectar desde fuera a 15001, manteniendo `listenPort=5001`
dentro de la aplicación. No se debe configurar el equipo con 15001 por ese motivo.
El archivo `docker-compose.anapath.yml` publica solamente 8090 y 9500; no hereda
los demás puertos del Compose principal.

### Pool preparado, todavía sin aplicar

Se añadió `docker-compose.port-pool.yml`, un overlay del Compose principal que
reserva 20 puertos TCP uno a uno: `5100, 5101, …, 5119`. Se puede usar cada puerto
para HL7 o ASTM, con un único equipo por puerto. Los puertos existentes se conservan.

Aplicación inicial, con el `.env` del entorno completo y la imagen ya construida:

```bash
docker compose -f docker-compose.yml -f docker-compose.port-pool.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.port-pool.yml up -d --no-deps analyzer-simulator-api
```

Esto requiere una recreación inicial del servicio. Si aún no existe su imagen,
agregar `--build` a ese primer `up`. Es necesario definir `HOST_MIDDLEWARE_HOST`
para que el Compose principal se pueda interpolar, incluso si se levanta solo
el simulador. Conservar ambos `-f` en despliegues futuros para mantener el pool.

El overlay enlaza el pool a `127.0.0.1` por defecto. Para pruebas desde otra PC,
definir `SIMULATOR_POOL_BIND_IP` en `.env` con la IP privada/VPN del servidor y
permitir TCP 5100–5119 desde los orígenes de prueba en su firewall. La dirección
debe existir en el host. No cambia las publicaciones anteriores del Compose, que
siguen sin dirección de enlace explícita.

Después de aplicar el overlay:

1. Registrar un equipo con `listenPort=5100`, modo `BIDIRECTIONAL`, sus pruebas y
   su destino de resultados. Usar 5101 para el siguiente y así sucesivamente.
2. Crear y activar su perfil de entrada y salida.
3. Consultar `GET /api/analyzers`: revisar `listenerActive` y `listenerError`.
4. Desde Mirth/LIS conectar a `IP_DEL_SERVIDOR:5100` y comprobar el ACK y worklist.
5. Procesar una muestra y verificar el ORU y su ACK en el receptor.

Estas altas no requieren reconstruir ni recrear el contenedor mientras se use un
puerto del pool ya publicado. Actualmente sí reinician todos los listeners dentro
del proceso; eso requiere una corrección si se quiere evitar interrupciones a los
demás equipos. El overlay no implementa reserva lógica, control de duplicados ni
selección de puertos en la UI, y no está aplicado al Compose exclusivo de AnaPath.

Referencia: [puertos de Compose](https://docs.docker.com/reference/compose-file/services/#ports),
[publicación de puertos](https://docs.docker.com/engine/network/port-publishing/),
[recreación mediante compose up](https://docs.docker.com/reference/cli/docker/compose/up/).

## Hallazgos que deben resolverse

### P1 — Contrato estricto que acepta incumplimientos

En `app/engine.py:309–329`, las diferencias pasan a `contract_warnings` y además se
omite `validate_hl7_behavior` si hay contrato estructural estricto. Se reprodujo
con una fecha de nacimiento `19801301`: se guardó y devolvió `MSA|AA` con `ERR` W.
El test existente también espera aceptación con advertencias.

Corrección: distinguir explícitamente modo advertencia y modo rechazo; para modo
estricto devolver `AE/AR` y no crear worklist. No eliminar validaciones de negocio
al activar un contrato. Actualizar la etiqueta de UI y las pruebas según la
política acordada. La inferencia desde un solo mensaje necesita revisión humana
de obligatoriedad, repeticiones, constantes y campos opcionales.

### P1 — API sin autenticación propia

`app/main.py` expone lectura de datos y operaciones de alta, edición, activación,
borrado de contratos y limpieza sin dependencias de autenticación. La prueba
creó un equipo sin credenciales y recibió HTTP 201. El Compose publica la UI/API
sin limitar su dirección de enlace y el MLLP usa TCP sin cifrado propio.

Corrección: acceso por red privada/VPN y autorización para la API; si se expone
mediante proxy, configurar HTTPS y control de acceso allí. Restringir los puertos
MLLP por origen. El control debe cubrir las rutas de datos y escritura, no solo
la pantalla principal. Revisar también la exposición del host CodeIgniter.

### P1 — Puertos inválidos, repetidos o reservados se guardan

`AnalyzerConfigRequest` y `_configured_analyzer` no verifican rango, unicidad ni
reservas. Se aceptaron con HTTP 201 `-1`, `70000`, `8090` y un puerto ya asignado.
La base se actualiza antes de intentar abrir el socket. `ListenerManager.start`
registra algunos fallos de bind y continúa; un puerto fuera de rango puede lanzar
una excepción no capturada por su `except OSError`.

Corrección: validar `0` como listener deshabilitado o `1–65535`, excluir puertos
del servicio, evitar duplicados y restringir al pool en Docker. Aplicar el cambio
solo si el nuevo listener abre, con reversión ante fallo. Devolver su estado real.

### P1 — Duplicados concurrentes y reintentos de mensajes

En `receive_hl7_order`, la comprobación de barcode ocurre antes del bloqueo que
inserta la orden. La tabla `orders` no tiene una restricción única correspondiente.
Con dos recepciones sincronizadas de la misma orden se persistieron dos registros
y ambos recibieron `AA`. Al reenviar secuencialmente un mensaje ya aceptado se
devuelve `AE` por duplicado, lo que complica recuperarse de un ACK perdido.

Corrección: deduplicar por equipo + identidad del mensaje, devolver el ACK previo
al reintento idéntico y proteger la creación con transacción/restricción en BD.
Definir también la identidad de orden, muestra y actualizaciones permitidas.

### P1 — Algunas fallas del perfil cierran la recepción sin ACK

`parse_with_profile` lanza `ValueError` si falta un mapeo requerido, mientras
`receive_hl7_order` solo captura `HL7OrderError`. Se reprodujo un mapeo requerido
`ZZZ-1`: la excepción salió del motor sin construir ACK. Es un fallo del canal,
distinto de un rechazo HL7 controlado.

Corrección: validar el perfil al guardar y convertir los errores esperables de
parseo/mapeo en un ACK negativo registrado. Acotar también tamaño de mensajes,
tiempo de lectura y conexiones simultáneas: los handlers actuales acumulan datos
y esperan conexiones sin esos límites.

### P1 — La salida no tiene validación integral del contrato

`app/profile_builder.py` construye la salida desde eventos y campos, sin validar
`outbound.schema`. Se produjo una salida que incumple el esquema suministrado.
Un perfil activo de solo entrada se puede guardar, pero luego el envío falla
porque no define `RESULT_FINAL`. Asimismo, el writer puede usar `RESULT_FINAL`
como respaldo de un evento que no tiene definición propia.

Corrección: validar estructura y rutas al guardar; verificar eventos y datos
requeridos al activar; validar el mensaje generado contra el contrato saliente
antes del envío. Permitir perfiles de solo entrada de forma explícita, o exigir
salida para equipos que procesan resultados. Revisar separadores y escape de
valores variables. El parser base y el writer aún usan `|` y `^` en varios sitios,
aunque el validador estructural reconoce separadores configurables.

### P2 — Editar el contrato sobrescribe su versión

Crear un nuevo perfil calcula una versión nueva por nombre; editar por `PUT`
conserva ID y versión y sobrescribe contenido. El esquema tampoco vincula cada
orden/mensaje a una revisión inmutable del contrato que se usó. Borrar el perfil
activo elimina la configuración y deja el flujo con las reglas de respaldo.

Corrección: revisiones inmutables con activación explícita, referencias de versión
en órdenes/mensajes y archivo lógico de contratos utilizados. Si se permiten
borrados, advertir y controlar específicamente el del contrato activo.

### P2 — Una edición reinicia todos los listeners

Las rutas de alta y edición de `app/main.py` llaman `listener_manager.restart()`;
este detiene e inicia todos los servidores, aunque solo cambie el nombre o el
destino de resultados. No hay bloqueo que serialice reinicios concurrentes.
La respuesta de configuración contiene la copia anterior al reinicio: se
comprobó `listenerActive=false` en el PUT mientras el GET ya mostraba activo.

Corrección: reconciliar solamente el listener afectado cuando cambia su puerto o
protocolo; serializar cambios y devolver el resultado actualizado. Una conexión
ya atendida por un handler puede continuar: el riesgo confirmado es la pausa de
aceptación de conexiones nuevas, no una desconexión garantizada de todas.

### P2 — Una actualización incompleta aplica valores por defecto

Con `PUT` que envía nombre y pruebas, se comprobó que puerto y destino existentes
se reemplazan por `0`, `localhost:2575`, y el modo pasa a `MANUAL`. Si no se envían
pruebas para un equipo genérico, la actualización puede rechazarse con 400.
La UI actual envía una configuración amplia, pero otros clientes pueden fallar.

Corrección: documentar PUT como reemplazo completo o introducir PATCH y usar
`exclude_unset=True` para modificaciones parciales. Validar campos obligatorios
de forma consistente.

## Persistencia y traslado

La persistencia básica funciona. `SimulatorStore` guarda equipos, órdenes,
mensajes y perfiles en SQLite, utiliza WAL y consultas parametrizadas. La
activación archiva los perfiles activos anteriores dentro de la transacción de
guardado. Los contratos no se comparten automáticamente entre equipos.

El Compose principal monta `./services/analyzer-simulator-api/data:/app/data`.
El perfil exclusivo AnaPath usa `anapath-simulator.db`; el principal usa
`simulator.db`. Copiar solamente código o reconstruir la imagen no migra esas
bases. Mantener el directorio persistente, sus permisos y el archivo correcto.

Antes de mover el servicio, crear una copia consistente mediante la API de backup
de SQLite o detenerlo para copiar. No copiar solo `.db` mientras está activo en
WAL. Restaurar la copia en un entorno aislado y verificar equipos, contratos
activos, órdenes y mensajes. Las escrituras de orden, estado y mensaje son
transacciones separadas: todavía no hay garantía atómica del flujo completo.

Operar por ahora con una sola instancia y un solo worker: el motor mantiene
equipos y órdenes en memoria y cada proceso arranca sus propios sockets. No hay
sincronización entre workers/réplicas. Faltan recuperación explícita de trabajos
interrumpidos, retención de mensajes, healthcheck del servicio y monitoreo de los
listeners; `restart: unless-stopped` no verifica estos últimos.

En Linux, verificar expresamente el destino `host.docker.internal`; el Compose
actual no agrega el mapeo `host-gateway`. Usar DNS/IP alcanzable de Mirth/LIS o
agregar el mapeo cuando el destino sea el propio host Docker. `localhost` desde
el contenedor apunta al mismo contenedor. Confirmar rutas y firewall en ambos
sentidos. El host CodeIgniter usa `network_mode: host` y requiere PostgreSQL
externo/local al host; este Compose no provisiona la base. El Dockerfile del host
usa `php spark serve` y no tiene supervisión independiente de ambos procesos.

## Evidencia y límites

- Suite existente: **18 pruebas aprobadas**, incluyendo contratos y PACS.
- Diagnóstico adicional: **15 comprobaciones**, con 3 capacidades verificadas y
  12 limitaciones reproducidas. Los `ISSUE` describen fallos existentes; el hecho
  de que el script termine correctamente significa que logró reproducirlos.
- Socket TCP real en loopback: alta/edición de listener sin reiniciar Uvicorn,
  orden MLLP sintética y ACK enmarcado positivo.
- Compose principal, AnaPath y overlay del pool: validación sintáctica con
  `config --quiet`. El overlay se comprobó con su enlace local por defecto y una
  IP alternativa de ejemplo; eso no prueba que tal IP exista en el servidor.
  El principal requirió proporcionar la variable obligatoria del servicio legado.
- No se ejecutó un despliegue Docker ni pruebas contra Mirth/LIS, PostgreSQL,
  firewall o servidor externo: el daemon Docker local no fue accesible desde esta
  sesión. No se inspeccionaron datos de pacientes de las bases actuales.
- La revisión funcional se concentró en el simulador; el host CodeIgniter se
  inspeccionó en configuración y persistencia, sin validación integral de PHP/BD.
- No se hizo auditoría de vulnerabilidades de dependencias ni prueba de carga.

Reproducción, desde `services/analyzer-simulator-api`, usando Python 3.12 y un
entorno aislado con `requirements.txt` y `httpx==0.28.1` instalados:

```bash
python -m unittest discover -s tests -v
python ../../scripts/audit_simulator.py
```

El diagnóstico usa datos sintéticos, una BD temporal y sockets exclusivamente en
127.0.0.1. Debe ejecutarse como proceso independiente. La evidencia de su ejecución
está en `docs/resultado_auditoria_simulador.json`.

## Orden de trabajo recomendado

1. Corregir rechazo estricto, validación de puertos y errores sin ACK.
2. Proteger el acceso y resolver duplicados/reintentos de forma transaccional.
3. Validar la salida y hacer los contratos trazables por revisión.
4. Implementar pool visible en UI/API y cambios de listeners por equipo.
5. Probar copia/restauración y el circuito completo en el servidor externo:
   alta, recepción, rechazo, procesamiento, envío, ACK, edición y reinicio.

Hasta completar esos puntos, utilizarlo como laboratorio controlado con datos
sintéticos y acceso privado, sin afirmar que una orden aceptada cumple el
contrato ni que el traslado está libre de problemas.
