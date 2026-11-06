# Analyzer Simulation Lab

Flujo actual: [puerto + contrato de entrada/salida + envío manual](../../docs/CONFIGURACION_EQUIPOS_HL7.md).
Sin perfil activo, el equipo HL7 queda en STANDBY. Un perfil por contrato exige
entrada y salida completas; los modos permisivo y aprendizaje habilitan la recepción
sin ese requisito. Los cambios posteriores
del contrato se aplican al siguiente envío/reenvío manual, conservando revisiones.

El pool **5100–5119/TCP** ya está publicado por defecto. El
[despliegue independiente en servidor](../../docs/DESPLIEGUE_SERVIDOR.md) utiliza
`docker-compose.server.yml`, con persistencia y configuración de red propias.

Simulador web/API para practicar integraciones HL7/ASTM con Mirth, LIS/CRM y
CodeIgniter.

## MVP incluido

- UI web en `http://localhost:8090`.
- Catalogo multi-analizador: CELL-DYN Ruby, XN-1000, ARCHITECT ci4100,
  UC-3500, equipo generico HL7 y equipo generico ASTM.
- Modo manual: crear muestra, seleccionar analizador, seleccionar pruebas,
  cargar muestra, procesar y enviar resultado HL7 ORU por MLLP.
- Modo bidireccional HL7/ASTM:
  - listeners MLLP por analizador HL7.
  - listeners TCP por analizador ASTM.
  - recibe `ORM^O01`, `OML^O21`, `OML^O33` y ordenes ASTM.
  - responde `ACK`/`NACK`.
  - crea ordenes pendientes en la worklist.
  - carga la muestra solo si el barcode escaneado existe en la worklist.
- Estados de equipo: `IDLE`, `WAITING_SAMPLE`, `SAMPLE_LOADED`, `PROCESSING`,
  `RESULT_READY`, `SENDING_RESULT`, `COMPLETED` y `ERROR`.
- Rack visual, barcode y barra de progreso por analizador.
- Mensajes RAW HL7, ACK y errores visibles en UI.
- Persistencia interna SQLite independiente del LIS.
- Configuracion editable desde UI/API:
  - agregar analizador.
  - editar protocolo HL7/ASTM.
  - editar puerto listener.
  - editar destino de resultados host/puerto.
  - editar pruebas soportadas.
  - parametrizar comportamiento HL7 por analizador.
- ASTM modelado para envio TCP simulado con registros `H`, `P`, `O`, `R`, `L`.
- Escenarios internos del equipo: offline, mantenimiento, calibracion, QC,
  reactivo bajo, rack lleno, puerta abierta, resultado critico, error de
  muestra, no respuesta y resultado malformado.
- Seccion PACS liviana: recibe `ORM^O01`, genera una imagen SVG no diagnostica,
  ofrece un visor web y notifica `IMAGE_READY` a Mirth sin simular DICOM.

## Ejecutar local con Python

```powershell
Push-Location services\analyzer-simulator-api
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:RESULT_DESTINATION_HOST='localhost'
$env:RESULT_DESTINATION_PORT='2575'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8090
```

## Ejecutar con Docker Compose

```powershell
Copy-Item .env.example .env
docker compose up --build analyzer-simulator-api
```

Variables:

```env
ANALYZER_SIMULATOR_PORT=8090
RESULT_DESTINATION_HOST=host.docker.internal
RESULT_DESTINATION_PORT=2575
ABBOTT_RUBY_ORDER_PORT=5001
SYSMEX_XN_ORDER_PORT=5002
ARCHITECT_ORDER_PORT=5003
SYSMEX_UC_ASTM_ORDER_PORT=5004
GENERIC_HL7_ORDER_PORT=5010
GENERIC_ASTM_ORDER_PORT=5011
PACS_ORDER_PORT=5020
PACS_NOTIFICATION_HOST=host.docker.internal
PACS_NOTIFICATION_PORT=2575
PACS_LEGACY_KEY_CODE=RIS_LOCAL
PACS_LEGACY_KEY=local-pacs-integration-key-change-me
PACS_LEGACY_IV_HEX=00112233445566778899aabbccddeeff
PACS_LEGACY_USERNAME=ris_local
ORDER_LISTENERS_ENABLED=true
SIMULATOR_DB_PATH=/app/data/simulator.db
```

`RESULT_DESTINATION_HOST` puede apuntar a Mirth Connect o directamente al
listener MLLP del host/CodeIgniter para pruebas iniciales.

La base interna del simulador se guarda en:

```text
services/analyzer-simulator-api/data/simulator.db
```

Esa base pertenece al simulador, no al LIS. Conserva configuracion de
analizadores, worklist, estados, mensajes y resultados aunque reinicies el
servicio.

## Puertos de ordenes

| Puerto | Analizador |
| --- | --- |
| `5001` | CELL-DYN Ruby |
| `5002` | Sysmex XN-1000 |
| `5003` | Abbott ARCHITECT ci4100 |
| `5004` | Sysmex UC-3500 ASTM |
| `5010` | Equipo generico HL7 |
| `5011` | Equipo generico ASTM |

Un sistema externo puede enviar `ORM^O01` u `OML^O33` por MLLP a equipos HL7, o
ordenes ASTM por TCP a equipos ASTM. La orden entra como `BIDIRECTIONAL`, queda
en `WAITING_SAMPLE` y aparece en la UI para cargar barcode/rack y procesar.

## Perfil AnaPath

El catalogo incluye un analizador `anapath-lis-01` con perfil `ANAPATH`.

- Listener de ordenes HL7 por defecto en `9500/tcp`.
- Espera ordenes `OML^O21` desde Mirth/HIS.
- Valida `PID`, `PV1`, `ORC`, `OBR`, codigo de procedimiento y descripcion.
- Responde `ACK^O21` con `MSA|AA|<MSH.10>|HIS-SEQ-xxxxxx` cuando acepta.
- Responde `MSA|AE|<MSH.10>|<motivo>` cuando hay error.
- Si recibe `ORC.1=CA`, cancela la orden previa por `orderId` o barcode.
- Almacena temporalmente paciente, visita, orden, procedimiento, muestra,
  topografia, informacion clinica y HL7 completo.
- Desde la UI puede emitir circuitos salientes:
  - `STATUS_CHANGE`: `OML^O21` con `ORC.1=SC`, `ORC.5=IP`, `OBR.25=IP`.
  - `NEW_SAMPLE`: `OML^O21` con `ORC.1=NW`, `ORC.5=NW`.
  - `DELETE_SAMPLE`: `OML^O21` con `ORC.1=CA`, `ORC.5=CA`.
- Cuando procesa la muestra, envia `ORU^R01` de anatomia patologica:
  - `NORMAL`: `OBX.11=F` informe final con PDF base64 dinamico.
  - `REPORT_SUPPLEMENTARY`: `OBX.11=C` informe suplementario con PDF base64 dinamico.
  - `REPORT_CANCELLED`: `OBX.11=X` con texto `CANCELACION INFORME`.
- Para compatibilidad con el transformer Mirth de AnaPath, en informes con
  PDF `OBX.2=ED` y el Base64 se envia directamente en `OBX.5.1`. El valor
  comienza con `JVBER`.
- El ORU conserva los identificadores recibidos: `ORC-2`, `ORC-3`, `ORC-4`,
  `OBR-2` y `OBR-3`.
- El PDF dinamico incluye datos basicos recibidos: paciente, orden, visita,
  procedimiento, muestra, topografia, informacion clinica y un resultado mock.

## Comportamiento configurable

### Estudios dentro del perfil del equipo

La configuración HL7 se edita en **Perfil del equipo**. Hay una sola definición
por equipo, con entrada, salida, estudios y generadores. **Guardar cambios** guarda
una revisión completa y la aplica al equipo en una sola transacción.

En **Estudios y analitos**, cada fila declara código, nombre, tipo, unidad,
referencia y generador. Se puede desactivar un analito sin eliminarlo. Los grupos
repetibles y escenarios se conservan dentro del mismo perfil. No hay que crear,
guardar o asignar un catálogo aparte.

La pestaña **Recepción** permite validar el contrato, recibir en modo permisivo o
aprender del primer mensaje externo. Los estudios todavía sin generadores pueden
recibirse y quedar en `pendingCatalogStudies`. Al completar sus analitos en este
mismo perfil y guardar, la orden pendiente se puede procesar. Los resultados ya
generados conservan sus definiciones y valores anteriores.

Los catálogos anteriores siguen disponibles para las órdenes históricas. Al abrir
un equipo que tiene uno asignado, se incorpora al borrador del perfil sin modificar
la configuración guardada. El siguiente guardado lo incluye en la revisión del
perfil. La URL anterior del catálogo redirige a esta pantalla única.

Si un perfil de salida requiere `order.fillerId` y la orden recibida no trae
ORC-3 ni OBR-3, el escritor asigna un identificador propio `SIM-…` basado en el
identificador interno persistido de la orden. Es estable entre vista previa,
envíos, reenvíos y reinicios; también funciona con órdenes ya guardadas. Cada
estudio de una salida multiestudio obtiene su identificador cuando le falta.
Los identificadores recibidos se conservan; el HL7 original y las fuentes
`order.orc3`, `order.obr3` e `inbound.*` siguen reflejando exactamente la entrada.

Cada analizador HL7 puede guardar un bloque `behavior` editable desde la UI.
Esto permite adaptar validaciones y eventos sin cambiar codigo fuente.

Campos principales:

- `acceptedMessageTypes`: tipos aceptados, por ejemplo `OML^O21`.
- `requiredSegments`: segmentos obligatorios, por ejemplo `MSH,PID,PV1,ORC,OBR`.
- `requiredFields`: campos obligatorios usando formato HL7, por ejemplo
  `PV1-2`, `PID-3`, `OBR-4.1` u `OBR-4.2`.
- `allowedOrderControls`: valores permitidos en `ORC.1`, por ejemplo `NW,CA`.
- `ackSuccessCode` y `ackErrorCode`: codigos usados en `MSA.1`.
- `ackSuccessText`: texto opcional en `MSA.3`; `AUTO_SEQUENCE` genera una
  secuencia automatica tipo `HIS-SEQ-000001`.
- `outboundEvents`: botones/eventos salientes habilitados en la UI, por ejemplo
  `STATUS_CHANGE,NEW_SAMPLE,DELETE_SAMPLE,REPORT`.
- `allowedScenarios`: escenarios seleccionables para simular errores o estados.
- `reportStatuses`: estados de informe esperados, por ejemplo `F,C,X`.

El parser base extrae los datos disponibles del mensaje, y luego el bloque
`behavior` decide si el mensaje se acepta con ACK positivo o se rechaza con ACK
de error. Asi se puede crear un nuevo analizador copiando el perfil AnaPath y
ajustando mensajes, campos obligatorios, eventos o respuestas desde la vista web.

Ejemplo de CSV para configurar el perfil AnaPath desde la pantalla:

| Campo en la UI | Valor CSV ejemplo | Que significa |
| --- | --- | --- |
| Mensajes aceptados CSV | `OML^O21` | Solo acepta ordenes de laboratorio tipo OML O21. |
| Segmentos obligatorios CSV | `MSH,PID,PV1,ORC,OBR` | El mensaje debe traer encabezado, paciente, visita, orden y procedimiento. |
| Campos obligatorios CSV | `MSH-10,PID-3,PID-5,PV1-2,ORC-2,OBR-4.1,OBR-4.2` | Valida control ID, paciente, nombre, tipo visita, orden HIS, codigo y descripcion del procedimiento. |
| ORC-1 permitidos | `NW,CA` | Acepta nueva orden (`NW`) o cancelacion (`CA`). |
| ACK correcto | `AA` | Responde aceptado en `MSA.1`. |
| ACK error | `AE` | Responde error de aplicacion en `MSA.1`. |
| Texto ACK correcto | `AUTO_SEQUENCE` | Agrega una secuencia automatica tipo `HIS-SEQ-000001`. |
| Eventos salientes CSV | `STATUS_CHANGE,NEW_SAMPLE,DELETE_SAMPLE,REPORT,REPORT_SUPPLEMENTARY,REPORT_CANCELLED` | Habilita botones para estado, nueva muestra, eliminar muestra e informes. |
| Escenarios permitidos CSV | `NORMAL,FORCE_CRITICAL,SAMPLE_ERROR,NO_RESPONSE,MALFORMED_RESULT,REPORT_SUPPLEMENTARY,REPORT_CANCELLED` | Escenarios que se pueden seleccionar para simular comportamiento. |
| Estados informe | `F,C,X` | Informe final, suplementario o cancelado. |

Con esos CSV, este HL7 cumple las reglas porque trae todos los segmentos y
campos requeridos:

Ejemplo minimo de entrada:

```hl7
MSH|^~\&|MIRTH|HOSPITAL|ANAPATH|VITROLINK|20260702103000||OML^O21|MSG0001|P|2.5
PID|1||12345||PACIENTE^DEMO
PV1|1|O
ORC|NW|ORD-001
OBR|1|ORD-001|SMP-001|P028^Biopsia excisional
SPM|1|SMP-001|SMP-001|B02^Biopsia escisional
```

Levante solo este perfil con:

```powershell
Copy-Item .env.anapath.example .env
docker compose -f docker-compose.anapath.yml up --build
```

## Regla de barcode por modo

El simulador se mantiene independiente del LIS:

| Modo | Quien crea la orden | Quien define barcode/sampleId |
| --- | --- | --- |
| `MANUAL` | Analyzer Simulator | Analyzer Simulator / usuario |
| `BIDIRECTIONAL` | Sistema externo | Sistema externo en la orden HL7/ASTM |

En `MANUAL`, la UI permite crear muestra y usar `sampleId` como barcode
simulado. En `BIDIRECTIONAL`, la UI bloquea la creacion manual de muestras: la
orden debe llegar por HL7/ASTM y el usuario carga la muestra escaneando/escribiendo
un barcode ya existente en la worklist.

Errores simulados desde esta fase:

- barcode no encontrado.
- muestra duplicada.
- intento de crear muestra manual en modo bidireccional.
- intento de enviar orden externa a un analizador en modo manual.

Ejemplo minimo de orden:

```hl7
MSH|^~\&|LIS|CLINIC|ANALYZER|SIMLAB|202606031430||ORM^O01|ORDMSG001|P|2.5.1
PID|1||12345||MATA^GIOVANNI
ORC|NW|ORD1001
OBR|1|ORD1001|SMP1001|CBC^HEMOGRAMA
```

## Perfil HL7 único por equipo

**Generar desde mensaje** analiza un ejemplo de entrada o salida y permite aplicar
la base al borrador. Detecta segmentos, repeticiones, componentes y datos del caso;
los OBX alimentan las filas de analitos de ese mismo perfil. No copia pacientes ni
valores de resultados al catálogo. Los generadores existentes se conservan.

**Campos del mensaje** permite elegir dónde leer cada dato o escribirlo en la
respuesta, incluyendo rutas propias del fabricante. La entrada y la salida tienen
rutas independientes dentro de la misma definición. No hay una segunda tabla de
campos básicos ni un generador que guarde por su cuenta.

Al compilar, la salida repite OBR por estudio y OBX por analito, conserva las rutas
propias y los otros eventos, y toma tipo, valor, unidades y referencias de la tabla.
El contrato de salida se deriva de esa plantilla; no quedan requisitos obsoletos
del ejemplo que contradigan las rutas editadas. La vista previa usa el borrador
completo y ejecuta la misma validación de salida que el envío real.

**Guardar cambios** persiste `name`, `inbound`, `outbound` y `catalog.studies`
juntos en una revisión inmutable. `expectedVersion` impide sobrescribir cambios
de otra sesión. El historial permite recuperar una revisión en el mismo borrador;
no se crean perfiles ni asignaciones separados para cada parte.

API principal:

- `GET /api/analyzers/{id}/equipment-profile`: perfil completo; incorpora datos anteriores al abrir.
- `PUT /api/analyzers/{id}/equipment-profile`: guardado atómico, con `expectedVersion`.
- `POST /api/analyzers/{id}/equipment-profile/infer`: incorpora un ejemplo en un borrador completo.
- `POST /api/analyzers/{id}/equipment-profile/preview`: genera la salida desde el mismo borrador.

Las API anteriores se conservan para compatibilidad. No permiten asignar otro
catálogo por fuera a un equipo que ya utiliza el perfil unificado. Las actualizaciones
de conexión tampoco cambian sus códigos ni su política de recepción.

### Recepción permisiva y aprendizaje desde el equipo

En el diseñador, **Modo de recepción** permite elegir:

- **Por contrato**: conserva las reglas estrictas o de advertencia del perfil.
- **Permisivo**: permite activar el listener sin contrato de entrada ni salida.
  Recibe HL7 válido con MSH-9 y MSH-10; registra mensajes que no son órdenes
  en Mensajes, sin crear órdenes ni resultados ficticios. Las órdenes admiten
  códigos desconocidos, que quedan pendientes de catálogo antes de procesarlos.
- **Aprender del primer mensaje externo**: al guardar y activar, espera un HL7
  por el puerto del equipo. El primer mensaje válido genera una revisión nueva
  con el contrato semántico y el mensaje base. Conserva los mapeos básicos,
  el nombre y la salida; continúa en modo permisivo para poder revisar y ajustar
  lo aprendido. Para repetir el aprendizaje, guarda otra revisión en ese modo.

Desmarcar la validación estricta sin tener contrato de entrada habilita la
recepción permisiva. El diseñador carga la revisión aprendida automáticamente
si no hay cambios pendientes en el formulario. Enviar resultados siempre
requiere una definición de salida y resolver los estudios pendientes.

La recepción y la inferencia aceptan segmentos separados por CR, LF, CRLF,
o por textos pegados como `\r`, `\n`, `\r\n` y `/r`. La conversión de textos
se limita a los límites de segmentos para conservar escapes HL7 y URLs.
Los reintentos con distintos saltos de línea conservan la misma identidad.

## Endpoints

- `GET /api/dashboard`
- `GET /api/analyzers`
- `POST /api/analyzers`
- `PUT /api/analyzers/{analyzer_id}`
- `PATCH /api/analyzers/{analyzer_id}/mode`
- `PATCH /api/analyzers/{analyzer_id}/state`
- `PATCH /api/analyzers/{analyzer_id}/scenario`
- `POST /api/analyzers/{analyzer_id}/clear`
- `GET /api/analyzers/{analyzer_id}/protocol-profiles`
- `POST /api/analyzers/{analyzer_id}/protocol-profiles`
- `PUT /api/analyzers/{analyzer_id}/protocol-profiles/{profile_id}`
- `POST /api/analyzers/{analyzer_id}/protocol-profiles/{profile_id}/activate`
- `POST /api/analyzers/{analyzer_id}/protocol-profiles/infer`
- `POST /api/analyzers/{analyzer_id}/protocol-profiles/preview`
- `GET /api/orders`
- `GET /api/messages`
- `POST /api/manual-samples`
- `POST /api/analyzers/{analyzer_id}/scan`
- `POST /api/orders/{order_id}/load`
- `POST /api/orders/{order_id}/process`
- `POST /api/orders/{order_id}/send`
- `POST /api/orders/{order_id}/emit`
- `GET /secure_link/code/{key_code}/payload/{payload}`

## ASTM

Los analizadores con protocolo `ASTM` generan resultados en formato ASTM:

```text
H|...
P|...
O|...
R|...
L|...
```

El envio se hace por TCP simulado al `resultDestinationHost` /
`resultDestinationPort` configurado en el analizador. Esto permite apuntar el
simulador a cualquier puerto donde tengas un canal externo escuchando.

En modo `BIDIRECTIONAL`, un analizador ASTM tambien puede recibir ordenes por su
`listenPort`. El listener responde `ACK` (`0x06`) si crea worklist o `NAK`
(`0x15`) si rechaza la orden.

## Siguientes fases

- WebSockets para progreso en tiempo real.
- Exportar logs/mensajes desde la UI.
