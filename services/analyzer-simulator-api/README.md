# Analyzer Simulation Lab

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
  - recibe `ORM^O01`, `OML^O33` y ordenes ASTM.
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
- ASTM modelado para envio TCP simulado con registros `H`, `P`, `O`, `R`, `L`.
- Escenarios internos del equipo: offline, mantenimiento, calibracion, QC,
  reactivo bajo, rack lleno, puerta abierta, resultado critico, error de
  muestra, no respuesta y resultado malformado.

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

## Endpoints

- `GET /api/dashboard`
- `GET /api/analyzers`
- `POST /api/analyzers`
- `PUT /api/analyzers/{analyzer_id}`
- `PATCH /api/analyzers/{analyzer_id}/mode`
- `PATCH /api/analyzers/{analyzer_id}/state`
- `PATCH /api/analyzers/{analyzer_id}/scenario`
- `GET /api/orders`
- `GET /api/messages`
- `POST /api/manual-samples`
- `POST /api/analyzers/{analyzer_id}/scan`
- `POST /api/orders/{order_id}/load`
- `POST /api/orders/{order_id}/process`
- `POST /api/orders/{order_id}/send`

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
