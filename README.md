# HL7 Sandbox

Sandbox minimo dividido en dos maquinas para aprender el flujo HL7:

```text
PC local                               Servidor con Docker
------------------                    ------------------------------
fake-analyzer.py  -- TCP/MLLP ----->  hl7-listener --> PostgreSQL
       ^                                  |
       +------------- ACK <---------------+
```

El repositorio incluye las dos piezas, pero `docker-compose.yml` levanta solamente
la parte del servidor: `hl7-listener` y `postgres`.

No es un LIS ni una integracion hospitalaria. No incluye API, mapping de codigos,
ASTM, deduplicacion, dashboard ni reglas clinicas.

## Parte Del Servidor

Requiere Docker Engine y Docker Compose. En el servidor:

```bash
git clone https://github.com/giomatamm20/Prueba-HL7.git
cd Prueba-HL7
cp .env.example .env
docker compose up --build -d
docker compose logs -f hl7-listener
```

Servicios que se ejecutan:

| Servicio | Funcion |
| --- | --- |
| `hl7-listener` | Escucha TCP/MLLP en el puerto `2575`, responde ACK y parsea HL7. |
| `postgres` | Guarda el mensaje RAW, el ACK, el JSON y cualquier error de parsing. |

El listener publica `2575/tcp` en el servidor. Ese puerto debe permitirse en el
firewall solamente desde la IP de la PC de simulacion mientras se practica.

Consultar los mensajes recibidos en el servidor:

```bash
docker compose exec postgres psql -U hl7user -d hl7sandbox -c "SELECT id, received_at, message_control_id, jsonb_pretty(parsed_json) AS parsed_json, parse_error FROM received_messages ORDER BY id DESC;"
```

Reiniciar la base de practica:

```bash
docker compose down -v
```

## Parte De La PC Local

La PC local no necesita Docker. Requiere Python 3 y acceso de red al puerto
`2575` del servidor.

Desde la carpeta del repositorio en esta PC, enviar una muestra:

```powershell
py services/fake-analyzer/app/main.py send --host <IP_DEL_SERVIDOR> --port 2575 --sample abbott_cbc_001.hl7
```

Enviar resultados automaticamente cada 10 segundos:

```powershell
py services/fake-analyzer/app/main.py auto --host <IP_DEL_SERVIDOR> --port 2575 --interval 10
```

El cliente imprime el ACK recibido. Las muestras que transmite estan en
`samples/hl7/`.

## Flujo Para Aprender

Una muestra contiene:

```hl7
MSH|^~\&|ABBOTT|LAB|LIS|HOSPITAL|202605261700||ORU^R01|MSG001|P|2.3
PID|1||12345||MATA^GIOVANNI
OBR|1||ORD001|CBC^HEMOGRAMA
OBX|1|NM|WBC||8.2|10^3/uL|4.0-10.0|N|||F
```

El listener produce JSON:

```json
{
  "messageControlId": "MSG001",
  "analyzer": "ABBOTT",
  "patientId": "12345",
  "patientName": "MATA^GIOVANNI",
  "orderId": "ORD001",
  "panel": "CBC",
  "tests": [
    { "code": "WBC", "value": 8.2, "unit": "10^3/uL" }
  ]
}
```

Para un mensaje valido devuelve:

```hl7
MSA|AA|MSG001
```

Si falta un segmento requerido o un valor numerico no es valido, devuelve
`MSA|AE|...` y guarda el error en PostgreSQL.

## Archivos Principales

| Archivo | Ejecuta en | Uso |
| --- | --- | --- |
| `docker-compose.yml` | Servidor | Ejecuta listener y PostgreSQL. |
| `services/hl7-listener/app/main.py` | Servidor | Socket MLLP y ACK. |
| `services/hl7-listener/app/parser.py` | Servidor | HL7 a JSON. |
| `services/hl7-listener/app/storage.py` | Servidor | Persistencia PostgreSQL. |
| `services/fake-analyzer/app/main.py` | PC local | Emisor fake y lector del ACK. |
| `samples/hl7/*.hl7` | PC local | Resultados de prueba. |

## Pruebas Locales

Sin Docker se puede verificar parser y comunicacion TCP/MLLP del listener:

```powershell
py -m unittest discover -s services/hl7-listener/tests -v
```
