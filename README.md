# HL7 Lab Integration Simulator

Simulacion realista del flujo de laboratorio clinico:

```text
Servidor Linux con Docker                  PC Windows local
----------------------------------         --------------------------------------
Analyzer Simulation Lab / Mirth     ---->  Host HL7 MLLP listener
HL7 MLLP / ASTM simulado                   ACK/NAK + parser HL7
ORU^R01 resultados                         JSON/modelos internos
ORM/OML ordenes                            PostgreSQL CRM/LIS
UI de analizadores                         Dashboard web
```

El equipo medico simulado vive en el servidor Linux o en Docker local. La PC
local actua como host/middleware receptor, procesa ACK/NAK, persiste datos
clinicos y muestra el flujo en un frontend simple.

## Puertos

| Equipo | Puerto | Uso |
| --- | --- | --- |
| PC local | `2575/tcp` | Listener HL7 MLLP que recibe al analyzer. |
| PC local | `8088/tcp` | Dashboard y API REST del host/CRM. |
| Docker/local | `8090/tcp` | Analyzer Simulation Lab UI/API. |
| Docker/local | `5001/tcp` | Listener ordenes HL7 CELL-DYN Ruby. |
| Docker/local | `5002/tcp` | Listener ordenes HL7 XN-1000. |
| Docker/local | `5003/tcp` | Listener ordenes HL7 ARCHITECT ci4100. |
| Docker/local | `5004/tcp` | Listener ordenes ASTM UC-3500. |
| Docker/local | `5010/tcp` | Listener ordenes HL7 generico. |
| Docker/local | `5011/tcp` | Listener ordenes ASTM generico. |

En este laboratorio la PC local es `192.168.1.101` y el servidor es
`192.168.1.122`.

## 1. PC Local: Host/Middleware CodeIgniter + CRM + Frontend

Requisitos locales:

- PHP 8.1+
- Composer
- Extension PHP `pdo_pgsql`
- PostgreSQL local
- Acceso entrante desde el servidor al puerto `2575`

Crear la configuracion local:

```powershell
Copy-Item .env.local.example .env.local
```

Ejemplo `.env.local`:

```env
DATABASE_URL=postgresql://hl7host:hl7password@localhost:5432/hl7crm
MLLP_HOST=0.0.0.0
MLLP_PORT=2575
WEB_HOST=0.0.0.0
WEB_PORT=8088
```

Crear usuario y base en PostgreSQL local:

```powershell
$env:PGPASSWORD='<POSTGRES_ADMIN_PASSWORD>'
$psql='C:\Program Files\PostgreSQL\17\bin\psql.exe'
& $psql -U postgres -h localhost -p 5432 -d postgres -c "CREATE ROLE hl7host LOGIN PASSWORD 'hl7password';"
& $psql -U postgres -h localhost -p 5432 -d postgres -c "CREATE DATABASE hl7crm OWNER hl7host;"
```

Instalar dependencias:

```powershell
Push-Location services\host-codeigniter
composer install
Copy-Item .env.example .env
Pop-Location
```

Arrancar API/dashboard desde la PC:

```powershell
Push-Location services\host-codeigniter
php spark serve --host 0.0.0.0 --port 8088
```

Abrir dashboard:

```text
http://localhost:8088
```

Arrancar el listener HL7 MLLP en otra terminal:

```powershell
Push-Location services\host-codeigniter
php spark hl7:mllp-listen
```

El host mantiene dos responsabilidades activas:

- Socket HL7 MLLP en `0.0.0.0:2575`.
- API/dashboard en `0.0.0.0:8088`.

## 2. Analyzer Simulation Lab: Modo manual HL7 ORU

El nuevo simulador vive en `services/analyzer-simulator-api` y expone una UI en:

```text
http://localhost:8090
```

Permite crear muestras manuales, seleccionar analizador, seleccionar pruebas,
cargar la muestra, procesarla y enviar un resultado `ORU^R01` por MLLP hacia
Mirth o directamente hacia el host CodeIgniter. Tambien abre listeners MLLP por
analizador HL7 para recibir ordenes `ORM^O01` / `OML^O33`, responder ACK/NACK y
crear worklist bidireccional.

Regla de operacion:

- En modo `MANUAL`, el simulador crea `sampleId`/barcode y no depende de orden
  externa.
- En modo `BIDIRECTIONAL`, la orden y el barcode vienen desde LIS/Mirth; la UI
  solo permite cargar la muestra si el barcode existe en la worklist.

Configurar destino del resultado en `.env`:

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
```

Levantar solo el simulador:

```powershell
docker compose up --build analyzer-simulator-api
```

API principal:

| Endpoint | Uso |
| --- | --- |
| `GET /api/dashboard` | Conteos del laboratorio simulado. |
| `GET /api/analyzers` | Analizadores configurados y estado. |
| `POST /api/analyzers` | Agregar un analizador simulado. |
| `PUT /api/analyzers/{id}` | Editar protocolo, puertos, destino y pruebas. |
| `PATCH /api/analyzers/{id}/mode` | Cambiar entre `MANUAL` y `BIDIRECTIONAL`. |
| `PATCH /api/analyzers/{id}/state` | Cambiar estado interno del equipo. |
| `PATCH /api/analyzers/{id}/scenario` | Forzar escenario interno del analizador. |
| `GET /api/orders` | Worklist/cola manual. |
| `GET /api/messages` | Mensajes HL7 enviados, ACK y errores. |
| `POST /api/manual-samples` | Crear muestra manual. |
| `POST /api/analyzers/{id}/scan` | Cargar muestra escaneando barcode. |
| `POST /api/orders/{id}/load` | Simular carga/escaneo de muestra. |
| `POST /api/orders/{id}/process` | Procesar y enviar resultado. |

Puertos de ordenes HL7 hacia el simulador:

| Puerto | Analizador |
| --- | --- |
| `5001` | CELL-DYN Ruby |
| `5002` | Sysmex XN-1000 |
| `5003` | Abbott ARCHITECT ci4100 |
| `5004` | Sysmex UC-3500 ASTM |
| `5010` | Equipo generico HL7 |
| `5011` | Equipo generico ASTM |

## 3. Servidor Linux: Analyzer HL7 legado

En el servidor:

```bash
git clone https://github.com/giomatamm20/Prueba-HL7.git
cd Prueba-HL7
cp .env.example .env
```

Configurar `.env` del servidor:

```env
HOST_MIDDLEWARE_HOST=192.168.1.101
HOST_MIDDLEWARE_PORT=2575
SEND_INTERVAL_SECONDS=10
ACK_TIMEOUT_SECONDS=8
RETRY_DELAY_SECONDS=5
ANALYZER_NAME=ABBOTT_CELL_DYN
```

Levantar el analyzer legado:

```bash
docker compose up --build -d --remove-orphans
docker compose logs -f fake-analyzer
```

El analyzer abre una conexion TCP hacia la PC, envia un mensaje MLLP, espera ACK
y registra si el host respondio `AA` o rechazo con `AE`.

Enviar un mensaje puntual desde el contenedor:

```bash
docker compose run --rm fake-analyzer python -m app.main send --sample abbott_cbc_001.hl7
```

## Mensajes Simulados

Muestras en `samples/hl7/`:

| Archivo | Tipo | Descripcion |
| --- | --- | --- |
| `abbott_order_001.hl7` | `ORM^O01` | Orden nueva de hemograma. |
| `abbott_cbc_001.hl7` | `ORU^R01` | Resultado CBC para paciente `12345`. |
| `abbott_cbc_002.hl7` | `ORU^R01` | Resultado CBC para paciente `67890`. |

Ejemplo resultado:

```hl7
MSH|^~\&|ABBOTT|LAB|HOST|CLINIC|202605261700||ORU^R01|MSG001|P|2.3
PID|1||12345||MATA^GIOVANNI
OBR|1||ORD001|CBC^HEMOGRAMA
OBX|1|NM|WBC||8.2|10^3/uL|4.0-10.0|N|||F
```

Modelo interno generado:

```json
{
  "eventType": "RESULT",
  "messageType": "ORU^R01",
  "messageControlId": "ABBOTT_CELL_DYN-20260528120000-000001",
  "analyzerCode": "ABBOTT_CELL_DYN",
  "patient": { "id": "12345", "name": "MATA^GIOVANNI" },
  "order": { "id": "ORD001", "panelCode": "CBC", "panelName": "HEMOGRAMA" },
  "tests": [
    { "code": "WBC", "value": 8.2, "unit": "10^3/uL" }
  ]
}
```

ACK positivo:

```hl7
MSA|AA|<MSH-10>
```

ACK de error:

```hl7
MSA|AE|<MSH-10>|<motivo>
```

## Datos Persistidos

La PC local almacena en PostgreSQL:

| Tabla | Contenido |
| --- | --- |
| `raw_hl7_messages` | HL7 original, JSON parseado, ACK, estado y errores. |
| `patients` | Pacientes recibidos por `PID`. |
| `lab_orders` | Ordenes/paneles recibidos por `ORM` o `ORU`. |
| `lab_results` | Resultados `OBX` normalizados. |

Consulta rapida:

```powershell
$env:PGPASSWORD='hl7password'
& 'C:\Program Files\PostgreSQL\17\bin\psql.exe' -U hl7host -h localhost -p 5432 -d hl7crm -c "SELECT id, message_type, message_control_id, ack_code, processing_status FROM raw_hl7_messages ORDER BY id DESC;"
```

## API REST Local

El dashboard consume:

| Endpoint | Uso |
| --- | --- |
| `GET /api/status` | Estado del listener, ultimo ACK y conteos. |
| `GET /api/messages` | Mensajes HL7 RAW, ACK y JSON. |
| `GET /api/patients` | Pacientes vistos. |
| `GET /api/orders` | Ordenes/paneles. |
| `GET /api/results` | Resultados de laboratorio. |

## Archivos Principales

| Archivo | Rol |
| --- | --- |
| `docker-compose.yml` | Analyzer Docker del servidor Linux. |
| `services/analyzer-simulator-api/app/main.py` | API del Analyzer Simulation Lab. |
| `services/analyzer-simulator-api/app/engine.py` | Motor de simulacion, estados y resultados. |
| `services/analyzer-simulator-api/app/store.py` | Persistencia interna SQLite del simulador. |
| `services/analyzer-simulator-api/app/astm.py` | Generador ASTM y envio TCP simulado. |
| `services/analyzer-simulator-api/app/hl7_orders.py` | Parser de ordenes `ORM^O01` / `OML^O33` y ACK. |
| `services/analyzer-simulator-api/app/listeners.py` | Listeners MLLP de ordenes por analizador. |
| `services/analyzer-simulator-api/app/protocols.py` | HL7 ORU y cliente MLLP. |
| `services/analyzer-simulator-api/app/static/index.html` | UI grafica del laboratorio simulado. |
| `services/fake-analyzer/app/main.py` | Cliente TCP/MLLP que simula el equipo medico. |
| `services/host-codeigniter/app/Commands/MllpListen.php` | Listener MLLP, procesamiento y ACK/NAK. |
| `services/host-codeigniter/app/Libraries/Hl7Parser.php` | Parser `ORM^O01` y `ORU^R01`. |
| `services/host-codeigniter/app/Models/CrmStore.php` | Persistencia CRM/LIS en PostgreSQL. |
| `services/host-codeigniter/app/Views/dashboard.php` | Frontend de monitoreo. |
| `database/init.sql` | Esquema PostgreSQL local. |

## Pruebas

```powershell
Push-Location services\host-codeigniter
composer install
php spark routes
```
