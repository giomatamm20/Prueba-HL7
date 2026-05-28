# HL7 Lab Integration Simulator

Simulacion realista del flujo de laboratorio clinico:

```text
Servidor Linux con Docker                  PC Windows local
----------------------------------         --------------------------------------
Fake Medical Analyzer / Middleware  ---->  Host HL7 MLLP listener
ABBOTT_CELL_DYN por TCP/MLLP               ACK/NAK + parser HL7
ORU^R01 resultados                         JSON/modelos internos
ORM^O01 ordenes                            PostgreSQL CRM/LIS
                                            Dashboard web
```

El equipo medico simulado vive en el servidor Linux. La PC local actua como
host/middleware receptor, procesa ACK/NAK, persiste datos clinicos y muestra el
flujo en un frontend simple.

## Puertos

| Equipo | Puerto | Uso |
| --- | --- | --- |
| PC local | `2575/tcp` | Listener HL7 MLLP que recibe al analyzer. |
| PC local | `8088/tcp` | Dashboard y API REST del host/CRM. |

En este laboratorio la PC local es `192.168.1.101` y el servidor es
`192.168.1.122`.

## 1. PC Local: Host/Middleware + CRM + Frontend

Requisitos locales:

- Python 3.11
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
py -3.11 -m venv .venv-host
.\.venv-host\Scripts\python.exe -m pip install -r services\host-middleware\requirements.txt
```

Arrancar host/middleware desde la PC:

```powershell
Push-Location services\host-middleware
..\..\.venv-host\Scripts\python.exe -m app.run
Pop-Location
```

Abrir dashboard:

```text
http://localhost:8088
```

El host mantiene dos responsabilidades activas:

- Socket HL7 MLLP en `0.0.0.0:2575`.
- API/dashboard en `0.0.0.0:8088`.

## 2. Servidor Linux: Analyzer HL7

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

Levantar el analyzer:

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
| `services/fake-analyzer/app/main.py` | Cliente TCP/MLLP que simula el equipo medico. |
| `services/host-middleware/app/mllp.py` | Listener MLLP, procesamiento y ACK/NAK. |
| `services/host-middleware/app/parser.py` | Parser `ORM^O01` y `ORU^R01`. |
| `services/host-middleware/app/storage.py` | Persistencia CRM/LIS en PostgreSQL. |
| `services/host-middleware/app/static/index.html` | Frontend de monitoreo. |
| `database/init.sql` | Esquema PostgreSQL local. |

## Pruebas

```powershell
.\.venv-host\Scripts\python.exe -m unittest discover -s services\host-middleware\tests -v
```
